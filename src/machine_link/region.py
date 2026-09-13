"""One vocabulary for places, which every provider spells differently.

Vast says `Mississippi, US`, Prime says `us-central1`, Verda says `FIN-02`, and only the first
of those tells you where the machine is. The other two are a datacenter's own name for itself,
which is worth keeping - it is what you quote to support, and what tells two otherwise identical
rows apart - but it is not an answer to "where".

Every provider already knows the country and mlink used to throw it away: Prime sends `country`
beside `dataCenter`, Verda has a `/locations` endpoint, Vast writes the code into the string.
So a place is resolved out of what the providers say and never out of a table here. A table of
codes would be a second thing to keep true, and the day it went stale it would be confidently
wrong about where your money is going.

How far this can go is set by the provider and not by mlink. Prime knows the country and stops:
its `us-central1` is a partner's name, not Google's, so reading a state into it would be an
invention. `eu-north1` resolving to FI and `EU-SE-1` to SE is the real win, and it is one no
amount of guessing would have got right.
"""

from __future__ import annotations

from dataclasses import dataclass

from .names import squash, starts

#: ISO 3166-1 alpha-2, which is what all three providers speak. Two letters and nothing else:
#: `US`, `FI`, `SE`. A longer code is a site name, not a country.
_CODE = 2


@dataclass(frozen=True, slots=True)
class Place:
    """Where a machine is. `raw` is always what the provider actually said."""

    raw: str
    #: The most specific thing the provider knows: `Mississippi`, `Finland 2`, `us-central1`.
    site: str = ""
    #: ISO 3166-1 alpha-2, or empty when the provider never said.
    country: str = ""

    @property
    def short(self) -> str:
        """Just the country, which is the one thing every provider reports about every row.

        A listing is read down a column, and the sites do not line up: Vast gives a US state but
        writes `Bulgaria, BG` where it has no state, Verda gives a rack, Prime gives a partner's
        name for a datacenter. Widening the column to the longest of those, to say something
        different on each row, buys less than the two letters every row can be compared by. The
        site is not lost - it is in `--region`, in `--json`, and in the line you confirm before
        any money is spent, which is where knowing it actually changes what you do.
        """
        return self.country or self.label

    @property
    def label(self) -> str:
        """The whole of it: the site, then the country, as Vast already writes both."""
        if not self.site:
            return self.country or self.raw
        if not self.country or squash(self.site) == squash(self.country):
            return self.site
        return f"{self.site}, {self.country}"


def parse(raw: str, country: str = "", site: str = "") -> Place:
    """Take a provider's region apart, using whatever it reported alongside.

    Vast puts the code in the string itself (`Mississippi, US`), and leaves the part before the
    comma empty for a host whose state it does not know, which arrives as `, US`. Prime reports
    the country in a field of its own. Verda reports both a country and a name for the code, so
    `FIN-02` arrives with `Finland 2`; the code stays as `raw`, because it is what you quote to
    support and what `--region fin-02` has to keep finding.
    """
    parts = [part.strip() for part in (raw or "").split(",") if part.strip()]
    if not country and parts and len(parts[-1]) == _CODE and parts[-1].isalpha():
        country = parts.pop()
    return Place(
        raw=(raw or "").strip(),
        site=site.strip() or ", ".join(parts),
        country=country.strip().upper(),
    )


def matches(query: str, place: Place) -> bool:
    """Whether `--region <query>` wants this place.

    Asked of the resolved place, the provider's own code, and the country, so `--region finland`
    and `--region fi` and `--region fin-02` all reach the same Verda rows. A region query may
    stop in the middle of a word, which is why `miss` still finds Mississippi.
    """
    wanted = squash(query)
    return not wanted or any(
        starts(text, wanted) for text in (place.label, place.raw, place.country)
    )
