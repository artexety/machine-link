"""One vocabulary, against the three catalogues it actually has to read."""

import json

import pytest

from machine_link.gpu import Gpu, matches, parse, squash
from machine_link.models import Filters, Machine, Offer
from machine_link.registry import Registry
from tests import gpu_catalogue
from tests.test_cli import mlink
from tests.test_providers import PRIME_ROUTES, VERDA_ROUTES


@pytest.mark.parametrize(
    ("raw", "model", "variant", "memory_gb", "count"),
    [
        # The same card, as each of the three providers writes it.
        ("RTX6000Ada_48GB", "RTX 6000", "Ada", 48, 0),
        ("RTX 6000Ada", "RTX 6000", "Ada", None, 0),
        ("1x RTX 6000 Ada 48GB", "RTX 6000", "Ada", 48, 1),
        ("V100_16GB", "V100", "", 16, 0),
        ("Tesla V100", "V100", "", None, 0),
        ("8x Tesla V100 16GB", "V100", "", 16, 8),
        ("H100_80GB", "H100", "", 80, 0),
        ("H100 SXM", "H100", "SXM", None, 0),
        ("4x H100 SXM5 80GB", "H100", "SXM5", 80, 4),
        ("RTX_PRO_6000B_96GB", "RTX PRO 6000", "B", 96, 0),
        ("RTX PRO 6000 Max-Q", "RTX PRO 6000", "Max-Q", None, 0),
        ("1x RTX PRO 6000 CC 96GB", "RTX PRO 6000", "CC", 96, 1),
        # A number glued to its qualifier comes apart; a number that is part of the name does not.
        ("RTX3080Ti_12GB", "RTX 3080", "Ti", 12, 0),
        ("RTX 4090D", "RTX 4090", "D", None, 0),
        ("RTX 4070S Ti", "RTX 4070", "S Ti", None, 0),
        ("L40S_48GB", "L40S", "", 48, 0),
        ("A10g", "A10g", "", None, 0),
        ("GB300", "GB300", "", None, 0),
        ("CMP 170HX", "CMP 170HX", "", None, 0),
        # Brands say who made it, not what it is.
        ("Q RTX 8000", "RTX 8000", "", None, 0),
        ("Quadro GV100", "GV100", "", None, 0),
        # Nothing numeric to divide on: it is all model.
        ("Titan Xp", "Titan Xp", "", None, 0),
        ("GTX TITAN X", "GTX TITAN X", "", None, 0),
    ],
)
def test_the_three_spellings_of_a_card_come_out_the_same(raw, model, variant, memory_gb, count):
    assert parse(raw) == Gpu(
        raw=raw, model=model, variant=variant, memory_gb=memory_gb, count=count
    )


@pytest.mark.parametrize("raw", gpu_catalogue.ALL)
def test_every_real_name_parses_into_something_that_can_be_shown_and_rented(raw):
    """The whole catalogue, not a flattering sample: no name may vanish from a listing."""
    card = parse(raw)
    assert card.raw == raw  # what the provider wants back is never lost
    assert card.label.strip()
    assert card.model
    assert matches("", card)  # an unfiltered listing shows it
    assert matches(card.model, card)  # and its own model finds it


def test_a_name_nobody_anticipated_still_lists_and_still_rents():
    """The rules are applied hopefully, never fatally: whatever is left over is the model."""
    card = parse("Photonic Q9 Tensor Fabric")
    assert (card.model, card.variant) == ("Photonic Q9", "Tensor Fabric")
    assert card.raw == "Photonic Q9 Tensor Fabric" and card.label == card.raw
    assert matches("photonic", card) and matches("tensor", card)
    wordless = parse("!!!")
    assert wordless.label == "!!!" and matches("!!", wordless)
    assert parse("").label == "" and parse("").model == ""


def test_memory_comes_from_the_name_first_and_the_provider_second():
    assert parse("H100 SXM", 80).memory_gb == 80  # Vast names never carry it
    assert parse("1x H100 SXM5 80GB", 40).memory_gb == 80  # the name is the thing being sold


@pytest.mark.parametrize(
    ("query", "raw"),
    [
        ("a100", "8x A100 SXM4 80GB"),
        ("A100", "A100_80GB"),
        ("rtx6000ada", "1x RTX 6000 Ada 48GB"),  # spacing is not part of the question
        ("RTX 6000 Ada", "RTX 6000Ada"),
        ("rtx 4090", "RTX4090_24GB"),
        ("4090", "RTX 4090D"),
        ("h100 sxm", "1x H100 SXM5 80GB"),
        ("80gb", "H100_80GB"),
        ("sxm4", "A100 SXM4"),
        ("v100", "Tesla V100"),
        ("tesla v100", "Tesla V100"),  # the provider's own wording still finds it
    ],
)
def test_gpu_queries_span_the_providers_however_they_are_written(query, raw):
    assert matches(query, parse(raw))


