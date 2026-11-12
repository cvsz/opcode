"""Centralized configuration for the OpenCode router stack.

All environment-driven settings live here with defaults and validation.
Load once at startup via Config.from_env().
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    """Router runtime configuration."""

    # Circuit breaker
    circuit_failure_threshold: int = 2
    circuit_cooldown_seconds: int = 300
    circuit_quota_cooldown_seconds: int = 3600
    key_cooldown_seconds: int = 300

    # Router behavior
    opencode_cmd: str = "opencode"
    open_code_query_timeout: float = 30.0
    route_timeout_seconds: int = 180
    max_models: int = 0
    adaptive_order: bool = False
    require_env_key: bool = False
    router_auto: bool = False
    router_archived: str = "1"

    # Dashboard
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8765
    dashboard_token_bytes: int = 24

    # Logging
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> Config:
        """Build Config from environment variables with sensible defaults."""

        def _int_env(name: str, default: int) -> int:
            try:
                return int(os.getenv(name, str(default)))
            except (TypeError, ValueError):
                return default

        def _float_env(name: str, default: float) -> float:
            try:
                return float(os.getenv(name, str(default)))
            except (TypeError, ValueError):
                return default

        def _bool_env(name: str, default: bool) -> bool:
            return os.getenv(name, str(int(default))).lower() in {"1", "true", "yes", "on"}

        return cls(
            circuit_failure_threshold=_int_env("CIRCUIT_FAILURE_THRESHOLD", 2),
            circuit_cooldown_seconds=_int_env("CIRCUIT_COOLDOWN_SECONDS", 300),
            circuit_quota_cooldown_seconds=_int_env("CIRCUIT_QUOTA_COOLDOWN_SECONDS", 3600),
            key_cooldown_seconds=_int_env("KEY_COOLDOWN_SECONDS", 300),
            opencode_cmd=os.getenv("OPENCODE", "opencode"),
            open_code_query_timeout=_float_env("OPENCODE_QUERY_TIMEOUT", 30.0),
            route_timeout_seconds=_int_env("TIMEOUT_SECONDS", 180),
            max_models=_int_env("MAX_MODELS", 0),
            adaptive_order=_bool_env("ADAPTIVE_ORDER", False),
            require_env_key=_bool_env("REQUIRE_ENV_KEY", False),
            router_auto=_bool_env("ROUTER_AUTO", False),
            router_archived=os.getenv("ROUTER_ARCHIVED", "1"),
            dashboard_host=os.getenv("DASHBOARD_HOST", "127.0.0.1"),
            dashboard_port=_int_env("DASHBOARD_PORT", 8765),
            dashboard_token_bytes=_int_env("DASHBOARD_TOKEN_BYTES", 24),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )