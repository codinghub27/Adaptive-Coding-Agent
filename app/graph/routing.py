"""Intent-to-agent and verification routing for the teaching graph.

Route keys (`RouteKey`: "dsa" | "debug" | "explain" | "clarify") and the node
names they map to (`ROUTE_NODES`) are a **STABLE contract** for Phase 07: the
stub node *implementations* registered under these names today will be
replaced by full subgraphs later, but the keys and names themselves must not
change without a coordinated migration.

`VerifyKey`/`verify_after`/`VERIFY_NODES` are the equivalent contract for the
post-execution branch: in Phase 06 every verdict status routes straight to
`final_response` (nothing yet knows how to act on a "fail"); Phase 07 will
retarget "fail" to the debugger loop instead.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, Literal

from app.graph.state import AgentState, RouteKey
from app.schemas.intent import Intent

__all__ = [
    "INTENT_ROUTES",
    "ROUTE_NODES",
    "VERIFY_NODES",
    "VerifyKey",
    "route_after",
    "select_route",
    "verify_after",
]

INTENT_ROUTES: Final[Mapping[Intent, RouteKey]] = MappingProxyType(
    {
        Intent.DSA_SOLVE: "dsa",
        Intent.DSA_HINT: "dsa",
        Intent.APPROACH_DISCUSSION: "dsa",
        Intent.CODE_DEBUG: "debug",
        Intent.ERROR_EXPLANATION: "debug",
        Intent.TEST_CASE_ANALYSIS: "debug",
        Intent.CODE_EXPLAIN: "explain",
        Intent.CONCEPT_EXPLANATION: "explain",
        Intent.IMAGE_CODE_ANALYSIS: "explain",
        Intent.CODE_REVIEW: "explain",
        Intent.OPTIMIZATION: "explain",
    }
)

ROUTE_NODES: Final[Mapping[RouteKey, str]] = MappingProxyType(
    {
        "dsa": "dsa_agent",
        "debug": "debug_agent",
        "explain": "explain_agent",
        "clarify": "clarify",
    }
)


def select_route(state: AgentState) -> RouteKey:
    """Decide which subgraph should handle this turn.

    Any failing check below routes to "clarify" rather than guessing:
    1. no (or empty) structured input,
    2. no intent, or a low-confidence one,
    3. the teaching plan itself already decided to clarify,
    4. otherwise, the fixed intent -> route mapping.
    """
    if state.structured_input is None or state.structured_input.is_empty:
        return "clarify"
    if state.intent is None or state.intent.low_confidence:
        return "clarify"
    if state.plan is not None and state.plan.solution_strategy == "clarify":
        return "clarify"
    return INTENT_ROUTES[state.intent.intent]


def route_after(state: AgentState) -> RouteKey:
    """LangGraph conditional-edge function: read the recorded route decision."""
    return state.route if state.route is not None else "clarify"


VerifyKey = Literal["pass", "fail", "inconclusive", "skipped"]

# Phase 06: every verdict status routes straight to "final_response" --
# nothing yet knows how to act on a "fail" verdict. Phase 07 retargets "fail"
# to the debugger loop instead of leaving this all-same-target mapping.
VERIFY_NODES: Final[Mapping[VerifyKey, str]] = MappingProxyType(
    {
        "pass": "final_response",
        "fail": "final_response",
        "inconclusive": "final_response",
        "skipped": "final_response",
    }
)


def verify_after(state: AgentState) -> VerifyKey:
    """LangGraph conditional-edge function: read this turn's verdict status.

    No verdict (nothing was executed/verified this turn) is "skipped", not a
    missing-data error -- the default, unverified path every non-code turn
    takes.
    """
    verification = state.verification
    if verification is None:
        return "skipped"
    return verification.status
