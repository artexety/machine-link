"""What every provider answers, and the plumbing they share.

A provider is on when the machine config has a [providers.<name>] section. Credentials come
from the environment (or ~/.config/mlink/.env) and are sent in a header, never on a command
line or in a URL.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Protocol

from ..config import Settings
from ..models import Machine, Offer
from ..ui import OPTS, Fail, err


class Provider(Protocol):
    name: str
    #: What to do when a machine rejects the key.
    key_hint: str

    def machines(self) -> list[Machine]:
        """Every machine this account has, with an empty host while one is still provisioning."""

    def offers(self, *, spot: bool = False) -> list[Offer]:
        """What this provider will rent right now."""

    def launch(self, offer: Offer, name: str, *, spot: bool = False) -> Machine:
        """Create one machine. Only ever called after explicit confirmation."""

    def terminate(self, machine_id: str) -> None:
        """Destroy one machine. Only ever called after explicit confirmation."""

    def ensure_key(self) -> str:
        """The provider's id for the local public key, uploading it first when it is missing."""


def enabled(settings: Settings) -> list[Provider]:
    """Every configured provider, in config order."""
    from .prime import Prime
    from .verda import Verda

    kinds = {Prime.name: Prime, Verda.name: Verda}
    for name in settings.providers:
        if name not in kinds:
            raise Fail(
                2,
                f"unknown provider section [providers.{name}]",
                f"known providers: {', '.join(kinds)}",
            )
    return [kinds[name](settings) for name in settings.providers]


def get(settings: Settings, name: str) -> Provider:
    for provider in enabled(settings):
        if provider.name == name:
            return provider
    raise Fail(
        2,
        f"the {name} provider is not configured",
        f"add a [providers.{name}] section to {settings.path}",
    )


def secret(env_var: str, where: str) -> str:
    value = os.environ.get(env_var, "").strip()
    if not value:
        raise Fail(
            2,
            f"${env_var} is not set",
            f"export it or add it to ~/.config/mlink/.env; it comes from {where}",
        )
    return value


def pubkey_text(settings: Settings) -> str:
    path = settings.ssh.pubkey
    if not path.is_file():
        raise Fail(2, f"public key {path} does not exist", "run: mlink init")
    return path.read_text().strip()


def same_key(a: str, b: str) -> bool:
    """Two public keys are one key when type and body match; the comment may differ."""
    return len(a.split()) >= 2 and a.split()[:2] == b.split()[:2]


def key_name(pubkey: str) -> str:
    """The key's comment, which is usually an email or a hostname, else a plain 'mlink'."""
    parts = pubkey.split()
    return parts[2] if len(parts) > 2 else "mlink"


def number(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def http(
    method: str,
    url: str,
    headers: dict[str, str],
    body: dict | None = None,
    *,
    timeout: float = 30,
    who: str = "the provider",
):
    """One HTTP call. JSON comes back parsed, other bodies as text. Secrets are never logged."""
    if OPTS.verbose:
        err.print(f"$ {method} {url}", style="dim", markup=False)
    data = json.dumps(body).encode() if body is not None else None
    sent = {"Accept": "application/json", **headers}
    if data is not None:
        sent["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=sent)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace").strip()[:200]
        if exc.code in (401, 403):
            raise Fail(
                2,
                f"{who} rejected the credentials ({exc.code})",
                "check the key and its permissions",
            ) from None
        raise Fail(
            1,
            f"{who} answered {exc.code}: {detail or exc.reason}",
            "retry, or look at the provider console",
        ) from None
    except (urllib.error.URLError, TimeoutError) as exc:
        raise Fail(
            1, f"{who} could not be reached: {exc}", "check your connection and retry"
        ) from None
    try:
        return json.loads(text) if text.strip() else {}
    except ValueError:
        return text.strip()
