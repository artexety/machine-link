"""Provider request and response contracts, against payloads recorded from the live APIs."""

import pytest

from machine_link import providers
from machine_link.models import Filters
from machine_link.providers.prime import Prime
from machine_link.providers.vast import Vast
from machine_link.providers.verda import Verda
from machine_link.ui import Fail

PRIME_AVAILABILITY = {
    "items": [
        {
            "cloudId": "gpu_1x_a6000",
            "gpuType": "A6000_48GB",
            "socket": "PCIe",
            "provider": "massedcompute",
            "region": "united_states",
            "dataCenter": "us-central-1",
            "gpuCount": 1,
            "stockStatus": "Available",
            "prices": {"onDemand": 0.54, "currency": "USD"},
            "images": ["ubuntu_22_cuda_12"],
        },
        {
            "cloudId": "gpu_8x_h100",
            "gpuType": "H100_80GB",
            "socket": "SXM5",
            "provider": "lambdalabs",
            "region": "united_states",
            "dataCenter": "us-east-1",
            "gpuCount": 8,
            "stockStatus": "Unavailable",
            "prices": {"onDemand": 23.92},
            "images": [],
        },
        {
            "cloudId": "cpu-d3_2vcpu-8gb",
            "gpuType": "CPU_NODE",
            "gpuCount": 1,
            "socket": "PCIe",
            "provider": "nebius",
            "region": "united_states",
            "dataCenter": "us-central1",
            "stockStatus": "Available",
            "prices": {"onDemand": 0.0496},
            "images": [],
        },
    ],
    "totalCount": 3,
}
PRIME_PODS = {
    "total_count": 4,
    "data": [
        {
            "id": "0f0e0d0c0b0a09080706050403020100",
            "name": "ptest",
            "status": "ACTIVE",
            "gpuName": "A6000_48GB",
            "sshConnection": "ssh ubuntu@203.0.113.7 -p 2222",
        },
        {
            "id": "aaaa",
            "name": "fresh",
            "status": "PROVISIONING",
            "gpuName": "A6000_48GB",
            "sshConnection": None,
        },
        {
            "id": "bbbb",
            "name": "old",
            "status": "TERMINATED",
            "sshConnection": "ssh ubuntu@1.2.3.4",
        },
        {
            "id": "cccc",
            "name": "multi",
            "status": "ACTIVE",
            "sshConnection": ["ssh ubuntu@5.6.7.8", "ssh ubuntu@5.6.7.9"],
        },
    ],
}
PRIME_KEYS = {
    "data": [
        {
            "id": "key1",
            "name": "macbook",
            "publicKey": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleKeyBody you@example.com",
            "isPrimary": True,
        }
    ]
}
VERDA_TOKEN = {"access_token": "tok", "token_type": "bearer"}
VERDA_TYPES = [
    {
        "instance_type": "1A6000.10V",
        "name": "RTX A6000 48GB",
        "gpu": {"description": "1x RTX A6000 48GB", "number_of_gpus": 1},
        "price_per_hour": "0.6100",
        "spot_price": "0.3050",
    },
    {
        "instance_type": "1A100.22V",
        "name": "A100 SXM4 80GB",
        "gpu": {"description": "1x A100 SXM4 80GB", "number_of_gpus": 1},
        "price_per_hour": "1.79",
        "spot_price": "0.895",
    },
    {
        "instance_type": "CPU.4V.16G",
        "name": "CPU",
        "gpu": {"description": "", "number_of_gpus": 0},
        "price_per_hour": "0.05",
        "spot_price": "0.02",
    },
]
VERDA_AVAILABILITY = [
    {"location_code": "FIN-01", "availabilities": ["1A100.22V", "CPU.4V.16G"]},
    {"location_code": "FIN-02", "availabilities": ["1A100.22V"]},
]
VERDA_SPOT_AVAILABILITY = [{"location_code": "FIN-03", "availabilities": ["1A6000.10V"]}]
VERDA_INSTANCES = [
    {
        "id": "11111111-2222-3333-4444-555555555555",
        "hostname": "vtest",
        "status": "running",
        "ip": "198.51.100.14",
        "instance_type": "1A100.22V",
        "location": "FIN-01",
        "gpu": {"description": "1x A100 SXM4 80GB"},
    },
    {
        "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "hostname": "new",
        "status": "provisioning",
        "ip": None,
        "instance_type": "1A100.22V",
        "location": "FIN-01",
    },
]
VERDA_KEYS = [
    {
        "id": "00000000-1111-2222-3333-444444444444",
        "name": "laptop",
        "key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleKeyBody you@example.com",
    }
]
VAST_OFFERS = {
    "offers": [
        {
            "id": 48480001,
            "gpu_name": "RTX 4090",
            "num_gpus": 1,
            "dph_total": 0.35,
            "min_bid": 0.2,
            "geolocation": "US, TX",
            "rentable": True,
            "verified": "verified",
            "cuda_max_good": 12.8,
            "disk_space": 400.0,
            "reliability": 0.99,
        },
        {
            "id": 48480002,
            "gpu_name": "H100 SXM",
            "num_gpus": 8,
            "dph_total": 17.6,
            "min_bid": 9.2,
            "geolocation": "Norway, NO",
            "rentable": True,
            "verified": "verified",
            "cuda_max_good": 12.8,
            "disk_space": 2048.0,
            "reliability": 0.98,
        },
    ],
    "truncated": False,
}
VAST_NAMES = {"success": True, "gpu_names": ["A100 SXM4", "H100 NVL", "H100 SXM", "RTX 4090"]}
VAST_INSTANCES = {
    "success": True,
    "next_token": None,
    "instances": [
        {
            "id": 31200001,
            "label": "vtrain",
            "actual_status": "running",
            "intended_status": "running",
            "ssh_host": "ssh5.vast.ai",
            "ssh_port": 10600,
            "public_ipaddr": "203.0.113.9",
            "gpu_name": "RTX 4090",
            "num_gpus": 1,
            "geolocation": "US, TX",
            "dph_total": 0.35,
        },
        {
            "id": 31200002,
            "label": None,
            "actual_status": "loading",
            "intended_status": "running",
            "ssh_host": None,
            "ssh_port": None,
            "gpu_name": "H100 SXM",
            "num_gpus": 8,
            "geolocation": "Norway, NO",
        },
    ],
}
VAST_KEYS = [
    {
        "id": 7001,
        "user_id": 1,
        "default": None,
        "public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleKeyBody you@example.com",
        "private_key": None,
        "created_at": 1789108558.0,
        "deleted_at": None,
    }
]

