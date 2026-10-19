"""Secret redaction and auth helpers.

Two responsibilities, both security-critical:

* :func:`redact` scrubs secret values (from :data:`rippleback.config.SECRET_ENV_VARS`)
  and common secret-shaped substrings out of any string before it is logged,
  returned over HTTP, or stored in an audit row.
* :func:`require_api_key` / :func:`check_api_key` enforce the ``RIPPLEBACK_API_KEY``
  bearer contract for mutating routes.
"""

from __future__ import annotations

import re
from typing import Any

from rippleback.config import SECRET_ENV_VARS, load_settings

# Substrings that look like a secret even when not sourced from an env var.
_SECRET_PATTERNS = (
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{8,}", re.IGNORECASE),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}", re.IGNORECASE),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)

_REDACTED = "[redacted]"


def _known_secret_values() -> list[str]:
    import os

    values: list[str] = []
    for name in SECRET_ENV_VARS:
        value = (os.environ.get(name) or "").strip()
        if len(value) >= 4:
            values.append(value)
    return values


def redact(text: Any) -> str:
    """Return ``text`` with every known secret value and secret-shaped token replaced.

    Accepts any value; stringifies non-str input. Always safe to call on an
    error message or audit detail before persisting or returning it.
    """
    if text is None:
        return ""
    out = str(text)
    for value in _known_secret_values():
        if value in out:
            out = out.replace(value, _REDACTED)
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub(_REDACTED, out)
    return out


def redact_mapping(data: Any) -> Any:
    """Recursively redact every string leaf of a dict/list structure."""
    if isinstance(data, str):
        return redact(data)
    if isinstance(data, dict):
        return {key: redact_mapping(value) for key, value in data.items()}
    if isinstance(data, list):
        return [redact_mapping(item) for item in data]
    return data


def check_api_key(provided: str | None) -> bool:
    """True when the supplied bearer key satisfies the configured requirement.

    When ``RIPPLEBACK_API_KEY`` is unset, no key is required and any provided
    key is accepted (returns True). When it is set, the provided key must match
    exactly (constant-time compare).
    """
    import hmac

    settings = load_settings()
    if not settings.api_key:
        return True
    if not provided:
        return False
    return hmac.compare_digest(provided, settings.api_key)


def extract_bearer(headers: Any) -> str | None:
    """Pull the bearer token out of a Starlette/ASGI headers mapping."""
    raw = headers.get("authorization") or headers.get("Authorization")
    if not raw:
        return None
    parts = str(raw).split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def write_guard_reason(tool_or_route: str, *, is_write: bool, headers: Any = None) -> str | None:
    """Return a human-readable refusal reason, or ``None`` when the call may proceed.

    Central policy for both the adapter's ``/api/mcp/tool`` route and the MCP
    server's own write tools:

    * Non-write tools are always allowed.
    * Writes require ``RIPPLEBACK_WRITES_ENABLED=true``.
    * When ``RIPPLEBACK_API_KEY`` is set, writes require a matching bearer key.
    * If the adapter would bind a non-loopback host with no API key configured,
      writes are refused outright (loudly, via the returned message).
    """
    if not is_write:
        return None
    settings = load_settings()
    if settings.adapter_bound_public and not settings.api_key_required:
        return (
            "writes are refused: the adapter is configured to bind a non-loopback "
            "host but RIPPLEBACK_API_KEY is not set; set RIPPLEBACK_API_KEY or "
            "bind to 127.0.0.1"
        )
    if not settings.writes_enabled:
        return (
            "writes are disabled; set RIPPLEBACK_WRITES_ENABLED=true to allow "
            f"mutating calls to {tool_or_route!r}"
        )
    if settings.api_key_required and not check_api_key(extract_bearer(headers or {})):
        return "invalid or missing API key (Authorization: Bearer <RIPPLEBACK_API_KEY>)"
    return None
