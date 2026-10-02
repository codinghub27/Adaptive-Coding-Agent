"""Transcript regression probes: the fixed instrument for ADAPTIVE-upgrade.

    .\\venv\\Scripts\\python.exe -m eval.transcript_probes --base-url http://127.0.0.1:8000

Drives the REAL HTTP API the web UI uses (`/chat/stream`, SSE) on a FRESH
account per teaching mode, replaying the T1..T13 transcript from a live UI
session that exposed F1..F10, plus two short sequences:

- `E*`  escalation: one problem, then repeated explicit asks for the full
        solution (and, for Challenge, a verified attempt) -- is the full
        solution reachable per mode, and is revealed code sandbox-verified?
- `C*`  continuity without `POST /conversations` first (B7).

Every check is a mechanical predicate over the `done` frame, so before/after
numbers are comparable. THE PROBE TEXTS AND CHECKS ARE FROZEN: change them
and every earlier measurement in `docs/features/ADAPTIVE-upgrade.md` stops
being comparable. Never send a `topic` form field (it bypasses inference).

`teaching_mode` is sent as a form field in every mode. Before the backend
reads it (ADAPTIVE-upgrade P4) FastAPI ignores it, which is exactly the
behaviour being measured: the UI control had no backend effect (F10).

`--langsmith` additionally checks, after the run, that every probed turn
produced exactly one `teaching_graph` root run in LangSmith.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import secrets
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import httpx

__all__ = ["main"]

MODES: Final = ("guidance", "balanced", "challenge")

# --------------------------------------------------------------------------
# Frozen probe texts
# --------------------------------------------------------------------------

T1_TEXT: Final = """Problem: Binary Tree Maximum Path Sum

A path in a binary tree is a sequence of nodes where each pair of adjacent nodes \
in the sequence has an edge connecting them. A node can only appear in the \
sequence at most once. The path does not need to pass through the root. The path \
sum of a path is the sum of the node values in the path. Given the root of a \
binary tree, return the maximum path sum of any non-empty path.

Example 1: root = [1,2,3] -> 6 (path 2 -> 1 -> 3)
Example 2: root = [-10,9,20,null,null,15,7] -> 42 (path 15 -> 20 -> 7)
Constraints: 1 <= number of nodes <= 3 * 10^4, -1000 <= Node.val <= 1000

Explain the intuition first, then how to recognize which approach to use, then \
the time and space complexity, then provide the Python solution and run tests."""

T2_TEXT: Final = "Give me the next hint."
T3_TEXT: Final = "give full answer"

T4_TEXT: Final = """Problem: Binary Tree Cameras

You are given the root of a binary tree. We install cameras on the tree nodes \
where each camera at a node can monitor its parent, itself, and its immediate \
children. Return the minimum number of cameras needed to monitor all nodes of \
the tree.

Example 1: root = [0,0,null,0,0] -> 1
Example 2: root = [0,0,null,0,null,0,null,null,0] -> 2
Constraints: 1 <= number of nodes <= 1000, Node.val == 0

Please do not immediately give code. Walk me through how to reason about it."""

T5_TEXT: Final = "give full code"

T6_TEXT: Final = """Problem: Recover a Tree From Preorder Traversal

We run a preorder depth-first search on the root of a binary tree. At each node \
we output D dashes (where D is the depth of this node), then the value of this \
node. The depth of the root is 0. If a node has only one child, that child is \
guaranteed to be the left child. Given the output string traversal of this \
traversal, recover the tree and return its root.

Example 1: traversal = "1-2--3--4-5--6--7" -> [1,2,5,3,4,6,7]
Example 2: traversal = "1-401--349---90--88" -> [1,401,null,349,88,90]"""

T7_TEXT: Final = """Problem: Word Ladder

A transformation sequence from word beginWord to word endWord using a dictionary \
wordList is a sequence of words beginWord -> s1 -> s2 -> ... -> sk such that every \
adjacent pair of words differs by a single letter, every si is in wordList, and \
sk == endWord. Given beginWord, endWord and wordList, return the number of words \
in the shortest transformation sequence, or 0 if no such sequence exists.

Example 1: beginWord = "hit", endWord = "cog", \
wordList = ["hot","dot","dog","lot","log","cog"] -> 5
Example 2: beginWord = "hit", endWord = "cog", \
wordList = ["hot","dot","dog","lot","log"] -> 0"""

T8_TEXT: Final = """Problem: Critical Connections in a Network