PRIME_ROUTES = {
    ("GET", "/availability/gpus?page_size=100"): PRIME_AVAILABILITY,
    ("GET", "/pods/?limit=100"): PRIME_PODS,
    ("GET", "/ssh_keys/"): PRIME_KEYS,
    ("POST", "/pods/"): lambda body: {"id": "newpod", "status": "PROVISIONING"},
    ("DELETE", "/pods/0f0e0d0c0b0a09080706050403020100"): {},
}
VAST_ROUTES = {
    ("POST", "/bundles/"): VAST_OFFERS,
    ("GET", "/gpu_names/unique/"): VAST_NAMES,
    ("GET", "/instances/"): VAST_INSTANCES,
    ("GET", "/ssh/"): VAST_KEYS,
    ("PUT", "/asks/48480002/"): lambda body: {"success": True, "new_contract": 31200003},
    ("DELETE", "/instances/31200001/"): {"success": True},
}
VERDA_ROUTES = {
    ("POST", "/oauth2/token"): VERDA_TOKEN,
    ("GET", "/instances"): VERDA_INSTANCES,
    ("GET", "/instance-types"): VERDA_TYPES,
    ("GET", "/instance-availability"): VERDA_AVAILABILITY,
    ("GET", "/instance-availability?is_spot=true"): VERDA_SPOT_AVAILABILITY,
    ("GET", "/sshkeys"): VERDA_KEYS,
    ("POST", "/instances"): lambda body: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    ("PUT", "/instances"): {},
}


# ---- Prime ------------------------------------------------------------------------------------


def test_prime_offers_carry_the_data_center_and_stock(settings, http):
    http(PRIME_ROUTES)
    a6000, h100, cpu = Prime(settings).offers(Filters())
    assert (a6000.id, a6000.gpu, a6000.region, a6000.price_hr, a6000.available) == (
        "gpu_1x_a6000",
        "A6000_48GB",
        "us-central-1",
        0.54,
        True,
    )
    assert (h100.gpu_count, h100.available) == (8, False)
    assert (cpu.gpu, cpu.gpu_count) == ("CPU_NODE", 0)  # Prime says gpuCount 1 for these
    assert Prime(settings).offers(Filters(), spot=True) == []


