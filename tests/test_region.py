"""Where a machine is, in one vocabulary, out of what the providers themselves report."""

from __future__ import annotations

import pytest

from machine_link.models import Filters, Offer
from machine_link.region import Place, matches, parse


@pytest.mark.parametrize(
    ("raw", "country", "site", "label", "short"),
    [
        # Vast writes the country into the string; nothing to resolve, nothing to change.
        ("Mississippi, US", "", "", "Mississippi, US", "US"),
        ("The Netherlands, NL", "", "", "The Netherlands, NL", "NL"),
        # a host whose state Vast does not know arrives as ", US"
        (", US", "", "", "US", "US"),
        ("US", "", "", "US", "US"),
        # Prime knows the country and stops there: the datacenter name is a partner's, not a place
        ("us-central1", "US", "", "us-central1, US", "US"),
        ("ewr", "US", "", "ewr, US", "US"),
        # the two that no guess would have got right
        ("eu-north1", "FI", "", "eu-north1, FI", "FI"),
        ("EU-SE-1", "SE", "", "EU-SE-1, SE", "SE"),
        # Verda names its own codes
        ("FIN-02", "FI", "Finland 2", "Finland 2, FI", "FI"),
        # a provider that says the country twice should not be made to say it twice
        ("FI", "FI", "", "FI", "FI"),
        # one that never said which country keeps whatever it did say, in both forms
        ("somewhere-1", "", "", "somewhere-1", "somewhere-1"),
        ("", "", "", "", ""),
    ],
)
def test_a_place_reads_the_same_whichever_provider_it_came_from(raw, country, site, label, short):
    """`label` is the whole of it, for the line you confirm before paying; `short` is what a
    column carries, because a listing is read down and only the country lines up."""
    place = parse(raw, country, site)
    assert (place.label, place.short) == (label, short)


def test_the_provider_code_is_never_lost():
    """It is what you quote to support, and what tells two racks in one country apart."""
    assert parse("FIN-02", "FI", "Finland 2") == Place(raw="FIN-02", site="Finland 2", country="FI")


@pytest.mark.parametrize(
    ("query", "raw", "country", "site"),
    [
        ("fi", "FIN-01", "FI", "Finland 1"),
        ("finland", "FIN-01", "FI", "Finland 1"),
        ("fin", "FIN-01", "FI", "Finland 1"),
        ("miss", "Mississippi, US", "", ""),  # a region query may stop mid-word
        ("us", "Mississippi, US", "", ""),
        ("us", "us-central1", "US", ""),
        ("cent", "us-central1", "US", ""),
        ("us-east", "us-east-1", "US", ""),
        ("", "anything", "", ""),
    ],
)
def test_a_region_query_finds_the_place_however_the_provider_spelled_it(query, raw, country, site):
    assert matches(query, parse(raw, country, site))


@pytest.mark.parametrize(
    ("query", "raw"),
    [
        # the reason a region query has to start at a word: three countries end in "us"
        ("us", "Belarus, BY"),
        ("us", "Cyprus, CY"),
        ("us", "Mauritius, MU"),
        ("fi", "Pacifica, US"),
        ("se", "Essex, GB"),
    ],
)
def test_a_country_code_does_not_turn_up_inside_another_country(query, raw):
    assert not matches(query, parse(raw))


def test_the_country_reaches_the_filters_and_the_launch_confirmation():
    prime = Offer("prime", "1", "H200_141GB", 1, "us-central1", 4.5, country="US")
    verda = Offer("verda", "2", "1H200.141S", 1, "FIN-03", 4.0, country="FI", site="Finland 3")
    vast = Offer("vast", "3", "H200 NVL", 1, "Bulgaria, BG", 3.61, gpu_memory_gb=143360)
    assert Filters(region="us").match(prime) and not Filters(region="us").match(verda)
    assert Filters(region="fi").match(verda) and not Filters(region="fi").match(prime)
    assert Filters(region="fin-03").match(verda)  # Verda's own code still finds it
    assert Filters(region="bulgaria").match(vast)
    assert "in Finland 3, FI at $4.00/hr" in verda.describe()
    assert "in us-central1, US at $4.50/hr" in prime.describe()