There are n servers numbered from 0 to n - 1 connected by undirected \
server-to-server connections forming a network, where connections[i] = [ai, bi] \
represents a connection between servers ai and bi. Any server can reach other \
servers directly or indirectly through the network. A critical connection is a \
connection that, if removed, will make some servers unable to reach some other \
server. Return all critical connections in the network in any order.

Example 1: n = 4, connections = [[0,1],[1,2],[2,0],[1,3]] -> [[1,3]]
Example 2: n = 2, connections = [[0,1]] -> [[0,1]]"""

T9_TEXT: Final = "give code for that"
T10_TEXT: Final = T8_TEXT + "\n\ngive full code"
T11_TEXT: Final = "hi"
T12_TEXT: Final = "I want to start DSA for placements within 4 months, give a proper plan"
T13_TEXT: Final = "Give me the next hint."

E0_TEXT: Final = """Problem: Longest Substring Without Repeating Characters

Given a string s, find the length of the longest substring without repeating \
characters.

Example 1: s = "abcabcbb" -> 3
Example 2: s = "bbbbb" -> 1
Example 3: s = "pwwkew" -> 3"""

E_ASK_TEXT: Final = "Please just give me the full solution code now."
E_ASK_TURNS: Final = 6

E_ATTEMPT_TEXT: Final = (
    E0_TEXT
    + """

Here is my attempt:

```python
def length_of_longest_substring(s: str) -> int:
    last = {}
    left = 0
    best = 0
    for right, ch in enumerate(s):
        if ch in last and last[ch] >= left:
            left = last[ch] + 1
        last[ch] = right
        best = max(best, right - left + 1)
    return best
```

Please give me the full solution code now."""
)

# --------------------------------------------------------------------------
# Predicates
# --------------------------------------------------------------------------

_PROBLEM_DENIAL_RE: Final = re.compile(
    r"no (specific |concrete )?problem statement|"
    r"(does not|doesn't|did not|didn't|haven't|have not) (yet )?provide[ds]? "
    r"(a |any |the )?(concrete |specific )?problem",
    re.IGNORECASE,
)
_GENERIC_RUNG_RE: Final = re.compile(r"restate the problem in your own words", re.IGNORECASE)
#: An ellipsis glued to a word -- a sentence cut mid-way. Code sections are
#: excluded before this is applied (`...` is legal Python).
_TRUNCATION_RE: Final = re.compile(r"[A-Za-z,(]\s?(\.\.\.|…)(\s|$|\))")
_CROSS_TOPIC_RE: Final = re.compile(r"for an? ([a-z][a-z _-]+?) problem", re.IGNORECASE)
_CODE_KINDS: Final = frozenset({"code", "patch", "pseudocode"})
_DEAD_END_RE: Final = re.compile(r"reached this level'?s ceiling", re.IGNORECASE)

TREE_TOPICS: Final = frozenset({"trees"})
GRAPH_FAMILY: Final = frozenset(
    {"graphs", "dfs", "bfs", "union_find", "topological_sort", "dijkstra", "bellman_ford"}
)


@dataclass(slots=True)
class Turn:
    """One probed turn: what was sent and what came back."""

    probe: str
    mode: str
    stages: list[str] = field(default_factory=list[str])
    body: dict[str, Any] = field(default_factory=dict[str, Any])
    error: str | None = None
    seconds: float = 0.0
    started_at: str = ""

    # -- accessors over the `done` frame ---------------------------------
    @property
    def generated(self) -> dict[str, Any]:
        return self.body.get("generated") or {}

    @property
    def route(self) -> str | None:
        return self.body.get("route")

    @property
    def topic(self) -> str | None:
        plan = self.body.get("plan") or {}
        return plan.get("topic")

    @property
    def text(self) -> str:
        return str(self.body.get("response") or "")

    @property
    def hint_level(self) -> int | None:
        return self.generated.get("hint_level")

    @property
    def hint_ceiling(self) -> int | None:
        return self.generated.get("hint_ceiling")

    @property
    def reveals_code(self) -> bool:
        return bool(self.generated.get("reveals_code"))

    @property
    def sections(self) -> list[dict[str, Any]]:
        return list(self.generated.get("sections") or [])

    @property
    def citations(self) -> list[str]:
        return list(self.generated.get("citations") or [])

    @property
    def verification_status(self) -> str | None:
        verdict = self.body.get("verification") or {}
        return verdict.get("status")

    def prose(self) -> str:
        """All non-code section text (falls back to the whole response)."""
        if not self.sections:
            return self.text
        return "\n".join(
            f"{s.get('title', '')}\n{s.get('body', '')}"
            for s in self.sections
            if s.get("kind") not in _CODE_KINDS
        )

    def hint_text(self) -> str:
        return "\n".join(
            str(s.get("body", "")) for s in self.sections if s.get("kind") == "next_hint"
        )


def knows_problem(turn: Turn) -> bool:
    return _PROBLEM_DENIAL_RE.search(turn.text) is None


def truncated(turn: Turn) -> bool:
    return _TRUNCATION_RE.search(turn.prose()) is not None


def cross_topic(turn: Turn) -> bool:
    """A rung/section naming a different topic's problem class ("For a heaps
    problem, trees is often the right shape")."""
    topic = (turn.topic or "").replace("_", " ")
    for match in _CROSS_TOPIC_RE.finditer(turn.prose()):
        named = match.group(1).strip().lower().replace("-", " ")
        if topic and named != topic and named.rstrip("s") != topic.rstrip("s"):
            return True
    return False


