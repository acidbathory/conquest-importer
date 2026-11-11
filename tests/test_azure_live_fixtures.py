"""The live Azure fixtures must stay sanitised and carry only ckg- objects."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).parent / "fixtures" / "azure-live"
GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def test_fixtures_exist_and_have_sources() -> None:
    assert (ROOT / "SOURCES.md").exists()
    assert len(list(ROOT.glob("*/*__list.json"))) >= 10


def test_no_raw_guid_secret_or_foreign_object() -> None:
    for f in ROOT.rglob("*.json"):
        text = f.read_text()
        for g in GUID.findall(text):
            assert g == "00000000-0000-0000-0000-000000000000", (f.name, g)
        assert not re.search(r"(?i)[?&](sig|token|code)=(?!MASKED)", text), f.name
        assert "client_secret" not in text and "Bearer" not in text, f.name
        if f.name.endswith("__list.json"):
            for item in json.loads(text)["value"]:
                assert item["name"].startswith(("ckg-", "ckgstore")), (f.name, item["name"])
