import os
import subprocess

import pytest

from machine_link import config
from machine_link.ui import Fail


def test_settings_parse_providers_and_static_machines(settings):
    assert list(settings.providers) == ["prime", "verda"]
    assert settings.git.name == "Ada"
    assert settings.ssh.pubkey.name == "id_ed25519.pub"
    (workstation,) = settings.machines
    assert (workstation.host, workstation.user, workstation.provider) == (
        "192.168.1.50",
        "alex",
        "static",
    )


def test_missing_config_is_exit_2_with_the_fix(isolated_home):
    with pytest.raises(Fail) as info:
        config.load_settings()
    assert info.value.code == 2
    assert "mlink init" in info.value.fix


def test_unknown_keys_are_reported_not_fatal(settings, capsys):
    settings.path.write_text('[ssh]\nusers = ["ubuntu"]\n[git]\nname = "x"\n')
    loaded = config.load_settings()
    assert loaded.git.name == "x"
    assert "unknown config key ssh.users" in capsys.readouterr().err


def test_static_machine_without_host_is_exit_2(settings):
    settings.path.write_text('[[machines]]\nname = "x"\n')
    with pytest.raises(Fail) as info:
        config.load_settings()
    assert info.value.code == 2


def test_env_file_fills_only_unset_variables(settings, monkeypatch):
    monkeypatch.setenv("PRIME_API_KEY", "from-shell")
    config.load_env(settings.path.parent / ".env")
    assert os.environ["PRIME_API_KEY"] == "from-shell"
    assert os.environ["VERDA_CLIENT_ID"] == "cid"


def test_env_file_can_be_pointed_elsewhere(settings, tmp_path, monkeypatch):
    other = tmp_path / "creds"
    other.write_text('export PRIME_API_KEY="quoted"\n# comment\n')
    other.chmod(0o600)
    monkeypatch.setenv("MLINK_ENV", str(other))
    monkeypatch.setenv("PRIME_API_KEY", "")
    config.load_env(settings.path.parent / ".env")
    assert os.environ["PRIME_API_KEY"] == "quoted"


def test_project_is_found_by_walking_up(project, monkeypatch):
    nested = project / "src" / "deep"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    loaded = config.load_project()
    assert loaded.path == project / "mlink.toml"
    assert loaded.dir == project
    assert [r.name for r in loaded.repos] == ["research"]
    assert loaded.sync[0].local == "~/runs"
    assert loaded.provision.commands == ["sudo apt-get install -y rsync"]


def test_no_project_means_nothing_to_deploy(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    loaded = config.load_project()
    assert loaded.path is None and loaded.repos == []


def test_empty_project_file_deploys_the_enclosing_repo(tmp_path, monkeypatch):
    (tmp_path / "mlink.toml").write_text("")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "git@github.com:acme/x.git\n", ""),
    )
    (repo,) = config.load_project().repos
    assert repo.url == "git@github.com:acme/x.git"
    assert repo.dest == "~/" + tmp_path.name


def test_repo_without_dest_is_exit_2(tmp_path, monkeypatch):
    (tmp_path / "mlink.toml").write_text('[[repos]]\nurl = "git@github.com:a/b.git"\n')
    monkeypatch.chdir(tmp_path)
    with pytest.raises(Fail) as info:
        config.load_project()
    assert info.value.code == 2


def test_invalid_toml_is_exit_2(tmp_path, monkeypatch):
    (tmp_path / "mlink.toml").write_text("[[repos]\n")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(Fail) as info:
        config.load_project()
    assert info.value.code == 2
