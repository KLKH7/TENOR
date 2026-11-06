"""``GranolaAPISource``: a polite, read-only client for the Granola public REST API.

Behaviour (see docs/engineering/SCHEMA_NOTES.md for what is verified vs. assumed):

* every request passes through :class:`RateLimiter` (burst 25 / 5 s, sustained 5 req/s);
* ``429`` → honour ``Retry-After`` if the server sends one (undocumented), else exponential
  backoff with jitter; ``5xx`` and transport errors → same backoff, bounded;
* ``404`` on Get Note → bounded backoff, then :class:`NoteNotReady` (processing notes 404);
* ``413 TRANSCRIPT_TOO_LARGE`` on ``include=transcript`` → fall back to the paginated
  ``GET /v1/notes/{id}/transcript`` endpoint;
* cursor pagination via ``cursor`` / ``hasMore``; ``page_size`` capped at the documented max.

The API key is read from ``GRANOLA_API_KEY`` and never logged, printed or included in errors.
"""

from __future__ import annotations

import logging
import os
import random
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel

from tenor.core import granola
from tenor.sources.base import NoteNotReady, SourceError
from tenor.sources.ratelimit import Clock, RateLimiter, SystemClock

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://public-api.granola.ai"
MAX_PAGE_SIZE = 30
MAX_TRANSCRIPT_PAGE_SIZE = 100

M = TypeVar("M", bound=BaseModel)