@pytest.mark.parametrize(
    ("query", "raw"),
    [("h100", "H200_141GB"), ("4090", "RTX 4080"), ("sxm", "H100 PCIE"), ("a100", "A10_24GB")],
)
def test_a_query_does_not_reach_the_next_card_along(query, raw):
    assert not matches(query, parse(raw))


def test_squash_is_what_makes_two_spellings_one_question():
    assert squash("RTX PRO 6000 Max-Q") == "rtxpro6000maxq" == squash("rtx_pro_6000_maxq")
    assert squash("") == "" and squash(None) == ""


def test_an_offer_reads_the_same_whichever_provider_it_came_from():
    prime = Offer("prime", "1", "H100_80GB", 8, "us-central-1", 24.0)
    vast = Offer("vast", "2", "H100 SXM", 8, "US, TX", 20.0, gpu_memory_gb=80)
    verda = Offer("verda", "3", "8x H100 SXM5 80GB", 8, "FIN-01", 22.0)
    assert prime.label == "8x H100 80GB"
    assert vast.label == "8x H100 SXM 80GB"
    assert verda.label == "8x H100 SXM5 80GB"
    for offer in (prime, vast, verda):
        assert Filters(gpu="h100").match(offer)
        assert Filters(gpu="80gb").match(offer)
        assert not Filters(gpu="h200").match(offer)


def test_every_listing_speaks_the_parsed_vocabulary(settings, project):
    """gpus and ls are the two places a GPU name is shown; neither may leak the raw one."""
    registry = Registry()
    registry.add(
        Machine(name="p", host="203.0.113.7", user="ubuntu", provider="prime", gpu="H100_80GB"),
        project,
    )
    registry.save()
    code, output = mlink("ls")
    assert code == 0
    assert "H100 80GB" in output and "H100_80GB" not in output
    entries = {m["name"]: m for m in json.loads(mlink("ls", "--json")[1])}
    assert entries["p"]["gpu"] == "H100_80GB"  # the provider's own string is still there
    assert entries["p"]["gpu_parsed"]["model"] == "H100"


def test_a_default_machine_name_is_the_model_whoever_is_renting_it():
    from machine_link.registry import unique_name

    for raw in ("1x A100 SXM4 80GB", "A100_80GB", "A100 SXM4"):
        assert unique_name(f"verda-{parse(raw).model}", set()) == "verda-A100"


def _line(output, *words):
    return next(r for r in output.splitlines() if all(w in r for w in words))


def _span(output, left, right):
    """How many characters the table gives between the start of two columns."""
    header = _line(output, "provider")
    return header.index(right) - header.index(left)


def test_the_gpus_table_gives_each_parsed_field_its_own_column(settings, project, http):
    """What separates two rows of the same card is the variant or the memory, so they get
    columns of their own rather than being crowded into the name."""
    http({**PRIME_ROUTES, **VERDA_ROUTES})
    code, output = mlink("gpus")
    assert code == 0
    header = _line(output, "provider")
    assert header.split() == ["#", "provider", "gpu", "GB", "n", "region", "$/hr"]
    assert "variant" not in header  # the column is there, unheaded, between gpu and GB
    assert _line(output, "verda", "A100").split()[2:4] == ["A100", "SXM4"]
    # gpu, then the unheaded variant column, then GB, with rich's two spaces between each.
    assert _span(output, "gpu", "GB") == len("RTX A6000") + 2 + len("SXM4") + 2
    # The one card the providers genuinely disagree about: Prime drops the RTX, so the model
    # column does too. Its variant cell is simply empty while other rows fill theirs.
    assert _line(output, "prime", "A6000").split()[2:4] == ["A6000", "48"]
    assert "1x A100 SXM4 80GB" not in output and "A6000_48GB" not in output


def test_a_column_no_row_fills_is_not_shown_at_all(settings, project, http):
    """A search for a card with no variants should not pay a column's width for the fact.

    An empty cell is invisible to split(), so this measures the table instead: GB sits right
    after the widest model, with nothing but rich's two spaces in between.
    """
    http({**PRIME_ROUTES, **VERDA_ROUTES})
    code, output = mlink("gpus", "--gpu", "a6000")
    assert code == 0
    assert "RTX A6000" in output  # the rows are all there, only the empty column went
    assert _span(output, "gpu", "GB") == len("RTX A6000") + 2


def test_filtering_uses_the_parsed_name_and_still_honours_the_raw_one():
    offer = Offer("vast", "1", "Q RTX 8000", 1, "", 0.4)
    assert Filters(gpu="rtx8000").match(offer)  # the parsed name drops the Quadro
    assert Filters(gpu="q rtx").match(offer)  # the raw one keeps it
