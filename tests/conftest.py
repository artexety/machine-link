"""Fixtures. The suite never touches the network, a real ssh, or the developer's own files."""

from __future__ import annotations

import pathlib
import re
import socket
import subprocess
from collections.abc import Callable

import pytest

from machine_link import config as config_mod
from machine_link.ui import OPTS

PUBKEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleKeyBody local-comment"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path_factory, monkeypatch):
    """No test may touch the real ~/.ssh/config, config or state directories.

    `mlink up` rewrites the managed ssh block as part of a normal run, so without this every
    test invoking it would edit the developer's own config.
    """
    home = tmp_path_factory.mktemp("home")
    (home / ".ssh").mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local" / "state"))
    monkeypatch.delenv("MLINK_CONFIG", raising=False)
    monkeypatch.delenv("MLINK_ENV", raising=False)
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: home))
    return home


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(self, address, *args, **kwargs):
        raise AssertionError(f"the suite tried to open a connection to {address!r}")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)


@pytest.fixture(autouse=True)
def default_options():
    OPTS.verbose, OPTS.config = False, None


class Calls(list):
    """Every argv handed to subprocess.run, plus canned answers matched on the command text."""

    def __init__(self):
        super().__init__()
        self.answers: list[tuple[str, subprocess.CompletedProcess]] = []

    def answer(self, needle: str, code: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.answers.append((needle, subprocess.CompletedProcess([], code, stdout, stderr)))

    def matching(self, needle: str) -> list[list[str]]:
        return [argv for argv in self if needle in " ".join(argv)]


@pytest.fixture
def calls(monkeypatch):
    recorded = Calls()

    def fake_run(argv, **kwargs):
        recorded.append(list(argv))
        joined = " ".join(argv)
        for needle, answer in recorded.answers:
            if needle in joined:
                return answer
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return recorded


class Http:
    """A fake transport: (method, path after the version prefix) -> answer, recording every call.

    A path with a query string falls back to the bare path, so paginated listings can be keyed
    without spelling their parameters out.
    """

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[tuple[str, str, dict, dict | None]] = []

    def __call__(self, method, url, headers, body=None, **kwargs):
        self.calls.append((method, url, headers, body))
        path = re.sub(r"^https?://[^/]+(/api)?/v[01]", "", url)
        answer = self.routes.get((method, path))
        if answer is None:
            answer = self.routes.get((method, path.split("?")[0]))
        if answer is None:
            raise AssertionError(f"unexpected call {method} {url}")
        return answer(body) if callable(answer) else answer

    def sent(self, method: str, path: str) -> list[dict | None]:
        return [body for m, url, _, body in self.calls if m == method and url.endswith(path)]


@pytest.fixture
def http(monkeypatch) -> Callable[[dict], Http]:
    """Install fake routes for both providers; returns the recorder."""

    def install(routes: dict) -> Http:
        from machine_link.providers import prime, vast, verda

        fake = Http(routes)
        monkeypatch.setattr(prime, "http", fake)
        monkeypatch.setattr(vast, "http", fake)
        monkeypatch.setattr(verda, "http", fake)
        return fake

    return install


@pytest.fixture
def settings(isolated_home, monkeypatch):
    """A machine config with both providers on, credentials in .env, and a key pair on disk."""
    key = isolated_home / ".ssh" / "id_ed25519"
    key.write_text("PRIVATE KEY MATERIAL")
    key.with_name("id_ed25519.pub").write_text(PUBKEY + "\n")
    conf = isolated_home / ".config" / "mlink"
    conf.mkdir(parents=True)
    (conf / "config.toml").write_text(
        '[ssh]\nidentity_file = "~/.ssh/id_ed25519"\n'
        '[git]\nname = "Ada"\nemail = "ada@example.com"\n'
        "[providers.prime]\n[providers.verda]\n"
        '[[machines]]\nname = "workstation"\nhost = "192.168.1.50"\nuser = "alex"\n'
    )
    (conf / ".env").write_text(
        "PRIME_API_KEY=prime-secret\nVAST_API_KEY=vast-secret\n"
        "VERDA_CLIENT_ID=cid\nVERDA_CLIENT_SECRET=csecret\n"
    )
    (conf / ".env").chmod(0o600)
    for name in ("PRIME_API_KEY", "VAST_API_KEY", "VERDA_CLIENT_ID", "VERDA_CLIENT_SECRET"):
        monkeypatch.setenv(name, "")  # restored after the test; .env fills it meanwhile
    return config_mod.load_settings()


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A project directory with a full mlink.toml, and the cwd inside it."""
    directory = tmp_path / "proj"
    directory.mkdir()
    (directory / "mlink.toml").write_text(
        '[[repos]]\nurl = "git@github.com:acme/research.git"\ndest = "~/research"\n'
        '[[sync]]\nremote = "~/research/runs"\nlocal = "~/runs"\n'
        '[provision]\ncommands = ["sudo apt-get install -y rsync"]\n'
    )
    monkeypatch.chdir(directory)
    return directory
