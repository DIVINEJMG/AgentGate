"""Checks that the AI quoted the human's own words, without demanding character-perfect copies.

Authority that only a human request may grant (open-web research, a named website, an approval
step) is proven by the AI quoting the request. Exact substring checks failed on harmless
differences such as letter case, spacing, quote styles or punctuation, and then either stopped the
request or cost another AI call. Here both sides are compared as sequences of words: the quote
must still be the human's words, in the human's order.
"""
from __future__ import annotations

import re
import unicodedata

_WORD = re.compile(r"[^\W_]+", re.UNICODE)


def words(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKC", text or "").casefold()
    return _WORD.findall(folded)


def grounded_in(quote: str | None, request: str) -> bool:
    """Whether `quote` appears in `request` as consecutive words."""
    needle, haystack = words(quote or ""), words(request)
    if not needle or len(needle) > len(haystack):
        return False
    width = len(needle)
    return any(haystack[start:start + width] == needle for start in range(len(haystack) - width + 1))
