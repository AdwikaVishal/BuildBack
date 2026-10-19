"""Interpretation providers for the RippleBack assistant.

The assistant separates *interpretation* (user message → intent + parameters)
from *execution* (MCP tool calls) and *explanation* (results → text). This
module provides the interpretation backends:

* :class:`DeterministicProvider` — the default. Rule-based, offline, and
  completely deterministic. It requires no credentials and no network access
  beyond the MCP server itself.
* :class:`BedrockProvider` — an **optional** Amazon Bedrock-backed
  interpretation provider. It is only used when the project explicitly selects
  it *and* the environment is configured for it (region + model id + boto3
  installed + AWS credentials available to the backend process). On any
  failure it falls back to the deterministic provider and says so — the
  application always runs without Bedrock credentials.

Safety properties (both providers):

* The model/provider may only select from live-discovered tools; the
  orchestrator validates every proposed tool name and arguments against the
  discovered input schemas before invocation.
* The provider never executes anything; it only returns an intent. All tool
  calls go through the MCP client.
* No credentials are ever exposed to the frontend; Bedrock runs backend-side
  only.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from rippleback.assistant.intents import ALL_INTENTS, Intent, parse_intent


class AssistantProvider(Protocol):
    """Anything that can map a user message to an Intent."""

    name: str

    def parse(self, message: str, context: dict[str, Any]) -> Intent:
        """Interpret ``message``; ``context`` carries tools/scenario metadata."""
        ...


@dataclass
class ProviderReport:
    """Why a provider was (or was not) used — surfaced to the user."""

    provider: str
    fallback_used: bool = False
    fallback_reason: str | None = None
    notes: list[str] = field(default_factory=list)


class DeterministicProvider:
    """Rule-based intent parsing. Default; needs no credentials or model."""

    name = "deterministic"

    def parse(self, message: str, context: dict[str, Any]) -> Intent:
        del context  # the deterministic parser needs no extra context
        return parse_intent(message)


class BedrockProvider:
    """Optional Amazon Bedrock-backed interpretation provider.

    Configuration (all via environment, never hardcoded):

    * ``RIPPLEBACK_BEDROCK_REGION`` — AWS region of the Bedrock endpoint.
    * ``RIPPLEBACK_BEDROCK_MODEL_ID`` — the model id to use. No default is
      invented; the provider is disabled until this is set.
    * AWS credentials come from the standard backend credential chain
      (env vars / profile / instance role) and are never read into code or
      logs.

    The provider is only *selected* when ``RIPPLEBACK_ASSISTANT_PROVIDER`` is
    ``bedrock``; any failure (missing deps, missing config, auth, rate limit,
    timeout, malformed model output) degrades to the deterministic provider
    with an explicit reason.
    """

    name = "bedrock"

    def __init__(
        self,
        region: str | None = None,
        model_id: str | None = None,
        fallback: DeterministicProvider | None = None,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.region = region or os.environ.get("RIPPLEBACK_BEDROCK_REGION") or None
        self.model_id = model_id or os.environ.get("RIPPLEBACK_BEDROCK_MODEL_ID") or None
        self.fallback = fallback or DeterministicProvider()
        self.timeout_seconds = timeout_seconds
        self._client: Any = None
        self._client_error: str | None = None

    # ── Availability ─────────────────────────────────────────────────────────

    @staticmethod
    def _boto3_available() -> bool:
        try:
            import boto3  # noqa: F401
        except ImportError:
            return False
        return True

    def unavailable_reason(self) -> str | None:
        """Return a human-readable reason Bedrock cannot be used, or None."""
        if not self.region:
            return "RIPPLEBACK_BEDROCK_REGION is not set"
        if not self.model_id:
            return "RIPPLEBACK_BEDROCK_MODEL_ID is not set"
        if not self._boto3_available():
            return "boto3 is not installed (pip install rippleback[bedrock])"
        return None

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if self._client_error:
            raise RuntimeError(self._client_error)
        try:
            import boto3
            from botocore.config import Config

            self._client = boto3.client(
                "bedrock-runtime",
                region_name=self.region,
                config=Config(read_timeout=int(self.timeout_seconds), retries={"max_attempts": 1}),
            )
            return self._client
        except Exception as exc:  # credentials/config errors surface here
            self._client_error = f"Bedrock client init failed: {exc}"
            raise RuntimeError(self._client_error) from exc

    # ── Parsing ──────────────────────────────────────────────────────────────

    def parse(self, message: str, context: dict[str, Any]) -> tuple[Intent, ProviderReport]:
        """Interpret via Bedrock, falling back deterministically on any failure."""
        report = ProviderReport(provider=self.name)
        reason = self.unavailable_reason()
        if reason:
            return self._fallback(message, report, f"Bedrock unavailable: {reason}")

        tools = context.get("tools") or []
        tool_catalog = [
            {
                "name": t.name,
                "description": t.description[:400],
                "required": (t.input_schema or {}).get("required", []),
            }
            for t in tools
        ]
        prompt = self._build_prompt(message, context, tool_catalog)
        try:
            client = self._get_client()
            response = client.converse(
                modelId=self.model_id,
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                system=[{"text": self._system_prompt()}],
                inferenceConfig={"maxTokens": 400, "temperature": 0.0},
            )
            text = self._extract_text(response)
            intent = self._parse_model_output(text, {t["name"] for t in tool_catalog})
            report.notes.append(f"interpreted by Bedrock model {self.model_id}")
            return intent, report
        except Exception as exc:  # auth, throttle, timeout, parse errors
            return self._fallback(message, report, f"Bedrock call failed: {exc}")

    def _fallback(self, message: str, report: ProviderReport, reason: str) -> tuple[Intent, ProviderReport]:
        report.fallback_used = True
        report.fallback_reason = reason
        report.provider = self.fallback.name
        report.notes.append("using the deterministic fallback interpreter")
        return self.fallback.parse(message, {}), report

    @staticmethod
    def _system_prompt() -> str:
        return (
            "You are the intent parser for RippleBack, a read-only project-"
            "disruption MCP assistant. You never compute analysis yourself; you "
            "only classify the user's request and extract literal parameters. "
            "Respond with a single JSON object and nothing else: "
            '{"intent": "<one of the allowed intents>", "params": {...}}. '
            "Allowed intents: " + ", ".join(ALL_INTENTS) + ". "
            "Only extract parameters literally present in the message (scenario, "
            "entity mention, delay_minutes, severity, option_id, "
            "expected_plan_version, reason). Never invent ids."
        )

    def _build_prompt(self, message: str, context: dict[str, Any], tool_catalog: list[dict]) -> str:
        lines = [
            "Live MCP tools (only these may be used):",
            json.dumps(tool_catalog),
        ]
        if context.get("scenario_id"):
            lines.append(f"Currently selected scenario: {context['scenario_id']}")
        if context.get("entity_ids"):
            lines.append("Entities in the selected scenario: " + ", ".join(context["entity_ids"]))
        lines.append(f"User message: {message}")
        return "\n".join(lines)

    @staticmethod
    def _extract_text(response: Any) -> str:
        chunks: list[str] = []
        for message in response.get("output", {}).get("message", {}).get("content", []):
            text = message.get("text")
            if text:
                chunks.append(text)
        return "\n".join(chunks).strip()

    def _parse_model_output(self, text: str, allowed_tools: set[str]) -> Intent:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise ValueError("model did not return a JSON object")
        payload = json.loads(match.group(0))
        intent_name = payload.get("intent")
        if intent_name not in ALL_INTENTS:
            raise ValueError(f"model returned unknown intent {intent_name!r}")
        raw_params = payload.get("params") or {}
        if not isinstance(raw_params, dict):
            raise ValueError("model params must be an object")
        params = {k: v for k, v in raw_params.items() if v is not None}
        # The model may not name raw tools; intent → tool mapping stays
        # deterministic in the orchestrator. Discard any tool names it invents.
        params.pop("tool", None)
        del allowed_tools
        return Intent(name=intent_name, params=params)


def make_provider(name: str | None = None) -> tuple[Any, ProviderReport]:
    """Build the configured provider; Bedrock falls back deterministically.

    ``name`` overrides ``RIPPLEBACK_ASSISTANT_PROVIDER``. Returns the provider
    plus a report describing what is actually in use.
    """
    selected = (name or os.environ.get("RIPPLEBACK_ASSISTANT_PROVIDER") or "deterministic").strip().lower()
    if selected == "bedrock":
        provider = BedrockProvider()
        reason = provider.unavailable_reason()
        if reason is None:
            return provider, ProviderReport(provider="bedrock")
        fallback = DeterministicProvider()
        report = ProviderReport(
            provider=fallback.name,
            fallback_used=True,
            fallback_reason=f"Bedrock unavailable: {reason}",
            notes=["using the deterministic fallback interpreter"],
        )
        return fallback, report
    if selected != "deterministic":
        report = ProviderReport(
            provider="deterministic",
            fallback_used=True,
            fallback_reason=f"unknown provider {selected!r}; using deterministic",
        )
        return DeterministicProvider(), report
    return DeterministicProvider(), ProviderReport(provider="deterministic")
