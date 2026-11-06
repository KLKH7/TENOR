"""Concentrate's memory as an MCP server (stdio), to sit next to Granola's official MCP.

Six read-only **tools** (things the model decides to call, with arguments) and one **resource**
(``concentrate://memory``: read-only data the client can show or attach, here what is being
served). The tool shapes echo Granola's MCP where they overlap (``get_meeting`` ~
``get_meetings``, ``get_transcript_lines`` ~ ``get_meeting_transcript(chunk_ids=...)``) and add
what's specific to Concentrate: dated fact history, commitment status, entity resolution and
line-level citations.

Over stdio, stdout *is* the protocol channel: nothing else may print to it. Logs go to stderr.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from tenor.mcp_server.service import STATUSES, UNFINISHED, MemoryQueryError, MemoryService

INSTRUCTIONS = """\
Concentrate is a precomputed memory over the user's meetings: verified facts with the dates they \
held (valid time) and the meeting where they were learnt (transaction time), commitments with \
their follow-ups, and resolved people/orgs/projects. Every item cites transcript line IDs \
(not_xxx:0042).
Start with ask_memory for open questions. Use get_facts for "what is/was X's Y" and how it \
changed, get_commitments for who promised what and whether it happened, resolve_entity when a \
name could mean several people (there are several Sams), and get_transcript_lines to check a \
citation before quoting it. Pass as_of (YYYY-MM-DD) for "as of" questions; the default is the \
day of the last meeting in the memory. Everything is read-only."""

READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)
# ask_memory can call an answer model (Anthropic API) when answer=true.
READ_ONLY_MAY_CALL_LLM = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True
)

AsOf = Annotated[
    str | None,
    Field(description="ISO date (YYYY-MM-DD) to answer as of. Default: last meeting day."),
]


def _guard(fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Turn an anticipated query error into an MCP tool error the model can read and fix."""
    try:
        return fn()
    except MemoryQueryError as exc:
        raise ToolError(str(exc)) from exc


def build_server(svc: MemoryService) -> MCPServer:
    """An MCP server exposing ``svc``. Run it with ``.run("stdio")``."""
    mcp: MCPServer = MCPServer(
        name="concentrate",
        title="Concentrate meeting memory",
        instructions=INSTRUCTIONS,
        version="0.1.0",
    )

    @mcp.tool(annotations=READ_ONLY_MAY_CALL_LLM)
    def ask_memory(
        question: Annotated[str, Field(description="A question about the meetings.")],
        as_of: AsOf = None,
        answer: Annotated[
            bool,
            Field(
                description="Also have Concentrate's answer model write a cited answer "
                "(costs one LLM call). Default false: you answer from the context."
            ),
        ] = False,
    ) -> dict[str, Any]:
        """The memory slice Concentrate answers from: facts about the entities the question
        names (with validity dates and status), related commitments, the closest facts and
        meeting digests, and the transcript lines they cite. Cite the [line IDs]."""
        return _guard(lambda: svc.ask(question, as_of, answer))

    @mcp.tool(annotations=READ_ONLY)
    def get_facts(
        entity: Annotated[str, Field(description="Name or id, e.g. 'Beacon Freight', 'sam_p'.")],
        predicate: Annotated[
            str | None,
            Field(description="Optional attribute filter by words, e.g. 'price', 'owner'."),
        ] = None,
        as_of: AsOf = None,
    ) -> dict[str, Any]:
        """Every version of an entity's facts known on as_of: what the value was, from when to
        when it held, which meeting changed it and why, and the lines that say so."""
        return _guard(lambda: svc.facts(entity, predicate, as_of))

    @mcp.tool(annotations=READ_ONLY)
    def get_commitments(
        person: Annotated[
            str | None, Field(description="Owner's name or id; omit for everyone.")
        ] = None,
        status: Annotated[
            str | None,
            Field(description=f"One of {', '.join((*STATUSES, UNFINISHED))}; omit for all."),
        ] = None,
        as_of: AsOf = None,
    ) -> dict[str, Any]:
        """Who promised what, by when, and what happened next (done, slipped, reassigned,
        dropped, or no follow-up), as of a date, newest first."""
        return _guard(lambda: svc.commitments(person, status, as_of))

    @mcp.tool(annotations=READ_ONLY)
    def resolve_entity(
        name: Annotated[str, Field(description="A name as people say it, e.g. 'Sam'.")],
    ) -> dict[str, Any]:
        """Which person/org/project a name refers to. Lists every candidate with role, email,
        meetings attended and what the memory knows, so you can tell them apart."""
        return _guard(lambda: svc.resolve(name))

    @mcp.tool(annotations=READ_ONLY)
    def get_meeting(
        meeting_id: Annotated[str, Field(description="Note id (not_...) or its Granola URL.")],
    ) -> dict[str, Any]:
        """One meeting: title, date, attendees, Granola's AI summary, Concentrate's digest, and
        the facts and commitments the memory took from it."""
        return _guard(lambda: svc.meeting(meeting_id))

    @mcp.tool(annotations=READ_ONLY)
    def get_transcript_lines(
        ids: Annotated[list[str], Field(description="Line IDs like 'not_AbC123:0042'.")],
        context: Annotated[
            int, Field(description="Neighbouring lines to include on each side (0-10).")
        ] = 0,
    ) -> dict[str, Any]:
        """The exact transcript lines behind a citation: text, capture channel (me/them),
        Granola's speaker label, timestamp, and the speaker Concentrate attributed."""
        return _guard(lambda: svc.lines(ids, context))

    @mcp.resource(
        "concentrate://memory",
        name="memory",
        title="What this server is serving",
        description="Store path, build status and config, counts, and the default as-of date.",
        mime_type="application/json",
    )
    def memory_info() -> str:
        return json.dumps(svc.summary(), indent=1)

    return mcp
