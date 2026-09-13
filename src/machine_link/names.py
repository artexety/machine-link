"""How mlink compares what you typed to what a provider calls something.

Every filter in mlink is a fragment of a name: `--gpu h200`, `--region fin`. Spacing and
punctuation are never part of the question, because the four providers punctuate the same thing
four ways. Word edges are, and that is the whole of the rule: a query starts where a word of the
name starts. Without that, `--gpu h200` answers with a `GH200`, which is a different card, and
`--region us` answers with Belarus, Cyprus and Mauritius.

Where a query may *stop* depends on what it is asking about, so there are two rules and not one.
A GPU name is mostly digits, and its digits keep going: `a10` is not an `A100` and `a40` is not
an `A4000`, so a GPU query has to land on both edges. A place name is mostly letters, and no
place is another place's prefix in a way that misleads - `miss` can only be Mississippi - so a
region query may stop anywhere and stay useful.
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
    return _run(text, wanted, exactly=True)


def starts(text: str, wanted: str) -> bool:
    """Whether the squashed `wanted` begins at a word of `text` and runs on from there."""
    return _run(text, wanted, exactly=False)


def _run(text: str, wanted: str, *, exactly: bool) -> bool:
    found = words(text)
    for start in range(len(found)):
        run = ""
        for word in found[start:]:
            run += word
            if run == wanted or (not exactly and run.startswith(wanted)):
                return True
            if not wanted.startswith(run):
                break
    return False
