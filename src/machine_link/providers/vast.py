"""Vast.ai, a marketplace of hosts. Contract: github.com/vast-ai/vast-python and docs.vast.ai.

Three things shape this module. A search answers at most 64 offers whatever limit is asked, so
the GPU filter has to reach the server; Vast matches names exactly, so the substring is first
turned into the catalogue names it occurs in. Instances are reached as root through Vast's ssh
proxy, a host such as ssh5.vast.ai with one port per instance. And a "bid" (interruptible)
offer is rented by naming a price; mlink bids the listed minimum.
"""

from __future__ import annotations

import json
from urllib.parse import urlencode

from ..config import Settings
from ..gpu import squash
from ..models import Filters, Machine, Offer
from ..ui import Fail
from . import Gone, http, number, pubkey_text, same_key, secret

API = "https://console.vast.ai/api"
WHERE = "cloud.vast.ai > Account > Keys"
KEY = "VAST_API_KEY"
DEFAULT_IMAGE = "vastai/base-image:@vastai-automatic-tag"
DEFAULT_DISK_GB = 50


def _gigabytes(megabytes: object) -> int | None:
    value = number(megabytes)
    return round(value / 1024) if value else None


class Vast:
    name = "vast"
    secrets = (KEY,)
    key_hint = (
        "Vast copies the account's ssh keys into an instance when it is created; run "
        "'mlink init' to register your key there, then recreate the instance"
    )

    def __init__(self, settings: Settings):
        self.settings = settings
        self.conf = settings.providers.get(self.name, {})

    def _call(self, method: str, path: str, body: dict | None = None, query: dict | None = None):
        headers = {"Authorization": f"Bearer {secret(KEY, WHERE)}"}
        url = API + path
        if query:
            url += "?" + urlencode({k: json.dumps(v) for k, v in query.items()})
        return http(method, url, headers, body, who="Vast")

    def _disk(self) -> int:
        return int(self.conf.get("disk_gb") or DEFAULT_DISK_GB)

    def machines(self) -> list[Machine]:
        found: list[Machine] = []
        query: dict = {"order_by": [{"col": "id", "dir": "asc"}], "limit": 25}
        while True:
            page = self._call("GET", "/v1/instances/", query=query)
            for item in page.get("instances") or []:
                found.append(
                    Machine(
                        name=item.get("label") or f"vast-{item['id']}",
                        host=item.get("ssh_host") or "",
                        user="root",
                        port=int(item.get("ssh_port") or 22),
                        provider=self.name,
                        id=str(item["id"]),
                        gpu=item.get("gpu_name") or "",
                        region=item.get("geolocation") or "",
                        status=str(item.get("actual_status") or item.get("intended_status") or ""),
                    )
                )
            if not page.get("next_token"):
                return found
            query["after_token"] = page["next_token"]

    def offers(self, filters: Filters, *, spot: bool = False) -> list[Offer]:
        """The cheapest verified offers, narrowed server-side as far as Vast's operators allow."""
        query: dict = {
            "verified": {"eq": True},
            "external": {"eq": False},
            "rentable": {"eq": True},
            "rented": {"eq": False},
            "type": "bid" if spot else "on-demand",
            "order": [["min_bid" if spot else "dph_total", "asc"]],
            "allocated_storage": self._disk(),
        }
        if filters.gpu:
            # Vast matches a name exactly and has no substring operator, so the fragment is
            # resolved to catalogue names here, in the same vocabulary --gpu is written in.
            names = self._call("GET", "/v0/gpu_names/unique/")["gpu_names"]
            wanted = [n for n in names if squash(filters.gpu) in squash(n)]
            if not wanted:
                return []
            query["gpu_name"] = {"in": wanted}
        if filters.min_count:
            query["num_gpus"] = {"gte": filters.min_count}
        if filters.id.isdigit():
            query["id"] = {"eq": int(filters.id)}
        found = []
        for item in self._call("POST", "/v0/bundles/", query)["offers"]:
            found.append(
                Offer(
                    provider=self.name,
                    id=str(item["id"]),
                    gpu=item.get("gpu_name") or "?",
                    gpu_count=int(item.get("num_gpus") or 1),
                    region=item.get("geolocation") or "",
                    price_hr=number(item.get("min_bid") if spot else item.get("dph_total")),
                    available=bool(item.get("rentable", True)),
                    spot=spot,
                    # A Vast name never carries memory; the offer reports it in MB per card.
                    gpu_memory_gb=_gigabytes(item.get("gpu_ram")),
                    raw={k: item.get(k) for k in ("cuda_max_good", "disk_space", "reliability")},
                )
            )
        return found

    def launch(self, offer: Offer, name: str, *, spot: bool = False) -> Machine:
        self.ensure_key()  # keys reach an instance only at creation
        body: dict = {
            "client_id": "me",
            "image": self.conf.get("image") or DEFAULT_IMAGE,
            "disk": self._disk(),
            "label": name,
            "runtype": "ssh",
        }
        if spot or offer.spot:
            body["price"] = offer.price_hr
        try:
            created = self._call("PUT", f"/v0/asks/{offer.id}/", body)
        except Fail as exc:
            if "no_such_ask" not in exc.message:
                raise
            raise Gone(offer.id) from None
        if not created.get("new_contract"):
            raise Fail(
                1,
                f"Vast did not create the instance: {created.get('msg') or created}",
                "check the offer is still listed and the account has credit, then retry",
            )
        return Machine(
            name=name,
            host="",
            user="root",
            provider=self.name,
            id=str(created["new_contract"]),
            gpu=offer.gpu,
            region=offer.region,
            status="loading",
        )

    def terminate(self, machine_id: str) -> None:
        self._call("DELETE", f"/v0/instances/{machine_id}/")

    def ensure_key(self) -> str:
        local = pubkey_text(self.settings)
        for key in self._call("GET", "/v0/ssh/"):  # the list says public_key, whatever the docs say
            if not key.get("deleted_at") and same_key(key.get("public_key") or "", local):
                return str(key["id"])
        created = self._call("POST", "/v0/ssh/", {"ssh_key": local})
        return str((created.get("key") or created)["id"])