def phantom_steps(turn: Turn) -> list[str]:
    """Progress steps streamed for work that did not happen."""
    phantom: list[str] = []
    status = turn.verification_status
    if "execute_code" in turn.stages and status in (None, "skipped"):
        phantom.append("execute_code")
    if "verify" in turn.stages and status in (None, "skipped"):
        phantom.append("verify")
    return phantom


def has_section(turn: Turn, kinds: Sequence[str], words: Sequence[str]) -> bool:
    for section in turn.sections:
        if section.get("kind") in kinds:
            return True
        blob = f"{section.get('title', '')} {section.get('body', '')}".lower()
        if any(w in blob for w in words):
            return True
    return False


# --------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------


class Api:
    def __init__(self, base_url: str, timeout: float) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout)
        self._token: str | None = None

    async def close(self) -> None:
        await self._client.aclose()

    async def fresh_account(self, label: str) -> str:
        username = f"probe_{label}_{secrets.token_hex(4)}"
        password = secrets.token_urlsafe(16)
        r = await self._client.post(
            "/auth/register", json={"username": username, "password": password}
        )
        r.raise_for_status()
        r = await self._client.post(
            "/auth/login", json={"username": username, "password": password}
        )
        r.raise_for_status()
        self._token = r.json()["access_token"]
        return username

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    async def new_conversation(self) -> str:
        r = await self._client.post("/conversations", json={"title": "t"}, headers=self._headers())
        r.raise_for_status()
        return str(r.json()["conversation_id"])

    async def profile(self) -> dict[str, Any]:
        r = await self._client.get("/profile", headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def turn(self, probe: str, mode: str, text: str, conversation_id: str | None) -> Turn:
        turn = Turn(probe=probe, mode=mode, started_at=datetime.now(UTC).isoformat())
        data: dict[str, str] = {"text": text, "teaching_mode": mode}
        if conversation_id is not None:
            data["conversation_id"] = conversation_id
        start = time.monotonic()
        try:
            async with self._client.stream(
                "POST", "/chat/stream", data=data, headers=self._headers()
            ) as response:
                response.raise_for_status()
                event: str | None = None
                async for line in response.aiter_lines():
                    if line.startswith("event: "):
                        event = line[7:].strip()
                    elif line.startswith("data: "):
                        payload = line[6:]
                        if event == "stage":
                            turn.stages.append(json.loads(payload)["node"])
                        elif event == "done":
                            turn.body = json.loads(payload)
                        elif event == "error":
                            turn.error = json.loads(payload).get("detail", "error")
        except httpx.HTTPError as exc:
            turn.error = type(exc).__name__
        turn.seconds = round(time.monotonic() - start, 1)
        return turn


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------


@dataclass(slots=True)
class Check:
    probe: str
    mode: str
    name: str
    passed: bool
    detail: str = ""


Checker = Callable[[dict[str, Turn], str], list[Check]]


def _c(probe: str, mode: str, name: str, ok: bool, detail: object = "") -> Check:
    return Check(probe=probe, mode=mode, name=name, passed=bool(ok), detail=str(detail))


def _ok(turn: Turn) -> bool:
    return turn.error is None and bool(turn.body)


def _escalation_not_dead_end(prev: Turn, turn: Turn, mode: str) -> tuple[bool, str]:
    """An explicit ask must move the learner on, never strand them (F4)."""
    if mode == "challenge":
        return (not turn.reveals_code, f"reveals_code={turn.reveals_code}")
    moved = (
        turn.reveals_code
        or (turn.hint_level or 0) > (prev.hint_level or 0)
        or bool(turn.generated.get("more_help_available"))
    )
    dead = _DEAD_END_RE.search(turn.text) is not None and not turn.reveals_code
    return (
        moved and not dead,
        f"reveals={turn.reveals_code} hl={prev.hint_level}->{turn.hint_level}",
    )


def check_transcript(t: dict[str, Turn], mode: str) -> list[Check]:
    out: list[Check] = []
    for probe, turn in t.items():
        out.append(_c(probe, mode, "completed", _ok(turn), turn.error or ""))
        if not _ok(turn):
            continue
        out.append(
            _c(probe, mode, "no_phantom_steps", not phantom_steps(turn), phantom_steps(turn))
        )
        out.append(_c(probe, mode, "no_truncated_text", not truncated(turn)))
        out.append(_c(probe, mode, "no_cross_topic", not cross_topic(turn)))
        if turn.reveals_code:
            out.append(
                _c(
                    probe,
                    mode,
                    "revealed_code_verified",
                    turn.verification_status == "pass",
                    turn.verification_status,
                )
            )
    if not all(_ok(x) for x in t.values()):
        return out

    t1, t2, t3, t4, t5 = t["T1"], t["T2"], t["T3"], t["T4"], t["T5"]
    t6, t7, t8, t9, t10 = t["T6"], t["T7"], t["T8"], t["T9"], t["T10"]
    t11, t12, t13 = t["T11"], t["T12"], t["T13"]

    out.append(_c("T1", mode, "topic_trees", t1.topic in TREE_TOPICS, t1.topic))
    out.append(_c("T1", mode, "route_dsa", t1.route == "dsa", t1.route))
    out.append(_c("T1", mode, "hint_specific", not _GENERIC_RUNG_RE.search(t1.text)))
    out.append(_c("T1", mode, "section_intuition", has_section(t1, ["key_insight"], ["intuition"])))
    out.append(_c("T1", mode, "section_recognition", has_section(t1, [], ["recogni"])))
    out.append(
        _c("T1", mode, "section_complexity", has_section(t1, ["complexity"], ["complexity"]))
    )

    out.append(_c("T2", mode, "topic_trees", t2.topic in TREE_TOPICS, t2.topic))
    out.append(_c("T2", mode, "knows_problem", knows_problem(t2)))
    out.append(
        _c(
            "T2",
            mode,
            "hint_advanced",
            t1.hint_level is not None and t2.hint_level == t1.hint_level + 1,
            f"{t1.hint_level}->{t2.hint_level}",
        )
    )
    out.append(
        _c(
            "T2",
            mode,
            "same_N",
            t2.hint_ceiling == t1.hint_ceiling,
            f"{t1.hint_ceiling}->{t2.hint_ceiling}",
        )
    )

    out.append(_c("T3", mode, "topic_trees", t3.topic in TREE_TOPICS, t3.topic))
    out.append(_c("T3", mode, "no_heaps_drift", "heap" not in t3.prose().lower()))
    out.append(_c("T3", mode, "knows_problem", knows_problem(t3)))
    ok, detail = _escalation_not_dead_end(t2, t3, mode)
    out.append(_c("T3", mode, "escalation_per_mode", ok, detail))

    out.append(_c("T4", mode, "topic_trees", t4.topic in TREE_TOPICS, t4.topic))
    out.append(_c("T4", mode, "no_code", not t4.reveals_code))
    out.append(_c("T4", mode, "states_reasoning", len(t4.sections) >= 2, len(t4.sections)))

    out.append(_c("T5", mode, "topic_trees", t5.topic in TREE_TOPICS, t5.topic))
    out.append(_c("T5", mode, "knows_problem", knows_problem(t5)))
    ok, detail = _escalation_not_dead_end(t4, t5, mode)
    out.append(_c("T5", mode, "escalation_per_mode", ok, detail))

    out.append(_c("T6", mode, "topic_trees_or_stack", t6.topic in {"trees", "stack"}, t6.topic))
    out.append(_c("T6", mode, "sections_present", len(t6.sections) >= 2, len(t6.sections)))

    out.append(_c("T7", mode, "topic_bfs", t7.topic == "bfs", t7.topic))
    out.append(_c("T7", mode, "recognition_explained", has_section(t7, [], ["recogni", "signal"])))

    out.append(_c("T8", mode, "topic_graph_family", t8.topic in GRAPH_FAMILY, t8.topic))

    out.append(
        _c("T9", mode, "same_topic", t9.topic == t8.topic and t9.topic is not None, t9.topic)
    )
    out.append(_c("T9", mode, "knows_problem", knows_problem(t9)))

    out.append(
        _c("T10", mode, "same_topic", t10.topic == t8.topic and t10.topic is not None, t10.topic)
    )
    out.append(
        _c(
            "T10",
            mode,
            "ladder_resumed",
            (t10.hint_level or 0) >= (t9.hint_level or 0)
            and t10.hint_level != 1
            or t10.reveals_code,
            f"{t9.hint_level}->{t10.hint_level}",
        )
    )
    out.append(
        _c(
            "T10",
            mode,
            "same_N",
            t10.hint_ceiling == t8.hint_ceiling or t10.reveals_code,
            f"{t8.hint_ceiling}->{t10.hint_ceiling}",
        )
    )

    out.append(_c("T11", mode, "route_clarify", t11.route == "clarify", t11.route))

    out.append(
        _c(
            "T12",
            mode,
            "not_ladder",
            t12.route != "dsa" and t12.hint_level is None,
            f"{t12.route} hl={t12.hint_level}",
        )
    )
    out.append(_c("T12", mode, "grounded", bool(t12.citations), t12.citations[:3]))

    out.append(_c("T13", mode, "no_generic_rung", not _GENERIC_RUNG_RE.search(t13.text)))
    return out


def check_escalation(e: dict[str, Turn], mode: str) -> list[Check]:
    out: list[Check] = []
    asks = [e[f"E{i}"] for i in range(1, E_ASK_TURNS + 1) if f"E{i}" in e]
    reveal_at = next((i + 1 for i, x in enumerate(asks) if x.reveals_code), None)
    for name, turn in e.items():
        if turn.reveals_code:
            out.append(
                _c(
                    name,
                    mode,
                    "revealed_code_verified",
                    turn.verification_status == "pass",
                    turn.verification_status,
                )
            )
        out.append(_c(name, mode, "no_phantom_steps", not phantom_steps(turn), phantom_steps(turn)))
    if mode == "guidance":
        out.append(_c("E", mode, "full_solution_reachable", reveal_at is not None, reveal_at))
    elif mode == "balanced":
        out.append(
            _c(
                "E",
                mode,
                "full_solution_reachable",
                reveal_at is not None and reveal_at >= 2,
                reveal_at,
            )
        )
    else:
        out.append(_c("E", mode, "no_reveal_without_attempt", reveal_at is None, reveal_at))
        after = [e[k] for k in ("E7", "E8") if k in e]
        out.append(
            _c(
                "E",
                mode,
                "reveal_after_verified_attempt",
                any(x.reveals_code for x in after),
                [x.reveals_code for x in after],
            )
        )
    return out


def check_continuity(c: dict[str, Turn], mode: str) -> list[Check]:
    c1, c2 = c["C1"], c["C2"]
    return [
        _c(
            "C1",
            mode,
            "conversation_created",
            bool(c1.body.get("conversation_id")),
            c1.body.get("conversation_id"),
        ),
        _c("C2", mode, "topic_trees", c2.topic in TREE_TOPICS, c2.topic),
        _c(
            "C2",
            mode,
            "hint_advanced",
            c1.hint_level is not None and c2.hint_level == c1.hint_level + 1,
            f"{c1.hint_level}->{c2.hint_level}",
        ),
    ]


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------

TRANSCRIPT: Final[tuple[tuple[str, str], ...]] = (
    ("T1", T1_TEXT),
    ("T2", T2_TEXT),
    ("T3", T3_TEXT),
    ("T4", T4_TEXT),
    ("T5", T5_TEXT),
    ("T6", T6_TEXT),
    ("T7", T7_TEXT),
    ("T8", T8_TEXT),
    ("T9", T9_TEXT),
    ("T10", T10_TEXT),
    ("T11", T11_TEXT),
    ("T12", T12_TEXT),
    ("T13", T13_TEXT),
)


async def _run_sequence(
    api: Api, mode: str, steps: Sequence[tuple[str, str]], *, create_conversation: bool
) -> dict[str, Turn]:
    conversation_id = await api.new_conversation() if create_conversation else None
    turns: dict[str, Turn] = {}
    for probe, text in steps:
        turn = await api.turn(probe, mode, text, conversation_id)
        turns[probe] = turn
        returned = turn.body.get("conversation_id")
        if conversation_id is None and returned:
            conversation_id = str(returned)
        print(
            f"  {mode:9s} {probe:4s} {turn.seconds:5.1f}s route={turn.route} topic={turn.topic} "
            f"hint={turn.hint_level}/{turn.hint_ceiling} code={turn.reveals_code} "
            f"verdict={turn.verification_status} err={turn.error}",
            flush=True,
        )
    return turns


async def run(args: argparse.Namespace) -> int:
    api = Api(args.base_url, timeout=args.timeout)
    checks: list[Check] = []
    record: dict[str, Any] = {"started_at": datetime.now(UTC).isoformat(), "modes": {}}
    all_turns: list[Turn] = []
    try:
        for mode in args.modes:
            mode_record: dict[str, Any] = {}
            username = await api.fresh_account(mode[:3])
            mode_record["username"] = username
            if "T" in args.parts:
                t = await _run_sequence(api, mode, TRANSCRIPT, create_conversation=True)
                checks += check_transcript(t, mode)
                mode_record["T"] = {k: _dump(v) for k, v in t.items()}
                all_turns += t.values()
            if "E" in args.parts:
                steps = [("E0", E0_TEXT)] + [
                    (f"E{i}", E_ASK_TEXT) for i in range(1, E_ASK_TURNS + 1)
                ]
                if mode == "challenge":
                    steps += [("E7", E_ATTEMPT_TEXT), ("E8", E_ASK_TEXT)]
                e = await _run_sequence(api, mode, steps, create_conversation=True)
                checks += check_escalation(e, mode)
                mode_record["E"] = {k: _dump(v) for k, v in e.items()}
                all_turns += e.values()
            if "C" in args.parts and mode == args.modes[0]:
                c = await _run_sequence(
                    api, mode, [("C1", T1_TEXT), ("C2", T2_TEXT)], create_conversation=False
                )
                checks += check_continuity(c, mode)
                mode_record["C"] = {k: _dump(v) for k, v in c.items()}
                all_turns += c.values()
            mode_record["profile"] = await api.profile()
            record["modes"][mode] = mode_record
    finally:
        await api.close()

    _write_record(Path(args.out), record)  # before the network check can fail
    if args.langsmith:
        try:
            checks += check_langsmith(all_turns, record["started_at"])
        except Exception as exc:  # noqa: BLE001 - report, never lose the run
            checks.append(_c("LS", "-", "langsmith_check_ran", False, type(exc).__name__))

    record["checks"] = [_check_dict(c) for c in checks]
    passed = sum(c.passed for c in checks)
    print()
    for c in checks:
        if not c.passed:
            print(f"  FAIL {c.mode:9s} {c.probe:4s} {c.name:30s} {c.detail}")
    by_name: dict[str, list[bool]] = {}
    for c in checks:
        by_name.setdefault(c.name, []).append(c.passed)
    print()
    for name, results in sorted(by_name.items()):
        print(f"  {name:32s} {sum(results):3d}/{len(results):<3d}")
    print(f"\nTOTAL {passed}/{len(checks)} = {passed / max(len(checks), 1):.1%}")

    _write_record(Path(args.out), record)
    return 0


def _write_record(out: Path, record: dict[str, Any]) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=1, default=str), encoding="utf-8")
    print(f"wrote {out}")


