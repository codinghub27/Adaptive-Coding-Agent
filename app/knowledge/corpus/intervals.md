---
title: Intervals
pattern: intervals
topic: intervals
pattern_family: intervals
difficulty: E:1 M:5 H:2
aliases: interval scheduling, merge intervals, overlapping ranges, sort by start
identification_signals: overlapping intervals, merge ranges, meeting rooms, insert interval, sort by start time
representative_problems: Merge Intervals | Medium | https://leetcode.com/problems/merge-intervals/ ; Insert Interval | Medium | https://leetcode.com/problems/insert-interval/ ; Meeting Rooms II | Medium | https://leetcode.com/problems/meeting-rooms-ii/ ; Non-overlapping Intervals | Medium | https://leetcode.com/problems/non-overlapping-intervals/
---

# Intervals

## Overview
Interval problems operate on a list of ranges (start, end) and ask you to merge, insert, count overlaps, or schedule them. Sorting by start (occasionally by end) collapses an apparently combinatorial problem into a single linear sweep, because once sorted, only adjacent-in-order intervals can possibly overlap.

## When to Recognize It
Cue: "Merge/insert/overlap problems on ranges." Recognize it when the input is a list of `[start, end]` pairs; the question mentions merging, overlapping, inserting a new range, scheduling meetings or rooms, or finding free time; and the answer doesn't depend on the original order of the intervals, only on their ranges.

## Core Intuition
After sorting by start time, walk once left to right holding "the interval built so far." Two intervals overlap exactly when the next one's start is <= the current one's end, so a single comparison per step decides merge-vs-emit. This is the same "sort, then one greedy pass" idea as classic greedy algorithms — see `greedy` for the general proof pattern that justifies why sorting first is enough.

## Identification Signals
- "merge the overlapping intervals"
- "insert a new interval"
- "minimum number of meeting rooms"
- "can a person attend all meetings"
- "non-overlapping intervals to remove"

## General Template
```python
def merge_intervals(intervals: list[list[int]]) -> list[list[int]]:
    """Merge all overlapping intervals; input need not be pre-sorted."""
    intervals.sort(key=lambda pair: pair[0])
    merged: list[list[int]] = []
    for start, end in intervals:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged
```

## Complexity
Time O(n log n), dominated by the sort; the sweep itself is O(n). Space O(n) for the sorted copy and output list.

## Common Mistakes
Forgetting to sort first, or sorting by the wrong key (end instead of start), then applying an adjacency check that assumes order. Using `<` instead of `<=` at the overlap boundary, which mishandles intervals that exactly touch (e.g. [1,3] and [3,5]). Mutating the input's sub-lists in place when the caller still needs the originals. For "meeting rooms" counting, tracking full intervals instead of separately sorted start/end event times.

## When NOT to Use
If ranges never need merging and you only need a running total over a fixed set of query ranges, a prefix-sum/difference array is simpler — see `prefix_sum`. If intervals arrive online and need efficient point/range updates plus queries, a segment tree (see `segment_tree`) is the right tool, not a one-pass sweep.

## Variations
Merge vs insert-and-merge (one new interval into an already-sorted list, doable in a single pass without re-sorting); minimum rooms / max overlap via a sweep-line of +1/-1 events; interval removal to make the rest non-overlapping (greedy by end time); free-time/gap finding across multiple schedules (employee free time).

## Representative Problems
- [Insert Interval](https://leetcode.com/problems/insert-interval/) — Medium
- [Merge Intervals](https://leetcode.com/problems/merge-intervals/) — Medium
- [Non-overlapping Intervals](https://leetcode.com/problems/non-overlapping-intervals/) — Medium
- [Meeting Rooms](https://leetcode.com/problems/meeting-rooms/) — Easy
- [Meeting Rooms II](https://leetcode.com/problems/meeting-rooms-ii/) — Medium
- [Minimum Interval to Include Each Query](https://leetcode.com/problems/minimum-interval-to-include-each-query/) — Hard
- [Remove Covered Intervals](https://leetcode.com/problems/remove-covered-intervals/) — Medium
- [Employee Free Time](https://leetcode.com/problems/employee-free-time/) — Hard
