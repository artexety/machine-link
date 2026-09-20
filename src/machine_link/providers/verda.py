"""Verda (formerly DataCrunch). Contract: github.com/verda-cloud/sdk-python.

Two things differ from the usual REST shape: authentication is an OAuth2 client-credentials
exchange, and `POST /instances` answers with the new id as plain text rather than JSON.
Images log in as root.
"""

from __future__ import annotations

import re
import time

from ..config import Settings
from ..models import Filters, Machine, Offer
from ..ui import Fail
from . import http, key_name, number, pubkey_text, same_key, secret

API = "https://api.verda.com/v1"
WHERE = "the Verda console > Credentials > Cloud API Credentials"
CLIENT_ID, CLIENT_SECRET = "VERDA_CLIENT_ID", "VERDA_CLIENT_SECRET"
#: Verda renames its images: the old "ubuntu-24.04-cuda-12.6" was gone by 2026-09, and a name it
#: does not know is a 400 at create. Current names are in the console; a CUDA one is accepted on
#: CPU-only instance types too, so this single default covers both.
DEFAULT_IMAGE = "24.04.cuda12.9"
DEFAULT_LOCATION = "FIN-01"
#: Verda answers 403 to a delete while the instance is still provisioning, so a plain 'down'
#: right after a 'launch' would report bad credentials and leave the machine billing.
DELETE_TRIES, DELETE_WAIT = 6, 15


class Verda:
    name = "verda"
    secrets = (CLIENT_ID, CLIENT_SECRET)
    key_hint = (
        "Verda attaches keys when the instance is created; run 'mlink init' to register "
        "your key there, then recreate the instance"
    )

    def __init__(self, settings: Settings):
        self.settings = settings
        self.conf = settings.providers.get(self.name, {})
        self._token = ""

    def _call(self, method: str, path: str, body: dict | None = None):
        if not self._token:
            grant = {
                "grant_type": "client_credentials",
                "client_id": secret(CLIENT_ID, WHERE),
                "client_secret": secret(CLIENT_SECRET, WHERE),
            }
            self._token = http("POST", API + "/oauth2/token", {}, grant, who="Verda")[
                "access_token"
            ]
        headers = {"Authorization": f"Bearer {self._token}"}
        return http(method, API + path, headers, body, timeout=60, who="Verda")

    def machines(self) -> list[Machine]:
        return [
            Machine(
                name=item.get("hostname") or f"verda-{item['id'][:8]}",
                host=item.get("ip") or "",
                user="root",
                provider=self.name,
                id=item["id"],
                gpu=(item.get("gpu") or {}).get("description") or item.get("instance_type") or "",
                region=item.get("location") or "",
                status=str(item.get("status") or ""),
            )
            for item in self._call("GET", "/instances")
        ]

    def _locations(self) -> dict[str, tuple[str, str]]:
        """`FIN-02` is Verda's name for a rack, not for a place. Its own catalogue says which
        place, so mlink asks rather than keeping a table of codes that would go stale."""
        try:
            found = self._call("GET", "/locations")
        except Fail:
            return {}  # a listing is not worth failing over a place name
        return {
            entry["code"]: (entry.get("name") or entry["code"], entry.get("country_code") or "")
            for entry in found
            if entry.get("code")
        }

    def offers(self, filters: Filters, *, spot: bool = False) -> list[Offer]:
        """One offer per instance type and location with capacity; unstocked types listed once."""
        where: dict[str, list[str]] = {}
        query = "?is_spot=true" if spot else ""
        for entry in self._call("GET", "/instance-availability" + query):
            for kind in entry.get("availabilities") or []:
                where.setdefault(kind, []).append(entry["location_code"])
        places = self._locations()
        found = []
        for kind in self._call("GET", "/instance-types"):
            gpu = kind.get("gpu") or {}
            price = number(kind.get("spot_price" if spot else "price_per_hour"))
            for location in sorted(where.get(kind["instance_type"], [])) or [""]:
                site, country = places.get(location, ("", ""))
                found.append(
                    Offer(
                        provider=self.name,
                        id=kind["instance_type"],
                        gpu=gpu.get("description") or kind.get("name") or kind["instance_type"],
                        gpu_count=int(gpu.get("number_of_gpus") or 0),
                        region=location,
                        price_hr=price,
                        country=country,
                        site=site,
                        available=bool(location),
                        spot=spot,
                        raw={"location_code": location},
                    )
                )
        return found

    def launch(self, offer: Offer, name: str, *, spot: bool = False) -> Machine:
        location = offer.raw.get("location_code") or self.conf.get("location") or DEFAULT_LOCATION
        body = {
            "instance_type": offer.id,
            "image": self.conf.get("image") or DEFAULT_IMAGE,
            "ssh_key_ids": [self.ensure_key()],
            "hostname": _hostname(name),
            "description": "created by machine-link",
            "location_code": location,
            "is_spot": spot or offer.spot,
        }
        if disk := self.conf.get("disk_gb"):
            body["os_volume"] = {"name": f"{name}-os", "size": int(disk)}
        try:
            created = self._call("POST", "/instances", body)
        except Fail as exc:
            if "image" not in exc.message.lower():
                raise
            raise Fail(1, exc.message, self._image_fix()) from None
        return Machine(
            name=name,
            host="",
            user="root",
            provider=self.name,
            id=created if isinstance(created, str) else created["id"],
            gpu=offer.gpu,
            region=location,
            country=offer.country,
            site=offer.site,
            status="ordered",
        )

    def _image_fix(self) -> str:
        """Verda renames its images, so quote what it answers with now rather than guessing."""
        try:
            images = self._call("GET", "/images")
        except Fail:
            return "look the image names up in the Verda console"
        plain = sorted(i["image_type"] for i in images if i.get("category") == "ubuntu")
        names = ", ".join(plain or sorted(i["image_type"] for i in images))
        return f"set providers.verda.image to one of: {names}"

    def terminate(self, machine_id: str) -> None:
        for attempt in range(DELETE_TRIES):
            try:
                self._call("PUT", "/instances", {"id": [machine_id], "action": "delete"})
                return
            except Fail as exc:
                if exc.code != 2 or attempt == DELETE_TRIES - 1:  # 2 is the credentials refusal
                    raise
                time.sleep(DELETE_WAIT)

    def ensure_key(self) -> str:
        local = pubkey_text(self.settings)
        for key in self._call("GET", "/sshkeys"):
            if same_key(key.get("key") or "", local):
                return key["id"]
        created = self._call("POST", "/sshkeys", {"name": key_name(local), "key": local})
        return created if isinstance(created, str) else created["id"]


def _hostname(name: str) -> str:
    """Verda takes alphanumerics and dashes, under 60 of them; mlink also allows dot and _."""
    return re.sub(r"[^A-Za-z0-9-]", "-", name)[:59]
