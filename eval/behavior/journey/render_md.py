"""Renders adaptive_journey_10.jsonl into a readable adaptive_journey_10.md."""

import json

recs = [json.loads(line) for line in open("adaptive_journey_10.jsonl", encoding="utf-8")]

out = [
    "# Adaptive Coding Agent: 10-session learner journey",
    "",
    "One learner (`{learner_name}`), from *confused intermediate* to *focused problem-solver* over 30 days.",
    "Every sandbox result was produced by actually running the code (`verify_examples.py`).",
    "Skill values are computed with ALPHA 0.3 (sandbox), CONCEPT_ALPHA 0.1 (graded answers, capped at HARD 0.68), PRIOR 0.5.",
    "",
]

for idx, r in enumerate(recs, 1):
    out += [
        f"---\n\n## {idx}. {r['title']} (session {r['session']})",
        f"`{r['conversation_id']}` · day {r['day']} · intent `{r['intent']}` · mode **{r['teaching_mode']}**"
        + (f" · problem: *{r['problem']}*" if r.get("problem") else ""),
        "",
        "**Behaviors:** " + ", ".join(f"`{b}`" for b in r["agent_behavior"]),
        "",
    ]
    for t in r["conversation"]:
        who = "🧑 **Learner**" if t["role"] == "user" else "🤖 **Agent**"
        out += [f"{who}", "", t["content"], ""]
        m = t.get("meta") or {}
        tags = []
        if "grade" in m:
            tags.append(f"grade=`{m['grade']}`")
        if "plan" in m:
            tags.append("plan=" + ", ".join(f"{k}:{v}" for k, v in m["plan"].items()))
        if "sandbox" in m:
            tags.append(
                f"sandbox {m['sandbox']['passed']}/{m['sandbox']['total']} ({m['sandbox']['provenance']})"
            )
        if "misconception" in m:
            tags.append(f"misconception=`{m['misconception']}`")
        if "memory_used" in m:
            tags.append("memory: " + "; ".join(m["memory_used"]))
        if "escalation" in m:
            tags.append(f"escalation: {m['escalation']}")
        if tags:
            out += ["> " + " · ".join(tags), ""]
    d = r["profile_after"]["skill_deltas"]
    out += [
        "**Profile change:** "
        + (
            ", ".join(
                f"`{k}` {v:+.3f} → {r['profile_after']['skills'][k]:.2f} ({r['profile_after']['skill_labels'][k]})"
                for k, v in d.items()
            )
            or "no skill moved (exposure only)"
        ),
        "",
        f"**Expected outcome:** {r['expected_outcome']}",
        "",
    ]

open("adaptive_journey_10.md", "w", encoding="utf-8").write("\n".join(out))
print("rendered", len(recs))
