"""Command behaviour: exit codes, gates, what gets written where, and what never happens."""

import json
import os
import sys
import time

import pytest
from typer.testing import CliRunner

from machine_link import cli, sshconf
from machine_link.registry import Registry
from machine_link.ui import Fail
from tests.test_providers import PRIME_ROUTES, VERDA_ROUTES

runner = CliRunner()
CLEAN = "main\nabc1234\n--mlink--\n\n--mlink--\n"
DIRTY = "main\nabc1234\n--mlink--\n M train.py\n--mlink--\n"
UNPUSHED = "main\nabc1234\n--mlink--\n\n--mlink--\nabc1234 wip\n"


def mlink(*args: str) -> tuple[int, str]:
    """Run a command; a Fail's exit code counts as the exit code, as it would in main()."""
    result = runner.invoke(cli.app, list(args))
    if isinstance(result.exception, Fail):
        return result.exception.code, result.stdout
    if result.exception and not isinstance(result.exception, SystemExit):
        raise result.exception
    return result.exit_code, result.stdout


@pytest.fixture
def healthy(calls):
    """A machine that answers every check the way a good one does."""
    calls.answer("printenv SSH_AUTH_SOCK", stdout="/tmp/ssh-agent.sock\n")
    calls.answer("git@github.com", stdout="Hi ada! You've successfully authenticated\n")
    calls.answer("--mlink--", stdout=CLEAN)
    calls.answer("test -d", code=1)
    return calls


@pytest.fixture
def rented(settings, project):
    """A registered Prime pod pinned to the project."""
    registry = Registry()
    registry.add(
        cli.Machine(
            name="ptest",
            host="203.0.113.7",
            user="ubuntu",
            provider="prime",
            id="0f0e0d0c0b0a09080706050403020100",
        ),
        project,
    )
    registry.save()
    return registry


# ---- up ---------------------------------------------------------------------------------------


def test_up_registers_a_typed_target_and_talks_only_through_its_alias(settings, project, healthy):
    code, _ = mlink("up", "ubuntu@203.0.113.7:2222", "--name", "trainer")
    assert code == 0
    remote_calls = [argv for argv in healthy if argv[0] in ("ssh", "scp", "rsync")]
    assert all("mlink-trainer" in argv for argv in remote_calls)
    assert not any("203.0.113.7" in " ".join(argv) for argv in remote_calls)
    stanza = sshconf.config_file().read_text()
    assert (
        "Host trainer mlink-trainer\n    HostName 203.0.113.7\n    User ubuntu\n    Port 2222"
        in stanza
    )
    registry = Registry()
    assert registry.projects == {str(project): "trainer"}
    assert [c for c in healthy.matching("git clone")][0][-1].endswith(
        "git@github.com:acme/research.git ~/research"
    )
    assert healthy.matching("sudo apt-get install -y rsync")


def test_up_stops_at_the_forwarding_check_with_exit_4(settings, project, calls):
    calls.answer("git@github.com", stdout="Hi ada! You've successfully authenticated\n")
    code, _ = mlink("up", "ubuntu@203.0.113.7")
    assert code == 4
    assert not calls.matching("git clone") and not calls.matching("apt-get")


def test_up_reports_a_rejected_key_as_exit_3_with_the_providers_fix(
    settings, project, calls, rented
):
    calls.answer(" true", code=255, stderr="ubuntu@203.0.113.7: Permission denied (publickey).")
    result = runner.invoke(cli.app, ["up"])
    assert isinstance(result.exception, Fail)
    assert result.exception.code == 3 and "mlink init" in result.exception.fix


