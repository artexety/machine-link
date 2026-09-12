"""mlink's own ssh-agent: the only agent a rented machine is ever shown.

Forwarding your normal agent to somebody else's hardware hands anyone with root there a socket
that signs as you, for anything, for as long as you stay connected. OpenSSH 8.9 can bind a key
to a route instead: mlink keeps a second agent holding the same identity, constrained to
`<machine> > github.com`, and forwards that one. Your own agent never leaves the laptop, and
the constraint is checked against real host keys by the agent here, so the machine cannot lie
about where a signature is going.

What remains is deliberate and documented: while you are connected, root on the machine can
authenticate to github.com as you. Closing that needs a per-repo deploy key, which needs a
GitHub token mlink does not ask for.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import remote
from .config import ALWAYS, CONSTRAINED, NEVER, Settings
from .models import Machine
from .sshconf import known_hosts
from .ui import Fail

#: Destination constraints, and the session-bind messages that prove a route, arrived in 8.9.
MIN_OPENSSH = (8, 9)
SOCKET = "~/.config/mlink/agent.sock"
GITHUB = "github.com"


def socket_path() -> Path:
    return Path(SOCKET).expanduser()


def _state_path() -> Path:
    return socket_path().with_name("agent.json")


def forward_value(mode: str) -> str:
    """What the machine's Host stanza puts after ForwardAgent. ssh expands the tilde itself."""
    return {CONSTRAINED: SOCKET, ALWAYS: "yes", NEVER: "no"}[mode]


def destination(machine: Machine) -> str:
    """A machine as known_hosts names it, which is how a constraint names it too."""
    return machine.host if machine.port == 22 else f"[{machine.host}]:{machine.port}"


def _known(dest: str, path: Path) -> bool:
    return remote.run(["ssh-keygen", "-F", dest, "-f", str(path)]).ok


def constraints(machines: list[Machine]) -> list[str]:
    """The routes the forwarded key may be used on: to each machine, and from it to GitHub.

    Both hops are needed. The agent tests the whole chain, so permitting `box>github.com`
    without permitting `box` refuses the signature.
    """
    routes = []
    for machine in machines:
        if machine.host and _known(dest := destination(machine), known_hosts()):
            routes += [dest, f"{dest}>{GITHUB}"]
    return routes


def openssh_version() -> tuple[int, int]:
    found = re.search(r"OpenSSH_(\d+)\.(\d+)", remote.run(["ssh", "-V"]).text)
    return (int(found[1]), int(found[2])) if found else (0, 0)


def _running(sock: Path) -> bool:
    """ssh-add answers 2 when it cannot reach an agent at all, 1 when the agent holds nothing."""
    return remote.run(["ssh-add", "-l"], env={"SSH_AUTH_SOCK": str(sock)}).code != 2


def _start(sock: Path) -> None:
    sock.unlink(missing_ok=True)  # a socket left behind by an agent that is gone
    if not remote.run(["ssh-agent", "-a", str(sock)]).ok:
        raise Fail(
            2,
            f"could not start mlink's ssh-agent at {SOCKET}",
            f'remove {sock} and retry, or set ssh.forward_agent = "always"',
        )


def ensure(machines: list[Machine], settings: Settings) -> None:
    """Load the identity into mlink's agent, bound to the machines whose host key we know.

    Called once a machine is reachable, because a constraint is written against a host key and
    a machine nobody has connected to yet has none.
    """
    if settings.ssh.forward_agent != CONSTRAINED:
        return
    if (version := openssh_version()) < MIN_OPENSSH:
        raise Fail(
            2,
            f"OpenSSH {version[0]}.{version[1]} cannot constrain a forwarded key "
            f"({MIN_OPENSSH[0]}.{MIN_OPENSSH[1]} added it)",
            'upgrade ssh, or set ssh.forward_agent = "always" in the config to forward '
            "unconstrained, which lets root on a machine authenticate as you while connected",
        )
    user_hosts = Path("~/.ssh/known_hosts").expanduser()
    if not _known(GITHUB, user_hosts):
        raise Fail(
            2,
            f"{GITHUB} is not in ~/.ssh/known_hosts, so the key cannot be bound to it",
            "run: mlink init",
        )
    routes = constraints(machines)
    if not routes:
        return
    sock = socket_path()
    sock.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if _running(sock):
        # Reloading costs a passphrase prompt, so it happens only when the routes changed.
        if _state_path().is_file() and json.loads(_state_path().read_text()) == routes:
            return
    else:
        _start(sock)  # a new agent holds nothing, whatever the state file remembers
    argv = ["ssh-add", "-H", str(known_hosts()), "-H", str(user_hosts)]
    for route in routes:
        argv += ["-h", route]
    result = remote.run([*argv, str(settings.ssh.key)], env={"SSH_AUTH_SOCK": str(sock)})
    if not result.ok:
        raise Fail(
            2,
            f"could not load {settings.ssh.identity_file} into mlink's agent: "
            f"{remote.short(result.text)}",
            "check the key and its passphrase, then retry",
        )
    _state_path().write_text(json.dumps(routes))
    _state_path().chmod(0o600)