def _check_dict(c: Check) -> dict[str, Any]:
    return {
        "probe": c.probe,
        "mode": c.mode,
        "name": c.name,
        "passed": c.passed,
        "detail": c.detail,
    }


def _dump(turn: Turn) -> dict[str, Any]:
    return {
        "stages": turn.stages,
        "error": turn.error,
        "seconds": turn.seconds,
        "started_at": turn.started_at,
        "route": turn.route,
        "intent": (turn.body.get("intent") or {}).get("intent"),
        "topic": turn.topic,
        "plan": turn.body.get("plan"),
        "hint_level": turn.hint_level,
        "hint_ceiling": turn.hint_ceiling,
        "reveals_code": turn.reveals_code,
        "verification": turn.verification_status,
        "citations": turn.citations,
        "sections": [
            {"kind": s.get("kind"), "title": s.get("title"), "body": str(s.get("body", ""))[:600]}
            for s in turn.sections
        ],
        "events": turn.body.get("events"),
        "skill_deltas": turn.body.get("skill_deltas"),
        "llm_calls": turn.body.get("llm_calls"),
        "errors": turn.body.get("errors"),
        "conversation_id": turn.body.get("conversation_id"),
    }


def _usage(run: Any) -> dict[str, Any]:
    meta: dict[str, Any] = (run.extra or {}).get("metadata", {})
    return meta if "total_tokens" in meta and "cost_usd" in meta else {}


