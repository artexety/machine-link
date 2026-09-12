"""Command behaviour: exit codes, gates, what gets written where, and what never happens."""

import json
import os
import sys
import time

import pytest
from typer.testing import CliRunner

from machine_link import cli, config, sshconf
from machine_link.registry import Registry
from machine_link.ui import Fail
from tests.conftest import CLEAN, DIRTY, PUBKEY, UNPUSHED
from tests.test_providers import PRIME_ROUTES, VAST_ROUTES, VERDA_ROUTES

runner = CliRunner()


def mlink(*args: str) -> tuple[int, str]:
    """Run a command; a Fail's exit code counts as the exit code, as it would in main()."""
    result = runner.invoke(cli.app, list(args))
    if isinstance(result.exception, Fail):
        return result.exception.code, result.stdout
    if result.exception and not isinstance(result.exception, SystemExit):
        raise result.exception
    return result.exit_code, result.stdout


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


# ---- help -------------------------------------------------------------------------------------


def test_help_lists_the_commands_in_lifecycle_order_without_truncating():
    result = runner.invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    listing = result.output.split("Commands:")[1].splitlines()
    names = [line.split()[0] for line in listing if line.startswith("  ") and line.strip()]
    assert names[:9] == ["init", "gpus", "launch", "up", "ssh", "push", "pull", "check", "down"]
    assert not any(line.rstrip().endswith("...") for line in listing)


# ---- init -------------------------------------------------------------------------------------


