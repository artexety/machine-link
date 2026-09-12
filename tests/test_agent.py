"""What a rented machine is allowed to do with the agent it is shown."""

import json
from pathlib import Path

import pytest

from machine_link import agent, config, remote, sshconf
from machine_link.models import Machine
from machine_link.ui import Fail
from tests.test_cli import mlink

TRAINER = Machine(name="trainer", host="203.0.113.7", user="ubuntu")
PROXIED = Machine(name="vtest", host="ssh5.vast.ai", user="root", port=17760)
MODERN = "OpenSSH_9.6p1, LibreSSL 3.3.6\n"


@pytest.fixture
def modern(calls):
    """A machine whose ssh can bind a key to a route, and which knows every host key asked for."""
    calls.answer("ssh -V", stderr=MODERN)
    return calls


def test_a_constraint_names_a_machine_the_way_known_hosts_does():
    assert agent.destination(TRAINER) == "203.0.113.7"
    assert agent.destination(PROXIED) == "[ssh5.vast.ai]:17760"


def test_both_hops_are_constrained_because_the_agent_tests_the_whole_chain(modern):
    assert agent.constraints([TRAINER, PROXIED]) == [
        "203.0.113.7",
        "203.0.113.7>github.com",
        "[ssh5.vast.ai]:17760",
        "[ssh5.vast.ai]:17760>github.com",
    ]


def test_a_machine_whose_host_key_is_unknown_is_left_out_rather_than_failing(modern):
    modern.answer("ssh-keygen -F 203.0.113.7", code=1)
    assert agent.constraints([TRAINER, PROXIED]) == [
        "[ssh5.vast.ai]:17760",
        "[ssh5.vast.ai]:17760>github.com",
    ]


def test_ensure_starts_the_agent_and_loads_the_key_bound_to_each_route(settings, modern):
    modern.answer("ssh-add -l", code=2)  # nothing is listening on the socket yet
    agent.ensure([TRAINER], settings)
    assert modern.matching(f"ssh-agent -a {agent.socket_path()}")
    added = modern.matching("ssh-add -H")[0]
    assert added == [
        "ssh-add",
        "-H",
        str(sshconf.known_hosts()),
        "-H",
        str(Path("~/.ssh/known_hosts").expanduser()),
        "-h",
        "203.0.113.7",
        "-h",
        "203.0.113.7>github.com",
        str(settings.ssh.key),
    ]
    assert json.loads((agent.socket_path().with_name("agent.json")).read_text()) == [
        "203.0.113.7",
        "203.0.113.7>github.com",
    ]


def test_a_second_run_reloads_nothing_while_the_routes_are_the_same(settings, modern):
    agent.ensure([TRAINER], settings)
    modern.clear()
    agent.ensure([TRAINER], settings)
    assert not modern.matching("ssh-add -H")
    agent.ensure([TRAINER, PROXIED], settings)
    assert modern.matching("ssh-add -H")


def test_an_agent_that_died_gets_the_key_again_however_current_the_routes_look(
    settings, modern, monkeypatch
):
    """A restarted agent holds nothing, so the routes it last held are evidence of nothing.

    The telling detail is that a fresh agent answers 'running, no identities', which looks
    exactly like a healthy one to anything that only asks whether an agent is there.
    """
    agent.ensure([TRAINER], settings)  # loads the key and records the routes
    modern.clear()
    honest, started = remote.run, []

    def run(argv, **kwargs):
        joined = " ".join(argv)
        if joined.startswith("ssh-agent -a"):
            started.append(joined)
        if joined.startswith("ssh-add -l"):
            return remote.Result(1 if started else 2)  # gone, then up but holding nothing
        return honest(argv, **kwargs)

    monkeypatch.setattr(remote, "run", run)
    agent.ensure([TRAINER], settings)
    assert started, "the stale socket was not noticed"
    assert modern.matching("ssh-add -H"), "the key was not loaded into the new agent"


@pytest.mark.parametrize("mode", ["always", "never"])
def test_the_other_modes_never_touch_mlinks_agent(settings, modern, mode):
    settings.ssh.forward_agent = mode
    agent.ensure([TRAINER], settings)
    assert not modern.matching("ssh-add") and not modern.matching("ssh-agent")


def test_an_ssh_too_old_to_bind_a_route_refuses_instead_of_downgrading(settings, calls):
    calls.answer("ssh -V", stderr="OpenSSH_8.6p1, LibreSSL 3.3.6\n")
    with pytest.raises(Fail) as info:
        agent.ensure([TRAINER], settings)
    assert info.value.code == 2
    assert 'forward_agent = "always"' in info.value.fix
    assert not calls.matching("ssh-add -H")


def test_github_must_be_a_known_host_before_a_key_can_be_bound_to_it(settings, modern):
    modern.answer("ssh-keygen -F github.com", code=1)
    with pytest.raises(Fail) as info:
        agent.ensure([TRAINER], settings)
    assert info.value.code == 2 and "mlink init" in info.value.fix


def test_the_stanza_forwards_mlinks_own_agent_unless_asked_otherwise():
    assert agent.forward_value("constrained") == "~/.config/mlink/agent.sock"
    assert agent.forward_value("always") == "yes"
    assert agent.forward_value("never") == "no"


def test_an_unknown_mode_is_a_config_error(settings):
    settings.path.write_text('[ssh]\nforward_agent = "sometimes"\n')
    with pytest.raises(Fail) as info:
        config.load_settings(str(settings.path))
    assert info.value.code == 2 and "constrained, always, never" in info.value.fix


def test_never_mode_forwards_nothing_and_skips_the_chain_check(settings, project, healthy):
    settings.path.write_text(
        settings.path.read_text().replace("[git]", 'forward_agent = "never"\n[git]')
    )
    assert mlink("up", "ubuntu@203.0.113.7", "--name", "t")[0] == 0
    assert "    ForwardAgent no\n" in sshconf.config_file().read_text()
    assert not healthy.matching("printenv SSH_AUTH_SOCK")
    assert not healthy.matching("ssh-add")


def test_up_binds_the_key_before_it_trusts_the_chain(settings, project, healthy):
    assert mlink("up", "ubuntu@203.0.113.7", "--name", "t")[0] == 0
    order = [i for i, argv in enumerate(healthy) if argv[:2] == ["ssh-add", "-H"]]
    chain = [i for i, argv in enumerate(healthy) if "printenv SSH_AUTH_SOCK" in " ".join(argv)]
    assert order and chain and order[0] < chain[0]
    assert f"    ForwardAgent {agent.SOCKET}\n" in sshconf.config_file().read_text()
