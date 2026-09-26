"""Tests for `app.graph.stages`: `STAGE_LABELS` key parity with `NODE_FUNCTIONS`.

Mirrors `tests/graph/test_build.py::test_node_functions_and_fallbacks_declare_the_same_names`.
"""

from app.graph.build import NODE_FUNCTIONS
from app.graph.stages import STAGE_LABELS


def test_stage_labels_cover_every_node_function() -> None:
    assert set(STAGE_LABELS) == set(NODE_FUNCTIONS)


def test_every_stage_label_is_a_non_empty_fixed_string() -> None:
    for label in STAGE_LABELS.values():
        assert isinstance(label, str)
        assert label != ""
