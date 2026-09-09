"""The machines this client knows about, and which one each project is using."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, fields
from pathlib import Path

from .models import Machine, looks_like_target, parse_target, valid_name
from .ui import Fail, say

FIELDS = {f.name for f in fields(Machine)}


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or "~/.local/state"
    return Path(base).expanduser() / "mlink"


class Registry:
    """Machines are global and disposable; the pointer to "the one I am using" is per project.

    That is what lets two projects run on two boxes at once, and what stops `mlink ssh` in
    one project from quietly acting on another project's machine.
    """

    def __init__(self, path: Path | None = None):
        self.path = path or state_dir() / "machines.json"
        data = json.loads(self.path.read_text()) if self.path.is_file() else {}
        self.machines: dict[str, Machine] = {
            name: Machine(**{k: v for k, v in entry.items() if k in FIELDS})
            for name, entry in (data.get("machines") or {}).items()
        }
        self.projects: dict[str, str] = dict(data.get("projects") or {})
        self.current: str = str(data.get("current") or "")

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "current": self.current,
            "projects": dict(sorted(self.projects.items())),
            "machines": {name: asdict(m) for name, m in sorted(self.machines.items())},
        }
        self.path.write_text(json.dumps(payload, indent=2) + "\n")

    def listed(self) -> list[Machine]:
        return [self.machines[name] for name in sorted(self.machines)]

    def add(self, machine: Machine, project: Path | None = None) -> Machine:
        """Register or update a machine and make it current, for `project` too when given."""
        self.machines[valid_name(machine.name)] = machine
        self.current = machine.name
        if project is not None:
            self.projects[str(project)] = machine.name
        return machine

    def remove(self, name: str) -> Machine:
        machine = self.machines.pop(name, None)
        if machine is None:
            raise Fail(2, f"no machine named {name!r}", "run: mlink ls")
        # Never repoint at some other box: a stale pointer must fail loudly, not act elsewhere.
        if self.current == name:
            self.current = ""
        self.projects = {p: n for p, n in self.projects.items() if n != name}
        return machine

    def pinned(self, project: Path | None) -> Machine | None:
        """The machine this project is using, if it is still registered."""
        name = self.projects.get(str(project), "") if project is not None else ""
        return self.machines.get(name)

    def users_of(self, name: str) -> list[str]:
        return sorted(p for p, n in self.projects.items() if n == name)

    def resolve(
        self,
        raw: str | None,
        default_user: str,
        project: Path | None,
        *,
        fallback: bool = True,
        name: str = "",
    ) -> Machine:
        """A known machine by name, a target typed on the command line, or this project's machine.

        A typed target is registered on the spot, under `name` or one made from its host, because
        every command needs the machine's ssh stanza to exist before it can do anything at all.
        """
        if raw:
            if known := self.machines.get(raw):
                return known
            if not looks_like_target(raw):
                raise Fail(2, f"no machine named {raw!r}", "run: mlink ls")
            user, host, port = parse_target(raw, default_user)
            for machine in self.machines.values():
                if (machine.host, machine.port, machine.user) == (host, port, user):
                    return machine
            base = "box-" + host.replace(".", "-") if host[0].isdigit() else host.split(".")[0]
            fresh = Machine(
                name=name or unique_name(base, self.machines), host=host, user=user, port=port
            )
            self.machines[valid_name(fresh.name)] = fresh
            return fresh
        if machine := self.pinned(project):
            say(f"using {machine}")
            return machine
        if fallback and (machine := self.machines.get(self.current)):
            elsewhere = (
                f", last used elsewhere; 'mlink use {machine.name}' pins it here" if project else ""
            )
            say(f"using {machine}{elsewhere}")
            return machine
        raise Fail(
            2,
            "no machine is set for this project",
            "pass a name or target, or run: mlink use <name>",
        )

    def refresh(self, found: dict[str, list[Machine]]) -> list[Machine]:
        """Fold in what each reached provider reports. Returns the machines that turned out gone.

        Your name for a machine wins over the provider's. A machine a reached provider no longer
        lists is dropped: its address is about to belong to somebody else.
        """
        by_id = {(m.provider, m.id): m for m in self.machines.values() if m.id}
        seen: set[tuple[str, str]] = set()
        for provider, machines in found.items():
            for fresh in machines:
                seen.add((provider, fresh.id))
                if known := by_id.get((provider, fresh.id)):
                    if (known.host, known.status) != (fresh.host, fresh.status):
                        say(f"~ {known.name} is now {fresh.user}@{fresh.host} ({fresh.status})")
                    known.host, known.user, known.port = fresh.host, fresh.user, fresh.port
                    known.status, known.gpu = fresh.status, fresh.gpu or known.gpu
                    known.region = fresh.region or known.region
                else:
                    fresh.name = unique_name(fresh.name, self.machines)
                    self.machines[fresh.name] = fresh
                    say(f"+ {fresh.name} ({provider}, {fresh.status})")
        gone = [
            m
            for m in self.machines.values()
            if m.provider in found and m.id and (m.provider, m.id) not in seen
        ]
        for machine in gone:
            self.remove(machine.name)
            say(f"- {machine.name} is gone from {machine.provider}")
        return gone


def unique_name(base: str, taken: dict | set) -> str:
    """`base` made safe as an ssh Host alias and unique against `taken`."""
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip("-.")[:60] or "machine"
    for n in range(1, 1000):
        candidate = stem if n == 1 else f"{stem}-{n}"
        if candidate not in taken:
            return candidate
    return stem
