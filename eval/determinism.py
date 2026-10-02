"""Evidence-path determinism: the same debug request, N times (ADAPTIVE-upgrade P5).

    .\\venv\\Scripts\\python.exe -m eval.determinism --runs 10

B2: the same debug request came back with a 5-case synthesised suite one time
and no suite the next, so whether a turn produced evidence about the learner
was a coin flip. This runs one fixed debug request (buggy code, a real problem
statement, NO worked examples -- so extraction finds nothing and synthesis is
the only path) through the real graph N times and reports the evidence path
each time: suite source, the learner-code verdict, and `solved`.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from collections.abc import Sequence

from qdrant_client import AsyncQdrantClient

from app.config import get_settings
from app.execution.runner import build_sandbox_runner
from app.graph.build import run_graph
from app.graph.state import RawInput
from app.knowledge.retrieve import create_retriever
from app.llm.client import Tracer, get_llm_client

__all__ = ["main"]

REQUEST = """Problem: Given an array of integers nums, return the length of the longest
strictly increasing contiguous subarray.

My code gives the wrong answer, can you find the bug?

```python
def longest_increasing_run(nums):
    if not nums:
        return 0
    best = 1
    current = 1
    for i in range(1, len(nums)):
        if nums[i] > nums[i - 1]:
            current += 1
        else:
            current = 1
    return best
```"""


async def run(runs: int) -> int:
    settings = get_settings()
    tracer = Tracer.disabled()
    qdrant = AsyncQdrantClient(url=settings.qdrant_url)
    retriever = await create_retriever(settings, qdrant, tracer)
    runner, close_runner = await build_sandbox_runner(settings)
    if runner is None:
        print("sandbox unavailable: refusing to measure (every run would read 'none')")
        close_runner()
        return 1
    paths: list[tuple[str, str | None, bool | None]] = []
    try:
        for index in range(runs):
            llm = get_llm_client(settings, tracer)
            result = await run_graph(
                RawInput(text=REQUEST), llm=llm, retriever=retriever, runner=runner
            )
            state = result.state
            initial = getattr(state.agent_result, "initial_verdict", None)
            solved = state.agent_output.solved if state.agent_output is not None else None
            path = (state.suite_source, initial.status if initial is not None else None, solved)
            paths.append(path)
            print(
                f"  run {index + 1:2d}: route={state.route} suite={path[0]} "
                f"verdict={path[1]} solved={path[2]}"
            )
    finally:
        close_runner()
        await qdrant.close()
    counts = Counter(paths)
    most, n = counts.most_common(1)[0]
    print(f"\nsame evidence path: {n}/{runs}  (modal path: {most})")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evidence-path determinism")
    parser.add_argument("--runs", type=int, default=10)
    args = parser.parse_args(argv)
    return asyncio.run(run(args.runs))


if __name__ == "__main__":
    sys.exit(main())
