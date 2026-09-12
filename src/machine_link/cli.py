"""The mlink command line."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Annotated

import typer
from rich.panel import Panel
from rich.table import Table

from . import __version__, agent, config, providers, remote, sshconf
from .config import Project, Repo, Settings
from .models import STATIC, Filters, Machine, Offer, looks_like_target, now, valid_name
from .registry import Registry, state_dir, unique_name
from .ui import OPTS, Fail, err, out, report, say, step, warn

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    rich_markup_mode=None,
    help="One command to prepare a rented or local machine for work.",
    epilog="Global options go before the command: mlink -v up.",
)

TargetArg = Annotated[
    str | None,
    typer.Argument(
        metavar="TARGET",
        help="A machine name, user@host:port, or a pasted ssh line.",
        show_default=False,
    ),
]
NameOpt = Annotated[
    str | None,
    typer.Option("--name", metavar="NAME", help="Register the machine under this name."),
]
YesOpt = Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation.")]
DryRunOpt = Annotated[bool, typer.Option("--dry-run", help="Show the plan and call no provider.")]
JsonOpt = Annotated[bool, typer.Option("--json", help="Machine-readable output.")]
GpuOpt = Annotated[
    str, typer.Option("--gpu", metavar="TEXT", help="GPU name contains this (a100, 4090).")
]
RegionOpt = Annotated[str, typer.Option("--region", metavar="TEXT", help="Region contains this.")]
MaxPriceOpt = Annotated[
    float | None, typer.Option("--max-price", metavar="N", help="At most this many $/hr.")
]
CountOpt = Annotated[
    int,
    typer.Option("--gpu-count", metavar="N", help="At least this many GPUs.", show_default=False),
]
ProviderOpt = Annotated[
    str | None, typer.Option("--provider", metavar="NAME", help="Only this provider.")
]
SpotOpt = Annotated[
    bool, typer.Option("--spot", help="Spot (interruptible) pricing where offered.")
]


def _version(value: bool) -> None:
    if value:
        out.print(__version__)
        raise typer.Exit()


@app.callback()
def main_options(
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Show every command and its output.")
    ] = False,
    config_path: Annotated[
        str | None,
        typer.Option("--config", metavar="PATH", help="Machine config path.", show_default=False),
    ] = None,
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="Print the version.")
    ] = False,
) -> None:
    OPTS.verbose, OPTS.config = verbose, config_path


@dataclass
class State:
    settings: Settings
    project: Project
    registry: Registry


def _load() -> State:
    settings = config.load_settings(OPTS.config)
    registry = Registry()
    for machine in settings.machines:
        registry.machines.setdefault(machine.name, machine)
    return State(settings, config.load_project(), registry)


def _link(state: State) -> bool:
    """Keep ~/.ssh/config in step with the registry. Machines without an address yet are skipped."""
    machines = [m for m in state.registry.listed() if m.host]
    return sshconf.write(
        machines,
        state.settings.ssh.identity_file,
        agent.forward_value(state.settings.ssh.forward_agent),
    )


def _machine(state: State, target: str | None, *, name: str | None = None) -> Machine:
    """Resolve a target. A newly typed one is saved and gets its stanza before anything uses it."""
    known = set(state.registry.machines)
    machine = state.registry.resolve(
        target,
        state.settings.ssh.default_user,
        state.project.dir,
        name=valid_name(name) if name else "",
    )
    if machine.name not in known:
        state.registry.save()
        _link(state)
    if not machine.host:
        raise Fail(
            2,
            f"{machine.name} has no address yet",
            "run 'mlink ls --refresh' in a moment, then retry",
        )
    return machine


def _hint(state: State, machine: Machine) -> str:
    if machine.provider == STATIC:
        return f"ssh-copy-id -i {state.settings.ssh.pubkey} {machine.user}@{machine.host}"
    return providers.get(state.settings, machine.provider).key_hint


def _repos(state: State, machine: Machine) -> list[Repo]:
    """What to inspect on a machine: this project's repos, plus whatever `up` deployed there."""
    repos = list(state.project.repos)
    dests = {repo.dest for repo in repos}
    return repos + [Repo(url="", dest=d) for d in machine.repos if d not in dests]


