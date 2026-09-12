"""Everything that runs through the system ssh, scp and rsync, always via the machine's alias.

Wrapping the binaries rather than a Python ssh library means behaviour matches the user's own
shell exactly, and the Host stanza mlink writes is the only ssh configuration there is.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Git, Project, Repo, Settings, Sync
from .models import Machine
from .ui import OPTS, Fail, err, step, warn

GITHUB_OK = "successfully authenticated"
#: A per-directory merge of every .gitignore in the tree, which macOS's openrsync honours too.
GITIGNORE = "--filter=:- .gitignore"
MARK = "--mlink--"
FIX_LOCAL_AGENT = "run 'mlink init' here; usually the local agent lost the key"
#: A constrained key needs the machine's own ssh client to prove the second hop, which is what
#: OpenSSH 8.9 added. An older image forwards the socket fine and is then refused by the agent.
CONSTRAINED_HINT = (
    "the machine's OpenSSH may be older than 8.9 and unable to use a route-bound key; "
    "check with 'mlink ssh -- ssh -V', then either use a newer image or set "
    'ssh.forward_agent = "always" in the config, which lets root on that machine '
    "authenticate as you while you are connected"
)


@dataclass(slots=True)
class Result:
    code: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.code == 0

    @property
    def text(self) -> str:
        return self.stdout + self.stderr


def run(
    argv: list[str],
    *,
    timeout: int | None = None,
    stream: bool = False,
    env: dict[str, str] | None = None,
) -> Result:
    """Run a local command. Output is captured, except that `stream` lets it through under -v."""
    if OPTS.verbose:
        err.print(f"$ {shlex.join(argv)}", style="dim", markup=False)
    live = stream and OPTS.verbose
    try:
        proc = subprocess.run(
            argv,
            capture_output=not live,
            text=True,
            timeout=timeout,
            env={**os.environ, **env} if env else None,
        )
    except FileNotFoundError:
        raise Fail(2, f"{argv[0]} is not on PATH", f"install {argv[0]} and retry") from None
    except subprocess.TimeoutExpired:
        return Result(124, "", "timed out")
    result = Result(proc.returncode, proc.stdout or "", proc.stderr or "")
    if OPTS.verbose and not live and result.text.strip():
        err.print(result.text.rstrip(), style="dim", markup=False)
    return result


def ssh(
    machine: Machine, command: str, *, timeout: int | None = None, stream: bool = False
) -> Result:
    argv = ["ssh", "-o", "BatchMode=yes", machine.alias, command]
    return run(argv, timeout=timeout, stream=stream)


def short(text: str, limit: int = 80) -> str:
    """The last line of some output, cut to fit on a checklist line."""
    lines = [line for line in text.strip().splitlines() if line.strip()]
    line = lines[-1].strip() if lines else ""
    return line if len(line) <= limit else line[: limit - 1] + "…"


# ---- local checks, used by init --------------------------------------------------------------


def agent_has_key(pubkey: Path) -> bool:
    parts = run(["ssh-keygen", "-lf", str(pubkey)]).stdout.split()
    return len(parts) > 1 and parts[1] in run(["ssh-add", "-l"]).text


def add_to_agent(key: Path) -> None:
    keychain = ["--apple-use-keychain"] if sys.platform == "darwin" else []
    run(["ssh-add", *keychain, str(key)])


def github_greets_locally() -> bool:
    argv = [
        "ssh",
        "-T",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "git@github.com",
    ]
    return GITHUB_OK in run(argv, timeout=15).text.lower()


# ---- the machine ---------------------------------------------------------------------------


def wait_reachable(machine: Machine, settings: Settings, key_hint: str) -> None:
    """Poll until the machine accepts a batch login. A rejected key aborts at once."""
    every = settings.ssh.connect_timeout
    argv = ["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={every}", machine.alias, "true"]
    deadline = time.monotonic() + settings.ssh.reachability_timeout
    while True:
        result = run(argv, timeout=every + 10)
        if result.ok:
            return
        if "permission denied" in result.text.lower():
            raise Fail(3, f"{machine.host} rejected the key", key_hint)
        if time.monotonic() >= deadline:
            raise Fail(
                3,
                f"{machine.host} was not reachable within {settings.ssh.reachability_timeout}s",
                "check that the machine is running and the port is right, then retry",
            )
        time.sleep(5)


def check_chain(machine: Machine, mode: str) -> None:
    """Agent forwarding works, and GitHub answers from the box through it.

    mlink never copies a key or token to a machine; the forwarded agent is the only way the box
    reaches GitHub, so without it there is nothing else to try.
    """
    if mode == "never":
        with step("agent forwarding on the machine") as st:
            st.note = 'off: ssh.forward_agent = "never"'
        return
    with step("agent forwarding on the machine") as st:
        if not ssh(machine, "printenv SSH_AUTH_SOCK").stdout.strip():
            raise Fail(
                4,
                "the machine has no forwarded agent socket",
                "use a machine whose sshd allows agent forwarding; mlink never copies keys",
            )
        st.note = mode
    with step("github.com greets from the machine"):
        command = "ssh -T -o StrictHostKeyChecking=accept-new git@github.com"
        if GITHUB_OK not in ssh(machine, command, timeout=30).text.lower():
            raise Fail(
                4,
                "forwarding works, but GitHub denied the key from the machine",
                CONSTRAINED_HINT if mode == "constrained" else FIX_LOCAL_AGENT,
            )


def set_git_identity(machine: Machine, git: Git) -> None:
    with step("git identity") as st:
        if not (git.name or git.email):
            st.note = "not configured, skipped"
            return
        pairs = (("name", git.name), ("email", git.email))
        commands = [f"git config --global user.{k} {shlex.quote(v)}" for k, v in pairs if v]
        result = ssh(machine, " && ".join(commands))
        if not result.ok:
            raise Fail(
                1,
                f"could not set the git identity: {short(result.text)}",
                "check [git] in the config",
            )
        st.note = f"{git.name} <{git.email}>"


def provision(machine: Machine, project: Project) -> None:
    for command in project.provision.commands:
        with step(short(command, 60)):
            result = ssh(machine, command, stream=True)
            if not result.ok:
                raise Fail(
                    1,
                    f"provision command failed: {command}\n  {short(result.text)}",
                    "run it over 'mlink ssh' to see the full output",
                )
    if project.provision.script:
        _run_script(machine, Path(project.provision.script).expanduser())


def _run_script(machine: Machine, script: Path) -> None:
    with step(f"script {script.name}"):
        if not script.is_file():
            raise Fail(2, f"provision.script {script} does not exist", "fix the path in mlink.toml")
        remote_path = (
            ssh(machine, "mktemp /tmp/mlink-script.XXXXXX").stdout.strip() or "/tmp/mlink-script"
        )
        if not run(["scp", str(script), f"{machine.alias}:{remote_path}"]).ok:
            raise Fail(1, f"could not copy {script} to the machine", "check the path and retry")
        result = ssh(machine, f"bash {remote_path}", stream=True)
        ssh(machine, f"rm -f {remote_path}")
        if not result.ok:
            raise Fail(
                1,
                f"provision script failed: {script}\n  {short(result.text)}",
                "run it over 'mlink ssh' to see the full output",
            )


def sync_repo(machine: Machine, repo: Repo) -> None:
    """Clone the repo, or fetch and fast-forward it when it is already there."""
    with step(f"repo {repo.name}") as st:
        if ssh(machine, f"test -d {repo.dest}/.git").ok:
            command = f"git -C {repo.dest} fetch --all --prune && git -C {repo.dest} pull --ff-only"
            pull = ssh(machine, command, stream=True)
            st.note = "fast-forwarded" if pull.ok else "pull --ff-only failed"
            if not pull.ok:
                warn(f"{repo.name}: {short(pull.text)}")
        else:
            branch = f" --branch {shlex.quote(repo.branch)}" if repo.branch else ""
            command = f"git clone{branch} {shlex.quote(repo.url)} {repo.dest}"
            clone = ssh(machine, command, timeout=1800, stream=True)
            if not clone.ok:
                raise Fail(
                    1,
                    f"clone of {repo.url} failed: {short(clone.text)}",
                    "run 'mlink init' to recheck the key, then retry",
                )
            st.note = "cloned"
        for command in repo.post_clone:
            result = ssh(machine, f"cd {repo.dest} && {command}", stream=True)
            if not result.ok:
                raise Fail(
                    1,
                    f"post_clone command failed: {command}\n  {short(result.text)}",
                    "run it over 'mlink ssh' to see the full output",
                )


@dataclass(slots=True)
class RepoState:
    name: str
    branch: str = "?"
    head: str = "?"
    dirty: bool = False
    unpushed: str = "?"

    @property
    def clean(self) -> bool:
        return not self.dirty and self.unpushed == "0"


def repo_state(machine: Machine, repo: Repo) -> RepoState:
    """Branch, HEAD, dirtiness and unpushed commit count of one repo on the machine."""
    script = (
        f"cd {repo.dest} 2>/dev/null || exit 9; "
        "git rev-parse --abbrev-ref HEAD; git rev-parse --short HEAD; "
        f"echo {MARK}; git status --porcelain; "
        f"echo {MARK}; git log --oneline @{{u}}.. 2>/dev/null || echo NOUPSTREAM"
    )
    result = ssh(machine, script)
    if result.code == 9:
        raise Fail(1, f"{repo.dest} is not on the machine", "run: mlink up")
    if not result.ok:
        raise Fail(
            1, f"could not read {repo.name} on the machine: {short(result.text)}", "run: mlink up"
        )
    head, status, log = (part.strip() for part in result.stdout.split(MARK))
    lines = head.splitlines()
    return RepoState(
        name=repo.name,
        branch=lines[0] if lines else "?",
        head=lines[1] if len(lines) > 1 else "?",
        dirty=bool(status),
        unpushed="?" if log == "NOUPSTREAM" else str(len(log.splitlines())),
    )


def pull(machine: Machine, mapping: Sync, *, delete: bool) -> Result:
    """Mirror the remote path's contents into the local one."""
    local = Path(mapping.local).expanduser()
    local.mkdir(parents=True, exist_ok=True)
    argv = ["rsync", "-az", "--partial", *(["--delete"] if delete else [])]
    return run([*argv, f"{machine.alias}:{mapping.remote.rstrip('/')}/", str(local)], stream=True)


def push(machine: Machine, mapping: Sync, *, delete: bool) -> Result:
    """Mirror the local path's contents into the remote one, minus whatever git ignores.

    The gitignore filter is what makes this usable on a working tree: it is the difference
    between sending your source and sending your virtualenv and your checkpoints. `pull` has
    no such filter, because a results directory is not a source tree.
    """
    local = str(Path(mapping.local).expanduser()).rstrip("/")
    argv = ["rsync", "-az", "--partial", GITIGNORE, *(["--delete"] if delete else [])]
    return run([*argv, f"{local}/", f"{machine.alias}:{mapping.remote}"], stream=True)
