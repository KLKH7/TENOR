"""Task phrases in first-person commitment and status lines (enhance-v2's cross-meeting cue).

People take on a task in one meeting ("I'll take the skills matrix, done by Thursday") and report
on it in another ("The skills matrix is done", "The skills matrix slipped, I'll get it done next
week"). The person who reports on a task is usually the person who took it. So when a line we
can't attribute (an in-person block, an unnamed system-audio passage) talks about a task in this
way, the people who talked about the same task on lines we *can* attribute (a platform-named
passage, the owner's microphone in a remote call) are the likely speakers.

Only hard-identified lines feed the index, never Enhance's own guesses, so the cue can't
reinforce its own mistakes. The phrase grammar is deliberately narrow: a first-person take/own
verb, or a definite noun phrase followed by a status verb.
"""

from __future__ import annotations

import re
from collections import Counter

from tenor.enhance.strings import fold

_NP = r"the\s+((?:[\w'\u2019-]+\s+){0,4}?[\w'\u2019-]+)"
_TAKE = re.compile(
    r"\bI(?:'ll|\u2019ll| will| can)\s+(?:take|own|handle|finish|grab|pick up|write up|send)\s+"
    + _NP
    + r"(?=\s*(?:[,.;!?]|$|\s(?:done|by|this|next|before|tomorrow|today)\b))",
    re.IGNORECASE,
)
_STATUS = re.compile(
    _NP + r"\s+(?:is|was)\s+(?:finally\s+)?(?:done|finished|ready|shipped|out|blocked|late)\b"
    r"|" + _NP + r"\s+slipped\b",
    re.IGNORECASE,
)
_FILLERS = {"um", "uh", "like", "so", "you", "know"}
_STOP_HEADS = {"thing", "part", "issue", "problem", "plan", "doc", "call", "meeting", "idea"}


def _norm(np: str) -> str | None:
    words = [w for w in re.findall(r"[\w'\u2019-]+", fold(np)) if w not in _FILLERS]
    deduped = [w for k, w in enumerate(words) if k == 0 or w != words[k - 1]]
    if not deduped or (len(deduped) == 1 and deduped[0] in _STOP_HEADS):
        return None  # "the thing is done" names no task
    return " ".join(deduped)


def task_phrases(text: str) -> list[str]:
    """Normalised task phrases this line takes on or reports on (may be empty)."""
    out: list[str] = []
    for m in _TAKE.finditer(text):
        key = _norm(m.group(1))
        if key and key not in out:
            out.append(key)
    for m in _STATUS.finditer(text):
        key = _norm(m.group(1) or m.group(2))
        if key and key not in out:
            out.append(key)
    return out


TaskIndex = dict[str, Counter[str]]  # task phrase -> hard-identified speakers who said it


def add_line(index: TaskIndex, text: str, speaker: str) -> None:
    """Record that ``speaker`` (hard-identified) took on or reported on the tasks in ``text``."""
    for key in task_phrases(text):
        index.setdefault(key, Counter())[speaker] += 1
