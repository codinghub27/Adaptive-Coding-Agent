"""Run the evaluation suite and print per-metric scores.

    .\\venv\\Scripts\\python.exe -m eval.run

Phase 08 deferred this, so until now every claim about the agent being
adaptive rested on hand-driven checks. This closes that: one command, five
metrics, printed as numbers that can be compared across commits.

Evaluation is LOCAL by design. LangSmith is already wired for tracing (one
`teaching_graph` run per turn, learner text redacted), which answers "what did
this turn do"; a harness answers "did the build get better or worse", needs to
run in CI without network to a third party, and needs to be cheap to re-run.
The two are complements, not substitutes.

Each case runs through the REAL graph: real intent classifier, real retriever
over live Qdrant, real LLM, real Docker sandbox. `session=None`, so nothing is
persisted and no learner profile is read -- every case is evaluated as a fresh
account, which is the hardest case for topic inference and keeps runs
independent of each other and of whatever is in the database.

Metrics
-------
routing          the turn reached the right agent
topic            `plan.topic` matches the label, including `None` where a topic
                 would have to be invented
hint_safety      a turn that asked for a hint did not reveal code (this is a
                 safety property, so it is reported separately and a failure
                 here matters more than a miss anywhere else)
debug_fix        sandbox-verified: the verdict matches what the labelled code
                 deserves, for the cases that submit code
groundedness     the answer cites retrieved knowledge chunks
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

from qdrant_client import AsyncQdrantClient

from app.config import Settings, get_settings
from app.execution.base import CodeRunner
from app.execution.runner import build_sandbox_runner
from app.graph.build import run_graph
from app.graph.state import RawInput
from app.knowledge.base import Retriever
from app.knowledge.retrieve import create_retriever
from app.llm.base import LLMClient
from app.llm.client import Tracer, get_llm_client
from eval.dataset import DATASET, Case

__all__ = ["main", "run_suite"]


@dataclass(slots=True)
class Metric:
    """One metric's tally, and the cases that missed."""

    name: str
    passed: int = 0
    total: int = 0
    misses: list[str] = field(default_factory=list[str])

    def record(self, case_id: str, ok: bool, detail: str = "") -> None:
        self.total += 1
        if ok:
            self.passed += 1
        else:
            self.misses.append(f"{case_id}{f' ({detail})' if detail else ''}")

    @property
    def score(self) -> float:
        return self.passed / self.total if self.total else 1.0

    def line(self) -> str:
        return f"{self.name:<14} {self.score:6.1%}  ({self.passed}/{self.total})"


@dataclass(slots=True)
class Outcome:
    """What one case actually did."""

    case_id: str
    route: str | None
    topic: str | None
    reveals_code: bool
    verdict: str | None
    solved: bool | None
    citations: int
    error: str | None = None


async def _run_case(
    case: Case, *, llm: LLMClient, retriever: Retriever | None, runner: CodeRunner | None
) -> Outcome:
    try:
        result = await run_graph(
            RawInput(text=case.text),
            llm=llm,
            retriever=retriever,
            runner=runner,
        )
    except Exception as exc:  # noqa: BLE001 - one bad case must not end the run
        return Outcome(case.id, None, None, False, None, None, 0, error=type(exc).__name__)

    state = result.state
    generated = state.generated_response
    return Outcome(
        case_id=case.id,
        route=state.route,
        topic=state.plan.topic if state.plan is not None else None,
        reveals_code=bool(generated is not None and generated.reveals_code),
        verdict=state.verification.status if state.verification is not None else None,
        solved=state.events[0].solved if state.events else None,
        citations=len(generated.citations) if generated is not None else 0,
    )


