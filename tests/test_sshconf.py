from machine_link import sshconf
from machine_link.models import Machine

KEY = "~/.ssh/id_ed25519"
SOCK = "~/.config/mlink/agent.sock"
TRAINER = Machine(name="trainer", host="203.0.113.7", user="ubuntu", port=2222)
BOX = Machine(name="box", host="203.0.113.8", user="root")


def test_stanza_carries_everything_mlink_relies_on():
    text = sshconf.stanza(TRAINER, KEY, SOCK, bare=True)
    assert text.startswith("Host trainer mlink-trainer\n")
    for line in (
        "HostName 203.0.113.7",
        "User ubuntu",
        "Port 2222",
        f"IdentityFile {KEY}",
        f"ForwardAgent {SOCK}",
        "ControlMaster auto",
        "UserKnownHostsFile ~/.config/mlink/known_hosts",
        "StrictHostKeyChecking accept-new",
    ):
        assert f"    {line}" in text


def test_default_port_is_not_written():
    assert "Port" not in sshconf.stanza(BOX, KEY, SOCK, bare=True)


def test_bare_alias_is_dropped_when_the_user_already_uses_the_name():
    text = "Host trainer\n    HostName elsewhere\n"
    block = sshconf.apply(text, [TRAINER], KEY, SOCK)
    assert "Host mlink-trainer\n" in block
    assert "Host trainer mlink-trainer" not in block
    assert sshconf.alias_for(TRAINER, _write(block)) == "mlink-trainer"


def test_apply_preserves_everything_outside_the_markers():
    before = "# mine\nHost *\n    AddKeysToAgent yes\n"
    first = sshconf.apply(before, [TRAINER], KEY, SOCK)
    assert first.startswith(before + "\n" + sshconf.BEGIN)
    second = sshconf.apply(first + "Host after\n    HostName x\n", [BOX], KEY, SOCK)
    assert second.startswith(before)
    assert second.endswith(sshconf.END + "\nHost after\n    HostName x\n")
    assert "mlink-trainer" not in second and "mlink-box" in second


def test_write_is_idempotent_and_backs_up_once(isolated_home):
    path = isolated_home / ".ssh" / "config"
    path.write_text("Host old\n    HostName 1.1.1.1\n")
    assert sshconf.write([TRAINER], KEY, SOCK, path) is True
    assert sshconf.write([TRAINER], KEY, SOCK, path) is False
    backup = path.with_name("config.mlink.bak")
    assert backup.read_text() == "Host old\n    HostName 1.1.1.1\n"
    assert path.stat().st_mode & 0o777 == 0o600
    assert sshconf.known_hosts().exists()
    assert sshconf.known_hosts().stat().st_mode & 0o777 == 0o600


def test_forget_host_closes_the_master_and_drops_both_key_forms(calls):
    sshconf.forget_host(TRAINER)
    assert ["ssh", "-O", "exit", "mlink-trainer"] in calls
    removed = [argv[2] for argv in calls if argv[:2] == ["ssh-keygen", "-R"]]
    assert removed == ["203.0.113.7", "[203.0.113.7]:2222"]
    assert all(argv[-1] == str(sshconf.known_hosts()) for argv in calls if argv[0] == "ssh-keygen")


def _write(text: str):
    path = sshconf.config_file()
    path.parent.mkdir(exist_ok=True)
    path.write_text(text)
    return path
