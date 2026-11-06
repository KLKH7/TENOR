"""Read-only tool allowlists for MCP callers.

Every MCP caller that touches a product (a sync source or a black-box adapter) is wrapped in
:class:`AllowlistCaller`, which refuses any tool outside its allowlist *before* anything is sent.
A typo, a carelessly copied template or a prompt-injected tool name cannot reach a write tool.
"""

from __future__ import annotations

from typing import Any

from tenor.sources.granola_mcp import McpCaller


class ToolNotAllowed(PermissionError):
    """Raised before a call is sent when the tool is not on the read-only allowlist."""


class AllowlistCaller:
    """Wraps an ``McpCaller``; refuses any tool outside ``allowed``."""

    def __init__(self, inner: McpCaller, allowed: frozenset[str], product: str = "product") -> None:
        self._inner = inner
        self.allowed = allowed
        self.product = product

    def call(self, tool: str, arguments: dict[str, Any]) -> str:
        if tool not in self.allowed:
            raise ToolNotAllowed(
                f"{tool!r} is not on {self.product}'s read-only allowlist; refusing to call it"
            )
        return self._inner.call(tool, arguments)