def _score(cases: Sequence[Case], outcomes: Sequence[Outcome]) -> list[Metric]:
    routing = Metric("routing")
    topic = Metric("topic")
    hint_safety = Metric("hint_safety")
    debug_fix = Metric("debug_fix")
    groundedness = Metric("groundedness")

    for case, got in zip(cases, outcomes, strict=True):
        if got.error is not None:
            for metric in (routing, topic, groundedness):
                metric.record(case.id, False, f"errored: {got.error}")
            continue

        routing.record(case.id, got.route == case.expected_route, f"got {got.route}")
        topic.record(case.id, got.topic == case.expected_topic, f"got {got.topic}")

        if case.hint_requested:
            hint_safety.record(case.id, not got.reveals_code, "revealed code")

        if case.expect_verified_pass is not None:
            # The learner-evidence is the EVENT's `solved`, not the turn's
            # `verification.status`. On a debug turn the outer verdict is the
            # verdict on the *patched* code, so it reads "pass" precisely when
            # the agent fixed the learner's bug -- which is the opposite of
            # what this metric is asking about (see `DebugResult.to_outcome`,
            # which derives `solved` from `initial_verdict` for the same
            # reason).
            debug_fix.record(
                case.id, got.solved is case.expect_verified_pass, f"solved={got.solved}"
            )

        # A contentless turn has nothing to ground against, so it is excluded
        # rather than counted as a groundedness failure.
        # Practice turns render corpus text directly without going through
        # response generation, so they carry no citations; a contentless turn
        # has nothing to ground against. Both are excluded rather than
        # counted as groundedness failures.
        if case.kind not in ("contentless", "practice"):
            groundedness.record(case.id, got.citations > 0, "no citations")

    return [routing, topic, hint_safety, debug_fix, groundedness]


async def run_suite(cases: Sequence[Case] = DATASET) -> list[Metric]:
    """Run `cases` through the real graph and return the scored metrics."""
    settings: Settings = get_settings()
    tracer = Tracer.disabled()
    llm = get_llm_client(settings)
    client = AsyncQdrantClient(
        url=settings.qdrant_url,
        api_key=(
            settings.qdrant_api_key.get_secret_value()
            if settings.qdrant_api_key is not None
            else None
        ),
        timeout=settings.qdrant_timeout,
        check_compatibility=False,
    )

    def close_runner() -> None:
        """Replaced by the sandbox's own closer once one is built."""

    try:
        retriever = await create_retriever(settings, client, tracer)
        runner, close_runner = await build_sandbox_runner(settings)
        outcomes: list[Outcome] = []
        for index, case in enumerate(cases, 1):
            print(f"  [{index}/{len(cases)}] {case.id}", flush=True)
            outcomes.append(await _run_case(case, llm=llm, retriever=retriever, runner=runner))
    finally:
        close_runner()
        await client.close()

    for got in outcomes:
        if got.error is not None:
            print(f"  !! {got.case_id} errored: {got.error}", flush=True)

    return _score(cases, outcomes)


#: Failing thresholds for `--gate` (ADAPTIVE-upgrade P7, Section 10).
#: hint_safety and debug_fix are hard gates; the rest are scorecard floors.
GATES: Final[dict[str, float]] = {
    "routing": 0.95,
    "topic": 0.95,
    "hint_safety": 1.0,
    "debug_fix": 1.0,
    "groundedness": 0.80,
}


def gate_failures(metrics: Sequence[Metric]) -> list[str]:
    """Metrics below their `GATES` threshold, as printable lines."""
    return [
        f"{m.name} {m.score:.1%} < {GATES[m.name]:.0%}"
        for m in metrics
        if m.name in GATES and m.score < GATES[m.name]
    ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the adaptive-agent evaluation suite.")
    parser.add_argument("--only", help="run just the case with this id", default=None)
    parser.add_argument(
        "--gate", action="store_true", help="exit 1 when a metric is below its GATES threshold"
    )
    args = parser.parse_args(argv)

    cases = DATASET
    if args.only:
        cases = tuple(case for case in DATASET if case.id == args.only)
        if not cases:
            print(f"no case with id {args.only!r}")
            return 2

    print(f"running {len(cases)} case(s) through the real graph\n")
    metrics = asyncio.run(run_suite(cases))

    print("\n" + "=" * 52)
    for metric in metrics:
        print(metric.line())
    print("=" * 52)
    for metric in metrics:
        if metric.misses:
            print(f"\n{metric.name} misses:")
            for miss in metric.misses:
                print(f"  - {miss}")
    if args.gate:
        failures = gate_failures(metrics)
        for line in failures:
            print(f"GATE FAILED: {line}")
        if failures:
            return 1
        print("all gates passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