class GranolaAPIError(SourceError):
    """Non-retryable API error. Carries status and path only — never request headers."""

    def __init__(self, status: int, path: str, detail: str = "") -> None:
        super().__init__(f"Granola API {status} on {path}{': ' + detail if detail else ''}")
        self.status = status
        self.path = path


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("datetimes sent to the Granola API must be timezone-aware")
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class GranolaAPISource:
    """Implements :class:`tenor.sources.base.Source` against the live API."""

    name = "granola"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        transport: httpx.BaseTransport | None = None,
        clock: Clock | None = None,
        rng: random.Random | None = None,
        max_retries: int = 6,
        not_ready_retries: int = 3,
        backoff_base_s: float = 1.0,
        backoff_cap_s: float = 60.0,
    ) -> None:
        key = api_key or os.environ.get("GRANOLA_API_KEY")
        if not key:
            raise SourceError("GRANOLA_API_KEY is not set")
        self._clock = clock or SystemClock()
        self._limiter = RateLimiter(self._clock)
        self._rng = rng or random.Random()
        self._max_retries = max_retries
        self._not_ready_retries = not_ready_retries
        self._backoff_base_s = backoff_base_s
        self._backoff_cap_s = backoff_cap_s
        self._http = httpx.Client(
            base_url=base_url or os.environ.get("GRANOLA_API_BASE_URL") or DEFAULT_BASE_URL,
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
            transport=transport,
            timeout=30.0,
        )

    def __repr__(self) -> str:
        return f"GranolaAPISource(base_url={str(self._http.base_url)!r})"

    def close(self) -> None:
        self._http.close()

    # -- request core -----------------------------------------------------------------------

    def _backoff(self, attempt: int) -> float:
        """Exponential backoff with "equal jitter": a random wait in [ceiling/2, ceiling]."""
        ceiling = min(self._backoff_cap_s, self._backoff_base_s * 2**attempt)
        return self._rng.uniform(ceiling / 2, ceiling)

    def _retry_after(self, response: httpx.Response, attempt: int) -> float:
        header = response.headers.get("Retry-After")
        if header is not None:
            try:
                return max(0.0, float(header))
            except ValueError:
                pass  # HTTP-date form: fall back to backoff
        return self._backoff(attempt)

    def _get(self, path: str, params: Mapping[str, Any] | None = None) -> httpx.Response:
        """GET with rate limiting and retries for 429/5xx/transport errors.

        Returns the final response for any status that is not retried (caller decides on
        404/413/4xx).
        """
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        for attempt in range(self._max_retries + 1):
            self._limiter.acquire()
            try:
                response = self._http.get(path, params=clean)
            except httpx.TransportError as exc:
                if attempt == self._max_retries:
                    raise SourceError(f"transport error on {path}: {type(exc).__name__}") from None
                log.warning("transport error on %s (%s); retrying", path, type(exc).__name__)
                self._clock.sleep(self._backoff(attempt))
                continue
            status = response.status_code
            log.debug("GET %s -> %d", path, status)
            if status == 429 and attempt < self._max_retries:
                wait = self._retry_after(response, attempt)
                log.warning("429 on %s; backing off %.2fs", path, wait)
                self._limiter.penalize(wait)
                continue
            if status >= 500 and attempt < self._max_retries:
                self._clock.sleep(self._backoff(attempt))
                continue
            return response
        raise AssertionError("unreachable")  # pragma: no cover

    def _get_model(self, path: str, model: type[M], params: Mapping[str, Any] | None = None) -> M:
        response = self._get(path, params)
        if response.status_code != 200:
            raise GranolaAPIError(response.status_code, path)
        return model.model_validate_json(response.content)

    # -- Source protocol --------------------------------------------------------------------

    def list_notes(
        self,
        *,
        updated_after: datetime | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        folder_id: str | None = None,
    ) -> Iterator[granola.NoteSummary]:
        """Yield all notes matching the filters, following ``cursor`` until ``hasMore`` is false."""
        params: dict[str, Any] = {
            "page_size": MAX_PAGE_SIZE,
            "updated_after": _iso(updated_after) if updated_after else None,
            "created_after": _iso(created_after) if created_after else None,
            "created_before": _iso(created_before) if created_before else None,
            "folder_id": folder_id,
        }
        cursor: str | None = None
        while True:
            page = self._get_model(
                "/v1/notes", granola.ListNotesOutput, {**params, "cursor": cursor}
            )
            yield from page.notes
            if not page.hasMore or page.cursor is None:
                return
            cursor = page.cursor

    def get_note(self, note_id: str) -> granola.Note:
        """Fetch a note with its transcript; retry 404s (still processing) a few times."""
        path = f"/v1/notes/{note_id}"
        for attempt in range(self._not_ready_retries + 1):
            response = self._get(path, {"include": "transcript"})
            if response.status_code == 200:
                note = granola.Note.model_validate_json(response.content)
                return (
                    note
                    if note.transcript is not None
                    else note.model_copy(update={"transcript": []})
                )
            if response.status_code == 413:
                note = self._get_model(path, granola.Note)
                return note.model_copy(update={"transcript": list(self._transcript_pages(note_id))})
            if response.status_code == 404:
                if attempt < self._not_ready_retries:
                    self._clock.sleep(self._backoff(attempt + 1))
                    continue
                raise NoteNotReady(note_id)
            raise GranolaAPIError(response.status_code, path)
        raise AssertionError("unreachable")  # pragma: no cover

    def _transcript_pages(self, note_id: str) -> Iterator[granola.TranscriptItem]:
        path = f"/v1/notes/{note_id}/transcript"
        cursor: str | None = None
        while True:
            page = self._get_model(
                path,
                granola.GetTranscriptOutput,
                {"page_size": MAX_TRANSCRIPT_PAGE_SIZE, "cursor": cursor},
            )
            yield from page.transcript
            if not page.hasMore or page.cursor is None:
                return
            cursor = page.cursor

    def list_folders(self) -> Iterator[granola.Folder]:
        """Yield all folders (the API returns them sorted alphabetically)."""
        cursor: str | None = None
        while True:
            page = self._get_model(
                "/v1/folders",
                granola.ListFoldersOutput,
                {"page_size": MAX_PAGE_SIZE, "cursor": cursor},
            )
            yield from page.folders
            if not page.hasMore or page.cursor is None:
                return
            cursor = page.cursor
