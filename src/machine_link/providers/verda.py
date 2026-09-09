"""Verda (formerly DataCrunch). Contract: github.com/verda-cloud/sdk-python.

Two things differ from the usual REST shape: authentication is an OAuth2 client-credentials
exchange, and `POST /instances` answers with the new id as plain text rather than JSON.
Images log in as root.
"""

from __future__ import annotations

from ..config import Settings
from ..models import Machine, Offer
from . import http, key_name, number, pubkey_text, same_key, secret

API = "https://api.verda.com/v1"
WHERE = "the Verda console > Credentials > Cloud API Credentials"
DEFAULT_IMAGE = "ubuntu-24.04-cuda-12.6"
DEFAULT_LOCATION = "FIN-01"


class Verda:
    name = "verda"
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
                "client_id": secret("VERDA_CLIENT_ID", WHERE),
                "client_secret": secret("VERDA_CLIENT_SECRET", WHERE),
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

    def offers(self, *, spot: bool = False) -> list[Offer]:
        """One offer per instance type and location with capacity; unstocked types listed once."""
        where: dict[str, list[str]] = {}
        query = "?is_spot=true" if spot else ""
        for entry in self._call("GET", "/instance-availability" + query):
            for kind in entry.get("availabilities") or []:
                where.setdefault(kind, []).append(entry["location_code"])
        found = []
        for kind in self._call("GET", "/instance-types"):
            gpu = kind.get("gpu") or {}
            price = number(kind.get("spot_price" if spot else "price_per_hour"))
            for location in sorted(where.get(kind["instance_type"], [])) or [""]:
                found.append(
                    Offer(
                        provider=self.name,
                        id=kind["instance_type"],
                        gpu=gpu.get("description") or kind.get("name") or kind["instance_type"],
                        gpu_count=int(gpu.get("number_of_gpus") or 0),
                        region=location,
                        price_hr=price,
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
            "hostname": name,
            "description": "created by machine-link",
            "location_code": location,
            "is_spot": spot or offer.spot,
        }
        if disk := self.conf.get("disk_gb"):
            body["os_volume"] = {"name": f"{name}-os", "size": int(disk)}
        created = self._call("POST", "/instances", body)
        return Machine(
            name=name,
            host="",
            user="root",
            provider=self.name,
            id=created if isinstance(created, str) else created["id"],
            gpu=offer.gpu,
            region=location,
            status="ordered",
        )

    def terminate(self, machine_id: str) -> None:
        self._call("PUT", "/instances", {"id": [machine_id], "action": "delete"})

    def ensure_key(self) -> str:
        local = pubkey_text(self.settings)
        for key in self._call("GET", "/sshkeys"):
            if same_key(key.get("key") or "", local):
                return key["id"]
        created = self._call("POST", "/sshkeys", {"name": key_name(local), "key": local})
        return created if isinstance(created, str) else created["id"]
