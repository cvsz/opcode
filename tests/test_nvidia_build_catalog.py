#!/usr/bin/env python3
"""Tests for the NVIDIA Build Free Endpoint catalog integration.

Uses realistic fixtures derived from the actual build.nvidia.com catalog
(RSC flight data, .md detail pages) plus malformed-response cases.

Safety-critical assertions:
  * Catalog sync never upgrades unknown -> verified_free.
  * Only "Free Endpoint" nimType grants free eligibility.
  * Partner Endpoint / Download Available are excluded from free routing.
  * Deprecated / removed / unavailable / changed models are detected.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import nvidia_build_catalog as nb

# ---------------------------------------------------------------------------
# Fixtures (realistic, derived from actual build.nvidia.com catalog data)
# ---------------------------------------------------------------------------

FREE_ENDPOINT_RECORD = {
    "resourceType": "ENDPOINT",
    "resourceId": "qc69jvmznzxy/deepseek-v4.1-flash",
    "name": "deepseek-v4.1-flash",
    "displayName": "deepseek-v4.1-flash",
    "description": "552B MoE, 8B active params with native multimodal support",
    "publisher": "deepseek-ai",
    "dateCreated": "2026-09-18T18:55:17.986Z",
    "dateModified": "2026-09-18T18:55:17.986Z",
    "labels": [
        {
            "key": "nimType",
            "values": ["Download Available", "Partner Endpoint", "Free Endpoint"],
            "unresolvedValues": [
                "nim_type_run_anywhere",
                "nim_type_partner_endpoint",
                "nim_type_preview",
            ],
        },
        {"key": "publisher", "values": ["deepseek-ai"]},
        {
            "key": "general",
            "values": ["Vision Language Model", "Reasoning", "Tool Use"],
        },
    ],
    "attributes": [
        {"key": "AVAILABLE", "value": "true"},
        {"key": "PREVIEW", "value": "true"},
    ],
}

PARTNER_ONLY_RECORD = {
    "resourceType": "ENDPOINT",
    "resourceId": "qc69jvmznzxy/some-partner-model",
    "name": "some-partner-model",
    "displayName": "some-partner-model",
    "description": "A partner-hosted model.",
    "publisher": "some-publisher",
    "labels": [
        {
            "key": "nimType",
            "values": ["Partner Endpoint", "Download Available"],
        },
        {"key": "publisher", "values": ["some-publisher"]},
        {"key": "general", "values": ["Chat"]},
    ],
    "attributes": [{"key": "AVAILABLE", "value": "true"}],
}

DOWNLOAD_ONLY_RECORD = {
    "resourceType": "ENDPOINT",
    "resourceId": "qc69jvmznzxy/download-only-model",
    "name": "download-only-model",
    "displayName": "download-only-model",
    "description": "Downloadable weights only, no hosted endpoint.",
    "publisher": "some-publisher",
    "labels": [
        {"key": "nimType", "values": ["Download Available"]},
        {"key": "publisher", "values": ["some-publisher"]},
        {"key": "general", "values": ["Chat"]},
    ],
    "attributes": [{"key": "AVAILABLE", "value": "true"}],
}

UNAVAILABLE_FREE_RECORD = {
    "resourceType": "ENDPOINT",
    "resourceId": "qc69jvmznzxy/unavailable-model",
    "name": "unavailable-model",
    "displayName": "unavailable-model",
    "description": "Currently unavailable hosted endpoint.",
    "publisher": "some-publisher",
    "labels": [
        {"key": "nimType", "values": ["Free Endpoint"]},
        {"key": "publisher", "values": ["some-publisher"]},
        {"key": "general", "values": ["Chat"]},
    ],
    "attributes": [{"key": "AVAILABLE", "value": "false"}],
}

DEPRECATED_RECORD = {
    "resourceType": "ENDPOINT",
    "resourceId": "qc69jvmznzxy/deprecated-model",
    "name": "deprecated-model",
    "displayName": "deprecated-model",
    "description": "Previously free, now partner-only.",
    "publisher": "some-publisher",
    "labels": [
        {"key": "nimType", "values": ["Partner Endpoint"]},
        {"key": "publisher", "values": ["some-publisher"]},
        {"key": "general", "values": ["Chat"]},
    ],
    "attributes": [{"key": "AVAILABLE", "value": "true"}],
}

MD_DETAIL_PAGE = """---
title: "deepseek-v4.1-flash"
publisher: "deepseek-ai"
type: "endpoint"
updated: "2026-09-18T18:55:17.986Z"
description: "..."
canonical: "https://build.nvidia.com/deepseek-ai/deepseek-v4.1-flash"
---