def test_prime_machines_parse_connection_strings_and_keep_provisioning_pods(settings, http):
    http(PRIME_ROUTES)
    ptest, fresh, multi = Prime(settings).machines()
    assert (ptest.host, ptest.port, ptest.user, ptest.status) == (
        "203.0.113.7",
        2222,
        "ubuntu",
        "active",
    )
    assert (fresh.host, fresh.status) == ("", "provisioning")
    assert multi.host == "5.6.7.8"


def test_prime_launch_sends_the_whole_triple_and_the_registered_key(settings, http):
    fake = http(PRIME_ROUTES)
    offer = Prime(settings).offers(Filters())[0]
    machine = Prime(settings).launch(offer, "trainer")
    (body,) = fake.sent("POST", "/pods/")
    assert body["provider"] == {"type": "massedcompute"}
    assert body["pod"] == {
        "name": "trainer",
        "cloudId": "gpu_1x_a6000",
        "gpuType": "A6000_48GB",
        "socket": "PCIe",
        "gpuCount": 1,
        "dataCenterId": "us-central-1",
        "image": "ubuntu_22_cuda_12",
        "sshKeyId": "key1",
    }
    assert (machine.id, machine.host, machine.provider, machine.gpu) == (
        "newpod",
        "",
        "prime",
        "A6000_48GB",
    )


def test_prime_uploads_the_key_when_the_account_lacks_it(settings, http):
    fake = http(
        {
            **PRIME_ROUTES,
            ("GET", "/ssh_keys/"): {"data": []},
            ("POST", "/ssh_keys/"): {"id": "fresh"},
        }
    )
    assert Prime(settings).ensure_key() == "fresh"
    (body,) = fake.sent("POST", "/ssh_keys/")
    assert body == {"name": "local-comment", "publicKey": settings.ssh.pubkey.read_text().strip()}


def test_prime_terminate_deletes_the_pod(settings, http):
    fake = http(PRIME_ROUTES)
    Prime(settings).terminate("0f0e0d0c0b0a09080706050403020100")
    assert fake.calls[-1][:2] == (
        "DELETE",
        "https://api.primeintellect.ai/api/v1/pods/0f0e0d0c0b0a09080706050403020100",
    )


def test_prime_without_a_key_is_exit_2(settings, http, monkeypatch):
    http(PRIME_ROUTES)
    monkeypatch.delenv("PRIME_API_KEY")
    with pytest.raises(Fail) as info:
        Prime(settings).offers(Filters())
    assert info.value.code == 2 and "PRIME_API_KEY" in info.value.message


# ---- Verda ------------------------------------------------------------------------------------


def test_verda_exchanges_the_credentials_once_and_sends_a_bearer(settings, http):
    fake = http(VERDA_ROUTES)
    provider = Verda(settings)
    provider.machines()
    provider.machines()
    tokens = fake.sent("POST", "/oauth2/token")
    assert tokens == [
        {"grant_type": "client_credentials", "client_id": "cid", "client_secret": "csecret"}
    ]
    assert fake.calls[-1][2] == {"Authorization": "Bearer tok"}


def test_verda_offers_join_prices_with_the_locations_that_have_capacity(settings, http):
    http(VERDA_ROUTES)
    offers = Verda(settings).offers(Filters())
    by_key = {(o.id, o.region): o for o in offers}
    assert by_key[("1A100.22V", "FIN-01")].price_hr == 1.79
    assert by_key[("1A100.22V", "FIN-02")].available
    assert not by_key[("1A6000.10V", "")].available  # priced, but in stock nowhere
    assert by_key[("CPU.4V.16G", "FIN-01")].gpu_count == 0


def test_verda_spot_offers_use_the_spot_price_and_the_spot_availability(settings, http):
    http(VERDA_ROUTES)
    spot = {(o.id, o.region): o for o in Verda(settings).offers(Filters(), spot=True)}
    assert spot[("1A6000.10V", "FIN-03")].price_hr == 0.305 and spot[("1A6000.10V", "FIN-03")].spot
    assert not spot[("1A100.22V", "")].available


def test_verda_machines_log_in_as_root_and_keep_ordered_instances(settings, http):
    http(VERDA_ROUTES)
    vtest, new = Verda(settings).machines()
    assert (vtest.user, vtest.host, vtest.gpu, vtest.region) == (
        "root",
        "198.51.100.14",
        "1x A100 SXM4 80GB",
        "FIN-01",
    )
    assert (new.host, new.status) == ("", "provisioning")