def _confirm(action: str, yes: bool) -> None:
    """Gate an irreversible action. It never proceeds unattended without --yes."""
    if yes:
        return
    if not sys.stdin.isatty():
        raise Fail(
            2, f"refusing to {action} without confirmation", "rerun with --yes if you are sure"
        )
    if not typer.confirm(f"{action}?", default=False):
        say("cancelled")
        raise typer.Exit(0)


# ---- setting up ------------------------------------------------------------------------------


@app.command()
def init(
    generate: Annotated[
        bool, typer.Option("--generate", help="Create the ssh key if it is missing.")
    ] = False,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Accept the defaults; do not prompt.")
    ] = False,
) -> None:
    """Set this computer up.

    Checks the ssh key, the agent and GitHub, writes the config and the managed block in
    ~/.ssh/config, and registers the public key at every provider whose credentials are set.
    Rerun it any time; it is also the doctor.
    """
    path = config.settings_path(OPTS.config)
    fresh = not path.is_file()
    settings = Settings(path=path) if fresh else config.load_settings(OPTS.config)
    config.load_env(path.parent / ".env")
    if fresh:
        settings.providers = {name: {} for name in providers.with_credentials()}
        if not yes and not sys.stdin.isatty():
            raise Fail(
                2,
                "there is no terminal to answer the setup questions on",
                "rerun with --yes to take the defaults",
            )
    if fresh and not yes:
        settings.ssh.identity_file = typer.prompt("ssh identity file", settings.ssh.identity_file)
        settings.ssh.default_user = typer.prompt("default remote user", settings.ssh.default_user)
        settings.git.name = typer.prompt("git user.name", settings.git.name)
        settings.git.email = typer.prompt("git user.email", settings.git.email)

    with step(f"identity file {settings.ssh.identity_file}") as st:
        if not settings.ssh.key.is_file():
            if not generate:
                raise Fail(
                    2,
                    f"{settings.ssh.key} does not exist",
                    f"ssh-keygen -t ed25519 -f {settings.ssh.identity_file}, "
                    "or rerun with --generate",
                )
            remote.run(["ssh-keygen", "-t", "ed25519", "-f", str(settings.ssh.key), "-N", ""])
            st.note = "generated"
    with step("key loaded in ssh-agent", spin=False):  # ssh-add may ask for a passphrase
        if not remote.agent_has_key(settings.ssh.pubkey):
            remote.add_to_agent(settings.ssh.key)
            if not remote.agent_has_key(settings.ssh.pubkey):
                raise Fail(
                    2,
                    "the ssh-agent does not hold the key",
                    f"ssh-add {settings.ssh.identity_file}",
                )
    with step("github.com greets the key"):
        if not remote.github_greets_locally():
            err.print(settings.ssh.pubkey.read_text().strip())
            raise Fail(
                2,
                "GitHub does not know this key",
                "add the key printed above at github.com/settings/keys",
            )
    with step(f"config {path}") as st:
        if fresh:
            config.write_settings(settings)
        on = ", ".join(settings.providers) or "none"
        st.note = f"{'written' if fresh else 'present'}; providers: {on}"
    mode = settings.ssh.forward_agent

    state = State(settings, Project(), Registry())
    for machine in settings.machines:
        state.registry.machines.setdefault(machine.name, machine)
    with step("managed block in ~/.ssh/config") as st:
        _link(state)
        state.registry.save()
        st.note = f"{len(state.registry.machines)} machines, forward_agent {mode}"
    if mode == agent.CONSTRAINED and agent.openssh_version() < agent.MIN_OPENSSH:
        warn(
            "this OpenSSH cannot bind a forwarded key to a route (8.9 added it), so 'mlink up' "
            f'will stop and ask you to set ssh.forward_agent = "always" in {path}'
        )
    for provider in providers.enabled(settings):
        with step(f"public key registered at {provider.name}") as st:
            try:
                provider.ensure_key()
            except Fail as exc:
                st.fail(f"{exc.message}; {exc.fix}")
    with step("rsync on PATH") as st:
        if not shutil.which("rsync"):
            st.fail("missing; 'mlink pull' needs it")
    if not settings.providers:
        say(
            "no provider credentials found; to rent machines, put them in "
            f"{path.parent / '.env'} and uncomment the provider in {path}"
        )
    rent = "'mlink gpus' or " if settings.providers else ""
    say(f"next: cd into a project, touch mlink.toml, then {rent}'mlink up <target>'")


