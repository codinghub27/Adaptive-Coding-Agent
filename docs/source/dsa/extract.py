"""Extract the DSA spreadsheets into `dsa_source.json` (the grounding data for
`app/knowledge/corpus/`). Re-run after editing the workbooks:

    venv/Scripts/python.exe docs/source/dsa/extract.py
"""

import json
from pathlib import Path
from typing import Any

import pandas as pd

HERE = Path(__file__).parent
PLACEMENT = HERE / "DSA_Placement_Ready_TopicPatternWise.xlsx"
PLAN = HERE / "DSA_LeetCode_6Month_Plan.xlsx"
OUT = HERE / "dsa_source.json"


def main() -> None:
    pw = pd.read_excel(PLACEMENT, sheet_name="Pattern-Wise Map", header=3)
    pw.columns = ["pattern", "cue", "representative_problem", "link"]
    pattern_map = [
        {k: (None if pd.isna(v) else str(v).strip()) for k, v in row.items()}
        for row in pw.dropna(subset=["pattern"]).to_dict("records")
    ]

    qr = pd.read_excel(PLAN, sheet_name="\U0001f4d6 Quick Reference", header=0)
    qr.columns = ["pattern", "cue"]
    quick_reference = [
        {"pattern": str(r.pattern).strip(), "cue": str(r.cue).strip()}
        for r in qr.iloc[1:].itertuples()
        if not pd.isna(r.pattern)
    ]

    ts = pd.read_excel(PLAN, sheet_name="\U0001f4ca Topic Summary")
    topic_summary = [
        {
            "topic": str(r["Topic"]),
            "n_problems": int(r["# Problems"]),
            "days": str(r["Days Covered"]),
            "difficulty_mix": str(r["Difficulty Mix"]),
            "focus_tip": str(r["Focus Tips"]),
        }
        for _, r in ts.iterrows()
    ]

    fs = pd.read_excel(PLAN, sheet_name="\U0001f4c5 Full Schedule")
    problems_by_topic: dict[str, list[dict[str, Any]]] = {}
    for _, r in fs.iterrows():
        problems_by_topic.setdefault(str(r["Topic"]), []).append(
            {
                "day": int(r["Day"]),
                "name": str(r["Problem Name"]).strip(),
                "difficulty": str(r["Difficulty"]).strip(),
                "link": str(r["LeetCode Link"]).strip(),
            }
        )

    OUT.write_text(
        json.dumps(
            {
                "_source": [PLACEMENT.name, PLAN.name],
                "pattern_map": pattern_map,
                "quick_reference": quick_reference,
                "topic_summary": topic_summary,
                "problems_by_topic": problems_by_topic,
                "total_problems": int(len(fs)),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"wrote {OUT} ({len(pattern_map)} patterns, {len(fs)} problems)")


if __name__ == "__main__":
    main()
