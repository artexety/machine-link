"""Invariants that must hold on every code path: no secret on a command line or in a URL."""

from machine_link import cli, sshconf
from tests.test_cli import CLEAN, mlink
from tests.test_providers import PRIME_ROUTES, VERDA_ROUTES

SECRETS = ("prime-secret", "vast-secret", "csecret", "PRIVATE KEY MATERIAL")


def test_no_key_material_or_secret_ever_reaches_a_command_line(settings, project, http, calls):
    http({**PRIME_ROUTES, **VERDA_ROUTES})
    calls.answer("printenv SSH_AUTH_SOCK", stdout="/tmp/agent\n")
    calls.answer("git@github.com", stdout="successfully authenticated\n")
    calls.answer("--mlink--", stdout=CLEAN)
    mlink("up", "ubuntu@203.0.113.7", "--name", "t")
    mlink("check")
    mlink("pull")
    mlink("gpus")
    mlink("ls", "--refresh")
    mlink("forget", "t")
    for argv in calls:
        joined = " ".join(argv)
        assert "id_ed25519" not in joined, argv
        assert not any(secret in joined for secret in SECRETS), argv
        assert "StrictHostKeyChecking=no" not in joined, argv


def test_credentials_travel_in_headers_never_in_urls(settings, project, http):
    fake = http({**PRIME_ROUTES, **VERDA_ROUTES})
    mlink("gpus")
    mlink("ls", "--refresh")
    assert fake.calls
    for _, url, _, body in fake.calls:
        assert not any(secret in url for secret in SECRETS), url
        if body and "client_secret" not in body:
            assert not any(secret in str(body) for secret in SECRETS)
    assert any(h.get("Authorization") == "Bearer prime-secret" for _, _, h, _ in fake.calls)


def test_nothing_is_destroyed_or_created_without_being_asked(settings, project, http, calls):
    fake = http({**PRIME_ROUTES, **VERDA_ROUTES})
    calls.answer("--mlink--", stdout=CLEAN)
    mlink("gpus")
    mlink("ls", "--refresh")
    mlink("launch", "1", "--dry-run")
    mutating = [(m, url) for m, url, *_ in fake.calls if m in ("POST", "PUT", "DELETE")]
    assert mutating and all(url.endswith("/oauth2/token") for _, url in mutating)


def test_the_stanza_never_disables_host_key_checking():
    machine = cli.Machine(name="x", host="h", user="u")
    assert "StrictHostKeyChecking=no" not in sshconf.stanza(machine, "~/.ssh/id_ed25519", bare=True)