# ---- renting ---------------------------------------------------------------------------------


def _offers(state: State, only: str | None, spot: bool, filters: Filters) -> list[Offer]:
    """Every matching offer across the configured providers, available and cheapest first."""
    handlers = [providers.get(state.settings, only)] if only else providers.enabled(state.settings)
    if not handlers:
        raise Fail(
            2,
            "no provider is configured, so there is nothing to rent",
            f"add a [providers.prime], [providers.vast] or [providers.verda] section to "
            f"{state.settings.path}",
        )
    found: list[Offer] = []
    for provider in handlers:
        with step(f"offers from {provider.name}") as st:
            try:
                quoted = [o for o in provider.offers(filters, spot=spot) if filters.match(o)]
            except Fail as exc:
                st.fail(exc.message)
                continue
            found += quoted
            st.note = f"{len(quoted)} matching"
    return sorted(found, key=lambda o: (not o.available, o.price_hr is None, o.price_hr or 0.0))


#: How many taken offers to walk past before giving up, so a bad day cannot spend its way
#: down the whole listing.
ATTEMPTS = 3


def _rows_file() -> Path:
    return state_dir() / "gpus.json"


@app.command()
def gpus(
    gpu: GpuOpt = "",
    region: RegionOpt = "",
    max_price: MaxPriceOpt = None,
    gpu_count: CountOpt = 0,
    provider: ProviderOpt = None,
    spot: SpotOpt = False,
    cpu: Annotated[bool, typer.Option("--cpu", help="Include CPU-only instances.")] = False,
    limit: Annotated[int, typer.Option("--limit", metavar="N", help="Rows to show.")] = 20,
    as_json: JsonOpt = False,
) -> None:
    """What your providers rent right now.

    Cheapest first, with a row number for launch. CPU-only instances are hidden unless --cpu.
    """
    state = _load()
    found = _offers(state, provider, spot, Filters(gpu, region, max_price, gpu_count, cpu))
    shown = found[:limit]
    _rows_file().parent.mkdir(parents=True, exist_ok=True)
    _rows_file().write_text(json.dumps([asdict(o) for o in shown]))
    if as_json:
        out.print(
            json.dumps(
                [
                    {"row": i, **asdict(o), "gpu_parsed": asdict(o.card)}
                    for i, o in enumerate(shown, 1)
                ],
                indent=2,
            )
        )
        return
    if not shown:
        say(
            "nothing matched" + ("" if cpu else "; CPU-only instances are hidden, --cpu shows them")
        )
        return
    # The parsed name gets a column per field rather than one crowded one: what separates two
    # rows of the same card is usually the variant or the memory, and a column can be scanned.
    # The variant carries no header, like the note column at the end: it reads as a continuation
    # of the name beside it, and any word for it is wider than the values it would label. A
    # field no row in this listing fills gets no column at all.
    cards = [o.card for o in shown]
    variants = any(card.variant for card in cards)
    memories = any(card.memory_gb is not None for card in cards)
    table = Table(box=None, pad_edge=False)
    table.add_column("#", justify="right")
    table.add_column("provider")
    table.add_column("gpu")
    if variants:
        table.add_column("")
    if memories:
        table.add_column("GB", justify="right")
    table.add_column("n", justify="right")
    table.add_column("region")
    table.add_column("$/hr", justify="right")
    table.add_column("")
    for number, (o, card) in enumerate(zip(shown, cards, strict=True), 1):
        row = [str(number), o.provider, card.model]
        if variants:
            row.append(card.variant)
        if memories:
            row.append("" if card.memory_gb is None else str(card.memory_gb))
        row += [
            str(o.gpu_count),
            o.region,
            f"{o.price_hr:.2f}" if o.price_hr is not None else "?",
            "unavailable" if not o.available else ("spot" if o.spot else ""),
        ]
        table.add_row(*row, style=None if o.available else "dim")
    out.print(table)
    say(f"{len(shown)} of {len(found)}; launch one with: mlink launch <#> --name <name>")


