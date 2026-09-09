from pathlib import Path

import pytest

from machine_link.models import Machine
from machine_link.registry import Registry, unique_name
from machine_link.ui import Fail

A = Path("/work/a")
B = Path("/work/b")


def machine(name: str, provider: str = "static", **kw) -> Machine:
    return Machine(name=name, host=f"10.0.0.{len(name)}", user="ubuntu", provider=provider, **kw)


def test_round_trip_through_json():
    registry = Registry()
    registry.add(machine("gpu1", "prime", id="p1", gpu="A100"), A)
    registry.save()
    again = Registry()
    assert again.machines["gpu1"].gpu == "A100"
    assert again.projects == {str(A): "gpu1"} and again.current == "gpu1"


def test_old_registry_fields_are_ignored(isolated_home):
    registry = Registry()
    registry.path.parent.mkdir(parents=True)
    registry.path.write_text(
        '{"machines": {"x": {"name": "x", "host": "h", "user": "u", "labels": {}, "projects": []}}}'
    )
    assert Registry().machines["x"].host == "h"


def test_resolve_prefers_the_projects_own_machine_over_the_global_one():
    registry = Registry()
    registry.add(machine("one"), A)
    registry.add(machine("two"), B)
    assert registry.resolve(None, "ubuntu", A).name == "one"
    assert registry.resolve(None, "ubuntu", B).name == "two"
    assert registry.resolve(None, "ubuntu", None).name == "two"


def test_fallback_to_the_last_used_machine_is_refusable():
    registry = Registry()
    registry.add(machine("one"), A)
    assert registry.resolve(None, "ubuntu", B).name == "one"
    with pytest.raises(Fail) as info:
        registry.resolve(None, "ubuntu", B, fallback=False)
    assert info.value.code == 2


def test_typed_targets_are_registered_on_the_spot_under_a_derived_or_given_name():
    registry = Registry()
    fresh = registry.resolve("root@203.0.113.7:2222", "ubuntu", A)
    assert (fresh.name, fresh.user, fresh.port) == ("box-203-0-113-7", "root", 2222)
    assert registry.machines["box-203-0-113-7"] is fresh
    assert registry.resolve("ssh root@203.0.113.7 -p 2222", "ubuntu", A) is fresh
    named = registry.resolve("gpu.example.org", "ubuntu", A, name="lab")
    assert named.name == "lab" and named.host == "gpu.example.org"


def test_names_win_over_addresses_and_unknown_names_fail_clearly():
    registry = Registry()
    registry.add(machine("gpu1"))
    assert registry.resolve("gpu1", "ubuntu", None).name == "gpu1"
    with pytest.raises(Fail) as info:
        registry.resolve("nope", "ubuntu", None)
    assert "no machine named 'nope'" in info.value.message


def test_remove_clears_every_pointer_instead_of_repointing():
    registry = Registry()
    registry.add(machine("one"), A)
    registry.add(machine("two"), B)
    registry.remove("two")
    assert registry.current == "" and registry.projects == {str(A): "one"}
    with pytest.raises(Fail):
        registry.remove("two")


def test_refresh_adds_updates_and_drops_gone_machines(capsys):
    registry = Registry()
    registry.add(machine("mine", "prime", id="p1", status="active"), A)
    registry.add(machine("static-box"))
    fresh = [
        Machine(
            name="pod-name",
            host="5.5.5.5",
            user="ubuntu",
            provider="prime",
            id="p1",
            status="active",
        ),
        Machine(
            name="new", host="", user="ubuntu", provider="prime", id="p2", status="provisioning"
        ),
    ]
    gone_verda = machine("vbox", "verda", id="v1")
    registry.add(gone_verda)
    gone = registry.refresh({"prime": fresh, "verda": []})
    assert [m.name for m in gone] == ["vbox"]
    assert registry.machines["mine"].host == "5.5.5.5"  # renamed locally, updated from the provider
    assert registry.machines["new"].status == "provisioning"
    assert "static-box" in registry.machines  # not provider-owned, never touched
    assert registry.projects == {str(A): "mine"}
    assert "- vbox is gone from verda" in capsys.readouterr().err


def test_refresh_leaves_a_providers_machines_alone_when_it_was_not_reached():
    registry = Registry()
    registry.add(machine("vbox", "verda", id="v1"))
    assert registry.refresh({"prime": []}) == []
    assert "vbox" in registry.machines


def test_unique_name_is_alias_safe_and_unique():
    assert unique_name("verda-1x A100 SXM4 80GB", set()) == "verda-1x-A100-SXM4-80GB"
    assert unique_name("prime-A6000_48GB", {"prime-A6000_48GB"}) == "prime-A6000_48GB-2"
    assert unique_name("...", set()) == "machine"