def test_verda_launch_reads_the_plain_text_id_and_sends_the_offers_location(settings, http):
    fake = http(VERDA_ROUTES)
    offer = next(o for o in Verda(settings).offers(Filters()) if o.region == "FIN-02")
    machine = Verda(settings).launch(offer, "vtest", spot=True)
    (body,) = fake.sent("POST", "/instances")
    assert body == {
        "instance_type": "1A100.22V",
        "image": "ubuntu-24.04-cuda-12.6",
        "ssh_key_ids": ["00000000-1111-2222-3333-444444444444"],
        "hostname": "vtest",
        "description": "created by machine-link",
        "location_code": "FIN-02",
        "is_spot": True,
    }
    assert (machine.id, machine.user, machine.region) == (
        "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "root",
        "FIN-02",
    )


def test_verda_config_can_pick_image_and_disk(settings, http):
    fake = http(VERDA_ROUTES)
    settings.providers["verda"] = {"image": "ubuntu-22.04", "disk_gb": 100, "location": "FIN-03"}
    offer = next(o for o in Verda(settings).offers(Filters()) if o.id == "1A6000.10V")
    Verda(settings).launch(offer, "big")
    (body,) = fake.sent("POST", "/instances")
    assert body["image"] == "ubuntu-22.04"
    assert body["os_volume"] == {"name": "big-os", "size": 100}
    assert body["location_code"] == "FIN-03"  # the offer had no location, so the config's applies


def test_verda_terminate_uses_the_action_verb(settings, http):
    fake = http(VERDA_ROUTES)
    Verda(settings).terminate("11111111")
    assert fake.sent("PUT", "/instances") == [{"id": ["11111111"], "action": "delete"}]


def test_verda_uploads_the_key_when_missing_and_reads_the_text_id(settings, http):
    fake = http(
        {**VERDA_ROUTES, ("GET", "/sshkeys"): [], ("POST", "/sshkeys"): lambda body: "new-id"}
    )
    assert Verda(settings).ensure_key() == "new-id"
    (body,) = fake.sent("POST", "/sshkeys")
    assert body["name"] == "local-comment" and body["key"].startswith("ssh-ed25519 ")


def test_verda_missing_secret_is_exit_2(settings, http, monkeypatch):
    http(VERDA_ROUTES)
    monkeypatch.delenv("VERDA_CLIENT_SECRET")
    with pytest.raises(Fail) as info:
        Verda(settings).machines()
    assert info.value.code == 2 and "VERDA_CLIENT_SECRET" in info.value.message


# ---- Vast -------------------------------------------------------------------------------------


def test_vast_offers_turn_the_gpu_hint_into_catalogue_names_and_price_bids_at_min_bid(
    settings, http
):
    fake = http(VAST_ROUTES)
    offers = Vast(settings).offers(Filters(gpu="h100", min_count=8))
    (sent,) = fake.sent("POST", "/bundles/")
    assert sent["gpu_name"] == {"in": ["H100 NVL", "H100 SXM"]}
    assert (sent["num_gpus"], sent["type"], sent["allocated_storage"]) == (
        {"gte": 8},
        "on-demand",
        50,
    )
    h100 = next(o for o in offers if o.id == "48480002")
    assert (h100.gpu, h100.gpu_count, h100.region, h100.price_hr, h100.available) == (
        "H100 SXM",
        8,
        "Norway, NO",
        17.6,
        True,
    )
    assert Vast(settings).offers(Filters(gpu="mi300")) == []  # no catalogue name: no search
    spot = Vast(settings).offers(Filters(), spot=True)
    assert fake.sent("POST", "/bundles/")[-1]["type"] == "bid"
    assert spot[0].spot and spot[0].price_hr == 0.2


def test_vast_machines_come_through_the_ssh_proxy_as_root(settings, http):
    http(VAST_ROUTES)
    running, loading = Vast(settings).machines()
    assert (running.name, running.host, running.port, running.user, running.status) == (
        "vtrain",
        "ssh5.vast.ai",
        10600,
        "root",
        "running",
    )
    assert (loading.name, loading.host, loading.status) == ("vast-31200002", "", "loading")


def test_vast_launch_registers_the_key_first_and_reads_the_contract_id(settings, http):
    fake = http(VAST_ROUTES)
    offer = next(o for o in Vast(settings).offers(Filters()) if o.id == "48480002")
    machine = Vast(settings).launch(offer, "trainer")
    order = [(m, url.split("/v0")[-1]) for m, url, _, _ in fake.calls]
    assert order.index(("GET", "/ssh/")) < order.index(("PUT", "/asks/48480002/"))
    (body,) = fake.sent("PUT", "/asks/48480002/")
    assert body == {
        "client_id": "me",
        "image": "vastai/base-image:@vastai-automatic-tag",
        "disk": 50,
        "label": "trainer",
        "runtype": "ssh",
    }
    assert (machine.id, machine.host, machine.user, machine.status, machine.gpu) == (
        "31200003",
        "",
        "root",
        "loading",
        "H100 SXM",
    )
    spot = next(o for o in Vast(settings).offers(Filters(), spot=True) if o.id == "48480002")
    Vast(settings).launch(spot, "cheap", spot=True)
    assert fake.sent("PUT", "/asks/48480002/")[-1]["price"] == 9.2


