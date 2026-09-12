# machine-link

[![PyPI](https://img.shields.io/pypi/v/machine-link)](https://pypi.org/project/machine-link/)
[![CI](https://github.com/artexety/machine-link/actions/workflows/ci.yml/badge.svg)](https://github.com/artexety/machine-link/actions/workflows/ci.yml)

One command to prepare a rented or local machine for work.

<p align="center">
  <a href="https://raw.githubusercontent.com/artexety/machine-link/main/mlink-demo.gif"><img
    src="https://raw.githubusercontent.com/artexety/machine-link/main/mlink-demo.gif" width="100%"
    alt="mlink gpus lists live offers from three providers, mlink launch rents row 1 and prepares it, mlink ls shows what it is costing, mlink down refuses to destroy a machine holding unpushed commits"
  ></a>
</p>

It rents from [Prime Intellect](https://www.primeintellect.ai), [Vast.ai](https://vast.ai) and [Verda](https://verda.com), on demand from all three and spot or interruptible from Vast and Verda, or adopts any machine you can already ssh to. Nothing is installed on the box and there is no daemon: mlink wraps the system `ssh`, `scp` and `rsync`, and leaves the machine reachable by every tool that already speaks ssh.

```bash
mlink gpus --gpu a100 --max-price 2    # what your providers rent right now, cheapest first
mlink launch 1 --name trainer --up     # rent row 1, wait for it, clone your repo onto it
mlink ssh                              # or plain: ssh trainer
mlink push                             # rsync work up, minus whatever git ignores
mlink pull                             # rsync results back
mlink down                             # refuses on unpushed work, then destroys it
```

## Install

```bash
uvx --from machine-link mlink --help   # try it without installing
uv tool install machine-link           # or: pipx install machine-link
mlink init                             # once per computer; rerun any time, it is the doctor too
```

Python 3.11+ on macOS or Linux; `typer` and `rich` are the only dependencies.

`init` checks that your ssh key is loaded in the agent and that GitHub greets it, writes `~/.config/mlink/config.toml` and the managed block in `~/.ssh/config`, and registers your public key at every provider it finds credentials for. Those go in the environment or in `~/.config/mlink/.env` (`chmod 600`):

```
PRIME_API_KEY=...            # app.primeintellect.ai > settings > API keys
VAST_API_KEY=...             # cloud.vast.ai > Account > Keys
VERDA_CLIENT_ID=...          # Verda console > Credentials > Cloud API Credentials
VERDA_CLIENT_SECRET=...
```

A GitHub account is assumed: both `init` and `up` check that github.com accepts the key. Other git hosts are not supported yet.

## What to deploy

An `mlink.toml` at your project's root. An empty one means "clone this repository to `~/<name>` on the box"; add sections as you need them:

```toml
[[repos]]
url = "git@github.com:you/research.git"
dest = "~/research"
post_clone = ["uv sync"]

[[sync]]                                  # for 'mlink push' and 'mlink pull'
remote = "~/research/runs"
local = "~/runs"

[provision]
commands = ["sudo apt-get install -y rsync tmux"]
```

`mlink up` waits for the machine, verifies agent forwarding and that GitHub answers from the box, sets your git identity, runs `[provision]`, then clones or fast-forwards the repos. It is idempotent, so run it again whenever you change the file.

## How it works

Each machine becomes one stanza in the managed block of `~/.ssh/config`:

```
Host trainer mlink-trainer
    HostName 203.0.113.7
    User ubuntu
    IdentityFile ~/.ssh/id_ed25519
    ForwardAgent ~/.config/mlink/agent.sock
    ControlMaster auto
    ControlPath ~/.ssh/mlink-%C
    ControlPersist 10m
    UserKnownHostsFile ~/.config/mlink/known_hosts
    StrictHostKeyChecking accept-new
```

Everything mlink does goes through that stanza, and so does `ssh trainer` from git, rsync or VS Code Remote-SSH. The agent it forwards is not yours but mlink's own, holding your key bound to that machine and its hop to GitHub, so no key or token is ever copied anywhere and root on the box can do nothing else with the socket ([the tradeoff in full](https://github.com/artexety/machine-link/blob/main/DOCS.md#agent-forwarding)). Host keys of rented machines live in mlink's own file and are deleted with the machine, so a recycled address never triggers a warning. Each project remembers its own machine, so two projects never mix up boxes, and `down` refuses to destroy one holding uncommitted or unpushed work. A machine it cannot reach counts as unsafe.

Every command, flag and exit code is in [DOCS.md](https://github.com/artexety/machine-link/blob/main/DOCS.md); annotated copies of both config files are in [examples/](https://github.com/artexety/machine-link/tree/main/examples).

## Releases and Contributing

There is no fixed release cadence; a release goes out when there is something worth shipping. Please let me know if you encounter a bug by [filing an issue](https://github.com/artexety/machine-link/issues).

All contributions are appreciated. If you are contributing a bug fix, please do so without any further discussion. If you plan to add a provider, or to change how machines are prepared, please open an issue first and discuss it. A pull request sent without that might end up rejected, because the core may be heading somewhere you are not aware of.

To learn more about making a contribution, see [CONTRIBUTING.md](https://github.com/artexety/machine-link/blob/main/CONTRIBUTING.md), which also covers the development setup and how a release is cut.

## License

machine-link has an MIT license, as found in the [LICENSE](https://github.com/artexety/machine-link/blob/main/LICENSE) file.
