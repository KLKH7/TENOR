"""The standard outside agent: one fixed model and prompt that may use only a product's read
tools, under the same tool budget for every product.

Why: Wispr Flow's MCP has no question-answering agent; Granola's has one
(``query_granola_meetings``). Comparing Granola's agent with nothing would be meaningless, and
comparing two different vendors' agents conflates model, prompt and data. The outside agent
holds the agent fixed and varies only the *data interface*: what an external agent (Claude,
ChatGPT, Cursor) can learn through each product's read tools. Granola's own agent stays an
extra row (:class:`~.products.GranolaProduct`).

Held fixed across products: model (``LLMClient.model``), prompt (``outside_agent.v1``),
:class:`ToolBudget` (tool calls, characters per tool result, turns), answer schema, and the
question text. Varies: the product's tools as the server publishes them (names, descriptions,
input schemas) and what they return. Every call goes through the product's
:class:`~concentrate.sources.allowlist.AllowlistCaller`; a tool outside the binding is refused
before anything is sent and the model is told so.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import date
from typing import Any

from concentrate.blackbox.base import AllowlistCaller, ProductAnswer, ToolNotAllowed
from concentrate.llm.client import LLMClient, ToolBackend, TurnRequest, load_prompt, run_turn
from concentrate.sources.granola_mcp import McpCaller, McpToolError

PROMPT_ID = "outside_agent.v1"
STAGE = "blackbox.outside_agent"

ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "abstain": {"type": "boolean"},
        "citations": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["answer", "abstain", "citations"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class ToolBinding:
    """Which of a product's read tools the outside agent may see and call."""

    product: str
    tools: tuple[str, ...]


# search → read meeting → read transcript, through each product's own read tools.
GRANOLA_BINDING = ToolBinding(
    "granola", ("search_meetings", "get_meetings", "get_meeting_transcript", "search_transcripts")
)
WISPR_BINDING = ToolBinding("wispr", ("search_meetings", "get_meeting"))
BINDINGS = {b.product: b for b in (GRANOLA_BINDING, WISPR_BINDING)}


@dataclass(frozen=True)
class ToolBudget:
    """Identical for every product. 40,000 characters is the largest single read either
    product's tools return (Wispr: ``char_limit`` max; Granola: ``max_chars`` default)."""

    max_tool_calls: int = 10
    max_result_chars: int = 40_000
    max_turns: int = 12
    max_tokens: int = 4000


DEFAULT_BUDGET = ToolBudget()
_LINE_SUFFIX = re.compile(r":\d{4}$")


def bind_tools(binding: ToolBinding, published: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The binding's tools as the server published them, in Messages API tool format.

    Raises :class:`ToolNotAllowed` if the server doesn't offer a bound tool: the comparison is
    only fair if every product's agent gets the tools its binding names.
    """
    by_name = {t["name"]: t for t in published}
    missing = [n for n in binding.tools if n not in by_name]
    if missing:
        raise ToolNotAllowed(f"{binding.product}: server does not offer {missing}")
    return [
        {
            "name": n,
            "description": by_name[n].get("description") or "",
            "input_schema": by_name[n].get("input_schema") or {"type": "object"},
        }
        for n in binding.tools
    ]


class OutsideAgentProduct:
    """A :class:`~.base.Product`: the standard outside agent over one product's read tools."""

    def __init__(
        self,
        binding: ToolBinding,
        caller: McpCaller,
        published_tools: list[dict[str, Any]],
        client: LLMClient,
        *,
        backend: ToolBackend | None = None,
        budget: ToolBudget = DEFAULT_BUDGET,
        as_of: date | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.name = f"{binding.product}+outside-agent"
        self.read_only_tools = frozenset(binding.tools)
        self.binding = binding
        self.caller = AllowlistCaller(caller, self.read_only_tools, self.name)
        self.tools = bind_tools(binding, published_tools)
        self.client = client
        self.backend = backend
        self.budget = budget
        self.as_of = as_of
        self.clock = clock
        self.system = load_prompt(PROMPT_ID)

    def _run_tool(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        try:
            out = self.caller.call(name, arguments)
        except ToolNotAllowed as exc:
            return str(exc), True
        except McpToolError as exc:
            return str(exc), True
        cap = self.budget.max_result_chars
        if len(out) > cap:
            out = out[:cap] + f"\n[outside agent: result cut at {cap} characters]"
        return out, False

    def ask(self, question: str) -> ProductAnswer:
        start = self.clock()
        spent0 = self.client.ledger.spent_usd()
        dated = f"Today is {self.as_of.isoformat()}.\n\n" if self.as_of else ""
        messages: list[dict[str, Any]] = [{"role": "user", "content": dated + question}]
        trace: list[dict[str, Any]] = []
        calls = 0
        text = ""
        for turn in range(self.budget.max_turns):
            last = turn == self.budget.max_turns - 1
            completion = run_turn(
                self.client,
                TurnRequest(
                    stage=STAGE,
                    prompt_id=PROMPT_ID,
                    system=self.system,
                    messages=tuple(messages),
                    tools=() if last else tuple(self.tools),
                    schema=ANSWER_SCHEMA,
                    max_tokens=self.budget.max_tokens,
                ),
                self.backend,
            )
            uses = completion.tool_uses
            if not uses:
                text = completion.text
                break
            messages.append({"role": "assistant", "content": completion.content})
            results = []
            for use in uses:
                if calls >= self.budget.max_tool_calls:
                    out, err = "tool budget exhausted: answer now from what you have", True
                else:
                    calls += 1
                    out, err = self._run_tool(use["name"], dict(use.get("input") or {}))
                trace.append({"tool": use["name"], "arguments": use.get("input"), "error": err})
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": use["id"],
                        "content": out,
                        "is_error": err,
                    }
                )
            messages.append({"role": "user", "content": results})
        return self._answer(
            text, trace, self.clock() - start, self.client.ledger.spent_usd() - spent0
        )

    def _answer(
        self, text: str, trace: list[dict[str, Any]], latency_s: float, cost: float
    ) -> ProductAnswer:
        raw = json.dumps({"final": text, "trace": trace}, ensure_ascii=False)
        try:
            out = json.loads(text)
            answer, abstain = str(out["answer"]), bool(out["abstain"])
            cites = [_LINE_SUFFIX.sub("", str(c)) for c in out.get("citations", [])]
        except (ValueError, KeyError, TypeError):
            return ProductAnswer(
                text=text, latency_s=latency_s, raw=raw, abstained=True, cost_usd=cost
            )
        return ProductAnswer(
            text=answer,
            cited_ids=list(dict.fromkeys(c for c in cites if c)),
            latency_s=latency_s,
            raw=raw,
            abstained=abstain,
            cost_usd=cost,
        )


def open_outside_agent(
    product: str, stack: ExitStack, client: LLMClient, **kw: Any
) -> OutsideAgentProduct:
    """Connect the outside agent to ``product``'s live MCP (``granola`` or ``wispr``). Live:
    may open a browser for sign-in. Tools are taken from the server's own ``tools/list``."""
    from concentrate.sources.mcp_transport import LiveMcpCaller, wispr_caller

    binding = BINDINGS[product]
    caller = stack.enter_context(wispr_caller() if product == "wispr" else LiveMcpCaller())
    return OutsideAgentProduct(binding, caller, caller.list_tools(), client, **kw)