def test_vast_offer_taken_between_listing_and_create_is_exit_2(settings, http):
    def gone(body):
        raise Fail(
            1, 'Vast answered 400: {"error":"invalid_args","msg":"error 404/3603: no_such_ask"}'
        )

    http({**VAST_ROUTES, ("PUT", "/asks/48480002/"): gone})
    offer = next(o for o in Vast(settings).offers(Filters()) if o.id == "48480002")
    with pytest.raises(Fail) as info:
        Vast(settings).launch(offer, "trainer")
    assert info.value.code == 2 and "mlink gpus" in info.value.fix


def test_vast_config_can_pick_image_and_disk(settings, http):
    fake = http(VAST_ROUTES)
    settings.providers["vast"] = {
        "image": "pytorch/pytorch:2.8.0-cuda12.8-cudnn9-devel",
        "disk_gb": 120,
    }
    offer = next(o for o in Vast(settings).offers(Filters()) if o.id == "48480002")
    assert fake.sent("POST", "/bundles/")[-1]["allocated_storage"] == 120
    Vast(settings).launch(offer, "big")
    (body,) = fake.sent("PUT", "/asks/48480002/")
    assert (body["image"], body["disk"]) == ("pytorch/pytorch:2.8.0-cuda12.8-cudnn9-devel", 120)


def test_vast_terminate_deletes_the_instance(settings, http):
    fake = http(VAST_ROUTES)
    Vast(settings).terminate("31200001")
    assert fake.calls[-1][:2] == ("DELETE", "https://console.vast.ai/api/v0/instances/31200001/")


def test_vast_uploads_the_key_when_missing_and_ignores_deleted_ones(settings, http):
    gone = [{**VAST_KEYS[0], "deleted_at": "2026-02-01T00:00:00Z"}]
    fake = http(
        {
            **VAST_ROUTES,
            ("GET", "/ssh/"): gone,
            ("POST", "/ssh/"): lambda body: {"success": True, "key": {"id": 7002}},
        }
    )
    assert Vast(settings).ensure_key() == "7002"
    (body,) = fake.sent("POST", "/ssh/")
    assert body["ssh_key"].startswith("ssh-ed25519 ")
    http(VAST_ROUTES)
    assert Vast(settings).ensure_key() == "7001"


def test_vast_missing_secret_is_exit_2(settings, http, monkeypatch):
    http(VAST_ROUTES)
    monkeypatch.delenv("VAST_API_KEY")
    with pytest.raises(Fail) as info:
        Vast(settings).machines()
    assert info.value.code == 2 and "VAST_API_KEY" in info.value.message


# ---- shared -----------------------------------------------------------------------------------


def test_http_reports_a_rejected_key_as_exit_2_even_when_vast_says_404(monkeypatch):
    import io
    import urllib.error
    import urllib.request

    body = b'{"success":false,"error":"auth_error","msg":"Invalid user key"}'

    def refuse(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, io.BytesIO(body))

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    with pytest.raises(Fail) as info:
        providers.http("GET", "https://console.vast.ai/api/v0/ssh/", {}, who="Vast")
    assert info.value.code == 2 and "rejected the credentials" in info.value.message


def test_same_key_ignores_the_comment_only():
    assert providers.same_key("ssh-ed25519 AAAA one", "ssh-ed25519 AAAA two")
    assert not providers.same_key("ssh-ed25519 AAAA", "ssh-ed25519 BBBB")
    assert not providers.same_key("", "ssh-ed25519 AAAA")


def test_enabled_follows_the_config_and_rejects_unknown_sections(settings):
    assert [p.name for p in providers.enabled(settings)] == ["prime", "verda"]
    settings.providers = {"lambda": {}}
    with pytest.raises(Fail) as info:
        providers.enabled(settings)
    assert info.value.code == 2 and "lambda" in info.value.message


def test_get_names_the_missing_section(settings):
    settings.providers = {}
    with pytest.raises(Fail) as info:
        providers.get(settings, "verda")
    assert "[providers.verda]" in info.value.fix
