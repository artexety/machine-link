# mlink command reference

Global options go **before** the command:

| Option | Meaning |
|---|---|
| `-v`, `--verbose` | Print every command, API call and its output |
| `--config PATH` | Machine config to use (default `~/.config/mlink/config.toml`, or `$MLINK_CONFIG`) |
| `--version` | Print the version |

A `TARGET` is a **machine name**, a `user@host:port`, or a pasted `ssh ...` line. Names win, so
a machine called `gpu1` is never mistaken for a hostname. A typed address is registered on the
spot. Omit the target and the command uses the machine this project is on.

---

## Setting up

### `mlink init`
Prepares this computer, and diagnoses it when rerun:

1. the ssh key exists (`--generate` creates an ed25519 one)
2. it is loaded in the agent (`ssh-add`, with the macOS keychain where available)
3. GitHub greets it (the public key is printed if not, so you can add it)
4. `~/.config/mlink/config.toml` exists (prompts for the values, `--yes` takes the defaults;
   the providers whose credentials are set are turned on)
5. the managed block in `~/.ssh/config` is current
6. the public key is registered at every configured provider, uploading it when missing
7. `rsync` is on PATH

Nothing is ever overwritten: an existing config is left as it is. Without a terminal, `--yes`
is required.

---

## Renting

### `mlink gpus`
Everything your configured providers will rent right now, cheapest first, with a row number.

| Flag | Meaning |
|---|---|
| `--gpu TEXT` | GPU name contains this, case-insensitive (`a100`, `4090`) |
| `--max-price N` | At most this many $/hr |
| `--gpu-count N` | At least this many GPUs |
| `--region TEXT` | Region contains this (`fin`, `us-east`) |
| `--provider NAME` | Only this provider |
| `--spot` | Spot or interruptible pricing: Verda, Vast |
| `--cpu` | Include CPU-only instances, hidden by default |
| `--limit N` | Rows to show (default 20) |
| `--json` | Machine-readable, with each row's id and provider record |

The rows are remembered, so a row number can stand in for an id in `launch`. Prime's region
column is the data center (`us-central-1`); Verda's is the location (`FIN-01`); Vast's is the
host's location (`US, TX`). Vast answers at most 64 offers per search, cheapest first, so narrow
it with `--gpu` or `--gpu-count`: the GPU fragment is matched against Vast's catalogue names and
sent along. With no provider configured, or an unknown `--provider`, it exits 2 and says what
to add.

### `mlink launch [ROW | ID]`
Creates a machine, registers it, writes its ssh alias.

```bash
mlink launch 1 --name trainer           # row 1 of the last 'mlink gpus'
mlink launch 1A100.22V --region FIN-02  # an offer id; filters narrow it down
mlink launch --gpu a6000 --max-price 1  # the cheapest available match
mlink launch 1 --up                     # ... and run 'up' once it is reachable
```

| Flag | Meaning |
|---|---|
| `--name NAME` | The machine's name (default: provider and GPU) |
| `--spot` | Spot instance where the provider supports it |
| `--up` | Run `mlink up` on it once it has an address |
| `--yes`, `-y` | Skip the confirmation |
| `--dry-run` | Print the resolved offer and price; call no provider |
| `--gpu`, `--region`, `--max-price`, `--gpu-count`, `--provider` | As in `gpus`, when no row is given |

A number is a row when the last listing has that many rows, otherwise an offer id; Vast's ids
are numbers. A Vast `--spot` launch bids the listed minimum price and can be outbid later.

The plan and its price are printed first. Without `--yes` it asks; outside a terminal it
refuses rather than proceeding. The machine is registered the moment the provider returns an
id, before the address wait, so nothing can bill unnoticed. The wait allows three times
`reachability_timeout` (45 minutes by default; some providers take a while). If it runs out,
the entry stays and `mlink ls --refresh` picks the address up later.

---

## Working on a machine

### `mlink up [TARGET]`
Makes a machine ready:

