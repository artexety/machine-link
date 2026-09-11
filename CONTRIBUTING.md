# Contributing to machine-link

Thanks for taking the time. Bug fixes need no discussion: open a pull request. For a new provider, or a change to how machines are prepared, please open an issue first so the design can be settled before you write it.

## Development

```bash
uv sync --all-groups
uv run ruff check && uv run ruff format --check && uv run pytest
```

Those are the three commands CI runs, on macOS and Linux across Python 3.11 to 3.14. Run them before opening a pull request.

Tests never reach the network or a real ssh: provider fixtures are payloads recorded from the live APIs, and an autouse fixture redirects `$HOME` so no test can touch your own files. Please keep it that way.

Never paste a real address, machine id or account id from a live run into a fixture. Fixtures ship inside every sdist. Use the RFC 5737 documentation addresses (`203.0.113.x`, `198.51.100.x`) and placeholder ids.

## Adding a provider

A provider is one module in `src/machine_link/providers/`. It carries three attributes, `name`, `secrets` (the environment variables holding its credentials) and `key_hint` (what to tell someone whose key the provider rejected), and answers five methods:

| Method | Does |
|---|---|
| `machines` | Every machine this account has, with an empty host while one is still provisioning |
| `offers` | What it will rent right now. The `Filters` argument may narrow the search server-side; the caller applies it again, so ignoring it is correct too |
| `launch` | Create one machine from an offer, and return it the moment an id exists, so nothing can bill unnoticed |
| `terminate` | Destroy one machine |
| `ensure_key` | The provider's id for the local public key, uploading it first when it is missing |

`verda.py` is the shortest complete example. `vast.py` is the one to read when the API cannot be asked for everything at once: it caps a search at 64 offers, has no substring matching, and so has to translate `--gpu h100` into the exact catalogue names before it asks.

Three places know about a provider, and all three need the new name:

1. `kinds()` in `providers/__init__.py`, which maps the name to the class
2. `PROVIDER_SECTIONS` in `config.py`, the block `mlink init` writes into `~/.config/mlink/config.toml`
3. `examples/config.toml`, the annotated copy

A provider is switched on by the presence of its `[providers.<name>]` section, so a user with no credentials for it is never bothered by it.

## Releasing

The version comes from the git tag through hatch-vcs. There is no version to bump in a file, and no release commit:

```bash
git tag vX.Y.Z
git push origin vX.Y.Z
```

CI builds, publishes to PyPI and creates the GitHub release with the built files attached.

The publish workflow uses `skip-existing`, so re-tagging a version that already shipped silently does nothing. If you need to correct a release, give it a new version number.
