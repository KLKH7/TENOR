"""A synchronous MCP client for any server: stdio subprocess, Streamable HTTP URL, or (in tests)
an in-process server object.

Granola's MCP needs OAuth, so it keeps its own caller (``sources.mcp_transport.LiveMcpCaller``).
Everything else (Concentrate's server, a product added from the template) goes through
:class:`SyncMcpClient`, which wraps ``mcp.Client`` in a blocking portal so the synchronous
benchmark can call tools. Like every caller here, it is wrapped in an allowlist by the product.
"""

from __future__ import annotations

import json
import threading
from contextlib import AbstractContextManager
from types import TracebackType
from typing import Any

from anyio.from_thread import BlockingPortal, start_blocking_portal
from mcp import Client, StdioServerParameters
from mcp.types import TextContent

from tenor.blackbox.base import ToolInfo
from tenor.sources.granola_mcp import McpToolError


class SyncMcpClient(AbstractContextManager["SyncMcpClient"]):
    """``McpCaller`` over ``mcp.Client``. ``target``: a URL, ``StdioServerParameters`` or an
    in-process server. Use as a context manager."""

    def __init__(self, target: Any, read_timeout_s: float = 300.0) -> None:
        self._target = target
        self._timeout = read_timeout_s
        self._portal_cm: AbstractContextManager[BlockingPortal] | None = None
        self._client_cm: AbstractContextManager[Client] | None = None
        self._portal: BlockingPortal | None = None
        self._client: Client | None = None
        self._lock = threading.Lock()

    @classmethod
    def stdio(
        cls, command: str, args: list[str], env: dict[str, str] | None = None
    ) -> SyncMcpClient:
        """Launch ``command args`` and speak MCP over its stdin/stdout."""
        return cls(StdioServerParameters(command=command, args=args, env=env))

    def __enter__(self) -> SyncMcpClient:
        self._portal_cm = start_blocking_portal()
        self._portal = self._portal_cm.__enter__()
        self._client_cm = self._portal.wrap_async_context_manager(
            Client(self._target, read_timeout_seconds=self._timeout)
        )
        self._client = self._client_cm.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._client_cm is not None:
            self._client_cm.__exit__(exc_type, exc, tb)
        if self._portal_cm is not None:
            self._portal_cm.__exit__(exc_type, exc, tb)

    def _live(self) -> tuple[BlockingPortal, Client]:
        if self._portal is None or self._client is None:
            raise RuntimeError("SyncMcpClient must be used as a context manager")
        return self._portal, self._client

    def call(self, tool: str, arguments: dict[str, Any]) -> str:
        portal, client = self._live()
        with self._lock:
            result = portal.call(client.call_tool, tool, arguments)
        text = "\n".join(c.text for c in result.content if isinstance(c, TextContent))
        if result.is_error:
            raise McpToolError(tool, text)
        if not text and result.structured_content is not None:
            text = json.dumps(result.structured_content)
        return text

    def list_tools(self) -> list[ToolInfo]:
        """The server's tools with their read-only/destructive annotations (if it sets them)."""
        portal, client = self._live()
        with self._lock:
            listed = portal.call(client.list_tools)
        return [
            ToolInfo(
                name=t.name,
                read_only_hint=t.annotations.read_only_hint if t.annotations else None,
                destructive_hint=t.annotations.destructive_hint if t.annotations else None,
            )
            for t in listed.tools
        ]
