"""How mlink compares what you typed to what a provider calls something.

Every filter in mlink is a fragment of a name. Spacing and punctuation are never part of the
question, because the three providers punctuate the same card three ways. Word edges are, and
that is the whole of the rule: a query starts where a word of the name starts, and stops where
one stops. A GPU name is mostly digits, and its digits keep going, so without both edges
`--gpu h200` answers with a `GH200`, `a10` with an `A100` and `a40` with an `A4000`, none of
which is the card that was asked for.
"""

from __future__ import annotations

import re

_WORD = re.compile(r"[a-z]+|\d+")


def squash(text: str) -> str:
    """A name with its punctuation and spacing removed, which is how a query is written."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def words(text: str) -> list[str]:
    """A name as the runs of letters and digits it is built from: where a query may begin.

    `GH200_96GB` is `gh 200 96 gb`, so a query may begin at `gh` and never at `h`.
    """
    return _WORD.findall((text or "").lower())


def spans(text: str, wanted: str) -> bool:
    """Whether the squashed `wanted` is a whole run of consecutive words of `text`."""
    found = words(text)
    for start in range(len(found)):
        run = ""
        for word in found[start:]:
            run += word
            if run == wanted:
                return True
            if not wanted.startswith(run):
                break
    return False