def test_up_without_a_project_deploys_nothing(settings, healthy, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code, _ = mlink("up", "ubuntu@203.0.113.7")
    assert code == 0
    assert not healthy.matching("git clone")


def test_a_machine_without_an_address_is_refused_everywhere_but_down_force(
    settings, project, calls, http
):
    fake = http(VERDA_ROUTES)
    registry = Registry()
    registry.add(cli.Machine(name="soon", host="", user="root", provider="verda", id="v1"), project)
    registry.save()
    assert mlink("up")[0] == 2
    assert mlink("check")[0] == 2
    assert mlink("pull")[0] == 2
    assert not sshconf.config_file().exists()  # no stanza without an address
    code, _ = mlink("down", "--yes")
    assert code == 5 and not fake.sent("PUT", "/instances")
    assert mlink("down", "--yes", "--force")[0] == 0
    assert fake.sent("PUT", "/instances") == [{"id": ["v1"], "action": "delete"}]
    assert not calls.matching("ssh-keygen")  # nothing to forget: it never had a host key


# ---- check, pull, ssh ----------------------------------------------------------------------


def test_check_exits_5_on_dirty_or_unpushed_work(settings, project, calls, rented):
    calls.answer("--mlink--", stdout=DIRTY)
    assert mlink("check")[0] == 5
    calls.answers.clear()
    calls.answer("--mlink--", stdout=UNPUSHED)
    assert mlink("check")[0] == 5
    calls.answers.clear()
    calls.answer("--mlink--", stdout=CLEAN)
    code, output = mlink("check", "--json")
    assert code == 0 and json.loads(output)[0]["unpushed"] == "0"


def test_pull_uses_rsync_through_the_alias_and_names_a_missing_rsync(
    settings, project, calls, rented
):
    code, _ = mlink("pull", "--delete")
    assert code == 0
    (argv,) = calls.matching("rsync")
    assert argv[:4] == ["rsync", "-az", "--partial", "--delete"]
    assert argv[4] == "mlink-ptest:~/research/runs/"
    calls.answer("rsync", code=127, stderr="bash: rsync: command not found")
    code, _ = mlink("pull")
    assert code == 1


def test_ssh_tells_a_command_from_a_target(settings, project, rented, calls, monkeypatch):
    executed = []
    monkeypatch.setattr(os, "execvp", lambda file, argv: executed.append(argv))
    mlink("ssh", "nvidia-smi", "-L")
    mlink("ssh", "ptest", "--", "nvidia-smi")
    mlink("ssh", "--", "cat ~/provisioned.txt; hostname")
    mlink("ssh")
    monkeypatch.setattr(sys, "argv", ["mlink", "ssh", "--", "make.sh"])  # an explicit -- wins
    mlink("ssh", "--", "make.sh")
    monkeypatch.setattr(sys, "argv", ["mlink", "ssh", "ptest", "--", "ls", "-la"])
    mlink("ssh", "ptest", "--", "ls", "-la")
    assert executed == [
        ["ssh", "mlink-ptest", "nvidia-smi", "-L"],
        ["ssh", "mlink-ptest", "nvidia-smi"],
        ["ssh", "mlink-ptest", "cat ~/provisioned.txt; hostname"],
        ["ssh", "mlink-ptest"],
        ["ssh", "mlink-ptest", "make.sh"],
        ["ssh", "mlink-ptest", "ls", "-la"],
    ]
    assert "cat" not in Registry().machines


# ---- ls, use, forget -----------------------------------------------------------------------


def test_ls_refresh_survives_one_provider_failing_and_forgets_gone_machines(
    settings, project, http, calls, rented
):
    routes = {**PRIME_ROUTES, **VERDA_ROUTES, ("GET", "/pods/?limit=100"): {"data": []}}
    routes[("POST", "/oauth2/token")] = lambda body: (_ for _ in ()).throw(
        Fail(2, "$VERDA_CLIENT_ID is not set")
    )
    http(routes)
    code, output = mlink("ls", "--refresh")
    assert code == 0
    assert "ptest" not in Registry().machines
    assert ["ssh-keygen", "-R", "203.0.113.7", "-f", str(sshconf.known_hosts())] in calls
    assert "mlink-ptest" not in sshconf.config_file().read_text()


def test_ls_marks_the_projects_machine_and_json_says_so(settings, project, rented):
    code, output = mlink("ls", "--json")
    assert code == 0
    entries = {m["name"]: m for m in json.loads(output)}
    assert entries["ptest"]["current"] is True
    assert entries["workstation"]["current"] is False  # from [[machines]] in the config


def test_use_and_forget(settings, project, rented, calls):
    assert mlink("use", "workstation")[0] == 0
    assert Registry().projects[str(project)] == "workstation"
    assert mlink("forget", "ptest")[0] == 0
    assert "ptest" not in Registry().machines
    assert calls.matching("ssh-keygen -R 203.0.113.7")
    assert mlink("use", "ptest")[0] == 2


# ---- gpus and launch -----------------------------------------------------------------------


def test_gpus_lists_cheapest_first_and_remembers_the_rows(settings, project, http):
    http({**PRIME_ROUTES, **VERDA_ROUTES})
    code, output = mlink("gpus", "--json", "--limit", "3")
    assert code == 0
    rows = json.loads(output)
    assert [(r["row"], r["provider"], r["price_hr"]) for r in rows] == [
        (1, "prime", 0.54),
        (2, "verda", 1.79),
        (3, "verda", 1.79),
    ]
    assert len(json.loads(cli._rows_file().read_text())) == 3
    code, output = mlink("gpus", "--provider", "verda", "--spot", "--gpu", "a6000", "--json")
    assert json.loads(output)[0]["price_hr"] == 0.305


def test_launch_refuses_unattended_and_creates_nothing_on_a_dry_run(settings, project, http):
    fake = http({**PRIME_ROUTES, **VERDA_ROUTES})
    mlink("gpus")
    assert mlink("launch", "1")[0] == 2
    assert mlink("launch", "1", "--dry-run")[0] == 0
    assert not fake.sent("POST", "/pods/") and not fake.sent("POST", "/instances")


def test_launch_registers_before_waiting_then_writes_the_alias(
    settings, project, http, monkeypatch
):
    pods = {
        "data": [
            {"id": "newpod", "name": "trainer", "status": "PROVISIONING", "sshConnection": None}
        ]
    }
    fake = http({**PRIME_ROUTES, **VERDA_ROUTES, ("GET", "/pods/?limit=100"): pods})
    seen_during_wait = []

    def sleep(seconds):
        seen_during_wait.append(Registry().machines["trainer"].host)
        pods["data"][0]["sshConnection"] = "ssh ubuntu@203.0.113.7 -p 2222"

    monkeypatch.setattr(time, "sleep", sleep)
    mlink("gpus")
    code, _ = mlink("launch", "1", "--name", "trainer", "--yes")
    assert code == 0
    assert seen_during_wait == [""]  # registered with no address before the first poll
    machine = Registry().machines["trainer"]
    assert (machine.host, machine.port, machine.id, machine.gpu) == (
        "203.0.113.7",
        2222,
        "newpod",
        "A6000_48GB",
    )
    assert Registry().projects == {str(project): "trainer"}
    assert "Host trainer mlink-trainer" in sshconf.config_file().read_text()
    assert fake.sent("POST", "/pods/")[0]["pod"]["dataCenterId"] == "us-central-1"


def test_launch_by_id_and_by_filters(settings, project, http):
    fake = http(
        {
            **PRIME_ROUTES,
            **VERDA_ROUTES,
            ("GET", "/pods/?limit=100"): {
                "data": [
                    {"id": "newpod", "status": "ACTIVE", "sshConnection": "ssh ubuntu@1.2.3.4"}
                ]
            },
        }
    )
    assert mlink("launch", "1A100.22V", "--region", "FIN-02", "--dry-run")[0] == 0
    assert mlink("launch", "--gpu", "a6000", "--max-price", "1", "--dry-run")[0] == 0
    assert mlink("launch", "gpu_8x_h100", "--dry-run")[0] == 2  # in the catalogue, but unavailable
    assert not fake.sent("POST", "/pods/")


# ---- down ------------------------------------------------------------------------------------


def test_down_refuses_unattended_and_on_unpushed_work(settings, project, http, calls, rented):
    fake = http(PRIME_ROUTES)
    calls.answer("--mlink--", stdout=CLEAN)
    assert mlink("down")[0] == 2
    calls.answers.clear()
    calls.answer("--mlink--", stdout=UNPUSHED)
    assert mlink("down", "--yes")[0] == 5
    assert not fake.sent("DELETE", "/pods/0f0e0d0c0b0a09080706050403020100")
    assert "ptest" in Registry().machines


def test_down_dry_run_destroys_nothing(settings, project, http, calls, rented):
    fake = http(PRIME_ROUTES)
    calls.answer("--mlink--", stdout=CLEAN)
    assert mlink("down", "--dry-run")[0] == 0
    assert not fake.calls or not fake.sent("DELETE", "/pods/0f0e0d0c0b0a09080706050403020100")


def test_down_force_terminates_forgets_the_host_and_unpins(settings, project, http, calls, rented):
    fake = http(PRIME_ROUTES)
    calls.answer("--mlink--", stdout=DIRTY)
    assert mlink("down", "--yes", "--force")[0] == 0
    assert fake.calls[-1][:2] == (
        "DELETE",
        "https://api.primeintellect.ai/api/v1/pods/0f0e0d0c0b0a09080706050403020100",
    )
    assert ["ssh", "-O", "exit", "mlink-ptest"] in calls
    assert calls.matching("ssh-keygen -R 203.0.113.7")
    assert "ptest" not in Registry().machines and Registry().projects == {}
    assert "mlink-ptest" not in sshconf.config_file().read_text()


def test_down_treats_an_unreachable_machine_as_unsafe(settings, project, http, calls, rented):
    http(PRIME_ROUTES)
    calls.answer("--mlink--", code=255, stderr="ssh: connect to host: Connection refused")
    assert mlink("down", "--yes")[0] == 5


def test_down_keeps_the_entry_when_asked(settings, project, http, calls, rented):
    http(PRIME_ROUTES)
    calls.answer("--mlink--", stdout=CLEAN)
    assert mlink("down", "--yes", "--keep")[0] == 0
    assert "ptest" in Registry().machines


def test_down_refuses_a_static_machine_and_another_projects_machine(
    settings, project, calls, rented, tmp_path, monkeypatch
):
    assert mlink("down", "workstation", "--yes")[0] == 2
    other = tmp_path / "other"
    other.mkdir()
    (other / "mlink.toml").write_text("")
    monkeypatch.chdir(other)
    code, _ = mlink("down", "--yes")
    assert code == 2  # ptest belongs to the first project; no silent fallback inside a project
