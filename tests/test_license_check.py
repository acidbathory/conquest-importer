"""The licence gate: dependency allowlist, fixture SOURCES.md, and the clean-room marker scan."""

import subprocess
import tomllib
from pathlib import Path

import pytest

from script_helpers import ROOT, load_script

lc = load_script("license_check")

ELV2 = "Elastic" + " License"  # built in two parts so this file does not trip its own scan


class FakeDist:
    """Just enough of importlib.metadata.Distribution."""

    def __init__(self, name: str, **meta: str | list[str]) -> None:
        self.metadata = _Meta({"Name": name, **meta})


class _Meta:
    def __init__(self, data: dict[str, str | list[str]]) -> None:
        self._d = data

    def __getitem__(self, key: str) -> str:
        v = self._d[key]
        return v if isinstance(v, str) else v[0]

    def get(self, key: str, default: str | None = None) -> str | None:
        return self[key] if key in self._d else default

    def get_all(self, key: str) -> list[str] | None:
        v = self._d.get(key)
        if v is None:
            return None
        return [v] if isinstance(v, str) else v


@pytest.mark.parametrize(
    "text",
    [
        "MIT",
        "MIT License",
        "BSD-3-Clause",
        "BSD License",
        "BSD-2-Clause",
        "Apache-2.0",
        "Apache Software License",
        "Apache License, Version 2.0",
        "ISC",
        "ISC License (ISCL)",
        "MPL-2.0",
        "Mozilla Public License 2.0 (MPL 2.0)",
        "PSF-2.0",
        "Python Software Foundation License",
        "Unlicense",
        "The Unlicense (Unlicense)",
        "MIT OR Apache-2.0",
        "(MIT AND BSD-3-Clause)",
        "Apache-2.0 OR GPL-3.0-only",
    ],
)
def test_allowed_licences(text: str) -> None:
    assert lc.is_allowed(text)


@pytest.mark.parametrize(
    "text",
    [
        "GPL-3.0-only",
        "GNU General Public License v3 (GPLv3)",
        "GNU Lesser General Public License v2 (LGPLv2)",
        "AGPL-3.0-or-later",
        "MIT AND GPL-2.0-only",
        "Proprietary",
        "SSPL-1.0",
        ELV2 + " 2.0",
        "",
        "UNKNOWN",
    ],
)
def test_refused_licences(text: str) -> None:
    assert not lc.is_allowed(text)


def test_licence_text_prefers_expression_then_license_then_classifiers() -> None:
    assert lc.licence_text(FakeDist("a", **{"License-Expression": "MIT", "License": "x"})) == "MIT"
    assert lc.licence_text(FakeDist("a", License="BSD-3-Clause")) == "BSD-3-Clause"
    classifiers = [
        "License :: OSI Approved :: MIT License",
        "License :: OSI Approved :: Apache Software License",
        "Programming Language :: Python",
    ]
    got = lc.licence_text(FakeDist("a", Classifier=classifiers))
    assert "MIT License" in got
    assert "Apache Software License" in got
    assert lc.licence_text(FakeDist("a")) == "UNKNOWN"


def test_long_license_field_is_treated_as_unknown_text() -> None:
    # Some packages paste the whole licence text into License:. It cannot be matched by name.
    assert lc.licence_text(FakeDist("a", License="x\n" * 400)) == "UNKNOWN"


def test_check_distributions_flags_outsiders_and_honours_exceptions() -> None:
    dists = [
        FakeDist("good", **{"License-Expression": "MIT"}),
        FakeDist("bad", License="GPL-3.0-only"),
        FakeDist("excused", License="LGPL-3.0-only"),
        FakeDist("mystery"),
    ]
    exceptions = {"excused": "dynamic link only, reviewed 2026-11"}
    failures = lc.check_distributions(dists, exceptions)
    assert sorted(f.split(":")[0] for f in failures) == ["bad", "mystery"]


def test_names_are_normalised_for_exceptions() -> None:
    dists = [FakeDist("Some_Pkg.x", License="GPL-3.0-only")]
    assert lc.check_distributions(dists, {"some-pkg-x": "why"}) == []


def test_exceptions_file_needs_a_reason(tmp_path: Path) -> None:
    f = tmp_path / "e.toml"
    f.write_text('[[exception]]\npackage = "x"\nlicense = "GPL"\nreason = ""\n')
    with pytest.raises(ValueError, match="reason"):
        lc.load_exceptions(f)
    f.write_text('[[exception]]\npackage = "X_y"\nlicense = "GPL"\nreason = "linked dynamically"\n')
    assert lc.load_exceptions(f) == {"x-y": "linked dynamically"}
    assert lc.load_exceptions(tmp_path / "missing.toml") == {}


def test_committed_exceptions_file_parses() -> None:
    lc.load_exceptions(ROOT / "license-exceptions.toml")


def test_sources_must_record_a_licence(tmp_path: Path) -> None:
    ok = tmp_path / "tests" / "fixtures" / "good"
    ok.mkdir(parents=True)
    (ok / "SOURCES.md").write_text("Source: x. Licence Apache-2.0.\n")
    british = tmp_path / "tests" / "fixtures" / "uk"
    british.mkdir()
    (british / "SOURCES.md").write_text("Licences and pinned versions\n")
    bad = tmp_path / "tests" / "fixtures" / "bad"
    bad.mkdir()
    (bad / "SOURCES.md").write_text("Fetched from somewhere.\n")
    problems = lc.check_sources(tmp_path)
    assert len(problems) == 1
    assert "bad" in problems[0]


def test_repo_sources_all_record_a_licence() -> None:
    assert lc.check_sources(ROOT) == []


def git(repo: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603
        ["git", "-C", str(repo), *args],  # noqa: S607
        check=True,
        capture_output=True,
    )


def test_clean_room_scan_flags_marker_in_code_but_not_docs(tmp_path: Path) -> None:
    git(tmp_path, "init", "-q")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "note.md").write_text(f"upstream is under the {ELV2} 2.0\n")
    (tmp_path / "ok.py").write_text("print('hi')\n")
    git(tmp_path, "add", ".")
    assert lc.check_clean_room(tmp_path) == []
    (tmp_path / "copied.py").write_text(f"# Licensed under the {ELV2} 2.0\n")
    (tmp_path / "SOURCES.md").write_text("see https://github.com/elastic/some-repo/blob/main/x\n")
    git(tmp_path, "add", ".")
    problems = lc.check_clean_room(tmp_path)
    assert any("copied.py" in p for p in problems)
    assert any("SOURCES.md" in p for p in problems)
    assert len(problems) == 2


def test_repo_is_clean_room() -> None:
    assert lc.check_clean_room(ROOT) == []


def test_gitleaks_allowlist_entries_are_documented() -> None:
    cfg = tomllib.loads((ROOT / "gitleaks.toml").read_text())
    entries = cfg["allowlists"]
    assert entries
    for entry in entries:
        assert len(entry["description"]) > 20
        # Never a blanket path exclusion: every entry matches on the value.
        assert entry["regexes"]
