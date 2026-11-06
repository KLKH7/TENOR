"""The black-box contract: ask a meeting-memory product a question, get back what it said.

A *product* is anything that answers questions about the user's meetings through MCP (Granola's
own agent, Concentrate's server, any other meeting tool). The benchmark sees only what an
external agent would see through that MCP: answer text, the meetings it cites, how long it took,
and the raw response. Nothing about how the product works inside.

Every product declares the MCP tools it may call (:attr:`Product.read_only_tools`). Its caller
is wrapped in :class:`AllowlistCaller`, which refuses everything else *before* anything is sent,
so a typo, a template copied carelessly or a prompt-injected tool name cannot reach a write tool.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from tenor.sources.allowlist import AllowlistCaller, ToolNotAllowed
from tenor.sources.granola_mcp import McpCaller

__all__ = [
    "AllowlistCaller",
    "McpProduct",
    "Product",
    "ProductAnswer",
    "ToolInfo",
    "ToolNotAllowed",
    "preflight",
]


class ProductAnswer(BaseModel):
    """What a product said, as an external agent would receive it."""

    model_config = ConfigDict(extra="forbid")

    text: str  # the answer, with fixed boilerplate (disclaimers) removed
    cited_ids: list[str] = Field(default_factory=list)  # meeting IDs (or line IDs) it cited
    latency_s: float
    raw: str  # the tool response exactly as received (PRIVATE: may quote meetings)
    abstained: bool | None = None  # None: the product has no explicit abstain signal
    cost_usd: float = 0.0  # only when the product reports spend billed to us (Concentrate)


@runtime_checkable
class Product(Protocol):
    """A meeting-memory product reachable over MCP."""

    name: str
    read_only_tools: frozenset[str]

    def ask(self, question: str) -> ProductAnswer:
        """Ask one question about the user's meetings."""
        ...


class ToolInfo(BaseModel):
    """What a server says about one tool (``tools/list``), for the read-only preflight."""

    name: str
    read_only_hint: bool | None = None
    destructive_hint: bool | None = None


def preflight(product: Product, tools: list[ToolInfo]) -> list[str]:
    """Problems with a product's allowlist given the server's own tool list; empty if fine.

    Fails when an allowed tool is missing, or when the server *annotates* it as not read-only or
    destructive. Unannotated tools pass: annotations are optional in MCP, so the allowlist (which
    a human checked against the checklist in docs/method/BLACKBOX.md) stays the real guard.
    """
    by_name = {t.name: t for t in tools}
    problems = []
    for name in sorted(product.read_only_tools):
        t = by_name.get(name)
        if t is None:
            problems.append(f"{name}: not offered by the server")
        elif t.read_only_hint is False or t.destructive_hint is True:
            problems.append(f"{name}: the server annotates it as not read-only")
    return problems


class McpProduct:
    """Base for one-tool products: call ``tool`` with the question, parse the text it returns.

    Subclasses set ``name``, ``tool`` and ``read_only_tools`` and implement :meth:`arguments`
    and :meth:`parse`. The caller is always wrapped in :class:`AllowlistCaller`.
    """

    name = "product"
    tool = ""
    read_only_tools: frozenset[str] = frozenset()

    def __init__(self, caller: McpCaller, clock: Callable[[], float] = time.perf_counter) -> None:
        if self.tool not in self.read_only_tools:
            raise ToolNotAllowed(f"{self.name}: its own tool {self.tool!r} is not allowlisted")
        self.caller = AllowlistCaller(caller, self.read_only_tools, self.name)
        self.clock = clock

    def arguments(self, question: str) -> dict[str, Any]:
        """The tool arguments for one question."""
        raise NotImplementedError

    def parse(self, raw: str, latency_s: float) -> ProductAnswer:
        """Turn the raw tool text into a :class:`ProductAnswer`."""
        raise NotImplementedError

    def ask(self, question: str) -> ProductAnswer:
        start = self.clock()
        raw = self.caller.call(self.tool, self.arguments(question))
        return self.parse(raw, self.clock() - start)