1. writes its ssh stanza and waits until it accepts a login (a rejected key stops here, exit 3;
   Vast's base image needs five to ten minutes on its first boot)
2. verifies agent forwarding on the machine (exit 4 if the box blocks it)
3. verifies GitHub answers **from the machine** through the forwarded agent (exit 4)
4. sets your git identity
5. runs `[provision]` commands, then the script (`--skip-provision` skips both)
6. clones each `[[repos]]` entry, or fetches and fast-forwards it, then runs `post_clone`
7. pins the machine to this project, records the deployed repos for `down`, prints a summary

`--name` names a machine given as an address. Provisioning output is shown with `-v`; on
failure the last line is printed either way.

### `mlink ssh [TARGET] [-- COMMAND...]`
```bash
mlink ssh                    # interactive
mlink ssh -- nvidia-smi -L   # one command
mlink ssh trainer -- htop
```
Everything after `--` is the command. Without `--`, the first word is the machine when it is a
known name or looks like an address (`user@host`, `10.0.0.1`, `gpu.example.org`); otherwise it
starts the command. Plain `ssh <name>` works from any tool too.

### `mlink pull [TARGET]`
Copies each `[[sync]]` remote path into its local path with `rsync -az --partial`;
`--delete` removes local files that are gone remotely. The machine needs rsync installed; `up`
warns when it is missing and the project has `[[sync]]` entries.

### `mlink check [TARGET]`
Exits 5 if any repo `up` deployed to the machine, or any `[[repos]]` entry of this project, has
uncommitted changes or unpushed commits. `--json` for scripts. This is the gate `down` runs.

---

## Managing machines

### `mlink ls`
The machines this client knows about; `*` marks the one this project uses (or, outside a
project, the last used). `--refresh` asks every configured provider first: new machines are
added, changed addresses updated, and machines a provider no longer lists are dropped along with
their host keys. A provider that cannot be reached is reported and skipped. `--json` for scripts.

### `mlink use NAME`
Points this project at a machine (or, outside a project, sets the default).

### `mlink link`
Rewrites the managed block in `~/.ssh/config` from the registry. Everything outside the markers
is preserved.

### `mlink forget NAME`
Drops a machine from the registry, `~/.ssh/config` and mlink's `known_hosts` **without
destroying it**. For boxes terminated elsewhere, or `[[machines]]` entries you no longer want
(remove them from the config too, or they come back).

### `mlink down [TARGET]`
Checks every repo `up` deployed to the machine for unpushed work, whichever directory you run it
from, confirms, destroys the machine at its provider, then forgets it.

| Flag | Meaning |
|---|---|
| `--yes`, `-y` | Skip the confirmation (required when not on a terminal) |
| `--force` | Destroy even with dirty or unpushed work |
| `--keep` | Leave the registry entry and alias in place |
| `--dry-run` | Print what would happen; call no provider |

Inside a project it acts on that project's machine or one you name, never on another project's
by fallback. It warns when other projects still point at the machine. Machines from
`[[machines]]` have no provider behind them and are refused; `forget` is for those.

---

## Exit codes

| Code | Meaning |
|---|---|
| 0 | success |
| 1 | unexpected error, or a remote command failed |
| 2 | local precondition or config problem; the fix is printed |
| 3 | machine unreachable, or it rejected the key |
| 4 | agent-forwarding chain broken |
| 5 | dirty or unpushed work found |

---

## Configuration

`~/.config/mlink/config.toml`, written by `init`:

| Key | Default | Meaning |
|---|---|---|
| `ssh.identity_file` | `~/.ssh/id_ed25519` | The key; its `.pub` is registered at each provider |
| `ssh.default_user` | `ubuntu` | Remote user when a target names none |
| `ssh.connect_timeout` | `5` | Seconds per connection attempt |
| `ssh.reachability_timeout` | `900` | Seconds to wait for a fresh machine to accept ssh; Vast's first boot takes 5-10 minutes |
| `git.name`, `git.email` | | Set with `git config --global` on the box |
| `providers.prime.image` | the offer's first image | Prime pod image |
| `providers.vast.image` | `vastai/base-image:@vastai-automatic-tag` | Docker image; Vast adds sshd to it |
| `providers.vast.disk_gb` | `50` | Disk of a Vast instance |
| `providers.verda.image` | `ubuntu-24.04-cuda-12.6` | Verda image |
| `providers.verda.location` | `FIN-01` | Used when an offer names no location |
| `machines[].name`, `.host`, `.user`, `.port` | | Machines with a fixed address and no API |

A provider is enabled by the presence of its `[providers.<name>]` section; `init` writes the sections for the providers whose credentials it finds and leaves the others commented out.

---

## Files

| Path | Contents |
|---|---|
| `~/.config/mlink/config.toml` | Who you are: ssh identity, git identity, providers, `[[machines]]` |
| `~/.config/mlink/.env` | Provider credentials for development; `chmod 600` |
| `~/.config/mlink/known_hosts` | Host keys of your machines, dropped when they go |
| `~/.local/state/mlink/machines.json` | The registry: machines and per-project pointers |
| `~/.local/state/mlink/gpus.json` | The last `mlink gpus` listing, for row numbers |
| `~/.ssh/config` | One managed block, one `Host` stanza per machine; everything outside the markers is untouched |
| `~/.ssh/config.mlink.bak` | One-time backup, taken the first time the block is written |
| `mlink.toml` | Per project: repos, sync paths, provisioning |

See `examples/` for annotated copies of both configs.
