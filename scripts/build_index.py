"""CLI entry point that (re)builds the Qdrant knowledge index.

Run from the repo root as:

    venv/Scripts/python.exe -m scripts.build_index [--recreate]

Loads the curated corpus (`app/knowledge/corpus/`), chunks it, embeds every
chunk, and upserts it into the configured Qdrant collection, deleting any
stale points left over from a previous run. Never prints exception details or
secrets -- only the failing exception's class name.
"""

import argparse
import asyncio
import json
import sys

from app.config import Settings
from app.knowledge.ingest import build_index


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build/refresh the Qdrant knowledge index.")
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="Delete and recreate the collection instead of upserting into the existing one.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    try:
        report = asyncio.run(build_index(Settings(), recreate=args.recreate))
    except Exception as exc:
        print(f"build_index failed: {type(exc).__name__}", file=sys.stderr)
        return 1

    print(json.dumps(report.model_dump(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