@app.command()
def launch(
    offer: Annotated[
        str | None,
        typer.Argument(
            metavar="ROW|ID",
            help="A row number from 'mlink gpus', or an offer id.",
            show_default=False,
        ),
    ] = None,
    name: NameOpt = None,
    gpu: GpuOpt = "",
    region: RegionOpt = "",
    max_price: MaxPriceOpt = None,
    gpu_count: CountOpt = 0,
    provider: ProviderOpt = None,
    spot: SpotOpt = False,
    up_after: Annotated[bool, typer.Option("--up", help="Run 'up' once it is reachable.")] = False,
    yes: YesOpt = False,
    dry_run: DryRunOpt = False,
) -> None:
    """Rent a machine from an offer.

    A row of the last 'mlink gpus', an offer id, or the cheapest match of the filters. The
    machine is registered and gets its ssh alias the moment the provider returns an id.
    """
    state = _load()
    candidates = _pick(state, offer, Filters(gpu, region, max_price, gpu_count), provider, spot)[
        :ATTEMPTS
    ]
    out.print(f"{_name_for(state, candidates[0], name)}: {candidates[0].describe()}")
    if dry_run:
        say("dry run: nothing was created")
        return
    if len(candidates) > 1:
        say(f"{len(candidates) - 1} more matching offers, in case this one is taken meanwhile")
    _confirm("create it", yes)
    machine, handler = _create(state, candidates, name, spot)
    with step("waiting for an address") as st:
        machine = _wait_for_address(handler, machine, 3 * state.settings.ssh.reachability_timeout)
        state.registry.add(machine, state.project.dir)
        state.registry.save()
        _link(state)
        st.note = f"{machine.user}@{machine.host}"
    say(f"{machine.name} is registered; ssh {sshconf.alias_for(machine)}")
    if up_after:
        up(target=machine.name, name=None, skip_provision=False)


def _name_for(state: State, offer: Offer, name: str | None) -> str:
    # The model rather than the provider's whole string: a Verda default was once
    # "verda-1x-A100-SXM4-80GB", and the same card from Prime was named nothing like it.
    base = f"{offer.provider}-{offer.card.model}"
    return valid_name(name or unique_name(base, state.registry.machines))


def _create(
    state: State, candidates: list[Offer], name: str | None, spot: bool
) -> tuple[Machine, providers.Provider]:
    """Create the first candidate still on the market.

    A marketplace offer can be taken between the listing and the create, which is not a reason
    to stop when another offer matched the same filters. Only a provider that says plainly that
    it created nothing lets the next one be tried: a maybe would risk paying for two machines.
    """
    for index, chosen in enumerate(candidates):
        handler = providers.get(state.settings, chosen.provider)
        machine_name = _name_for(state, chosen, name)
        if index:
            out.print(f"{machine_name}: {chosen.describe()}")
        with step(f"create {machine_name} at {chosen.provider}") as st:
            try:
                machine = handler.launch(chosen, machine_name, spot=spot)
            except providers.Gone as exc:
                st.fail(exc.message)
                continue
            machine.price_hr, machine.created = chosen.price_hr, now()
            # Registered before anything else can go wrong: an unregistered machine still bills.
            state.registry.add(machine, state.project.dir)
            state.registry.save()
            st.note = machine.id
            return machine, handler
    raise Fail(
        2,
        f"all {len(candidates)} matching offers were taken while launching",
        "run 'mlink gpus' again; a busy marketplace turns over in seconds",
    )


