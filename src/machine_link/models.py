"""Machines, offers, and the target syntax every command accepts."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field

from .ui import Fail

STATIC = "static"
#: Names become ssh Host patterns, so they must be safe as one.
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")


def valid_name(name: str) -> str:
    if not NAME_RE.match(name or ""):
        raise Fail(
            2,
            f"invalid machine name {name!r}",
            "use letters, digits, dot, dash or underscore, starting with a letter or digit",
        )
    return name


@dataclass(slots=True)
class Machine:
    """One machine, wherever it came from: an ssh endpoint, plus enough to find it again."""

    name: str
    host: str
    user: str
    port: int = 22
    provider: str = STATIC
    id: str = ""
    gpu: str = ""
    region: str = ""
    status: str = ""
    #: Paths of the repos `up` deployed here, so `down` can check them from any directory.
    repos: list[str] = field(default_factory=list)

    @property
    def alias(self) -> str:
        """The Host pattern mlink itself always uses. The bare name is for people."""
        return "mlink-" + self.name

    def __str__(self) -> str:
        return f"{self.name} ({self.user}@{self.host}:{self.port})"


@dataclass(slots=True)
class Offer:
    """Something a provider will rent right now. `raw` keeps what `launch` has to send back."""

    provider: str
    id: str
    gpu: str
    gpu_count: int
    region: str
    price_hr: float | None
    available: bool = True
    spot: bool = False
    raw: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        return f"{self.gpu_count}x {self.gpu}" if self.gpu_count > 1 else self.gpu

    @property
    def price(self) -> str:
        return f"${self.price_hr:.2f}/hr" if self.price_hr is not None else "?/hr"

    def describe(self) -> str:
        where = f" in {self.region}" if self.region else ""
        spot = " (spot)" if self.spot else ""
        return f"{self.provider} {self.label}{where} at {self.price}{spot}"


def looks_like_target(raw: str) -> bool:
    """Whether a word is an address rather than a machine name or a command."""
    if raw.startswith("ssh "):
        return True
    if any(ch in raw for ch in " /~"):
        return False  # a command line or a path, never an address
    return any(ch in raw for ch in "@.:")


def parse_target(raw: str, default_user: str) -> tuple[str, str, int]:
    """`user@host:port`, or a pasted `ssh user@host -p port` line, as (user, host, port)."""
    try:
        tokens = shlex.split(raw.strip())
    except ValueError as exc:
        raise Fail(
            2, f"cannot parse target {raw!r}: {exc}", "quote the connection string"
        ) from None
    if tokens and tokens[0] == "ssh":
        tokens = tokens[1:]
    user, host, port = "", "", 22
    words = iter(tokens)
    for token in words:
        if token == "-p":
            port = _port(next(words, ""))
        elif token.startswith("-p"):
            port = _port(token[2:])
        elif token in ("-i", "-l", "-o", "-F", "-J", "-L", "-R"):
            next(words, None)  # takes a value; not ours to interpret
        elif token.startswith("-"):
            continue
        elif not host:
            user, _, host = token.rpartition("@")
            if ":" in host:
                host, _, text = host.partition(":")
                port = _port(text)
    if not host or ":" in host or "[" in host:
        raise Fail(2, f"no usable host in target {raw!r}", "example: ubuntu@203.0.113.7:2222")
    return user or default_user, host, port


def _port(value: str) -> int:
    if not value.isdigit() or not 0 < int(value) < 65536:
        raise Fail(2, f"invalid port {value!r}", "ports are numbers between 1 and 65535")
    return int(value)
