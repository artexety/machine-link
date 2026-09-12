"""One vocabulary for GPU names, which every provider spells differently.

Prime says `H100_80GB`, Vast says `H100 SXM`, Verda says `1x H100 SXM5 80GB`, and none of them
is wrong. Each name is parsed into the same shape so a listing reads the same wherever a row
came from, and so `--gpu` means one thing everywhere.

Two rules hold the design together. The raw string is kept, because it is what a provider wants
back when you rent, and because a name nobody anticipated must still list and still be rentable:
parsing never raises and never drops a row, it just gives up quietly and leaves the whole name
as the model. And the count stays on the offer, which every provider reports as a number;
`Gpu.count` only records that a name spelled it out, so the two can never drift apart.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Who made it, not what it is. Providers disagree on whether to say it at all: Verda writes
#: "Tesla V100" where Prime writes "V100_16GB", and Vast abbreviates Quadro to "Q".
BRANDS = frozenset({"nvidia", "geforce", "tesla", "quadro", "q", "amd", "radeon"})
#: Families whose letters are a prefix to the number rather than part of it, so `RTX3080Ti`
#: separates but `L40S`, `A10g` and `GB300` stay whole.
FAMILIES = frozenset({"rtx", "gtx", "rx", "gt"})
#: Qualifiers a number may be glued to: `6000Ada`, `4090D`, `2080S`, `RTX_PRO_6000B`.
SUFFIXES = frozenset({"ada", "ti", "s", "d", "b", "xt", "super"})

_TOKENS = re.compile(r"[\s_/,]+")
_COUNT = re.compile(r"^(\d+)x$", re.I)
_MEMORY = re.compile(r"^(\d+)\s*g(?:b|ib)?$", re.I)
_NUMBERED = re.compile(r"^(?P<prefix>[a-z]*)(?P<number>\d{2,5})(?P<suffix>[a-z-]*)$", re.I)


@dataclass(frozen=True, slots=True)
class Gpu:
    """One GPU name, taken apart. `raw` is always what the provider actually said."""

    raw: str
    model: str = ""
    variant: str = ""
    memory_gb: int | None = None
    #: Only when the name itself spelled it out, as Verda's "8x A100 SXM4 80GB" does. The
    #: offer's own gpu_count is what mlink counts by; this is here to be consumed, not trusted.
    count: int = 0

    @property
    def label(self) -> str:
        """How mlink writes it: the same words for the same card, whoever is renting it."""
        parts = (self.model, self.variant, f"{self.memory_gb}GB" if self.memory_gb else "")
        return " ".join(part for part in parts if part) or self.raw


def parse(raw: str, memory_gb: int | None = None) -> Gpu:
    """Take a provider's GPU name apart. `memory_gb` fills in for a name that omits it.

    Vast never puts memory in a name and reports it as a separate number; Prime and Verda put
    it in the name. What the name says wins, because it is the thing being sold.
    """
    words = [word for word in _TOKENS.split((raw or "").strip()) if word]
    count, memory, tokens = 0, None, []
    for index, word in enumerate(words):
        if index == 0 and (found := _COUNT.match(word)):
            count = int(found[1])
        elif found := _MEMORY.match(word):
            memory = int(found[1])
        elif word.lower() not in BRANDS:
            tokens += _split(word)
    model, variant = _divide(tokens)
    return Gpu(raw=raw, model=model, variant=variant, memory_gb=memory or memory_gb, count=count)


def _split(word: str) -> list[str]:
    """`RTX3080Ti` into three words, but never `L40S` or `A10g`, which are whole names."""
    found = _NUMBERED.match(word)
    if not found:
        return [word]
    prefix, number, suffix = found["prefix"], found["number"], found["suffix"]
    if prefix and prefix.lower() not in FAMILIES:
        return [word]
    if suffix and suffix.lower() not in SUFFIXES:
        return [word]
    return [part for part in (prefix.upper(), number, suffix) if part]


def _divide(tokens: list[str]) -> tuple[str, str]:
    """Everything up to the number names the card; everything after it qualifies the card.

    "RTX PRO 6000 Max-Q" divides at 6000, and so does "RTX PRO 6000B". A name with no number
    at all, such as "Titan Xp", is all model: there is nothing to qualify.
    """
    for index, token in enumerate(tokens):
        if any(char.isdigit() for char in token):
            return " ".join(tokens[: index + 1]), " ".join(tokens[index + 1 :])
    return " ".join(tokens), ""


def squash(text: str) -> str:
    """A name with its punctuation and spacing removed, which is how `--gpu` compares them."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def matches(query: str, gpu: Gpu) -> bool:
    """Whether `--gpu <query>` wants this card.

    Compared with the spacing taken out, so `rtx6000ada`, `RTX 6000 Ada` and `rtx 6000ada` are
    one query, and against the provider's own wording too, so nothing becomes unfindable by
    being parsed.
    """
    wanted = squash(query)
    return not wanted or wanted in squash(gpu.label) or wanted in squash(gpu.raw)