def _pick(
    state: State, arg: str | None, filters: Filters, only: str | None, spot: bool
) -> list[Offer]:
    """A row of the last listing, an offer id, or every available match, cheapest first."""
    rows = json.loads(_rows_file().read_text()) if _rows_file().is_file() else []
    if arg and arg.isdigit() and 1 <= int(arg) <= len(rows):
        return [Offer(**rows[int(arg) - 1])]
    if arg:
        filters.id, filters.cpu = arg, True  # an explicit id may name a CPU instance
    offers = [o for o in _offers(state, only, spot, filters) if o.available]
    if offers:
        return offers[:1] if arg else offers  # you named this offer; no other one will do
    if arg:
        raise Fail(
            2,
            f"{arg!r} is neither a row of the last 'mlink gpus' listing ({len(rows)} rows) "
            "nor an available offer id",
            "run 'mlink gpus' again and pick a row",
        )
    raise Fail(2, "no available offer matches the filters", "run 'mlink gpus' to see what exists")


def _wait_for_address(handler: providers.Provider, machine: Machine, timeout: int) -> Machine:
    """Poll the provider until the fresh machine has an address. It stays registered either way."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for fresh in handler.machines():
            if fresh.id == machine.id and fresh.host:
                fresh.name = machine.name
                fresh.gpu, fresh.region = machine.gpu or fresh.gpu, machine.region or fresh.region
                fresh.price_hr, fresh.created = machine.price_hr, machine.created
                return fresh
        time.sleep(10)
    raise Fail(
        1,
        f"{machine.provider} gave {machine.name} no address within {timeout}s; it stays registered",
        f"run 'mlink ls --refresh' in a moment, then 'mlink up {machine.name}'",
    )


# ---- working on a machine ---------------------------------------------------------------------


@app.command()
def up(
    target: TargetArg = None,
    name: NameOpt = None,
    skip_provision: Annotated[
        bool, typer.Option("--skip-provision", help="Skip [provision].")
    ] = False,
) -> None:
    """Prepare a machine and clone the repos.

    Waits for it, verifies agent forwarding and GitHub from the machine, sets the git identity,
    runs [provision], then clones or fast-forwards each [[repos]] entry. Idempotent.
    """
    state = _load()
    started = time.monotonic()
    machine = _machine(state, target, name=name)
    if state.project.path:
        say(f"project {state.project.path}")
    else:
        say("no mlink.toml above the working directory; nothing will be deployed")
    _link(state)
    with step(f"reachable {machine.user}@{machine.host}:{machine.port}"):
        remote.wait_reachable(machine, state.settings, _hint(state, machine))
    # Only now: a route is bound to a host key, and a machine nobody has reached has none yet.
    with step("mlink's agent holds the key for this machine") as st:
        agent.ensure(state.registry.listed(), state.settings)
        st.note = state.settings.ssh.forward_agent
    remote.check_chain(machine, state.settings.ssh.forward_agent)
    remote.set_git_identity(machine, state.settings.git)
    if state.project.sync and not remote.ssh(machine, "command -v rsync").ok:
        warn(
            "the machine has no rsync, so 'mlink pull' cannot copy results back; "
            "add 'sudo apt-get install -y rsync' to [provision] commands"
        )
    if not skip_provision:
        remote.provision(machine, state.project)
    for repo in state.project.repos:
        remote.sync_repo(machine, repo)
    machine.repos = sorted({*machine.repos, *(repo.dest for repo in state.project.repos)})
    state.registry.add(machine, state.project.dir)
    state.registry.save()

    grid = Table.grid(padding=(0, 2))
    grid.add_row("machine", str(machine))
    grid.add_row("elapsed", f"{time.monotonic() - started:.1f}s")
    for repo in state.project.repos:
        repo_state = remote.repo_state(machine, repo)
        grid.add_row(repo.name, f"{repo_state.branch} @ {repo_state.head}")
    grid.add_row("next", f"mlink ssh {machine.name}  /  ssh {sshconf.alias_for(machine)}")
    out.print(Panel(grid, title="machine-link", expand=False))


@app.command(context_settings={"ignore_unknown_options": True, "allow_extra_args": True})
def ssh(ctx: typer.Context, target: TargetArg = None) -> None:
    """Open a session, or run one command.

    mlink ssh [TARGET] [-- COMMAND...]. Without --, the first word is the machine when it is a
    known name or an address; otherwise it starts the command.
    """
    state = _load()
    words = ([target] if target else []) + list(ctx.args)
    if "--" in sys.argv:  # explicit: everything after -- is the command, whatever it looks like
        command = sys.argv[sys.argv.index("--") + 1 :]
        target = words[0] if len(words) > len(command) else None
    elif words and (words[0] in state.registry.machines or looks_like_target(words[0])):
        target, command = words[0], words[1:]
    else:
        target, command = None, words
    machine = _machine(state, target)
    argv = ["ssh", machine.alias, *command]
    if OPTS.verbose:
        err.print(f"$ {shlex.join(argv)}", style="dim", markup=False)
    os.execvp("ssh", argv)


@app.command()
def push(
    target: TargetArg = None,
    delete: Annotated[
        bool, typer.Option("--delete", help="Delete remote files gone locally.")
    ] = False,
) -> None:
    """Rsync the [[sync]] paths up.

    The same pairs 'pull' brings down, travelling the other way. Anything your .gitignore files
    exclude stays here, so a working tree can be sent without its artefacts.
    """
    state = _load()
    if not state.project.sync:
        raise Fail(2, "no [[sync]] entries in mlink.toml", "add remote/local pairs, then retry")
    machine = _machine(state, target)
    failed = 0
    for mapping in state.project.sync:
        with step(f"push {mapping.local}") as st:
            local = Path(mapping.local).expanduser()
            if not local.is_dir():
                failed += 1
                st.fail(f"{local} is not a directory here")
                continue
            result = remote.push(machine, mapping, delete=delete)
            if result.ok:
                st.note = mapping.remote
                continue
            failed += 1
            st.fail(remote.short(result.text) or "rsync failed")
            _no_rsync(result)
    if failed == len(state.project.sync):
        raise Fail(1, "every sync mapping failed", "check the paths over 'mlink ssh'")


def _no_rsync(result: remote.Result) -> None:
    if "command not found" in result.text or "status 127" in result.text:
        raise Fail(
            1,
            "the machine has no rsync",
            "add 'sudo apt-get install -y rsync' to [provision] commands, then rerun 'mlink up'",
        )


@app.command()
def pull(
    target: TargetArg = None,
    delete: Annotated[
        bool, typer.Option("--delete", help="Delete local files gone remotely.")
    ] = False,
) -> None:
    """Rsync the [[sync]] paths back."""
    state = _load()
    if not state.project.sync:
        raise Fail(2, "no [[sync]] entries in mlink.toml", "add remote/local pairs, then retry")
    machine = _machine(state, target)
    failed = 0
    for mapping in state.project.sync:
        with step(f"pull {mapping.remote}") as st:
            result = remote.pull(machine, mapping, delete=delete)
            if result.ok:
                st.note = str(Path(mapping.local).expanduser())
                continue
            failed += 1
            st.fail(remote.short(result.text) or "rsync failed")
            _no_rsync(result)
    if failed == len(state.project.sync):
        raise Fail(1, "every sync mapping failed", "check the remote paths over 'mlink ssh'")


@app.command()
def check(target: TargetArg = None, as_json: JsonOpt = False) -> None:
    """Exit 5 on uncommitted or unpushed work.

    Inspects every repo 'up' deployed to the machine and this project's [[repos]]. This is the
    gate 'down' runs.
    """
    state = _load()
    machine = _machine(state, target)
    repos = _repos(state, machine)
    if not repos:
        raise Fail(
            2,
            f"no [[repos]] here and nothing was deployed to {machine.name} by 'mlink up'",
            "run it inside a project",
        )
    states = [remote.repo_state(machine, repo) for repo in repos]
    if as_json:
        out.print(json.dumps([asdict(s) for s in states], indent=2))
    else:
        table = Table(box=None, pad_edge=False)
        for column in ("repo", "branch", "dirty", "unpushed"):
            table.add_column(column)
        for s in states:
            table.add_row(s.name, s.branch, "yes" if s.dirty else "no", s.unpushed)
        out.print(table)
    if not all(s.clean for s in states):
        raise typer.Exit(5)


# ---- letting go -------------------------------------------------------------------------------


@app.command()
def down(
    target: TargetArg = None,
    yes: YesOpt = False,
    force: Annotated[
        bool, typer.Option("--force", help="Destroy even with dirty or unpushed work.")
    ] = False,
    keep: Annotated[
        bool, typer.Option("--keep", help="Leave the machine in the registry.")
    ] = False,
    dry_run: DryRunOpt = False,
) -> None:
    """Destroy a machine, after a safety check.

    Refuses on uncommitted or unpushed work, asks, terminates the machine at its provider, then
    forgets its alias, host key and address.
    """
    state = _load()
    # Inside a project only that project's machine (or a named one) may be destroyed.
    machine = state.registry.resolve(
        target,
        state.settings.ssh.default_user,
        state.project.dir,
        fallback=state.project.dir is None,
    )
    if machine.provider == STATIC or not machine.id:
        raise Fail(
            2,
            f"{machine.name} has no provider behind it, so it cannot be destroyed from here",
            f"drop it with: mlink forget {machine.name}",
        )
    handler = providers.get(state.settings, machine.provider)
    with step(f"checking {machine.name} for unpushed work") as st:
        unsafe = _unsafe(state, machine)
        if unsafe:
            st.fail(unsafe)
        else:
            st.note = "clean"
    if unsafe and not force:
        raise Fail(
            5,
            f"{machine.name}: {unsafe}",
            "push the work, or rerun with --force to destroy the machine anyway",
        )
    safe = not unsafe
    for other in state.registry.users_of(machine.name):
        if other != str(state.project.dir):
            warn(f"{other} is also using {machine.name}")
    out.print(
        f"destroy {machine} at {machine.provider}" + ("  [WORK WOULD BE LOST]" if not safe else "")
    )
    if dry_run:
        say("dry run: nothing was destroyed")
        return
    _confirm("destroy it", yes)
    with step(f"terminate {machine.name}") as st:
        handler.terminate(machine.id)
        st.note = "requested"
    if keep:
        return
    sshconf.forget_host(machine)
    state.registry.remove(machine.name)
    state.registry.save()
    _link(state)
    say(f"{machine.name} removed from the registry, ~/.ssh/config and known_hosts")


def _unsafe(state: State, machine: Machine) -> str:
    """Why destroying the machine would lose work, or "" when every deployed repo is pushed.

    A machine that cannot be inspected counts as unsafe: silence is not a clean tree.
    """
    repos = _repos(state, machine)
    if not repos:
        say(f"no [[repos]] here and nothing was deployed to {machine.name}; nothing to check")
        return ""
    if not machine.host:
        return "it has no address yet, so its work could not be inspected"
    try:
        states = [remote.repo_state(machine, repo) for repo in repos]
    except Fail as exc:
        return f"it could not be inspected ({remote.short(exc.message)})"
    reasons = [
        f"{s.name} has {'uncommitted changes' if s.dirty else f'{s.unpushed} unpushed commits'}"
        for s in states
        if not s.clean
    ]
    return "; ".join(reasons)


# ---- managing machines ------------------------------------------------------------------------


@app.command()
def ls(
    refresh: Annotated[
        bool, typer.Option("--refresh", "-r", help="Ask every configured provider first.")
    ] = False,
    as_json: JsonOpt = False,
) -> None:
    """The machines this client knows about.

    * marks the one this project uses. --refresh asks every provider first and drops the
    machines that are gone.
    """
    state = _load()
    if refresh:
        _refresh(state)
    machines = state.registry.listed()
    marked = state.registry.pinned(state.project.dir) or state.registry.machines.get(
        state.registry.current
    )
    if as_json:
        out.print(
            json.dumps(
                [
                    {
                        **asdict(m),
                        "current": m is marked,
                        "uptime": m.uptime,
                        "spend": m.spend,
                        "gpu_parsed": asdict(m.card),
                    }
                    for m in machines
                ],
                indent=2,
            )
        )
        return
    if not machines:
        say("no machines yet: 'mlink up <target>', 'mlink launch', or [[machines]] in the config")
        return
    table = Table(box=None, pad_edge=False)
    # The port rides along with the host rather than taking a column of its own: two more
    # columns would otherwise truncate the address on an 80-column terminal.
    for column in ("", "name", "provider", "user@host", "gpu", "status", "$/hr", "up"):
        table.add_column(column, justify="right" if column == "$/hr" else "left")
    for m in machines:
        table.add_row(
            "*" if m is marked else "",
            m.name,
            m.provider,
            f"{m.user}@{m.host or '?'}" + (f":{m.port}" if m.port != 22 else ""),
            m.card.label,
            m.status,
            "" if m.price_hr is None else f"{m.price_hr:.2f}",
            m.uptime,
        )
    out.print(table)
    say(_burn(machines))


def _burn(machines: list[Machine]) -> str:
    """What the fleet costs. Silent about machines mlink did not rent, which it cannot price."""
    rate = sum(m.price_hr or 0.0 for m in machines)
    spent = sum(m.spend or 0.0 for m in machines)
    priced = [m for m in machines if m.price_hr is not None]
    line = f"{len(machines)} machine" + ("s" if len(machines) != 1 else "")
    if priced:
        line += f", ${rate:.2f}/hr, ${spent:.2f} so far"
    if len(priced) < len(machines):
        line += f"; {len(machines) - len(priced)} with no price mlink knows"
    return line


def _refresh(state: State) -> None:
    """Ask every provider what it has. One being down must not hide the others."""
    found: dict[str, list[Machine]] = {}
    for provider in providers.enabled(state.settings):
        with step(f"query {provider.name}") as st:
            try:
                found[provider.name] = provider.machines()
                st.note = f"{len(found[provider.name])} machines"
            except Fail as exc:
                st.fail(exc.message)
    for gone in state.registry.refresh(found):
        sshconf.forget_host(gone)
    state.registry.save()
    _link(state)


@app.command()
def use(
    name: Annotated[
        str, typer.Argument(metavar="NAME", help="The machine to point this project at.")
    ],
) -> None:
    """Point this project at a machine.

    Outside a project, sets the default.
    """
    state = _load()
    machine = state.registry.machines.get(name)
    if machine is None:
        raise Fail(2, f"no machine named {name!r}", "run: mlink ls")
    state.registry.add(machine, state.project.dir)
    state.registry.save()
    say(f"{machine} is now current" + (f" for {state.project.dir}" if state.project.dir else ""))


@app.command()
def forget(
    name: Annotated[str, typer.Argument(metavar="NAME", help="The machine to drop.")],
) -> None:
    """Drop a machine without destroying it.

    Removes it from the registry, ~/.ssh/config and known_hosts.
    """
    state = _load()
    machine = state.registry.remove(name)
    sshconf.forget_host(machine)
    state.registry.save()
    _link(state)
    say(f"forgot {machine}")
    if any(m.name == name for m in state.settings.machines):
        say(f"it comes from [[machines]] in {state.settings.path}; remove it there too")


@app.command()
def link() -> None:
    """Rewrite the managed block in ~/.ssh/config."""
    state = _load()
    with step("managed block in ~/.ssh/config") as st:
        changed = _link(state)
        st.note = f"{len(state.registry.machines)} machines" + (
            "" if changed else ", already current"
        )
    for machine in state.registry.listed():
        say(f"  ssh {sshconf.alias_for(machine)}")


def main() -> None:
    try:
        app()
    except Fail as exc:
        report(exc)
        raise SystemExit(exc.code) from None
    except KeyboardInterrupt:
        err.print("interrupted", style="dim")
        raise SystemExit(130) from None
    except Exception as exc:
        if OPTS.verbose:
            raise
        report(Fail(1, f"{type(exc).__name__}: {exc}", "rerun with -v for the traceback"))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
