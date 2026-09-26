---
title: Trie
pattern: trie
topic: tries
pattern_family: trie
difficulty: E:1 M:5 H:4
aliases: prefix tree, digital tree, radix tree lite, word dictionary
identification_signals: prefix search, autocomplete, word dictionary, starts with, longest common prefix
representative_problems: Implement Trie (Prefix Tree) | Medium | https://leetcode.com/problems/implement-trie-prefix-tree/ ; Word Search II | Hard | https://leetcode.com/problems/word-search-ii/ ; Design Add and Search Words Data Structure | Medium | https://leetcode.com/problems/design-add-and-search-words-data-structure/ ; Replace Words | Medium | https://leetcode.com/problems/replace-words/
---

# Trie

## Overview
A trie (prefix tree) stores a set of strings so every path from the root spells a prefix shared by many words, letting you insert, exact-search, and prefix-search all in time proportional to the string length, independent of how many words are stored. It's the go-to structure whenever "prefix" is the operation, not "substring" or plain set membership.

## When to Recognize It
Cue: "Prefix search, autocomplete, word dictionary." Recognize it when the problem repeatedly asks "does any word start with this prefix"; when you need autocomplete-style suggestions; when a wildcard `.` search over a growing word dictionary is required; or when a word-search/board problem needs to prune many simultaneous string matches at once.

## Core Intuition
Each trie node is a dictionary of child nodes keyed by the next character, plus a flag marking "a word ends here." Sharing prefixes as shared path segments means inserting or looking up a string costs O(length), not O(number of words stored) — the tree deduplicates shared prefixes for you. Searching many words against one text (Word Search II) becomes efficient because the trie lets you abandon a branch the instant no stored word can match it.

## Identification Signals
- "starts with"
- "prefix search" / "autocomplete"
- "add and search word" with wildcard support
- "word dictionary"
- "longest common prefix of a set of strings"

## General Template
```python
class TrieNode:
    def __init__(self) -> None:
        self.children: dict[str, "TrieNode"] = {}
        self.is_word: bool = False


class Trie:
    def __init__(self) -> None:
        self.root = TrieNode()

    def insert(self, word: str) -> None:
        node = self.root
        for ch in word:
            node = node.children.setdefault(ch, TrieNode())
        node.is_word = True

    def starts_with(self, prefix: str) -> bool:
        node = self.root
        for ch in prefix:
            if ch not in node.children:
                return False
            node = node.children[ch]
        return True
```

## Complexity
Time O(L) per insert/search/prefix-check, where L is the string's length, independent of how many words are already stored. Space O(sum of unique characters across all inserted prefixes), worst case O(total characters across all words) when no prefixes are shared.

## Common Mistakes
Using a fixed-size array of 26 children when the alphabet isn't guaranteed lowercase a-z — a dict of children is more robust. Forgetting the `is_word` flag and treating "prefix exists" as "word exists," which breaks exact-match search. Not handling the wildcard `.` case in "search with wildcards" problems, which requires trying every child instead of one lookup. Rebuilding the trie from scratch per query instead of reusing it across repeated searches.

## When NOT to Use
If you only need a fixed, unchanging set of strings for exact membership checks, a hash set (`hashing`) is simpler and lighter than a trie. If the task is about substrings anywhere in a text (not prefixes from the start), a suffix structure or straightforward string search fits better than a trie.

## Variations
Trie plus DFS on a grid for multi-word search (Word Search II); trie with wildcard support (Design Add and Search Words); trie storing (string, value) pairs for prefix-summed lookups (Map Sum Pairs); trie over word suffixes for compound-word detection (Concatenated Words); trie replacing every word in a sentence with its shortest known root (Replace Words).

## Representative Problems
- [Implement Trie (Prefix Tree)](https://leetcode.com/problems/implement-trie-prefix-tree/) — Medium
- [Design Add and Search Words Data Structure](https://leetcode.com/problems/design-add-and-search-words-data-structure/) — Medium
- [Word Search II](https://leetcode.com/problems/word-search-ii/) — Hard
- [Replace Words](https://leetcode.com/problems/replace-words/) — Medium
- [Map Sum Pairs](https://leetcode.com/problems/map-sum-pairs/) — Medium
- [Concatenated Words](https://leetcode.com/problems/concatenated-words/) — Hard
- [Palindrome Pairs](https://leetcode.com/problems/palindrome-pairs/) — Hard
- [Stream of Characters](https://leetcode.com/problems/stream-of-characters/) — Hard
