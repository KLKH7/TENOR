"""Live MCP transport: OAuth (browser sign-in, dynamic client registration) + Streamable HTTP.

``LiveMcpCaller`` is a synchronous context manager wrapping one async MCP session, so the
rest of Concentrate (synchronous) can call tools. On first use it opens the browser for the
product's sign-in (Granola by default; Wispr Flow via :func:`wispr_caller`) and catches the
redirect on a local port; tokens and the registered client are stored in one file per product
under ``~/.config/concentrate/`` (mode 600, outside the repo) and are never logged. Later runs
reuse and refresh them silently.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import webbrowser
from collections.abc import AsyncIterator
from contextlib import AbstractContextManager, asynccontextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import TracebackType
from typing import Any
from urllib.parse import parse_qs, urlparse

import anyio
import httpx2
from anyio.from_thread import BlockingPortal, start_blocking_portal
from mcp import ClientSession
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import (
    AuthorizationCodeResult,
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthToken,
)
from mcp.types import TextContent
from pydantic import AnyUrl

from tenor.sources.granola_mcp import GRANOLA_MCP_URL, McpToolError

log = logging.getLogger(__name__)

DEFAULT_TOKEN_PATH = Path(
    os.environ.get("CONCENTRATE_MCP_TOKENS") or "~/.config/concentrate/granola-mcp.json"
).expanduser()
WISPR_TOKEN_PATH = Path(
    os.environ.get("CONCENTRATE_WISPR_TOKENS") or "~/.config/concentrate/wispr-mcp.json"
).expanduser()
CALLBACK_PORT = int(os.environ.get("CONCENTRATE_MCP_CALLBACK_PORT", "33418"))
_RETRY_AFTER = re.compile(r'"?retry_after_seconds"?\s*[:=]\s*"?([\d.]+)')


class FileTokenStorage(TokenStorage):
    """OAuth tokens + registered client info in a single private JSON file."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _read(self) -> dict[str, Any]:
        return json.loads(self.path.read_text()) if self.path.exists() else {}

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        tmp.chmod(0o600)
        tmp.replace(self.path)

    async def get_tokens(self) -> OAuthToken | None:
        raw = self._read().get("tokens")
        return OAuthToken.model_validate(raw) if raw else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self._write({**self._read(), "tokens": tokens.model_dump(mode="json")})

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        raw = self._read().get("client")
        return OAuthClientInformationFull.model_validate(raw) if raw else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self._write({**self._read(), "client": client_info.model_dump(mode="json")})


def _wait_for_redirect(port: int, timeout_s: float = 300) -> AuthorizationCodeResult:
    """Serve one request on 127.0.0.1:port/callback and return its code/state."""
    result: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            query = parse_qs(urlparse(self.path).query)
            for key in ("code", "state", "iss", "error"):
                if key in query:
                    result[key] = query[key][0]
            ok = "code" in result
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            msg = "Signed in. You can close this tab." if ok else "Sign-in failed."
            self.wfile.write(f"<p style='font-family:sans-serif'>{msg}</p>".encode())

        def log_message(self, *args: object) -> None:  # keep codes out of logs
            pass

    server = HTTPServer(("127.0.0.1", port), Handler)
    server.timeout = timeout_s
    server.handle_request()
    server.server_close()
    if "code" not in result:
        raise McpToolError("oauth", f"sign-in did not complete ({result.get('error', 'timeout')})")
    return AuthorizationCodeResult(
        code=result["code"], state=result.get("state"), iss=result.get("iss")
    )


async def _callback() -> AuthorizationCodeResult:
    return await anyio.to_thread.run_sync(_wait_for_redirect, CALLBACK_PORT)


@asynccontextmanager
async def _session(
    url: str, storage: TokenStorage, label: str = "Granola"
) -> AsyncIterator[ClientSession]:
    async def open_browser(auth_url: str) -> None:
        print(f"Opening your browser to sign in to {label}…")
        webbrowser.open(auth_url)

    oauth = OAuthClientProvider(
        server_url=url,
        client_metadata=OAuthClientMetadata(
            client_name="Concentrate (research prototype)",
            redirect_uris=[AnyUrl(f"http://127.0.0.1:{CALLBACK_PORT}/callback")],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            token_endpoint_auth_method="none",
            application_type="native",
        ),
        storage=storage,
        redirect_handler=open_browser,
        callback_handler=_callback,
    )
    async with (
        # Long read timeout: query-style tools can take a while (SSE responses).
        httpx2.AsyncClient(auth=oauth, timeout=httpx2.Timeout(30.0, read=300.0)) as http,
        streamable_http_client(url, http_client=http) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        yield session


class LiveMcpCaller(AbstractContextManager["LiveMcpCaller"]):
    """Synchronous ``McpCaller`` backed by a live MCP session. Use as a context manager."""

    def __init__(
        self,
        url: str = GRANOLA_MCP_URL,
        token_path: Path = DEFAULT_TOKEN_PATH,
        label: str = "Granola",
    ) -> None:
        self._url = url
        self._label = label
        self._storage = FileTokenStorage(token_path)
        self._portal_cm: AbstractContextManager[BlockingPortal] | None = None
        self._session_cm: AbstractContextManager[ClientSession] | None = None
        self._portal: BlockingPortal | None = None
        self._session: ClientSession | None = None
        self._lock = threading.Lock()

    def __enter__(self) -> LiveMcpCaller:
        self._portal_cm = start_blocking_portal()
        self._portal = self._portal_cm.__enter__()
        self._session_cm = self._portal.wrap_async_context_manager(
            _session(self._url, self._storage, self._label)
        )
        self._session = self._session_cm.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._session_cm is not None:
            self._session_cm.__exit__(exc_type, exc, tb)
        if self._portal_cm is not None:
            self._portal_cm.__exit__(exc_type, exc, tb)

    def call(self, tool: str, arguments: dict[str, Any]) -> str:
        if self._portal is None or self._session is None:
            raise RuntimeError("LiveMcpCaller must be used as a context manager")
        session = self._session
        with self._lock:
            result = self._portal.call(session.call_tool, tool, arguments)
        text = "\n".join(
            c.text for c in getattr(result, "content", []) if isinstance(c, TextContent)
        )
        if getattr(result, "is_error", False):
            m = _RETRY_AFTER.search(text)
            limited = "rate_limit" in text or "rate limit" in text.lower()
            raise McpToolError(tool, text, float(m.group(1)) if (m and limited) else None)
        return text

    def list_tools(self) -> list[dict[str, Any]]:
        """The server's tools as published (name, description, input schema): what an outside
        agent is shown. Read-only: ``tools/list`` is protocol metadata, not a tool call."""
        if self._portal is None or self._session is None:
            raise RuntimeError("LiveMcpCaller must be used as a context manager")
        with self._lock:
            listed = self._portal.call(self._session.list_tools)
        return [
            {"name": t.name, "description": t.description or "", "input_schema": t.input_schema}
            for t in listed.tools
        ]


def wispr_caller() -> LiveMcpCaller:
    """A caller for Wispr Flow's MCP (URL from ``WISPR_MCP_URL``; its own token file)."""
    from tenor.sources.wispr_mcp import WISPR_MCP_URL

    return LiveMcpCaller(WISPR_MCP_URL, WISPR_TOKEN_PATH, label="Wispr Flow")