def check_langsmith(turns: Sequence[Turn], started_at: str, wait_s: float = 30.0) -> list[Check]:
    """Exactly one `teaching_graph` root per probed turn, each with nested runs."""
    from langsmith import Client  # local: only this optional check needs it

    from app.config import get_settings

    settings = get_settings()
    if settings.langsmith_api_key is None:
        return [_c("LS", "-", "langsmith_configured", False, "no api key")]
    client = Client(
        api_key=settings.langsmith_api_key.get_secret_value(), api_url=settings.langsmith_endpoint
    )
    since = datetime.fromisoformat(started_at)
    expected = sum(1 for t in turns if _ok(t))
    deadline = time.monotonic() + wait_s
    roots: list[Any] = []
    while True:
        roots = [
            r
            for r in client.list_runs(
                project_name=settings.langsmith_project, is_root=True, start_time=since
            )
            if r.name == "teaching_graph"
        ]
        if len(roots) >= expected or time.monotonic() > deadline:
            break
        time.sleep(3)
    stray = [
        r
        for r in client.list_runs(
            project_name=settings.langsmith_project, is_root=True, start_time=since
        )
        if r.name != "teaching_graph"
    ]
    out = [
        _c("LS", "-", "one_root_per_turn", len(roots) == expected, f"{len(roots)}/{expected}"),
        _c("LS", "-", "no_parentless_runs", not stray, f"{len(stray)} stray roots"),
    ]
    # Metadata lands with each run's final patch; wait for it on the listing
    # (one call per poll) instead of reading runs one by one, which trips
    # LangSmith's rate limit on a 60-turn run.
    while time.monotonic() < deadline + wait_s and not all(_usage(r) for r in roots):
        time.sleep(5)
        listed = {
            r.id: r
            for r in client.list_runs(
                project_name=settings.langsmith_project, is_root=True, start_time=since
            )
        }
        roots = [listed.get(r.id, r) for r in roots]
    for r in roots:
        meta = _usage(r)
        out.append(
            _c(
                "LS",
                "-",
                "usage_metadata",
                bool(meta),
                {k: meta.get(k) for k in ("transport", "total_tokens", "cost_usd")},
            )
        )
    # Full turns run ~25 nodes; `clarify` legitimately runs ~12, so the
    # nesting bar applies to routed (non-clarify) turns only. Sampled (5).
    routed = [r for r in roots if (r.outputs or {}).get("route") not in (None, "clarify")]
    for r in routed[:5]:
        try:
            nested = list(
                client.list_runs(project_name=settings.langsmith_project, trace_id=r.trace_id)
            )
        except Exception as exc:  # noqa: BLE001 - a rate limit must not lose the run
            out.append(_c("LS", "-", "nested_runs_ge_25", False, type(exc).__name__))
            continue
        out.append(
            _c(
                "LS",
                "-",
                "nested_runs_ge_25",
                len(nested) - 1 >= 25,
                f"{r.id} nested={len(nested) - 1}",
            )
        )
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Transcript regression probes")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--modes", nargs="+", default=list(MODES), choices=MODES)
    parser.add_argument("--parts", default="TEC", help="any of T (transcript), E, C")
    parser.add_argument("--timeout", type=float, default=240.0)
    parser.add_argument("--langsmith", action="store_true")
    parser.add_argument("--out", default="eval/results/transcript_probes.json")
    args = parser.parse_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
