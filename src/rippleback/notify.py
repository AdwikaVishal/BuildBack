"""Outbound notifications (Slack webhook and/or SMTP).

Notifications are strictly best-effort: a slow or broken channel must never
fail a disruption record, a commit, or a sync. Every payload is passed through
:func:`rippleback.security.redact` first, and every channel has a short timeout.

Configuration (all optional):

* ``SLACK_WEBHOOK_URL`` — incoming-webhook URL. Missing → Slack is skipped.
* ``SMTP_HOST``, ``SMTP_PORT``, ``SMTP_USERNAME``, ``SMTP_PASSWORD``,
  ``SMTP_FROM``, ``SMTP_TO`` (comma-separated), ``SMTP_TLS`` (default on).
  Missing host/from/to → email is skipped.

``NOTIFY_EVENTS`` (comma-separated) narrows which events are sent; default is
every supported event.
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any

import httpx

from rippleback.config import _env, _env_bool, _env_list
from rippleback.security import redact, redact_mapping

logger = logging.getLogger("rippleback.notify")

SUPPORTED_EVENTS = (
    "disruption_recorded",
    "disruption_resolved",
    "commit_recorded",
    "sync_failed",
    "writeback_failed",
)
DEFAULT_TIMEOUT_SECONDS = 3.0


def _enabled_events() -> set[str]:
    configured = _env_list("NOTIFY_EVENTS")
    return set(configured) if configured else set(SUPPORTED_EVENTS)


def format_message(event: str, payload: dict[str, Any]) -> str:
    """One-line, redacted, deterministic message body."""
    safe = redact_mapping(payload)
    details = ", ".join(f"{key}={safe[key]}" for key in sorted(safe))
    return f"[RippleBack] {event}: {details}"


def send_slack(text: str, *, url: str | None = None) -> bool:
    """POST ``text`` to the Slack webhook; return whether it was accepted."""
    webhook = url or _env("SLACK_WEBHOOK_URL")
    if not webhook:
        return False
    try:
        response = httpx.post(
            webhook, json={"text": redact(text)}, timeout=DEFAULT_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        return True
    except Exception as exc:  # network or HTTP failure — never raise
        logger.warning("slack notification failed: %s", redact(str(exc)))
        return False


def send_email(subject: str, body: str) -> bool:
    """Send an SMTP notification when configured; return whether it was sent."""
    host = _env("SMTP_HOST")
    sender = _env("SMTP_FROM")
    recipients = _env_list("SMTP_TO")
    if not host or not sender or not recipients:
        return False
    port_raw = _env("SMTP_PORT", "587")
    try:
        port = int(port_raw)
    except ValueError:
        port = 587
    message = EmailMessage()
    message["Subject"] = redact(subject)
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message.set_content(redact(body))
    try:
        with smtplib.SMTP(host, port, timeout=DEFAULT_TIMEOUT_SECONDS) as smtp:
            if _env_bool("SMTP_TLS"):
                smtp.starttls(context=ssl.create_default_context())
            username = _env("SMTP_USERNAME")
            if username:
                smtp.login(username, os.environ.get("SMTP_PASSWORD", ""))
            smtp.send_message(message)
        return True
    except Exception as exc:  # network or auth failure — never raise
        logger.warning("smtp notification failed: %s", redact(str(exc)))
        return False


def notify_event(event: str, payload: dict[str, Any]) -> dict[str, bool]:
    """Deliver one event over every configured channel.

    Returns ``{"slack": bool, "email": bool}``; missing configuration reports
    ``False`` rather than raising. Unknown event names are ignored (returns
    ``False`` for every channel) so callers can pass new events safely.
    """
    if event not in SUPPORTED_EVENTS or event not in _enabled_events():
        return {"slack": False, "email": False}
    text = format_message(event, payload)
    return {"slack": send_slack(text), "email": send_email(text, text)}
