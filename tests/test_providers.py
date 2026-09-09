"""Provider request and response contracts, against payloads recorded from the live APIs."""

import pytest

from machine_link import providers
from machine_link.providers.prime import Prime
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
    ],
    "totalCount": 2,
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

PRIME_ROUTES = {
    ("GET", "/availability/gpus?page_size=100"): PRIME_AVAILABILITY,
    ("GET", "/pods/?limit=100"): PRIME_PODS,
    ("GET", "/ssh_keys/"): PRIME_KEYS,
    ("POST", "/pods/"): lambda body: {"id": "newpod", "status": "PROVISIONING"},
    ("DELETE", "/pods/0f0e0d0c0b0a09080706050403020100"): {},
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
    a6000, h100 = Prime(settings).offers()
    assert (a6000.id, a6000.gpu, a6000.region, a6000.price_hr, a6000.available) == (
        "gpu_1x_a6000",
        "A6000_48GB",
        "us-central-1",
        0.54,
        True,
    )
    assert (h100.gpu_count, h100.available) == (8, False)
    assert Prime(settings).offers(spot=True) == []


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
    offer = Prime(settings).offers()[0]
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
        Prime(settings).offers()
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
    offers = Verda(settings).offers()
    by_key = {(o.id, o.region): o for o in offers}
    assert by_key[("1A100.22V", "FIN-01")].price_hr == 1.79
    assert by_key[("1A100.22V", "FIN-02")].available
    assert not by_key[("1A6000.10V", "")].available  # priced, but in stock nowhere
    assert by_key[("CPU.4V.16G", "FIN-01")].gpu_count == 0


def test_verda_spot_offers_use_the_spot_price_and_the_spot_availability(settings, http):
    http(VERDA_ROUTES)
    spot = {(o.id, o.region): o for o in Verda(settings).offers(spot=True)}
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
    offer = next(o for o in Verda(settings).offers() if o.region == "FIN-02")
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
    offer = next(o for o in Verda(settings).offers() if o.id == "1A6000.10V")
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


# ---- shared -----------------------------------------------------------------------------------


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
