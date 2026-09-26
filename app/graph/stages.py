"""Fixed, learner-facing labels for graph node names, used by SSE stage events.

**Security requirement, and the reason this table exists:** a stage label is
only ever looked up from this fixed table by node name. No part of a graph
update, user input, code, or model output may appear in a stage event --
`app.graph.build.stream_graph` must only ever emit `label`s that come from
this mapping (or `DEFAULT_STAGE_LABEL`), never anything derived from state.

`STAGE_LABELS` declares a label for every key of `app.graph.nodes.
NODE_FUNCTIONS` (see `tests/graph/test_stages.py` for the enforced key-parity
check, mirroring `app.graph.nodes.FALLBACKS`'s own parity with
`NODE_FUNCTIONS`), so a future node cannot ship without a label.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

__all__ = ["DEFAULT_STAGE_LABEL", "STAGE_LABELS"]

STAGE_LABELS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "understand_input": "Reading your input",
        "classify_intent": "Working out what you need",
        "load_learner_profile": "Recalling how you learn",
        "plan_teaching": "Planning how to help",
        "retrieve_knowledge": "Looking up references",
        "route": "Choosing an approach",
        "dsa_agent": "Working through the problem",
        "debug_agent": "Debugging your code",
        "explain_agent": "Reading your code",
        "clarify": "Asking for a bit more",
        "execute_code": "Running your code in the sandbox",
        "verify": "Checking the results",
        "final_response": "Writing your answer",
        "update_learner_model": "Updating what I know about you",
    }
)

DEFAULT_STAGE_LABEL: Final = "Working"
