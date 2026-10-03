"""The tutoring loop (ADAPTIVE-tutoring G1..G5): guiding questions, grading,
misconceptions, code-submission review and in-session progression.

Everything a learner is ASKED comes from the curated bank in
`app/knowledge/tutoring/` (agent-authored, corpus-anchored). Everything a
learner WRITES is untrusted data: it is graded against that bank, never used as
a rubric, never placed in a hint, never written to a trace.
"""
