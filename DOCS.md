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
5. the managed block in `~/.ssh/config` is current, and the forwarding mode it carries
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
| `--gpu TEXT` | GPU name contains this, spacing and case ignored (`a100`, `4090`, `rtx6000ada`) |
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

The `gpu` and `GB` columns, and the unheaded one between them, are the parsed name rather than
the provider's, so two rows of the same card differ where they actually differ. A column no row
fills is not shown: see **GPU names** below.

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

**When an offer is taken meanwhile.** A marketplace offer can be rented by somebody else
between the listing and the create. If you launched by **filters**, mlink moves to the next
offer that matched, up to three attempts, printing each one and its price; the fallbacks are
the offers that already passed your filters, so `--max-price` still binds. If you launched by
**row or id** you named that offer, and mlink exits 2 rather than renting a different one. Only
a provider that says plainly it created nothing lets the next be tried, which today means Vast;
anything ambiguous stops, because a maybe would risk paying for two machines.

---

## Working on a machine

### `mlink up [TARGET]`
Makes a machine ready:

1. writes its ssh stanza and waits until it accepts a login (a rejected key stops here, exit 3;
   Vast's base image needs five to ten minutes on its first boot)
2. loads your key into mlink's own agent, bound to this machine and to its hop to github.com
3. verifies agent forwarding on the machine (exit 4 if the box blocks it)
4. verifies GitHub answers **from the machine** through the forwarded agent (exit 4)
5. sets your git identity
6. runs `[provision]` commands, then the script (`--skip-provision` skips both)
7. clones each `[[repos]]` entry, or fetches and fast-forwards it, then runs `post_clone`
8. pins the machine to this project, records the deployed repos for `down`, prints a summary

Steps 2 to 4 depend on `ssh.forward_agent`; see **Agent forwarding** below. Under
`forward_agent = "never"` they are skipped and a private repo will fail to clone in step 7.

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

### `mlink push [TARGET]`
Copies each `[[sync]]` local path into its remote path with
`rsync -az --partial --filter=':- .gitignore'`; `--delete` removes remote files that are gone
locally. Every `.gitignore` in the tree is honoured, so a working tree goes up without its
virtualenv or its checkpoints, and a local path that does not exist is reported rather than
created.

The `[[sync]]` pairs travel both ways: `push` sends the local side up, `pull` brings the remote
side down. To send code rather than results, add a pair for it:

```toml
[[sync]]
remote = "~/research"
local = "."
```

That is also how to work on a machine with `forward_agent = "never"`, where the box cannot
reach GitHub at all.

### `mlink pull [TARGET]`
Copies each `[[sync]]` remote path into its local path with `rsync -az --partial`;
`--delete` removes local files that are gone remotely. There is no gitignore filter on the way
down, because a results directory is not a source tree. The machine needs rsync installed; `up`
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

The `$/hr` and `up` columns, and the burn line under the table, cover the machines **mlink
rented**: it records the offer's price and the moment of creation, so it can tell you what the
fleet costs per hour and what it has cost so far. A machine mlink merely adopted, from
`[[machines]]` or from `ls --refresh`, has no price it can honestly quote and is left blank and
counted separately. `--json` adds `price_hr`, `created`, `uptime` and `spend`.

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

## GPU names

Every provider spells a GPU differently. Prime writes `RTX6000Ada_48GB`, Vast writes
`RTX 6000Ada`, Verda writes `1x RTX 6000 Ada 48GB`, and all three mean the same card. mlink
parses each name into a model, a variant and a memory size, and shows and filters on that, so a
listing reads the same whichever provider a row came from:

| Provider says | `gpu` | `variant` | `GB` |
|---|---|---|---|
| `RTX6000Ada_48GB` | RTX 6000 | Ada | 48 |
| `RTX 6000Ada` | RTX 6000 | Ada | |
| `1x RTX 6000 Ada 48GB` | RTX 6000 | Ada | 48 |
| `Q RTX 8000` | RTX 8000 | | |
| `H100 SXM` plus `gpu_ram` | H100 | SXM | 80 |

Each field gets its own column in `mlink gpus`, because what separates two rows of the same card
is usually the variant or the memory, and that is worth being able to scan down. The variant
column carries no header, like the note column at the end of the row: it reads as a continuation
of the name beside it, and any word for it would be wider than the values it labels.

```
#  provider  gpu         GB  n  region        $/hr
1  prime     H100        80  1  us-central-3  2.35
2  vast      H100  NVL   94  1  Malaysia, MY  2.60
3  vast      H100  PCIE  80  1  Czechia, CZ   2.62
4  vast      H100  SXM   80  1  Japan, JP     2.69
```

A column no row in the listing fills is left out rather than standing empty, so a search for a
card with no variants is exactly as narrow as it was before the column existed:

```
#  provider  gpu   GB  n  region        $/hr
1  vast      L40S  45  1  Japan, JP     0.61
3  prime     L40S  48  1  us-central-2  0.82
7  verda     L40S  48  1                1.37  unavailable
```

`mlink ls` keeps the one column, since by then you know what you rented.

A Vast name never carries memory, so it is taken from the offer's own `gpu_ram`; where a name
does carry it the name wins, because that is the thing being sold. Brand words (`Tesla`,
`Quadro`, Vast's `Q`) are dropped: they say who made it, not what it is. The count stays on the
offer, which every provider reports as a number; parsing a `8x` out of a Verda name only stops
it turning up in the model.

`--gpu` compares with the spacing and punctuation removed, and against the provider's own
wording as well as the parsed one. So `rtx6000ada`, `RTX 6000 Ada` and `rtx 6000ada` are one
question, and `q rtx` still finds what Vast calls `Q RTX 8000`. For Vast the same comparison
picks the catalogue names to send server-side, so a spacing-free query works there too.

Parsing never drops a row. A name the rules do not recognise keeps its whole self as the model,
lists normally and rents normally, and the raw string is always kept because it is what a
provider may want back at launch. `--json` carries it as `gpu`, with the parsed fields under
`gpu_parsed`.

One thing it does not paper over: Prime calls an RTX A6000 an `A6000_48GB` while Vast and Verda
call it `RTX A6000`, so `--gpu "rtx a6000"` misses Prime's rows. `--gpu a6000` finds all three.
Normalising that away would mean keeping a table of marketing names, which would be wrong within
a quarter.

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
| `ssh.forward_agent` | `constrained` | What a machine sees of your agent: `constrained`, `always` or `never` |
| `git.name`, `git.email` | | Set with `git config --global` on the box |
| `providers.prime.image` | the offer's first image | Prime pod image |
| `providers.vast.image` | `vastai/base-image:@vastai-automatic-tag` | Docker image; Vast adds sshd to it |
| `providers.vast.disk_gb` | `50` | Disk of a Vast instance |
| `providers.verda.image` | `ubuntu-24.04-cuda-12.6` | Verda image |
| `providers.verda.location` | `FIN-01` | Used when an offer names no location |
| `machines[].name`, `.host`, `.user`, `.port` | | Machines with a fixed address and no API |

A provider is enabled by the presence of its `[providers.<name>]` section; `init` writes the sections for the providers whose credentials it finds and leaves the others commented out.

---

## Agent forwarding

A machine reaches GitHub through a forwarded agent, because mlink never copies a key or a token
onto one. The forwarded agent is not the one you use yourself: mlink keeps a second agent at
`~/.config/mlink/agent.sock`, holding your identity with OpenSSH destination constraints that
permit exactly two hops, to each machine and from each machine to github.com. The constraints
are checked by the agent on your computer, against the host keys in
`~/.config/mlink/known_hosts` and `~/.ssh/known_hosts`, so a machine cannot claim a signature is
going somewhere it is not.

| Mode | What a machine can do with the socket | Cost |
|---|---|---|
| `constrained` (default) | Authenticate to github.com, from that machine, while you are connected | Needs OpenSSH 8.9+ locally **and** on the machine |
| `always` | Anything your agent can do, while you are connected | The old `ssh -A` exposure |
| `never` | Nothing | Private repos do not clone; use a public repo, or `[provision]` |

The stanza carries the mode as `ForwardAgent ~/.config/mlink/agent.sock`, `ForwardAgent yes` or
`ForwardAgent no`, so `ssh trainer`, git, rsync and VS Code Remote-SSH all get the same
treatment as mlink itself.

The key is loaded once and reloaded only when the set of machines changes, so a passphrase is
asked for at most once per agent. `mlink init` reports the mode and warns if the local ssh is
too old for `constrained`; `mlink up` refuses rather than downgrading silently. If `up` reaches
step 4 and GitHub denies the key, the machine's own ssh client is usually older than 8.9 and
cannot prove the second hop: check with `mlink ssh -- ssh -V`.

What `constrained` does **not** prevent: while you are connected, root on that machine can
authenticate to github.com as you, and therefore push to any repository you can write to. Only
`never` closes that, and a per-repository deploy key would, which mlink does not issue.

---

## Files

| Path | Contents |
|---|---|
| `~/.config/mlink/config.toml` | Who you are: ssh identity, git identity, providers, `[[machines]]` |
| `~/.config/mlink/.env` | Provider credentials for development; `chmod 600` |
| `~/.config/mlink/known_hosts` | Host keys of your machines, dropped when they go |
| `~/.config/mlink/agent.sock` | mlink's own ssh-agent: your key, bound to your machines and to github.com |
| `~/.config/mlink/agent.json` | The routes that agent's key is currently bound to |
| `~/.local/state/mlink/machines.json` | The registry: machines, their price and start time, per-project pointers |
| `~/.local/state/mlink/gpus.json` | The last `mlink gpus` listing, for row numbers |
| `~/.ssh/config` | One managed block, one `Host` stanza per machine; everything outside the markers is untouched |
| `~/.ssh/config.mlink.bak` | One-time backup, taken the first time the block is written |
| `mlink.toml` | Per project: repos, sync paths, provisioning |

See `examples/` for annotated copies of both configs.
