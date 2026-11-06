"""In-memory fake of the Granola public API, served through ``httpx.MockTransport``.

It implements the documented surface (List Notes, Get Note, paginated transcript, List
Folders) with real cursor pagination and its own server-side rate limiter running on the same
``FakeClock`` as the client, so tests can assert that the client never trips a 429 under
normal operation. Failure modes are injectable: forced 429s, notes still processing (excluded
from List, 404 on Get), a List→Get race (listed but 404 on Get N times), and transcripts too
large to inline (413).
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from tenor.core import granola
from tenor.sources.ratelimit import FakeClock

API_KEY = "grn_test_SECRET_do_not_log_1234"


@dataclass
class FakeGranola:
    clock: FakeClock
    notes: dict[str, dict[str, Any]] = field(default_factory=dict)
    folders: list[dict[str, Any]] = field(default_factory=list)
    processing: set[str] = field(default_factory=set)  # hidden from List, 404 on Get
    get_404s: dict[str, int] = field(default_factory=dict)  # listed, but Get 404s N times (race)
    too_large: set[str] = field(default_factory=set)
    force_429: int = 0
    retry_after: str | None = "2"
    requests: list[httpx.Request] = field(default_factory=list)
    rate_limited: int = 0
    _window: deque[float] = field(default_factory=deque)

    # -- helpers ----------------------------------------------------------------------------

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    @staticmethod
    def _page(items: list[Any], params: httpx.QueryParams, key: str, max_size: int) -> Any:
        size = int(params.get("page_size", "10"))
        if not 1 <= size <= max_size:
            return httpx.Response(400, json={"message": "page_size out of range"})
        start = int(params.get("cursor") or 0)
        chunk = items[start : start + size]
        more = start + size < len(items)
        return httpx.Response(
            200, json={key: chunk, "hasMore": more, "cursor": str(start + size) if more else None}
        )

    def _server_rate_limited(self) -> bool:
        now = self.clock.now()
        while self._window and now - self._window[0] >= 5.0:
            self._window.popleft()
        if len(self._window) >= 25:
            return True
        self._window.append(now)
        return False

    # -- routing ----------------------------------------------------------------------------

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers.get("Authorization") != f"Bearer {API_KEY}":
            return httpx.Response(401, json={"message": "Unauthorized - Invalid API key"})
        if self.force_429 > 0 or self._server_rate_limited():
            self.force_429 = max(0, self.force_429 - 1)
            self.rate_limited += 1
            headers = {"Retry-After": self.retry_after} if self.retry_after else {}
            return httpx.Response(429, headers=headers)

        path, params = request.url.path, request.url.params
        if path == "/v1/notes":
            return self._list_notes(params)
        if path == "/v1/folders":
            return self._page(self.folders, params, "folders", 30)
        parts = path.split("/")
        if len(parts) == 4 and parts[2] == "notes":
            return self._get_note(parts[3], params)
        if len(parts) == 5 and parts[2] == "notes" and parts[4] == "transcript":
            note = self.notes.get(parts[3])
            if note is None or parts[3] in self.processing:
                return httpx.Response(404)
            return self._page(note["transcript"] or [], params, "transcript", 100)
        return httpx.Response(404)

    def _list_notes(self, params: httpx.QueryParams) -> httpx.Response:
        def after(n: dict[str, Any], key: str, bound: str | None) -> bool:
            return bound is None or datetime.fromisoformat(n[key]) > datetime.fromisoformat(bound)

        visible = [
            n
            for nid, n in self.notes.items()
            if nid not in self.processing and after(n, "updated_at", params.get("updated_after"))
        ]
        visible.sort(key=lambda n: (n["created_at"], n["id"]), reverse=True)
        fields = set(granola.NoteSummary.model_fields)
        summaries = [{k: v for k, v in n.items() if k in fields} for n in visible]
        return self._page(summaries, params, "notes", 30)

    def _get_note(self, note_id: str, params: httpx.QueryParams) -> httpx.Response:
        note = self.notes.get(note_id)
        if note is None or note_id in self.processing:
            return httpx.Response(404)
        if self.get_404s.get(note_id, 0) > 0:
            self.get_404s[note_id] -= 1
            return httpx.Response(404)
        include = params.get("include") == "transcript"
        if include and note_id in self.too_large:
            return httpx.Response(413, json={"code": "TRANSCRIPT_TOO_LARGE"})
        body = dict(note) if include else {**note, "transcript": None}
        return httpx.Response(200, content=json.dumps(body))
