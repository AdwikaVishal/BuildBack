"""RippleBack conversational assistant (MCP client orchestration layer).

The assistant sits *beside* the MCP server, never inside it: it translates
natural-language requests into sequences of real MCP tool calls, then explains
the structured results. All analysis stays in the existing backend engines.
"""

from rippleback.assistant.intents import Intent, parse_intent
from rippleback.assistant.orchestrator import Assistant, ChatResponse, ChatStep
from rippleback.assistant.providers import (
    BedrockProvider,
    DeterministicProvider,
    ProviderReport,
    make_provider,
)

__all__ = [
    "Assistant",
    "BedrockProvider",
    "ChatResponse",
    "ChatStep",
    "DeterministicProvider",
    "Intent",
    "ProviderReport",
    "make_provider",
    "parse_intent",
]