@pytest.fixture
def fresh_computer(isolated_home, calls, monkeypatch):
    """A key pair on disk and in the agent, GitHub happy, no config yet, no credentials."""
    key = isolated_home / ".ssh" / "id_ed25519"
    key.write_text("PRIVATE KEY MATERIAL")
    key.with_name("id_ed25519.pub").write_text(PUBKEY + "\n")
    calls.answer("ssh-keygen -lf", stdout="256 SHA256:abc local-comment (ED25519)\n")
    calls.answer("ssh-add -l", stdout="256 SHA256:abc local-comment (ED25519)\n")
    calls.answer("git@github.com", stdout="Hi ada! You've successfully authenticated\n")
    for name in ("PRIME_API_KEY", "VERDA_CLIENT_ID", "VERDA_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    return calls


def test_init_turns_on_only_the_providers_it_has_credentials_for(fresh_computer, http, monkeypatch):
    monkeypatch.setenv("PRIME_API_KEY", "prime-secret")
    fake = http(PRIME_ROUTES)
    assert mlink("init", "--yes")[0] == 0
    written = config.settings_path().read_text()
    assert "\n[providers.prime]\n# image" in written and "\n# [providers.verda]\n# image" in written
    assert config.load_settings().providers == {"prime": {}}
    assert fake.sent("GET", "/ssh_keys/")  # the key was checked at the one provider that is on
    monkeypatch.delenv("PRIME_API_KEY")
    assert mlink("init", "--yes")[0] == 0  # a rerun diagnoses; it never rewrites the config
    assert config.settings_path().read_text() == written


def test_init_without_credentials_says_how_to_rent_later(fresh_computer):
    result = runner.invoke(cli.app, ["init", "--yes"])
    assert result.exit_code == 0
    assert config.load_settings().providers == {}
    assert "uncomment the provider" in result.output
    assert "mlink gpus" not in result.output


def test_init_needs_a_terminal_or_yes(fresh_computer):
    assert mlink("init")[0] == 2
    assert not config.settings_path().exists()


# ---- up ---------------------------------------------------------------------------------------


def test_up_registers_a_typed_target_and_talks_only_through_its_alias(settings, project, healthy):
    code, _ = mlink("up", "ubuntu@203.0.113.7:2222", "--name", "trainer")
    assert code == 0
    remote_calls = [  # 'ssh -V' asks the local binary its version; it connects to nothing
        argv for argv in healthy if argv[0] in ("ssh", "scp", "rsync") and argv[1:2] != ["-V"]
    ]
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
    calls.answer("ssh -V", stderr="OpenSSH_9.6p1, LibreSSL 3.3.6\n")
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


def test_up_records_the_deployed_repos_so_down_and_check_work_from_anywhere(
    settings, project, healthy, http, rented, tmp_path, monkeypatch
):
    fake = http(PRIME_ROUTES)
    assert mlink("up")[0] == 0
    assert Registry().machines["ptest"].repos == ["~/research"]
    monkeypatch.chdir(tmp_path)  # no mlink.toml anywhere above here
    healthy.answers.clear()
    healthy.answer("--mlink--", stdout=DIRTY)
    assert mlink("check")[0] == 5
    assert mlink("down", "--yes")[0] == 5
    assert not fake.sent("DELETE", "/pods/0f0e0d0c0b0a09080706050403020100")
    healthy.answers.clear()
    healthy.answer("--mlink--", stdout=CLEAN)
    assert mlink("down", "--yes")[0] == 0
    assert fake.sent("DELETE", "/pods/0f0e0d0c0b0a09080706050403020100")
    inspected = [argv[-1] for argv in healthy.matching("--mlink--")]
    assert inspected and all(script.startswith("cd ~/research ") for script in inspected)


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
    code, output = mlink("gpus", "--cpu", "--json", "--limit", "1")
    assert json.loads(output)[0]["gpu"] == "CPU_NODE"  # the cheapest row once CPU nodes show


def test_renting_needs_a_configured_provider(settings, http):
    http({**PRIME_ROUTES, **VERDA_ROUTES})
    settings.path.write_text("[ssh]\n[providers.prime]\n")
    assert mlink("gpus", "--provider", "verda")[0] == 2  # known, but not configured
    assert mlink("gpus", "--provider", "nimbus")[0] == 2  # not a provider at all
    settings.path.write_text("[ssh]\n")
    assert mlink("gpus")[0] == 2
    assert mlink("launch", "--gpu", "a100", "--dry-run")[0] == 2


def test_gpus_and_launch_reach_vast_with_the_gpu_hint_and_accept_its_numeric_ids(settings, http):
    fake = http(VAST_ROUTES)
    settings.path.write_text("[ssh]\n[providers.vast]\n")
    code, output = mlink("gpus", "--gpu", "h100", "--gpu-count", "8", "--json")
    assert code == 0 and [r["id"] for r in json.loads(output)] == ["48480002"]
    assert fake.sent("POST", "/bundles/")[-1]["gpu_name"] == {"in": ["H100 NVL", "H100 SXM"]}
    assert mlink("launch", "48480002", "--dry-run")[0] == 0  # beyond the one row: an offer id
    assert fake.sent("POST", "/bundles/")[-1]["id"] == {"eq": 48480002}
    assert mlink("launch", "7", "--dry-run")[0] == 2  # neither a row nor an id


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
    result = runner.invoke(cli.app, ["down", "--yes"])
    assert isinstance(result.exception, Fail) and result.exception.code == 5
    assert "could not be inspected" in result.exception.message  # not "unpushed work"
    calls.answers.clear()
    calls.answer("--mlink--", stdout=UNPUSHED)
    result = runner.invoke(cli.app, ["down", "--yes"])
    assert "research has 1 unpushed commits" in result.exception.message


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


# ---- what a marketplace, a meter and a working tree need ---------------------------------------


def _with_vast(settings):
    settings.path.write_text(settings.path.read_text() + "[providers.vast]\n")


def _gone(body):
    raise Fail(1, 'Vast answered 400: {"error":"invalid_args","msg":"no_such_ask"}')


@pytest.fixture
def no_wait(monkeypatch):
    """These tests are about which offer gets taken, not about waiting for its address."""
    monkeypatch.setattr(cli, "_wait_for_address", lambda handler, machine, timeout: machine)


def test_a_taken_offer_falls_through_to_the_next_match(settings, project, http, no_wait):
    """Vast turns over in seconds; an offer that is gone is not a reason to stop."""
    _with_vast(settings)
    fake = http({**VAST_ROUTES, ("PUT", "/asks/48480001/"): _gone})
    code, _ = mlink("launch", "--provider", "vast", "--yes")
    assert code == 0
    assert fake.sent("PUT", "/asks/48480002/")  # the cheap one was taken, the next one was not
    assert [m.id for m in Registry().machines.values() if m.provider == "vast"] == ["31200003"]


def test_failover_never_reaches_past_the_filters_you_gave(settings, project, http):
    """The fallbacks are the offers that already matched, so --max-price still binds."""
    _with_vast(settings)
    fake = http({**VAST_ROUTES, ("PUT", "/asks/48480001/"): _gone})
    code, _ = mlink("launch", "--provider", "vast", "--max-price", "9", "--yes")
    assert code == 2  # the only other offer is $17.60/hr and was never a candidate
    assert not fake.sent("PUT", "/asks/48480002/")
    assert not [m for m in Registry().machines.values() if m.provider == "vast"]


def test_every_offer_taken_is_exit_2_with_nothing_created(settings, project, http):
    _with_vast(settings)
    routes = {**VAST_ROUTES}
    for ask in ("48480001", "48480002"):
        routes[("PUT", f"/asks/{ask}/")] = _gone
    http(routes)
    code, _ = mlink("launch", "--provider", "vast", "--yes")
    assert code == 2
    assert not [m for m in Registry().machines.values() if m.provider == "vast"]


def test_a_named_offer_is_never_swapped_for_another_one(settings, project, http):
    """You asked for that offer. Quietly renting a different one would be a surprise bill."""
    _with_vast(settings)
    http({**VAST_ROUTES, ("PUT", "/asks/48480002/"): _gone})
    mlink("gpus", "--provider", "vast")
    rows = json.loads(cli._rows_file().read_text())
    row = 1 + next(i for i, o in enumerate(rows) if o["id"] == "48480002")
    assert mlink("launch", str(row), "--yes")[0] == 2
    assert not [m for m in Registry().machines.values() if m.provider == "vast"]


def test_a_machine_mlink_rented_remembers_what_it_costs(settings, project, http, no_wait):
    http({**PRIME_ROUTES, **VERDA_ROUTES})
    mlink("gpus")
    assert mlink("launch", "1", "--name", "t", "--yes")[0] == 0
    machine = Registry().machines["t"]
    assert machine.price_hr == json.loads(cli._rows_file().read_text())[0]["price_hr"]
    assert machine.created and machine.uptime == "0m"
    assert machine.spend is not None and machine.spend < 0.01


def test_ls_shows_the_burn_rate_and_leaves_adopted_machines_unpriced(settings, project):
    registry = Registry()
    registry.add(
        cli.Machine(
            name="old",
            host="203.0.113.7",
            user="ubuntu",
            provider="vast",
            id="1",
            port=17760,
            price_hr=0.50,
            created="2026-09-12T00:00:00+00:00",
        ),
        project,
    )
    registry.save()
    code, output = mlink("ls")
    assert code == 0
    assert "0.50" in output and "$/hr" in output
    assert "1 machines" not in output  # it is on camera in the README demo
    assert "ubuntu@203.0.113.7:17760" in output  # the port rides with the host, not a column
    assert "alex@192.168.1.50 " in output  # ... and a default port is not spelled out
    entries = {m["name"]: m for m in json.loads(mlink("ls", "--json")[1])}
    assert entries["workstation"]["price_hr"] is None  # a [[machines]] box has no meter
    assert entries["workstation"]["spend"] is None and entries["workstation"]["uptime"] == ""
    assert entries["old"]["spend"] > 0 and entries["old"]["uptime"].endswith("m")


def test_push_sends_the_tree_up_without_what_git_ignores(
    settings, project, calls, rented, isolated_home
):
    (isolated_home / "runs").mkdir()
    code, _ = mlink("push")
    assert code == 0
    (argv,) = calls.matching("rsync")
    assert argv[:4] == ["rsync", "-az", "--partial", "--filter=:- .gitignore"]
    assert argv[-2:] == [str(isolated_home / "runs") + "/", "mlink-ptest:~/research/runs"]


def test_push_names_a_local_path_that_is_not_there_rather_than_creating_it(
    settings, project, calls, rented
):
    code, _ = mlink("push")  # 'local' is ~/runs and nothing made it
    assert code == 1
    assert not calls.matching("rsync")


def test_push_and_pull_are_the_same_pairs_in_opposite_directions(
    settings, project, calls, rented, isolated_home
):
    local = isolated_home / "runs"
    local.mkdir()
    mlink("push", "--delete")
    mlink("pull", "--delete")
    up, down = calls.matching("rsync")
    assert up[-2:] == [f"{local}/", "mlink-ptest:~/research/runs"]
    assert down[-2:] == ["mlink-ptest:~/research/runs/", str(local)]
    assert "--delete" in up and "--delete" in down