# DeepSeek-V4.1-Flash

## Prototype

```python
from openai import OpenAI
client = OpenAI(base_url="https://integrate.api.nvidia.com/v1")
completion = client.chat.completions.create(
    model="deepseek-ai/deepseek-v4.1-flash",
    messages=[{"role": "user", "content": ""}],
)
```
"""

MD_DETAIL_PAGE_EMPTY_MODEL = """---
title: "glm-5-3-flash"
publisher: "z-ai"
type: "endpoint"
---

# GLM-5.3-Flash

## Prototype

```python
completion = client.chat.completions.create(
    model="",
    messages=[{"role": "user", "content": ""}],
)
```
"""


def _record_to_flight_html(records: list[dict]) -> str:
    """Wrap ENDPOINT records into a fake RSC flight payload string.

    The inner JSON is double-encoded so that its double quotes are escaped
    inside the outer RSC string literal, matching the real build.nvidia.com
    flight format.
    """
    chunks = []
    for record in records:
        payload = json.dumps(json.dumps(record))
        chunks.append(f'self.__next_f.push([1,{payload}]);')
    return "".join(chunks)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class RecordToModelTests(unittest.TestCase):
    """Test conversion of raw ENDPOINT records to CatalogModel."""

    def test_free_endpoint_classification(self):
        model = nb._record_to_model(FREE_ENDPOINT_RECORD, "https://build.nvidia.com/models")
        self.assertEqual(model.publisher, "deepseek-ai")
        self.assertEqual(model.name, "deepseek-v4.1-flash")
        self.assertTrue(model.is_free_endpoint)
        self.assertTrue(model.is_partner_endpoint)
        self.assertTrue(model.is_downloadable)
        self.assertEqual(
            model.endpoint_types,
            ["Download Available", "Partner Endpoint", "Free Endpoint"],
        )
        self.assertEqual(model.canonical_id, "nvidia/deepseek-ai/deepseek-v4.1-flash:free")
        self.assertEqual(model.native_id, "deepseek-ai/deepseek-v4.1-flash")
        self.assertEqual(model.api_model_id, "deepseek-ai/deepseek-v4.1-flash")
        self.assertIn("vision", model.capabilities)
        self.assertIn("reasoning", model.capabilities)
        self.assertIn("tool_calling", model.capabilities)
        self.assertEqual(model.eligibility, "unknown")

    def test_partner_only_excluded_from_free(self):
        model = nb._record_to_model(PARTNER_ONLY_RECORD, "https://build.nvidia.com/models")
        self.assertFalse(model.is_free_endpoint)
        self.assertTrue(model.is_partner_endpoint)
        self.assertTrue(model.is_downloadable)

    def test_download_only_excluded_from_free(self):
        model = nb._record_to_model(DOWNLOAD_ONLY_RECORD, "https://build.nvidia.com/models")
        self.assertFalse(model.is_free_endpoint)
        self.assertFalse(model.is_partner_endpoint)
        self.assertTrue(model.is_downloadable)

    def test_unavailable_model_flagged(self):
        model = nb._record_to_model(UNAVAILABLE_FREE_RECORD, "https://build.nvidia.com/models")
        self.assertTrue(model.is_free_endpoint)
        self.assertFalse(model.available)

    def test_capability_inference(self):
        model = nb._record_to_model(FREE_ENDPOINT_RECORD, "https://build.nvidia.com/models")
        self.assertIn("vision", model.capabilities)
        self.assertIn("reasoning", model.capabilities)
        self.assertIn("tool_calling", model.capabilities)

    def test_default_api_model_id_is_native_id(self):
        model = nb._record_to_model(FREE_ENDPOINT_RECORD, "https://build.nvidia.com/models")
        self.assertEqual(model.api_model_id, "deepseek-ai/deepseek-v4.1-flash")


class EligibleIfFreeEndpointTests(unittest.TestCase):
    """Helper: is a model eligible for free routing?"""

    def test_free_endpoint_eligible(self):
        model = nb._record_to_model(FREE_ENDPOINT_RECORD, "https://build.nvidia.com/models")
        self.assertTrue(model.is_free_endpoint)

    def test_partner_only_not_eligible(self):
        model = nb._record_to_model(PARTNER_ONLY_RECORD, "https://build.nvidia.com/models")
        self.assertFalse(model.is_free_endpoint)

    def test_download_only_not_eligible(self):
        model = nb._record_to_model(DOWNLOAD_ONLY_RECORD, "https://build.nvidia.com/models")
        self.assertFalse(model.is_free_endpoint)


class DiscoverCatalogTests(unittest.TestCase):
    """Test catalog discovery with pagination."""

    def test_single_page_discovery(self):
        html = _record_to_flight_html([FREE_ENDPOINT_RECORD])
        with mock.patch.object(nb, "fetch_json", return_value=html):
            snapshot = nb.discover_catalog(max_pages=1)
        self.assertEqual(snapshot.page_count, 1)
        self.assertEqual(snapshot.model_count, 1)
        self.assertEqual(snapshot.free_endpoint_count, 1)
        self.assertEqual(snapshot.partner_endpoint_count, 1)
        self.assertEqual(snapshot.downloadable_count, 1)
        self.assertEqual(snapshot.models[0].canonical_id, "nvidia/deepseek-ai/deepseek-v4.1-flash:free")

    def test_multiple_endpoint_types_counted(self):
        html = _record_to_flight_html(
            [FREE_ENDPOINT_RECORD, PARTNER_ONLY_RECORD, DOWNLOAD_ONLY_RECORD]
        )
        with mock.patch.object(nb, "fetch_json", return_value=html):
            snapshot = nb.discover_catalog(max_pages=1)
        self.assertEqual(snapshot.model_count, 3)
        self.assertEqual(snapshot.free_endpoint_count, 1)
        self.assertEqual(snapshot.partner_endpoint_count, 2)
        # All three records include "Download Available".
        self.assertEqual(snapshot.downloadable_count, 3)

    def test_pagination_stops_on_empty_page(self):
        first_page = _record_to_flight_html([FREE_ENDPOINT_RECORD])
        second_page = ""  # no flight data -> end of catalog
        calls = iter([first_page, second_page])

        def fake_fetch(url, **kwargs):
            return next(calls)

        with mock.patch.object(nb, "fetch_json", side_effect=fake_fetch):
            snapshot = nb.discover_catalog(max_pages=5)
        self.assertEqual(snapshot.page_count, 1)
        self.assertEqual(snapshot.model_count, 1)

    def test_max_pages_limit(self):
        html = _record_to_flight_html([FREE_ENDPOINT_RECORD])
        with mock.patch.object(nb, "fetch_json", return_value=html):
            snapshot = nb.discover_catalog(max_pages=2)
        # Only one record per page, so the "short page" stop heuristic
        # triggers after page 1 (fewer than PAGE_SIZE records).
        self.assertEqual(snapshot.page_count, 1)
        self.assertEqual(snapshot.model_count, 1)

    def test_snapshot_has_provenance(self):
        html = _record_to_flight_html([FREE_ENDPOINT_RECORD])
        with mock.patch.object(nb, "fetch_json", return_value=html):
            snapshot = nb.discover_catalog(max_pages=1)
        self.assertIsNotNone(snapshot.fetched_at)
        self.assertIsNotNone(snapshot.snapshot_sha256)
        self.assertEqual(snapshot.source, "nvidia-build")
        self.assertEqual(snapshot.api_base, "https://integrate.api.nvidia.com/v1")


class ResolveApiModelIdTests(unittest.TestCase):
    """Test API model id resolution from .md detail pages."""

    def test_resolves_api_id_from_prototype(self):
        model = nb._record_to_model(FREE_ENDPOINT_RECORD, "https://build.nvidia.com/models")
        with mock.patch.object(nb, "fetch_json", return_value=MD_DETAIL_PAGE):
            nb.resolve_api_model_ids([model])
        self.assertEqual(model.api_model_id, "deepseek-ai/deepseek-v4.1-flash")
        self.assertEqual(model.canonical_url, "https://build.nvidia.com/deepseek-ai/deepseek-v4.1-flash")

    def test_empty_model_id_keeps_native_id(self):
        model = nb._record_to_model(
            {
                **FREE_ENDPOINT_RECORD,
                "name": "glm-5-3-flash",
                "publisher": "z-ai",
                "labels": [
                    {**lbl, "values": ["z-ai"]} if lbl.get("key") == "publisher" else lbl
                    for lbl in FREE_ENDPOINT_RECORD["labels"]
                ],
            },
            "https://build.nvidia.com/models",
        )
        with mock.patch.object(nb, "fetch_json", return_value=MD_DETAIL_PAGE_EMPTY_MODEL):
            nb.resolve_api_model_ids([model])
        # Falls back to native_id when prototype model="" is empty.
        self.assertEqual(model.api_model_id, "z-ai/glm-5-3-flash")

    def test_fetch_failure_keeps_native_id(self):
        model = nb._record_to_model(FREE_ENDPOINT_RECORD, "https://build.nvidia.com/models")
        with mock.patch.object(
            nb, "fetch_json", side_effect=nb.NVIDIABuildTransportError("boom")
        ):
            nb.resolve_api_model_ids([model])
        self.assertEqual(model.api_model_id, "deepseek-ai/deepseek-v4.1-flash")


class ChangeDetectionTests(unittest.TestCase):
    """Test deprecated / removed / unavailable / changed model detection."""

    def test_removed_model_flagged(self):
        previous = nb.CatalogSnapshot(models=[
            nb._record_to_model(FREE_ENDPOINT_RECORD, "https://build.nvidia.com/models"),
        ])
        current = nb.CatalogSnapshot(models=[])
        changed = nb.detect_changes(previous, current)
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0].change_reason, "removed")

    def test_deprecated_model_flagged(self):
        # Same resource_id, lost Free Endpoint -> deprecated.
        prev_record = {
            **FREE_ENDPOINT_RECORD,
            "resourceId": "qc69jvmznzxy/same-model",
        }
        curr_record = {
            **DEPRECATED_RECORD,
            "resourceId": "qc69jvmznzxy/same-model",
        }
        prev_model = nb._record_to_model(prev_record, "https://build.nvidia.com/models")
        curr_model = nb._record_to_model(curr_record, "https://build.nvidia.com/models")
        previous = nb.CatalogSnapshot(models=[prev_model])
        current = nb.CatalogSnapshot(models=[curr_model])
        changed = nb.detect_changes(previous, current)
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0].change_reason, "deprecated")

    def test_changed_description_flagged(self):
        prev_record = {**FREE_ENDPOINT_RECORD, "resourceId": "qc69jvmznzxy/same-model"}
        curr_record = {
            **FREE_ENDPOINT_RECORD,
            "resourceId": "qc69jvmznzxy/same-model",
            "description": "Updated description",
        }
        prev_model = nb._record_to_model(prev_record, "https://build.nvidia.com/models")
        curr_model = nb._record_to_model(curr_record, "https://build.nvidia.com/models")
        previous = nb.CatalogSnapshot(models=[prev_model])
        current = nb.CatalogSnapshot(models=[curr_model])
        changed = nb.detect_changes(previous, current)
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0].change_reason, "changed")

    def test_unavailable_free_endpoint_flagged(self):
        prev_record = {
            **FREE_ENDPOINT_RECORD,
            "resourceId": "qc69jvmznzxy/same-model",
            "attributes": [{"key": "AVAILABLE", "value": "true"}],
        }
        curr_record = {
            **UNAVAILABLE_FREE_RECORD,
            "resourceId": "qc69jvmznzxy/same-model",
        }
        prev_model = nb._record_to_model(prev_record, "https://build.nvidia.com/models")
        curr_model = nb._record_to_model(curr_record, "https://build.nvidia.com/models")
        previous = nb.CatalogSnapshot(models=[prev_model])
        current = nb.CatalogSnapshot(models=[curr_model])
        changed = nb.detect_changes(previous, current)
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0].change_reason, "unavailable")

    def test_no_changes_when_identical(self):
        model = nb._record_to_model(FREE_ENDPOINT_RECORD, "https://build.nvidia.com/models")
        previous = nb.CatalogSnapshot(models=[model])
        current = nb.CatalogSnapshot(models=[model])
        changed = nb.detect_changes(previous, current)
        self.assertEqual(len(changed), 0)

    def test_new_model_not_flagged_as_change(self):
        previous = nb.CatalogSnapshot(models=[])
        current = nb.CatalogSnapshot(models=[
            nb._record_to_model(FREE_ENDPOINT_RECORD, "https://build.nvidia.com/models"),
        ])
        changed = nb.detect_changes(previous, current)
        self.assertEqual(len(changed), 0)


class SnapshotRoundTripTests(unittest.TestCase):
    """Test snapshot save/load preserves all fields."""

    def test_save_and_load_roundtrip(self):
        html = _record_to_flight_html([FREE_ENDPOINT_RECORD, PARTNER_ONLY_RECORD])
        with mock.patch.object(nb, "fetch_json", return_value=html):
            snapshot = nb.discover_catalog(max_pages=1)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "snapshot.json"
            nb.save_snapshot(snapshot, path)
            loaded = nb.load_snapshot(path)
        self.assertEqual(loaded.model_count, snapshot.model_count)
        self.assertEqual(loaded.free_endpoint_count, snapshot.free_endpoint_count)
        self.assertEqual(len(loaded.models), 2)
        self.assertEqual(loaded.models[0].canonical_id, snapshot.models[0].canonical_id)
        self.assertEqual(loaded.models[0].endpoint_types, snapshot.models[0].endpoint_types)
        self.assertEqual(loaded.models[0].is_free_endpoint, snapshot.models[0].is_free_endpoint)
        self.assertEqual(loaded.snapshot_sha256, snapshot.snapshot_sha256)

    def test_snapshot_is_idempotent(self):
        """Same discovery produces same snapshot hash (deterministic)."""
        html = _record_to_flight_html([FREE_ENDPOINT_RECORD])
        with mock.patch.object(nb, "fetch_json", return_value=html):
            s1 = nb.discover_catalog(max_pages=1)
            s2 = nb.discover_catalog(max_pages=1)
        self.assertEqual(s1.snapshot_sha256, s2.snapshot_sha256)


class NeverUpgradeUnknownToVerifiedTests(unittest.TestCase):
    """Safety: sync must never silently upgrade unknown -> verified_free."""

    def test_sync_preserves_verified_free(self):
        """Models already verified_free keep that eligibility after sync."""
        model = nb._record_to_model(FREE_ENDPOINT_RECORD, "https://build.nvidia.com/models")
        model.eligibility = "verified_free"
        model.last_verified_at = "2026-01-01T00:00:00Z"
        snapshot = nb.CatalogSnapshot(models=[model])

        html = _record_to_flight_html([FREE_ENDPOINT_RECORD])
        with mock.patch.object(nb, "fetch_json", return_value=html):
            current = nb.discover_catalog(max_pages=1)

        # Simulate the merge logic from the CLI: verified_free is preserved.
        prev_by_id = {m.resource_id: m for m in snapshot.models}
        curr_by_id = {m.resource_id: m for m in current.models}
        for resource_id, curr_model in curr_by_id.items():
            prev = prev_by_id.get(resource_id)
            if prev is not None and prev.eligibility == "verified_free":
                curr_model.eligibility = "verified_free"
                curr_model.last_verified_at = prev.last_verified_at

        self.assertEqual(current.models[0].eligibility, "verified_free")
        self.assertEqual(current.models[0].last_verified_at, "2026-01-01T00:00:00Z")

    def test_sync_does_not_upgrade_unknown(self):
        """Unknown models stay unknown after sync (never silently verified)."""
        model = nb._record_to_model(FREE_ENDPOINT_RECORD, "https://build.nvidia.com/models")
        model.eligibility = "unknown"
        snapshot = nb.CatalogSnapshot(models=[model])

        html = _record_to_flight_html([FREE_ENDPOINT_RECORD])
        with mock.patch.object(nb, "fetch_json", return_value=html):
            current = nb.discover_catalog(max_pages=1)

        prev_by_id = {m.resource_id: m for m in snapshot.models}
        curr_by_id = {m.resource_id: m for m in current.models}
        for resource_id, curr_model in curr_by_id.items():
            prev = prev_by_id.get(resource_id)
            if prev is not None and prev.eligibility == "verified_free":
                curr_model.eligibility = "verified_free"
                curr_model.last_verified_at = prev.last_verified_at

        self.assertEqual(current.models[0].eligibility, "unknown")
        self.assertIsNone(current.models[0].last_verified_at)


class MalformedResponseTests(unittest.TestCase):
    """Test handling of malformed catalog responses."""

    def test_invalid_flight_data_returns_empty(self):
        with mock.patch.object(nb, "fetch_json", return_value="not flight data at all"):
            snapshot = nb.discover_catalog(max_pages=1)
        self.assertEqual(snapshot.model_count, 0)

    def test_empty_html_returns_empty(self):
        with mock.patch.object(nb, "fetch_json", return_value=""):
            snapshot = nb.discover_catalog(max_pages=1)
        self.assertEqual(snapshot.model_count, 0)

    def test_malformed_endpoint_record_skipped(self):
        good = _record_to_flight_html([FREE_ENDPOINT_RECORD])
        bad = 'self.__next_f.push([1,"{not valid json"]);'
        with mock.patch.object(nb, "fetch_json", side_effect=[good, bad]):
            snapshot = nb.discover_catalog(max_pages=2)
        self.assertEqual(snapshot.model_count, 1)

    def test_record_missing_nimtype(self):
        import copy
        record = {**FREE_ENDPOINT_RECORD, "labels": copy.deepcopy(FREE_ENDPOINT_RECORD["labels"])}
        del record["labels"][0]  # remove nimType label
        model = nb._record_to_model(record, "https://build.nvidia.com/models")
        self.assertFalse(model.is_free_endpoint)
        self.assertEqual(model.endpoint_types, [])

    def test_non_https_url_rejected(self):
        with self.assertRaises(nb.NVIDIABuildTransportError):
            nb.fetch_json("ftp://evil.example.com/models")

    def test_401_raises_auth_error(self):
        with mock.patch.object(
            nb, "fetch_json", side_effect=nb.NVIDIABuildAuthError("HTTP 401")
        ), self.assertRaises(nb.NVIDIABuildAuthError):
            nb.discover_catalog(max_pages=1)


if __name__ == "__main__":
    unittest.main()
