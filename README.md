# machine-link

One command to prepare a rented or local machine for work.

```bash
mlink gpus --gpu a100 --max-price 2    # what your providers rent right now, cheapest first
mlink launch 1 --name trainer --up     # rent row 1, wait for it, clone your repo onto it
mlink ssh                              # or plain: ssh trainer
mlink pull                             # rsync results back
mlink down                             # refuses on unpushed work, then destroys it
```

## Highlights

- Rents GPUs from [Prime Intellect](https://www.primeintellect.ai) and [Verda](https://verda.com),
  or adopts any machine you can already ssh to.
- Wraps the system `ssh`, `scp` and `rsync`. No Python SSH library, no daemon, no agent on the box.
- Every machine becomes a `Host` alias in `~/.ssh/config`, so `ssh trainer` works in git, rsync
  and VS Code Remote-SSH.
- Reaches GitHub from the machine only through the forwarded agent. No key or token is ever
  copied anywhere.
- Refuses to destroy a machine that has uncommitted or unpushed work.
- Remembers which machine each project uses. Two projects, two boxes, no mix-ups.
- Depends on `typer` and `rich` only. Python 3.11+, macOS and Linux.

## Providers

| Provider | Rent | Notes |
|---|---|---|
| [Prime Intellect](https://www.primeintellect.ai) | yes | on-demand pods |
| [Verda](https://verda.com) | yes | on-demand and spot instances |
| Local | no | any machine with a fixed address, listed under `[[machines]]` |

More providers are planned. Adding one is a single module; see [Contributing](#contributing).

## Installation

```bash
uv tool install machine-link        # or: pipx install machine-link
uvx --from machine-link mlink --help  # try it without installing
```

Then, once per computer:

```bash
mlink init
```

`init` checks that your ssh key exists and is loaded in the agent, that GitHub greets it,
writes `~/.config/mlink/config.toml` and the managed block in `~/.ssh/config`, and registers
your public key at every configured provider. Rerun it any time; it is also the doctor.

Provider credentials go in the environment or in `~/.config/mlink/.env` (`chmod 600`); `init`
turns on the providers it finds credentials for:

```
PRIME_API_KEY=...            # app.primeintellect.ai > settings > API keys
VERDA_CLIENT_ID=...          # Verda console > Credentials > Cloud API Credentials
VERDA_CLIENT_SECRET=...
```

## Usage

### Rent a machine

```bash
mlink gpus                              # GPU rows only, every provider, cheapest first
mlink gpus --spot                       # Verda spot prices
mlink launch 3 --name trainer           # row 3 of the last listing
mlink launch 1A100.22V --region FIN-02  # or an offer id
mlink launch --gpu a6000 --up           # or the cheapest match, then run 'up'
```

`launch` prints the offer and its price and asks before spending anything; with no terminal it
refuses unless you pass `--yes`. The machine is registered the moment the provider returns an
id, so a hiccup never leaves a billing machine you do not know about.

### Prepare it

Put an `mlink.toml` at your project's root. An empty file means "clone this repository's
`origin` to `~/<name>` on the box". Add sections as needed:

```toml
[[repos]]
url = "git@github.com:you/research.git"
dest = "~/research"
post_clone = ["uv sync"]

[[sync]]                                  # for 'mlink pull'
remote = "~/research/runs"
local = "~/runs"

[provision]
commands = ["sudo apt-get install -y rsync tmux"]
script = ""                               # a local script, run on the box
```

```bash
mlink up                                # this project's machine
mlink up ubuntu@203.0.113.7 --name lab  # or any address, registered on the spot
```

`up` waits for the machine, verifies agent forwarding and that GitHub answers from the box,
sets your git identity, runs `[provision]`, then clones or fast-forwards the repos. It is
idempotent.

### Work, then let go

```bash
mlink ssh -- nvidia-smi        # one command, or an interactive session without arguments
mlink pull                     # rsync the [[sync]] paths back
mlink check                    # exit 5 if anything is uncommitted or unpushed
mlink down                     # the same check, a confirmation, then the machine is destroyed
```

The check covers every repo `up` deployed to the machine, whichever directory you run it
from. `down` treats a machine it cannot reach as unsafe. `--force` overrides the gate.
When a machine goes, so do its alias, its host key and its control connection; `mlink forget`
does the same without destroying anything.

## How it works

Each machine gets one stanza in the managed block of `~/.ssh/config`:

```
Host trainer mlink-trainer
    HostName 203.0.113.7
    User ubuntu
    IdentityFile ~/.ssh/id_ed25519
    ForwardAgent yes
    ControlMaster auto
    ControlPath ~/.ssh/mlink-%C
    ControlPersist 10m
    UserKnownHostsFile ~/.config/mlink/known_hosts
    StrictHostKeyChecking accept-new
```

Everything mlink does goes through that stanza, and so does anything else you point at the
name. Agent forwarding is scoped to your machines. All of `up` shares one multiplexed
connection. Host keys of rented machines live in mlink's own file and are deleted when the
machine is destroyed, so a recycled address never triggers a host-key warning. Everything
outside the block markers is left untouched; a one-time backup is kept at
`~/.ssh/config.mlink.bak`.

The pointer from a project to its machine lives in `~/.local/state/mlink/machines.json`.
Commands run outside a project fall back to the last machine used and say so; `down` never does.

## Configuration

`~/.config/mlink/config.toml`, written by `init`:

| Key | Default | Meaning |
|---|---|---|
| `ssh.identity_file` | `~/.ssh/id_ed25519` | The key; its `.pub` is registered at each provider |
| `ssh.default_user` | `ubuntu` | Remote user when a target names none |
| `ssh.connect_timeout` | `5` | Seconds per connection attempt |
| `ssh.reachability_timeout` | `600` | Seconds to wait for a fresh machine to accept ssh |
| `git.name`, `git.email` | | Set with `git config --global` on the box |
| `providers.prime.image` | the offer's first image | Prime pod image |
| `providers.verda.image` | `ubuntu-24.04-cuda-12.6` | Verda image |
| `providers.verda.location` | `FIN-01` | Used when an offer names no location |
| `machines[].name`, `.host`, `.user`, `.port` | | Machines with a fixed address and no API |

A provider is enabled by the presence of its `[providers.<name>]` section; `init` writes the
sections for the providers whose credentials it finds and leaves the others commented out.
Annotated copies of both files are in [examples/](examples/); every command and flag is in
[DOCS.md](DOCS.md).

## Exit codes

| Code | Meaning |
|---|---|
| 0 | success |
| 1 | unexpected error, or a remote command failed |
| 2 | local precondition or config problem; the fix is printed |
| 3 | machine unreachable, or it rejected the key |
| 4 | agent-forwarding chain broken |
| 5 | dirty or unpushed work |

## Development

```bash
uv sync --all-groups
uv run ruff check && uv run ruff format --check && uv run pytest
```

Tests never reach the network or a real ssh. Provider fixtures are payloads recorded from the
live APIs, and an autouse fixture redirects `$HOME` so no test can touch your own files.

## Contributing

Pull requests are welcome, providers especially. A provider is one module in
`src/machine_link/providers/` with five methods (`machines`, `offers`, `launch`, `terminate`,
`ensure_key`) and a section name in the config; `verda.py` is a complete example. Add tests
against recorded API payloads and run the checks above before opening a pull request.

## License

[MIT](LICENSE)
