"""Offline tests for the live read-back sanitiser."""

import json
from typing import Any

from test_azure_estate_arm import load

SUB = "00000000-1111-2222-3333-444444444444"
TENANT = "11111111-2222-3333-4444-555555555555"
PRINCIPAL = "99999999-8888-7777-6666-555555555555"


def test_ids_and_guids_become_placeholders() -> None:
    rb = load("readback")
    doc: dict[str, Any] = {
        "id": f"/subscriptions/{SUB}/resourceGroups/azure-signup-rg/providers/X/y/ckg-a",
        "tenantId": TENANT,
        "identity": {"principalId": PRINCIPAL, "tenantId": TENANT},
        "scopes": [f"/subscriptions/{SUB.upper()}"],
        "keep": "00000000-0000-0000-0000-000000000000",
        "name": "ckg-a",
    }
    out = rb.sanitise(doc, SUB, TENANT)
    text = json.dumps(out)
    assert SUB not in text and SUB.upper() not in text and TENANT not in text
    assert PRINCIPAL not in text
    assert out["id"].startswith("/subscriptions/SUBSCRIPTION_ID/resourceGroups/RESOURCE_GROUP/")
    assert out["tenantId"] == "TENANT_ID"
    assert out["identity"]["principalId"].startswith("GUID-")
    assert (
        out["identity"]["principalId"] == rb.sanitise(doc, SUB, TENANT)["identity"]["principalId"]
    )
    assert out["keep"] == "00000000-0000-0000-0000-000000000000"


def test_signature_and_token_query_values_are_masked() -> None:
    rb = load("readback")
    url = "https://h.invalid/p?api-version=1&sig=abc123&code=zzz&token=t0k"
    out = rb.sanitise({"u": url, "email": "ckg-test@example.invalid"}, SUB, TENANT)
    assert "abc123" not in out["u"] and "zzz" not in out["u"] and "t0k" not in out["u"]
    assert out["email"] == "ckg-test@example.invalid"


def test_only_ckg_objects_are_kept_from_a_list() -> None:
    rb = load("readback")
    items = [{"name": "ckg-x"}, {"name": "someone-elses"}, {"name": "ckgstoreab12cd"}]
    assert [i["name"] for i in rb.only_ours(items)] == ["ckg-x", "ckgstoreab12cd"]
