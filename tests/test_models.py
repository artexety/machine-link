import pytest

from machine_link.models import Machine, Offer, looks_like_target, parse_target, valid_name
from machine_link.ui import Fail


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("203.0.113.7", ("ubuntu", "203.0.113.7", 22)),
        ("root@203.0.113.7:2222", ("root", "203.0.113.7", 2222)),
        ("ssh ubuntu@203.0.113.7 -p 2222", ("ubuntu", "203.0.113.7", 2222)),
        ("ssh -p2222 -i ~/.ssh/key ubuntu@host.example", ("ubuntu", "host.example", 2222)),
        ("ssh -o StrictHostKeyChecking=no user@1.2.3.4", ("user", "1.2.3.4", 22)),
    ],
)
def test_parse_target_accepts_every_form(raw, expected):
    assert parse_target(raw, "ubuntu") == expected


@pytest.mark.parametrize("raw", ["", "-p 22", "user@", "[::1]", "host:99999", "host:abc"])
def test_parse_target_rejects_garbage(raw):
    with pytest.raises(Fail) as info:
        parse_target(raw, "ubuntu")
    assert info.value.code == 2


def test_looks_like_target_tells_addresses_from_names_and_commands():
    assert looks_like_target("user@host")
    assert looks_like_target("10.0.0.1")
    assert looks_like_target("ssh host")
    assert looks_like_target("gpu.example.org")
    assert not looks_like_target("trainer")
    assert not looks_like_target("nvidia-smi")
    assert not looks_like_target("cat ~/provisioned.txt")
    assert not looks_like_target("./run.sh")


@pytest.mark.parametrize("name", ["trainer", "gpu-1", "a.b_c", "x" * 63])
def test_valid_names(name):
    assert valid_name(name) == name


@pytest.mark.parametrize("name", ["", "-lead", "has space", "star*", "q?", "x" * 64])
def test_invalid_names_are_exit_2(name):
    with pytest.raises(Fail) as info:
        valid_name(name)
    assert info.value.code == 2


def test_machine_alias_is_prefixed_and_str_is_readable():
    machine = Machine(name="trainer", host="1.2.3.4", user="ubuntu", port=2222)
    assert machine.alias == "mlink-trainer"
    assert str(machine) == "trainer (ubuntu@1.2.3.4:2222)"


def test_offer_describe_mentions_everything_a_person_checks_before_paying():
    offer = Offer("verda", "1A100.22V", "A100", 2, "FIN-01", 0.895, spot=True)
    assert offer.describe() == "verda 2x A100 in FIN-01 at $0.90/hr (spot)"
    assert Offer("prime", "x", "A6000", 1, "", None).describe() == "prime A6000 at ?/hr"
