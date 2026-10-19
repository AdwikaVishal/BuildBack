"""The RippleBack conversational orchestrator.

Pipeline (each concern is separate, per the architecture):

1. **User message** — arrives from the frontend chat panel.
2. **Intent + parameter extraction** — a provider (:mod:`providers`) maps the
   message to an :class:`~rippleback.assistant.intents.Intent`.
3. **Tool selection** — this module decides which *live-discovered* MCP tools
   the intent needs and in what order.
4. **MCP invocation** — every call goes through
   :class:`rippleback.client.RippleBackMCPClient` (a genuine MCP client); no
   engine is called directly.
5. **Result interpretation** — :mod:`explain` turns structured results into
   concise Markdown.
6. **Final explanation** — sections are assembled into one reply with an
   explicit tool-call trace.

Rules enforced here:

* No scheduling, recovery, graph, or constraint algorithms live in this layer —
  only sequencing of real tool calls.
* Entity and scenario resolution uses **live plan data** from
  ``get_plan_state``; ids are never hardcoded or guessed. Ambiguity produces a
  clarification question, not a guess.
* Multi-step workflows feed **actual intermediate results** (e.g. the options
  returned by ``find_alternative_dependencies`` are what
  ``compare_recovery_options`` receives). Intermediate results are never
  invented; a failed step stops the chain with an honest explanation.
* A maximum number of tool calls per user turn bounds any loop.
* Nothing is ever written back to the committed plan; every reply labels
  hypothetical results as hypothetical.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from rippleback.assistant import intents as I
from rippleback.assistant.explain import (
    explain,
    explain_schedule_missing,
    explain_tool_error,
)
from rippleback.assistant.providers import (
    DeterministicProvider,
    ProviderReport,
    make_provider,
)
from rippleback.client import (
    MCPConnectionError,
    MCPToolFailedError,
    MCPToolInfo,
    RippleBackMCPClient,
)

DEFAULT_MAX_TOOL_CALLS = 8

#: Intents that benefit from entity resolution when a mention is present but
#: do not fail without one (the mention may simply be absent).
_OPTIONAL_ENTITY_INTENTS = {
    I.COMPARE,
    I.FORECAST,
    I.EVALUATE,
    I.SCHEDULE,
    I.RECOVERY_WORKFLOW,
    I.WORKLOAD,
}

#: Tool names the orchestrator knows how to sequence. Anything else (e.g. a
#: future scheduling tool) is discovered dynamically and routed by name.
_SCHEDULING_MATCH = re.compile(r"schedul", re.IGNORECASE)


@dataclass
class ChatStep:
    """One attempted MCP tool call within a turn (shown in the UI trace)."""

    tool: str
    arguments: dict[str, Any]
    status: str  # "ok" | "error" | "skipped"
    duration_ms: int | None = None
    result: Any = None
    error: str | None = None
    summary: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "arguments": self.arguments,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "result": self.result,
            "error": self.error,
            "summary": self.summary,
        }


@dataclass
class ChatResponse:
    """Everything the frontend needs to render one assistant turn."""

    reply: str
    intent: str
    provider: str = "deterministic"
    provider_notes: list[str] = field(default_factory=list)
    steps: list[ChatStep] = field(default_factory=list)
    needs_clarification: bool = False
    scenario_id: str | None = None
    tool_calls_made: int = 0
    truncated: bool = False
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "reply": self.reply,
            "intent": self.intent,
            "provider": self.provider,
            "provider_notes": self.provider_notes,
            "steps": [s.to_dict() for s in self.steps],
            "needs_clarification": self.needs_clarification,
            "scenario_id": self.scenario_id,
            "tool_calls_made": self.tool_calls_made,
            "truncated": self.truncated,
            "duration_ms": self.duration_ms,
        }


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


class Assistant:
    """Turn user messages into MCP tool workflows and explained replies."""

    def __init__(
        self,
        client: RippleBackMCPClient,
        provider: Any | None = None,
        provider_report: ProviderReport | None = None,
        max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
    ) -> None:
        self.client = client
        if provider is None:
            provider, provider_report = make_provider()
        self.provider = provider
        self.provider_report = provider_report or ProviderReport(provider=getattr(provider, "name", "deterministic"))
        self.max_tool_calls = max_tool_calls
        self._call_count = 0

    # ── Entry point ──────────────────────────────────────────────────────────

    async def handle_message(
        self, message: str, scenario_id: str | None = None
    ) -> ChatResponse:
        started = time.monotonic()
        self._call_count = 0
        response = ChatResponse(reply="", intent=I.HELP, scenario_id=scenario_id)

        try:
            tools = await self.client.list_tools()
        except MCPConnectionError as exc:
            response.reply = (
                "**MCP server unreachable.** I could not talk to the RippleBack "
                f"MCP server at `{self.client.url}`: {exc}.\n\n"
                "Start the server (`python -m rippleback.server`) and the adapter "
                "(`rippleback-adapter`), then retry — the Retry button in chat "
                "re-runs this message. Nothing was executed."
            )
            response.duration_ms = int((time.monotonic() - started) * 1000)
            return response

        context: dict[str, Any] = {"scenario_id": scenario_id, "tools": tools}
        parsed = self.provider.parse(message, context)
        if isinstance(parsed, tuple):
            intent, report = parsed
        else:
            intent, report = parsed, self.provider_report
        response.provider = report.provider
        response.provider_notes = list(report.notes)
        if report.fallback_used and report.fallback_reason:
            response.provider_notes.append(f"fallback: {report.fallback_reason}")
        response.intent = intent.name

        if intent.name == I.HELP:
            response.reply = self._help_reply(tools)
            response.duration_ms = int((time.monotonic() - started) * 1000)
            return response

        # Clarification demanded during parsing (e.g. unparseable request)
        if intent.clarification:
            response.reply = intent.clarification
            response.needs_clarification = True
            response.duration_ms = int((time.monotonic() - started) * 1000)
            return response

        # ── Scenario + entity resolution against live plan data ──
        plan: dict[str, Any] | None = None
        if scenario_id and (
            intent.name in I.REQUIRES_SCENARIO or intent.name in I.REQUIRES_ENTITY
        ):
            step = await self._run_tool("get_plan_state", {"scenario_id": scenario_id})
            response.steps.append(step)
            if step.status != "ok":
                response.reply = explain_tool_error("get_plan_state", step.error or "unknown error")
                response.duration_ms = int((time.monotonic() - started) * 1000)
                return response
            plan = step.result if isinstance(step.result, dict) else None
        elif intent.name in I.REQUIRES_SCENARIO:
            response.reply = (
                "Which scenario should I analyze? The dashboard sends its "
                "currently selected scenario with each message — pick one in the "
                "scenario selector and ask again."
            )
            response.needs_clarification = True
            response.duration_ms = int((time.monotonic() - started) * 1000)
            return response

        entity_id: str | None = intent.params.get("entity_id")
        if plan and not entity_id and intent.name in _OPTIONAL_ENTITY_INTENTS:
            # Best-effort resolution so "compare recovery options for payment-api"
            # works; failure is fine — those intents do not require an entity.
            entity_id, _ = self._resolve_entity(message, plan)
        if intent.name in I.REQUIRES_ENTITY and not entity_id:
            resolved, ambiguous = self._resolve_entity(message, plan)
            if resolved is None:
                response.reply = self._entity_clarification(plan, ambiguous)
                response.needs_clarification = True
                response.duration_ms = int((time.monotonic() - started) * 1000)
                return response
            entity_id = resolved

        # Delay events need a positive delay_minutes; ask rather than guess.
        event_type = intent.params.get("event_type") or "service_outage"
        delay_minutes = intent.params.get("delay_minutes")
        if (
            intent.name in (I.TRIAGE, I.SIMULATE_DISRUPTION, I.RECOVERY_WORKFLOW)
            and event_type in I.DELAY_EVENTS
            and not delay_minutes
        ):
            response.reply = (
                f"You mentioned a **{event_type.replace('_', ' ')}** — how many "
                "minutes is the delay? (e.g. \"a 120 minute delivery delay\")"
            )
            response.needs_clarification = True
            response.duration_ms = int((time.monotonic() - started) * 1000)
            return response

        # ── Intent → tool workflow ──
        handler = {
            I.PLAN_STATE: self._plan_workflow,
            I.SIMULATE_DISRUPTION: self._simulate_workflow,
            I.TRIAGE: self._triage_workflow,
            I.ALTERNATIVES: self._alternatives_workflow,
            I.SUBSTITUTES: self._substitutes_workflow,
            I.EVALUATE: self._evaluate_workflow,
            I.CONSTRAINTS: self._constraints_workflow,
            I.COMPARE: self._compare_workflow,
            I.FORECAST: self._forecast_workflow,
            I.WORKLOAD: self._workload_workflow,
            I.SCHEDULE: self._schedule_workflow,
            I.RECOVERY_WORKFLOW: self._recovery_workflow,
        }.get(intent.name)

        if handler is None:
            response.reply = self._help_reply(tools)
            response.duration_ms = int((time.monotonic() - started) * 1000)
            return response

        try:
            reply = await handler(
                response=response,
                scenario_id=scenario_id,
                entity_id=entity_id,
                plan=plan,
                params=intent.params,
                tools=tools,
            )
        except _MaxCallsReached:
            reply = (
                "**Stopped: the per-turn tool-call limit was reached** "
                f"({self.max_tool_calls} calls). This bounds runaway workflows; "
                "ask again to continue from the results above."
            )
            response.truncated = True

        response.reply = reply
        response.tool_calls_made = sum(
            1 for s in response.steps if s.status != "skipped"
        )
        response.duration_ms = int((time.monotonic() - started) * 1000)
        return response

    # ── Tool execution ───────────────────────────────────────────────────────

    async def _run_tool(self, tool: str, arguments: dict[str, Any]) -> ChatStep:
        if self._call_count >= self.max_tool_calls:
            return ChatStep(tool=tool, arguments=arguments, status="skipped",
                            error="per-turn tool-call limit reached")
        self._call_count += 1
        started = time.monotonic()
        try:
            result = await self.client.call_tool(tool, arguments)
            duration_ms = int((time.monotonic() - started) * 1000)
            return ChatStep(
                tool=tool,
                arguments=arguments,
                status="ok",
                duration_ms=duration_ms,
                result=result,
                summary=explain(tool, result),
            )
        except MCPToolFailedError as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            return ChatStep(
                tool=tool, arguments=arguments, status="error",
                duration_ms=duration_ms, error=exc.message,
                summary=explain_tool_error(tool, exc.message),
            )
        except MCPConnectionError as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            return ChatStep(
                tool=tool, arguments=arguments, status="error",
                duration_ms=duration_ms, error=str(exc),
                summary=f"Connection failure: {exc}",
            )

    # ── Entity / scenario resolution (live data only) ────────────────────────

    def _resolve_entity(
        self, message: str, plan: dict[str, Any] | None
    ) -> tuple[str | None, bool]:
        """Resolve a mentioned entity against live plan data.

        Returns ``(entity_id, ambiguous)``. ``entity_id`` is None when nothing
        in the message matches an entity id or name; ambiguous flags a
        message-level word that matched nothing so the caller can ask.
        """
        if not plan:
            return None, False
        normalized_message = _normalize(message)
        best: tuple[int, str] | None = None
        for entity in plan.get("entities", []):
            candidates = {
                _normalize(str(entity.get("id", ""))),
                _normalize(str(entity.get("name", ""))),
            }
            for candidate in candidates:
                if len(candidate) < 3 or not candidate:
                    continue
                if candidate in normalized_message:
                    if best is None or len(candidate) > best[0]:
                        best = (len(candidate), entity["id"])
        return (best[1] if best else None), False

    def _entity_clarification(self, plan: dict[str, Any] | None, _ambiguous: bool) -> str:
        if not plan:
            return "I need a scenario before I can resolve that entity — pick a scenario and try again."
        entities = plan.get("entities", [])
        listing = ", ".join(f"`{e['id']}` ({e.get('name', '')})" for e in entities[:12])
        more = f" (+{len(entities) - 12} more)" if len(entities) > 12 else ""
        return (
            "I could not match that to an entity in this scenario, and I will not "
            "guess an id. Did you mean one of:\n\n"
            f"{listing}{more}\n\n"
            "Ask again naming one of those ids (or its exact name)."
        )

    # ── Workflow builders ────────────────────────────────────────────────────

    async def _plan_workflow(self, **kw: Any) -> str:
        response: ChatResponse = kw["response"]
        scenario_id: str | None = kw["scenario_id"]
        plan = kw["plan"]
        if plan is None and scenario_id:
            step = await self._run_tool("get_plan_state", {"scenario_id": scenario_id})
            response.steps.append(step)
            plan = step.result if step.status == "ok" else None
            if step.status != "ok":
                return explain_tool_error("get_plan_state", step.error or "unknown error")
        return explain("get_plan_state", plan)

    async def _simulate_workflow(self, **kw: Any) -> str:
        response: ChatResponse = kw["response"]
        scenario_id: str | None = kw["scenario_id"]
        entity_id: str | None = kw["entity_id"]
        params: dict[str, Any] = kw["params"]
        args = {
            "scenario_id": scenario_id,
            "entity_id": entity_id,
            "reason": params.get("reason")
            or f"{params.get('event_type', 'service_outage').replace('_', ' ')} reported in chat",
            "severity": params.get("severity", "high"),
        }
        step = await self._run_tool("simulate_disruption", args)
        response.steps.append(step)
        if step.status != "ok":
            return explain_tool_error("simulate_disruption", step.error or "unknown error")
        base = explain("simulate_disruption", step.result)
        return base + "\n\n_Next: ask \"find recovery alternatives\" or \"what is blocked and what can continue\"._"

    async def _triage_workflow(self, **kw: Any) -> str:
        response: ChatResponse = kw["response"]
        scenario_id: str | None = kw["scenario_id"]
        entity_id: str | None = kw["entity_id"]
        params: dict[str, Any] = kw["params"]
        args: dict[str, Any] = {
            "scenario_id": scenario_id,
            "entity_id": entity_id,
            "event_type": params.get("event_type", "service_outage"),
        }
        if params.get("reason"):
            args["reason"] = params["reason"]
        if params.get("delay_minutes") is not None:
            args["delay_minutes"] = params["delay_minutes"]
        step = await self._run_tool("analyze_blocked_tasks", args)
        response.steps.append(step)
        if step.status != "ok":
            return explain_tool_error("analyze_blocked_tasks", step.error or "unknown error")
        return explain("analyze_blocked_tasks", step.result)

    async def _alternatives_workflow(self, **kw: Any) -> str:
        response: ChatResponse = kw["response"]
        scenario_id: str | None = kw["scenario_id"]
        entity_id: str | None = kw["entity_id"]
        step = await self._run_tool(
            "find_alternative_dependencies",
            {"scenario_id": scenario_id, "entity_id": entity_id},
        )
        response.steps.append(step)
        if step.status != "ok":
            return explain_tool_error("find_alternative_dependencies", step.error or "unknown error")
        return explain("find_alternative_dependencies", step.result)

    async def _substitutes_workflow(self, **kw: Any) -> str:
        response: ChatResponse = kw["response"]
        scenario_id: str | None = kw["scenario_id"]
        entity_id: str | None = kw["entity_id"]
        step = await self._run_tool(
            "find_substitute_options",
            {"scenario_id": scenario_id, "entity_id": entity_id},
        )
        response.steps.append(step)
        if step.status != "ok":
            return explain_tool_error("find_substitute_options", step.error or "unknown error")
        return explain("find_substitute_options", step.result)

    async def _evaluate_workflow(self, **kw: Any) -> str:
        response: ChatResponse = kw["response"]
        scenario_id: str | None = kw["scenario_id"]
        params: dict[str, Any] = kw["params"]
        plan: dict[str, Any] | None = kw["plan"]
        option_id = params.get("option_id")
        if not option_id:
            response.needs_clarification = True
            return (
                "Which recovery option should I evaluate? Pass its `option_id` "
                "(e.g. `drop-dependency:backend-integration->payment-api`) — ask "
                "\"find recovery alternatives\" first to see the ids."
            )
        version = params.get("expected_plan_version")
        note = ""
        if version is None:
            version = (plan or {}).get("version", 1)
            note = f"_(using committed plan version {version})_"
        step = await self._run_tool(
            "evaluate_recovery_plan",
            {
                "scenario_id": scenario_id,
                "option_id": option_id,
                "expected_plan_version": version,
            },
        )
        response.steps.append(step)
        if step.status != "ok":
            return explain_tool_error("evaluate_recovery_plan", step.error or "unknown error")
        return explain("evaluate_recovery_plan", step.result) + ("\n\n" + note if note else "")

    async def _constraints_workflow(self, **kw: Any) -> str:
        response: ChatResponse = kw["response"]
        scenario_id: str | None = kw["scenario_id"]
        step = await self._run_tool("validate_hard_constraints", {"scenario_id": scenario_id})
        response.steps.append(step)
        if step.status != "ok":
            return explain_tool_error("validate_hard_constraints", step.error or "unknown error")
        return explain("validate_hard_constraints", step.result)

    async def _compare_workflow(self, **kw: Any) -> str:
        response: ChatResponse = kw["response"]
        scenario_id: str | None = kw["scenario_id"]
        entity_id: str | None = kw["entity_id"]
        params: dict[str, Any] = kw["params"]
        options: list[dict[str, Any]] = []
        if params.get("option_id"):
            options = [{"option_id": params["option_id"]}]
        elif entity_id:
            alt_step = await self._run_tool(
                "find_alternative_dependencies",
                {"scenario_id": scenario_id, "entity_id": entity_id},
            )
            response.steps.append(alt_step)
            if alt_step.status != "ok":
                return explain_tool_error("find_alternative_dependencies", alt_step.error or "unknown error")
            options = (alt_step.result or {}).get("options", [])
        if not options:
            response.needs_clarification = True
            return (
                "I need the recovery options to compare. Name the disrupted "
                "entity (\"compare recovery options for payment-api\") or pass "
                "option ids directly."
            )
        step = await self._run_tool(
            "compare_recovery_options",
            {"scenario_id": scenario_id, "options": options},
        )
        response.steps.append(step)
        if step.status != "ok":
            return explain_tool_error("compare_recovery_options", step.error or "unknown error")
        return explain("compare_recovery_options", step.result)

    async def _forecast_workflow(self, **kw: Any) -> str:
        response: ChatResponse = kw["response"]
        scenario_id: str | None = kw["scenario_id"]
        entity_id: str | None = kw["entity_id"]
        params: dict[str, Any] = kw["params"]
        args: dict[str, Any] = {"scenario_id": scenario_id}
        option_id = params.get("option_id")
        if option_id and option_id.lower().startswith("substitute:"):
            # The forecast tool rejects substitute ids; resolve real changes.
            sub_step = await self._run_tool(
                "find_substitute_options",
                {"scenario_id": scenario_id, "entity_id": entity_id},
            )
            response.steps.append(sub_step)
            match = None
            for option in (sub_step.result or {}).get("options", []):
                if option.get("option_id") == option_id:
                    match = option
                    break
            if sub_step.status != "ok":
                return explain_tool_error("find_substitute_options", sub_step.error or "unknown error")
            if match is None:
                return (
                    f"Substitute option `{option_id}` was not found for this "
                    "scenario; run \"find substitute options\" to list valid ids."
                )
            args["changes"] = match.get("changes", [])
        elif option_id:
            args["option_id"] = option_id
            if entity_id:
                args["disrupted_entity_id"] = entity_id
        step = await self._run_tool("recalculate_delivery_forecast", args)
        response.steps.append(step)
        if step.status != "ok":
            return explain_tool_error("recalculate_delivery_forecast", step.error or "unknown error")
        return explain("recalculate_delivery_forecast", step.result)

    async def _workload_workflow(self, **kw: Any) -> str:
        response: ChatResponse = kw["response"]
        scenario_id: str | None = kw["scenario_id"]
        plan: dict[str, Any] | None = kw["plan"]
        version = (plan or {}).get("version", 1)
        args: dict[str, Any] = {
            "scenario_id": scenario_id,
            "expected_plan_version": version,
        }
        if kw["params"].get("resource_id"):
            args["resource_id"] = kw["params"]["resource_id"]
        step = await self._run_tool("rebalance_workload", args)
        response.steps.append(step)
        if step.status != "ok":
            return explain_tool_error("rebalance_workload", step.error or "unknown error")
        return explain("rebalance_workload", step.result)

    async def _schedule_workflow(self, **kw: Any) -> str:
        response: ChatResponse = kw["response"]
        scenario_id: str | None = kw["scenario_id"]
        tools: list[MCPToolInfo] = kw["tools"]
        scheduling = self._find_scheduling_tool(tools)
        if scheduling is None:
            return explain_schedule_missing([t.name for t in tools])
        # A scheduling tool exists: call it with its required arguments only.
        schema = scheduling.input_schema or {}
        args: dict[str, Any] = {}
        missing: list[str] = []
        for key in schema.get("required", []):
            if key == "scenario_id" and scenario_id:
                args[key] = scenario_id
            elif key in kw["params"]:
                args[key] = kw["params"][key]
            else:
                missing.append(key)
        if missing:
            response.needs_clarification = True
            return (
                f"The scheduling tool `{scheduling.name}` requires "
                f"{', '.join(repr(m) for m in missing)}, which I cannot infer "
                "safely. Please provide them and ask again."
            )
        step = await self._run_tool(scheduling.name, args)
        response.steps.append(step)
        if step.status != "ok":
            return explain_tool_error(scheduling.name, step.error or "unknown error")
        return (
            explain(scheduling.name, step.result)
            + "\n\n_Generated schedule is hypothetical and read-only — it is not "
            "committed to the plan._"
        )

    async def _recovery_workflow(self, **kw: Any) -> str:
        """Multi-step: triage → alternatives → compare (→ schedule if any)."""
        response: ChatResponse = kw["response"]
        scenario_id: str | None = kw["scenario_id"]
        entity_id: str | None = kw["entity_id"]
        params: dict[str, Any] = kw["params"]
        tools: list[MCPToolInfo] = kw["tools"]

        sections: list[str] = []
        event_type = params.get("event_type", "service_outage")
        triage_args: dict[str, Any] = {
            "scenario_id": scenario_id,
            "entity_id": entity_id,
            "event_type": event_type,
        }
        if params.get("reason"):
            triage_args["reason"] = params["reason"]
        if params.get("delay_minutes") is not None:
            triage_args["delay_minutes"] = params["delay_minutes"]

        triage_step = await self._run_tool("analyze_blocked_tasks", triage_args)
        response.steps.append(triage_step)
        if triage_step.status == "skipped":
            raise _MaxCallsReached()
        if triage_step.status != "ok":
            return (
                "The workflow stopped at the first step:\n\n"
                + explain_tool_error("analyze_blocked_tasks", triage_step.error or "unknown error")
                + "\n\nI did not continue to recovery options because the "
                "blockage picture failed — retry once the server is healthy."
            )
        sections.append(explain("analyze_blocked_tasks", triage_step.result))

        alt_step = await self._run_tool(
            "find_alternative_dependencies",
            {"scenario_id": scenario_id, "entity_id": entity_id},
        )
        # Append before the status checks so failed/skipped steps stay visible
        # in the UI trace.
        response.steps.append(alt_step)
        if alt_step.status == "skipped":
            raise _MaxCallsReached()
        if alt_step.status != "ok":
            sections.append(
                "### Recovery alternatives\n\n"
                + explain_tool_error("find_alternative_dependencies", alt_step.error or "unknown error")
                + "\n\n_Triage above still stands._"
            )
            return "\n\n".join(sections)
        sections.append(explain("find_alternative_dependencies", alt_step.result))

        options = (alt_step.result or {}).get("options", [])
        if options:
            compare_step = await self._run_tool(
                "compare_recovery_options",
                {"scenario_id": scenario_id, "options": options},
            )
            response.steps.append(compare_step)
            if compare_step.status == "skipped":
                raise _MaxCallsReached()
            if compare_step.status == "ok":
                sections.append(explain("compare_recovery_options", compare_step.result))
            else:
                sections.append(
                    "### Option comparison\n\n"
                    + explain_tool_error("compare_recovery_options", compare_step.error or "unknown error")
                )

        scheduling = self._find_scheduling_tool(tools)
        if scheduling is not None and options:
            schedule_step = await self._run_tool(
                scheduling.name, {"scenario_id": scenario_id}
            )
            response.steps.append(schedule_step)
            if schedule_step.status == "ok":
                sections.append(explain(scheduling.name, schedule_step.result))

        sections.append(
            "_All results above are hypothetical and read-only. Nothing has been "
            "committed; a structurally feasible option still needs human review "
            "before adoption._"
        )
        return "\n\n".join(sections)

    # ── Helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _find_scheduling_tool(tools: list[MCPToolInfo]) -> MCPToolInfo | None:
        """Discover a scheduling tool by name — never by hardcoded assumption."""
        for tool in tools:
            if _SCHEDULING_MATCH.search(tool.name):
                return tool
        return None

    @staticmethod
    def _help_reply(tools: list[MCPToolInfo]) -> str:
        listing = "\n".join(
            f"- `{t.name}` — {t.description.splitlines()[0][:120] if t.description else ''}"
            for t in tools
        )
        return (
            "I map your requests onto the RippleBack MCP server's live tools and "
            "explain the structured results. Try:\n\n"
            "- \"Show me the current plan.\"\n"
            "- \"What happens if the payment API goes down?\"\n"
            "- \"What is blocked and what can continue?\"\n"
            "- \"Find recovery alternatives for payment-api.\"\n"
            "- \"The payment API is down — tell me what is blocked, find "
            "alternatives, and compare the options.\"\n"
            "- \"Validate this recovery proposal: drop-dependency:...\"\n"
            "- \"Can you rebalance the workload?\"\n"
            "- \"Generate a recovery schedule after this disruption.\"\n\n"
            f"Live tools on the server ({len(tools)}):\n\n{listing}\n\n"
            "_Everything I run is hypothetical and read-only — the committed plan "
            "is never modified._"
        )


class _MaxCallsReached(Exception):
    """Internal: the per-turn tool-call budget is exhausted."""
