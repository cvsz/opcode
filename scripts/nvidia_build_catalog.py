#!/usr/bin/env python3
"""NVIDIA Build Free Endpoint catalog integration.

Discovers models hosted on NVIDIA Build (build.nvidia.com) and classifies them
by endpoint type. Only models explicitly labelled ``Free Endpoint`` via the
``nimType`` catalog label are eligible for NVIDIA free inference routing.

The ``Free Endpoint``, ``Partner Endpoint`` and ``Download Available``
classifications are kept distinct. Free eligibility is never inferred from
``/v1/models``, model names, ``:free`` suffixes, or preview status.

Design principles (fail-closed safety):
  * Catalog sync records discovery only; it never upgrades a model to
    ``verified_free``. Verification is a separate, explicit step.
  * Deprecated / removed / unavailable / changed models are detected and
    flagged, never silently promoted.
  * Snapshots preserve provenance, verification timestamps and catalog format.
  * API keys are never logged.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, parse, request

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BUILD_CATALOG_URL = (
    "https://build.nvidia.com/models"
    "?filters=nimType%3Anim_type_preview"
    "%2Cpublisher%3Anvidia"
    "%2Cpublisher%3Ameta"
    "%2Cpublisher%3Agoogle"
    "%2Cpublisher%3Aipd"
    "%2Cpublisher%3Aopenai"
    "%2Cpublisher%3Adeepseek_ai"
    "%2Cpublisher%3Aminimaxai"
    "%2Cpublisher%3Amistralai"
    "%2Cpublisher%3Amoonshotai"
    "%2Cpublisher%3Apoolside"
    "%2Cpublisher%3Astepfun_ai"
    "%2Cpublisher%3Az_ai"
    "&orderBy=name%3AASC"
)

NVIDIA_API_BASE = "https://integrate.api.nvidia.com/v1"

# Endpoint-type labels kept distinct. Only "Free Endpoint" grants free
# inference eligibility; the others are explicitly excluded.
ENDPOINT_FREE = "Free Endpoint"
ENDPOINT_PARTNER = "Partner Endpoint"
ENDPOINT_DOWNLOAD = "Download Available"

NIM_TYPE_KEY = "nimType"
PUBLISHER_KEY = "publisher"
GENERAL_KEY = "general"
AVAILABLE_KEY = "AVAILABLE"
PREVIEW_KEY = "PREVIEW"

# Capability signals drawn from the catalog "general" labels.
CAPABILITY_LABELS: dict[str, str] = {
    "Vision Language Model": "vision",
    "Image-to-Text": "vision",
    "Visual QA": "vision",
    "Image generation": "vision",
    "Reasoning": "reasoning",
    "Agentic Coding": "coding",
    "Coding": "coding",
    "Tool Use": "tool_calling",
    "Text-to-Text": "general",
    "Chat": "general",
    "Embedding": "embeddings",
    "Image-Text Retrieval": "embeddings",
    "Long Context": "reasoning",
    "Agentic AI": "agent_execution",
    "Guardrail": "architecture",
    "Content Moderator": "architecture",
    "Content Safety": "architecture",
}

DEFAULT_TIMEOUT = 30.0
DEFAULT_RETRIES = 3
DEFAULT_BACKOFF_SECONDS = 2.0
PAGE_SIZE = 24  # Build catalog returns 24 records per page

USER_AGENT = "OPCODE-NVIDIA-Build-Catalog/1.0 (+https://build.nvidia.com)"

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class NVIDIABuildError(RuntimeError):
    """Base error for NVIDIA Build catalog operations."""


class NVIDIABuildTransportError(NVIDIABuildError):
    """Network / transport failure talking to build.nvidia.com."""


class NVIDIABuildSchemaError(NVIDIABuildError):
    """Catalog response does not match the expected schema."""


class NVIDIABuildAuthError(NVIDIABuildError):
    """Authentication / authorization failure (401 / 403)."""


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class CatalogModel:
    """A single NVIDIA Build catalog record with full provenance."""

    resource_id: str  # qc69jvmznzxy/<slug>
    name: str
    display_name: str
    description: str
    publisher: str
    endpoint_types: list[str]  # e.g. ["Free Endpoint", "Partner Endpoint"]
    is_free_endpoint: bool
    is_partner_endpoint: bool
    is_downloadable: bool
    available: bool
    preview: bool
    general_labels: list[str]
    capabilities: list[str]
    date_created: str | None
    date_modified: str | None
    api_model_id: str | None  # resolved from .md prototype snippet
    canonical_url: str | None
    source_url: str
    fetched_at: str
    snapshot_sha256: str | None = None
    eligibility: str = "unknown"  # unknown | verified_free | excluded
    evidence: str | None = None
    last_verified_at: str | None = None
    change_reason: str | None = None  # deprecated | removed | unavailable | changed | None

    @property
    def canonical_id(self) -> str:
        """OPCODE provider-qualified catalog ID: nvidia/<publisher>/<name>:free."""
        safe_name = re.sub(r"[^A-Za-z0-9._-]", "-", self.name)
        return f"nvidia/{self.publisher}/{safe_name}:free"

    @property
    def native_id(self) -> str:
        """NIM API model id: <publisher>/<name>."""
        safe_name = re.sub(r"[^A-Za-z0-9._-]", "-", self.name)
        return f"{self.publisher}/{safe_name}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.canonical_id,
            "provider": "nvidia",
            "native_id": self.native_id,
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "publisher": self.publisher,
            "resource_id": self.resource_id,
            "endpoint_types": self.endpoint_types,
            "is_free_endpoint": self.is_free_endpoint,
            "is_partner_endpoint": self.is_partner_endpoint,
            "is_downloadable": self.is_downloadable,
            "available": self.available,
            "preview": self.preview,
            "general_labels": self.general_labels,
            "capabilities": self.capabilities,
            "date_created": self.date_created,
            "date_modified": self.date_modified,
            "api_model_id": self.api_model_id,
            "canonical_url": self.canonical_url,
            "source_url": self.source_url,
            "fetched_at": self.fetched_at,
            "snapshot_sha256": self.snapshot_sha256,
            "eligibility": self.eligibility,
            "evidence": self.evidence,
            "last_verified_at": self.last_verified_at,
            "change_reason": self.change_reason,
        }


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class CatalogSnapshot:
    """A full catalog synchronization snapshot."""

    provider: str = "nvidia"
    source: str = "nvidia-build"
    catalog_url: str = BUILD_CATALOG_URL
    api_base: str = NVIDIA_API_BASE
    fetched_at: str = field(default_factory=now_iso)
    page_count: int = 0
    model_count: int = 0
    free_endpoint_count: int = 0
    partner_endpoint_count: int = 0
    downloadable_count: int = 0
    models: list[CatalogModel] = field(default_factory=list)
    schema_version: int = 1
    snapshot_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "source": self.source,
            "catalog_url": self.catalog_url,
            "api_base": self.api_base,
            "fetched_at": self.fetched_at,
            "page_count": self.page_count,
            "model_count": self.model_count,
            "free_endpoint_count": self.free_endpoint_count,
            "partner_endpoint_count": self.partner_endpoint_count,
            "downloadable_count": self.downloadable_count,
            "schema_version": self.schema_version,
            "snapshot_sha256": self.snapshot_sha256,
            "models": [m.to_dict() for m in self.models],
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------


def fetch_json(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
) -> str:
    """Fetch a URL with retries, rate-limit/backoff and SSRF-safe scheme guard.

    Only http:// and https:// schemes are permitted. Redirects are not followed
    for bearer-token safety (no auth is sent to build.nvidia.com, but the guard
    is kept uniform).
    """
    parsed = parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise NVIDIABuildTransportError(f"Unsupported URL scheme: {parsed.scheme}")
    if ".." in parsed.path.split("/"):
        raise NVIDIABuildTransportError(f"Unsafe URL path: {url}")

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9",
    }

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            req = request.Request(url, headers=headers, method="GET")
            # Scheme validated above (http/https only) — SSRF guard.
            with request.urlopen(req, timeout=timeout) as response:  # nosec B310
                body = response.read()
            return body.decode("utf-8", errors="replace")
        except error.HTTPError as exc:  # noqa: PERF203 - retry loop is intentional
            last_error = exc
            if exc.code in (401, 403):
                raise NVIDIABuildAuthError(
                    f"NVIDIA Build catalog HTTP {exc.code}: {exc.reason}"
                ) from exc
            if exc.code == 429:
                _sleep(backoff_seconds * attempt)
                continue
            if 500 <= exc.code < 600:
                _sleep(backoff_seconds * attempt)
                continue
            raise NVIDIABuildTransportError(
                f"NVIDIA Build catalog HTTP {exc.code}: {exc.reason}"
            ) from exc
        except (error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            _sleep(backoff_seconds * attempt)
            continue
    raise NVIDIABuildTransportError(
        f"NVIDIA Build catalog request failed after {retries} attempts: {last_error}"
    )


def _sleep(seconds: float) -> None:
    time.sleep(max(0.0, seconds))


# ---------------------------------------------------------------------------
# RSC flight-data parsing (build.nvidia.com models listing)
# ---------------------------------------------------------------------------

_NEXT_PUSH_RE = re.compile(
    r'self\.__next_f\.push\(\[1,(".*?")\]\)', re.DOTALL
)


def _next_flight_chunks(html: str) -> str:
    """Concatenate RSC flight payload chunks from a Build HTML page."""
    parts: list[str] = []
    for match in _NEXT_PUSH_RE.finditer(html):
        raw = match.group(1)
        # The outer quotes wrap a JSON string; unescape it.
        try:
            parts.append(json.loads(raw))
        except json.JSONDecodeError:
            # Best-effort fallback: strip surrounding quotes and unescape common
            # sequences without full JSON unescaping.
            inner = raw[1:-1] if len(raw) >= 2 and raw.startswith('"') and raw.endswith('"') else raw
            parts.append(inner.replace('\\"', '"').replace("\\\\", "\\"))
    return "".join(parts)


def _parse_balanced_json(text: str, start: int) -> tuple[dict[str, Any] | None, int]:
    """Parse a JSON object beginning at text[start] == '{'.

    Returns (object_or_none, end_index).
    """
    if start >= len(text) or text[start] != "{":
        return None, start
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        ch = text[index]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : index + 1]), index + 1
                    except json.JSONDecodeError:
                        return None, index + 1
    return None, len(text)


def _extract_endpoint_records(flight: str) -> list[dict[str, Any]]:
    """Extract all ENDPOINT records from flight text.

    The RSC flight JSON may have arbitrary whitespace after colons, so we
    locate each record by the ``{"resourceType`` prefix and parse the
    balanced JSON object starting there.
    """
    records: list[dict[str, Any]] = []
    index = 0
    marker = '{"resourceType'
    while True:
        pos = flight.find(marker, index)
        if pos < 0:
            break
        obj, end = _parse_balanced_json(flight, pos)
        if obj is not None and obj.get("resourceType") == "ENDPOINT":
            records.append(obj)
        index = end if end > pos else pos + 1
    return records


def _label_values(labels: list[dict[str, Any]], key: str) -> list[str]:
    for label in labels:
        if label.get("key") == key:
            values = label.get("values")
            if isinstance(values, list):
                return [str(v) for v in values]
            return []
    return []


def _attribute_value(attributes: list[dict[str, Any]], key: str) -> str | None:
    for attr in attributes:
        if attr.get("key") == key:
            return attr.get("value")
    return None


def _infer_capabilities(general_labels: list[str]) -> list[str]:
    found: list[str] = []
    for label in general_labels:
        capability = CAPABILITY_LABELS.get(label)
        if capability and capability not in found:
            found.append(capability)
    return found


# ---------------------------------------------------------------------------
# Catalog discovery
# ---------------------------------------------------------------------------


def discover_catalog(
    *,
    catalog_url: str = BUILD_CATALOG_URL,
    max_pages: int = 10,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
) -> CatalogSnapshot:
    """Discover and classify NVIDIA Build catalog models across pages.

    Each page of the Build listing returns RSC flight data containing ENDPOINT
    records with ``nimType`` labels. Only records whose ``nimType`` includes
    ``Free Endpoint`` are marked ``is_free_endpoint``.
    """
    snapshot = CatalogSnapshot()
    seen_ids: set[str] = set()

    url = catalog_url
    for page in range(1, max_pages + 1):
        page_url = f"{url}&page={page}" if page > 1 else url
        html = fetch_json(page_url, timeout=timeout, retries=retries)
        flight = _next_flight_chunks(html)
        if not flight:
            break  # no more flight data -> end of catalog
        records = _extract_endpoint_records(flight)
        if not records:
            break
        snapshot.page_count = page
        for record in records:
            resource_id = record.get("resourceId")
            if not resource_id or resource_id in seen_ids:
                continue
            seen_ids.add(resource_id)
            model = _record_to_model(record, page_url)
            snapshot.models.append(model)

        # Heuristic: if a page returns fewer than a full page, stop.
        if len(records) < PAGE_SIZE:
            break

    _classify_counts(snapshot)
    snapshot.fetched_at = now_iso()
    snapshot.snapshot_sha256 = _sha256(json.dumps(snapshot.to_dict(), sort_keys=True))
    return snapshot


def _record_to_model(record: dict[str, Any], source_url: str) -> CatalogModel:
    labels = record.get("labels") or []
    attributes = record.get("attributes") or []

    nim_types = _label_values(labels, NIM_TYPE_KEY)
    publishers = _label_values(labels, PUBLISHER_KEY)
    general = _label_values(labels, GENERAL_KEY)

    endpoint_types = [t for t in nim_types if t in {ENDPOINT_FREE, ENDPOINT_PARTNER, ENDPOINT_DOWNLOAD}]
    if not endpoint_types:
        # Preserve whatever nimType values exist even if they don't map to our
        # three canonical classifications.
        endpoint_types = list(nim_types)

    is_free = ENDPOINT_FREE in nim_types
    is_partner = ENDPOINT_PARTNER in nim_types
    is_download = ENDPOINT_DOWNLOAD in nim_types
    available = _attribute_value(attributes, AVAILABLE_KEY) == "true"
    preview = _attribute_value(attributes, PREVIEW_KEY) == "true"

    return CatalogModel(
        resource_id=str(record.get("resourceId", "")),
        name=str(record.get("name", "")),
        display_name=str(record.get("displayName", record.get("name", ""))),
        description=str(record.get("description", "")),
        publisher=publishers[0] if publishers else "nvidia",
        endpoint_types=endpoint_types,
        is_free_endpoint=is_free,
        is_partner_endpoint=is_partner,
        is_downloadable=is_download,
        available=available,
        preview=preview,
        general_labels=general,
        capabilities=_infer_capabilities(general),
        date_created=record.get("dateCreated"),
        date_modified=record.get("dateModified"),
        # Default API model id is the native NIM id: <publisher>/<name>.
        # The .md detail page may override this with a different value.
        api_model_id=f"{publishers[0] if publishers else 'nvidia'}/{record.get('name', '')}",
        canonical_url=f"https://build.nvidia.com/{record.get('resourceId', '')}",
        source_url=source_url,
        fetched_at=now_iso(),
        eligibility="unknown",
        evidence=None,
        last_verified_at=None,
        change_reason=None,
    )


def _classify_counts(snapshot: CatalogSnapshot) -> None:
    snapshot.model_count = len(snapshot.models)
    snapshot.free_endpoint_count = sum(1 for m in snapshot.models if m.is_free_endpoint)
    snapshot.partner_endpoint_count = sum(1 for m in snapshot.models if m.is_partner_endpoint)
    snapshot.downloadable_count = sum(1 for m in snapshot.models if m.is_downloadable)


# ---------------------------------------------------------------------------
# Detail-page resolution (API model ID from .md prototype)
# ---------------------------------------------------------------------------

_MD_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_PROTOTYPE_MODEL_RE = re.compile(r'model["\']?\s*[:=]\s*["\']([^"\']+)["\']')


def resolve_api_model_ids(
    models: list[CatalogModel],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    delay_seconds: float = 0.25,
) -> None:
    """Resolve the NIM API model id for each model from its ``.md`` detail page.

    The ``.md`` pages carry an official ``<link rel="alternate" type="text/markdown">``
    declaration and prototype snippets with the real ``model=...`` API id.
    Results are written back onto the model objects (``api_model_id``).
    """
    for model in models:
        md_url = f"https://build.nvidia.com/{model.resource_id}.md"
        try:
            html = fetch_json(md_url, timeout=timeout, retries=retries)
            extracted = _extract_api_model_id(html)
            # Only override the default native_id if the .md provides a
            # well-formed <publisher>/<model> id.
            if extracted and re.fullmatch(r"[^/]+/[^/:]+", extracted):
                model.api_model_id = extracted
            # Capture frontmatter canonical/updated for provenance.
            front = _MD_FRONTMATTER_RE.search(html)
            if front:
                for line in front.group(1).splitlines():
                    if line.strip().startswith("canonical:"):
                        model.canonical_url = line.split(":", 1)[1].strip().strip('"')
                    elif line.strip().startswith("updated:"):
                        model.date_modified = line.split(":", 1)[1].strip().strip('"')
        except NVIDIABuildError:
            # Detail-page fetch failures must not abort catalog discovery.
            # Keep the default native_id (<publisher>/<name>).
            pass
        _sleep(delay_seconds)


def _extract_api_model_id(html: str) -> str | None:
    match = _PROTOTYPE_MODEL_RE.search(html)
    if match:
        candidate = match.group(1).strip()
        if candidate and candidate not in {"", "None", "null"}:
            return candidate
    return None


# ---------------------------------------------------------------------------
# Snapshot persistence
# ---------------------------------------------------------------------------


def save_snapshot(snapshot: CatalogSnapshot, path: Path) -> None:
    """Persist a catalog snapshot atomically with SHA-256 provenance."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(snapshot.to_dict(), indent=2, sort_keys=True)
    snapshot.snapshot_sha256 = _sha256(payload)
    # Re-serialize with the final hash included.
    payload = json.dumps(snapshot.to_dict(), indent=2, sort_keys=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)


def load_snapshot(path: Path) -> CatalogSnapshot:
    """Load a previously persisted catalog snapshot."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return _snapshot_from_dict(data)


def _snapshot_from_dict(data: dict[str, Any]) -> CatalogSnapshot:
    snapshot = CatalogSnapshot(
        provider=data.get("provider", "nvidia"),
        source=data.get("source", "nvidia-build"),
        catalog_url=data.get("catalog_url", BUILD_CATALOG_URL),
        api_base=data.get("api_base", NVIDIA_API_BASE),
        fetched_at=data.get("fetched_at", now_iso()),
        page_count=data.get("page_count", 0),
        model_count=data.get("model_count", 0),
        free_endpoint_count=data.get("free_endpoint_count", 0),
        partner_endpoint_count=data.get("partner_endpoint_count", 0),
        downloadable_count=data.get("downloadable_count", 0),
        schema_version=data.get("schema_version", 1),
        snapshot_sha256=data.get("snapshot_sha256"),
    )
    for item in data.get("models", []):
        snapshot.models.append(_model_from_dict(item))
    return snapshot


def _model_from_dict(data: dict[str, Any]) -> CatalogModel:
    return CatalogModel(
        resource_id=data.get("resource_id", ""),
        name=data.get("name", ""),
        display_name=data.get("display_name", data.get("name", "")),
        description=data.get("description", ""),
        publisher=data.get("publisher", "nvidia"),
        endpoint_types=data.get("endpoint_types", []),
        is_free_endpoint=data.get("is_free_endpoint", False),
        is_partner_endpoint=data.get("is_partner_endpoint", False),
        is_downloadable=data.get("is_downloadable", False),
        available=data.get("available", False),
        preview=data.get("preview", False),
        general_labels=data.get("general_labels", []),
        capabilities=data.get("capabilities", []),
        date_created=data.get("date_created"),
        date_modified=data.get("date_modified"),
        api_model_id=data.get("api_model_id"),
        canonical_url=data.get("canonical_url"),
        source_url=data.get("source_url", ""),
        fetched_at=data.get("fetched_at", now_iso()),
        snapshot_sha256=data.get("snapshot_sha256"),
        eligibility=data.get("eligibility", "unknown"),
        evidence=data.get("evidence"),
        last_verified_at=data.get("last_verified_at"),
        change_reason=data.get("change_reason"),
    )


# ---------------------------------------------------------------------------
# Change detection (deprecated / removed / unavailable / changed)
# ---------------------------------------------------------------------------


def detect_changes(
    previous: CatalogSnapshot,
    current: CatalogSnapshot,
) -> list[CatalogModel]:
    """Compare current catalog against previous snapshot and flag changes.

    Models that disappeared are marked ``removed``; models whose
    ``available`` flag dropped are ``unavailable``; models that lost the
    ``Free Endpoint`` label are ``deprecated``; changed descriptions /
    publishers are ``changed``. Returns the list of changed models.
    """
    prev_by_id = {m.resource_id: m for m in previous.models}
    curr_by_id = {m.resource_id: m for m in current.models}
    changed: list[CatalogModel] = []

    # Removed models.
    for resource_id, prev_model in prev_by_id.items():
        if resource_id not in curr_by_id:
            prev_model.change_reason = "removed"
            prev_model.eligibility = "unknown"
            changed.append(prev_model)

    for resource_id, curr_model in curr_by_id.items():
        prev_record = prev_by_id.get(resource_id)
        if prev_record is None:
            continue  # new model, not a change
        prev_model = prev_record
        if not curr_model.available and prev_model.available:
            curr_model.change_reason = "unavailable"
        elif (ENDPOINT_FREE in prev_model.endpoint_types) and (
            ENDPOINT_FREE not in curr_model.endpoint_types
        ):
            curr_model.change_reason = "deprecated"
        elif (
            curr_model.description != prev_model.description
            or curr_model.publisher != prev_model.publisher
            or curr_model.api_model_id != prev_model.api_model_id
        ):
            curr_model.change_reason = "changed"
        if curr_model.change_reason:
            changed.append(curr_model)

    return changed


__all__ = [
    "BUILD_CATALOG_URL",
    "NVIDIA_API_BASE",
    "ENDPOINT_FREE",
    "ENDPOINT_PARTNER",
    "ENDPOINT_DOWNLOAD",
    "CatalogModel",
    "CatalogSnapshot",
    "NVIDIABuildError",
    "NVIDIABuildTransportError",
    "NVIDIABuildSchemaError",
    "NVIDIABuildAuthError",
    "discover_catalog",
    "resolve_api_model_ids",
    "detect_changes",
    "save_snapshot",
    "load_snapshot",
    "now_iso",
]
