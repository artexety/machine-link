"""Prime Intellect pods. Contract: https://api.primeintellect.ai/openapi.json.

Prime resells several underlying clouds, so an offer is a (cloudId, provider, data center)
triple and all three go back when a pod is created. The data center is not optional in
practice, whatever the spec says.
"""

from __future__ import annotations

from ..config import Settings
from ..models import Machine, Offer, parse_target
from . import http, key_name, number, pubkey_text, same_key, secret

API = "https://api.primeintellect.ai/api/v1"
WHERE = "app.primeintellect.ai > settings > API keys"
KEY = "PRIME_API_KEY"
OVER = {"TERMINATED", "DELETING"}


class Prime:
    name = "prime"
    secrets = (KEY,)
    key_hint = (
        "Prime attaches the key when the pod is created; run 'mlink init' to register "
        "your key there, then recreate the pod"
    )

    def __init__(self, settings: Settings):
        self.settings = settings
        self.conf = settings.providers.get(self.name, {})

    def _call(self, method: str, path: str, body: dict | None = None):
        headers = {"Authorization": f"Bearer {secret(KEY, WHERE)}"}
        return http(method, API + path, headers, body, who="Prime Intellect")

    def machines(self) -> list[Machine]:
        found = []
        for pod in self._call("GET", "/pods/?limit=100")["data"]:
            status = str(pod.get("status") or "").upper()
            if status in OVER:
                continue
            line = pod.get("sshConnection")
            if isinstance(line, list):  # multi-node pods list one line per node; the first leads
                line = next((item for item in line if item), None)
            user, host, port = parse_target(line, "ubuntu") if line else ("ubuntu", "", 22)
            found.append(
                Machine(
                    name=pod.get("name") or f"prime-{pod['id'][:8]}",
                    host=host,
                    user=user,
                    port=port,
                    provider=self.name,
                    id=pod["id"],
                    gpu=pod.get("gpuName") or "",
                    status=status.lower(),
                )
            )
        return found

    def offers(self, *, spot: bool = False) -> list[Offer]:
        if spot:
            return []  # the public availability endpoint has no spot rows
        found = []
        for item in self._call("GET", "/availability/gpus?page_size=100")["items"]:
            stock = str(item.get("stockStatus") or "").lower()
            found.append(
                Offer(
                    provider=self.name,
                    id=item["cloudId"],
                    gpu=item.get("gpuType") or "?",
                    gpu_count=int(item.get("gpuCount") or 1),
                    region=item.get("dataCenter") or item.get("region") or "",
                    price_hr=number((item.get("prices") or {}).get("onDemand")),
                    available=stock not in ("unavailable", "none", "out_of_stock"),
                    raw=item,
                )
            )
        return found

    def launch(self, offer: Offer, name: str, *, spot: bool = False) -> Machine:
        raw = offer.raw
        pod = {
            "name": name,
            "cloudId": offer.id,
            "gpuType": offer.gpu,
            "socket": raw.get("socket"),
            "gpuCount": offer.gpu_count,
            "dataCenterId": raw.get("dataCenter"),
            "image": self.conf.get("image") or (raw.get("images") or [None])[0],
            "diskSize": self.conf.get("disk_gb"),
            "sshKeyId": self.ensure_key(),
        }
        body = {
            "pod": {k: v for k, v in pod.items() if v},
            "provider": {"type": raw.get("provider")},
        }
        created = self._call("POST", "/pods/", body)
        return Machine(
            name=name,
            host="",
            user="ubuntu",
            provider=self.name,
            id=created["id"],
            gpu=offer.gpu,
            region=offer.region,
            status="provisioning",
        )

    def terminate(self, machine_id: str) -> None:
        self._call("DELETE", f"/pods/{machine_id}")

    def ensure_key(self) -> str:
        local = pubkey_text(self.settings)
        for key in self._call("GET", "/ssh_keys/")["data"]:
            if same_key(key.get("publicKey") or "", local):
                return key["id"]
        created = self._call("POST", "/ssh_keys/", {"name": key_name(local), "publicKey": local})
        return created["id"]
