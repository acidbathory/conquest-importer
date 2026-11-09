"""Load a folder written by `extract-grafana-notifications` (or the same API payloads by hand).

Every file is optional and read once, with a size cap. A file that cannot be read is reported
in `problems` with a fixed sentence; it does not stop the others. Raw contact point payloads
(with `settings`) go through the sanitiser, so a hand-made export cannot bring a secret in.
"""

import json
import re
from pathlib import Path
from typing import Any

from importer.core.ids import MAX_INPUT_BYTES
from importer.sources.grafana.notifications.contact_points import read_contact_points
from importer.sources.grafana.notifications.models import (
    MAX_DATASOURCES,
    MAX_FILES,
    MAX_TEMPLATE_CHARS,
    MAX_TEMPLATES,
    DataSource,
    NotificationError,
    NotificationExport,
    Template,
    as_dict,
    as_list,
    safe_ref,
)
from importer.sources.grafana.notifications.policies import parse_policy_tree
from importer.sources.grafana.notifications.rulefiles import load_rule_file
from importer.sources.grafana.notifications.templates import (
    DEFINE_NAME,
    MAX_DEFINES,
    make_template,
    mark_overridden,
)
from importer.sources.grafana.notifications.timings import parse_mute_timings

UID = re.compile(r"[A-Za-z0-9_-]{1,40}")
DS_TYPE = re.compile(r"[a-z0-9_.-]{1,60}")
SHA256 = re.compile(r"[0-9a-f]{64}")
FILE_CONTACT_POINTS = "contact-points.json"
FILE_POLICIES = "policies.json"
FILE_MUTE_TIMINGS = "mute-timings.json"
FILE_TEMPLATES = "templates.json"
FILE_DATASOURCES = "datasources.json"
RULE_FILES = "rule-files"


def _read_json(path: Path) -> Any:
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise NotificationError("larger than the importer accepts")
    try:
        return json.loads(path.read_text())
    except RecursionError:
        raise NotificationError("nested too deeply to read") from None
    except json.JSONDecodeError:
        raise NotificationError("not JSON") from None


def read_templates(payload: Any) -> list[Template]:
    found: list[Template] = []
    for item in as_list(payload)[:MAX_TEMPLATES]:
        entry = as_dict(item)
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            continue
        text = entry.get("template", entry.get("text"))
        if isinstance(text, str):
            found.append(make_template(safe_ref(name), text[:MAX_TEMPLATE_CHARS]))
        elif isinstance(entry.get("sha256"), str) and SHA256.fullmatch(entry["sha256"]):
            length = entry.get("length")
            defines = [d for d in as_list(entry.get("defines")) if isinstance(d, str)]
            found.append(
                Template(
                    name=safe_ref(name),
                    sha256=entry["sha256"],
                    length=length if isinstance(length, int) and length >= 0 else 0,
                    defines=[d for d in defines if DEFINE_NAME.fullmatch(d)][:MAX_DEFINES],
                )
            )
    return found


def read_datasources(payload: Any) -> list[DataSource]:
    """Type and uid only: names, URLs and credentials in the payload are not read."""
    found: list[DataSource] = []
    for item in as_list(payload)[:MAX_DATASOURCES]:
        entry = as_dict(item)
        kind, uid = entry.get("type"), entry.get("uid")
        if (
            isinstance(kind, str)
            and isinstance(uid, str)
            and DS_TYPE.fullmatch(kind)
            and UID.fullmatch(uid)
        ):
            found.append(DataSource(type=kind, uid=uid))
    return found


def _rule_files(folder: Path, export: NotificationExport) -> None:
    files = sorted(folder.glob("*.json"))[:MAX_FILES]
    for path in files:
        kind = "loki" if path.name.startswith("loki-") else "prometheus"
        try:
            export.rule_groups += load_rule_file(path, kind)
        except (ValueError, OSError):
            export.problems.append(f"rule file {path.name[:60]} could not be read")


def _load(export: NotificationExport, folder: Path, name: str, use: Any) -> None:
    path = folder / name
    if not path.is_file():
        return
    try:
        use(_read_json(path))
    except NotificationError as exc:
        export.problems.append(f"{name} could not be read: {exc}")
    except (ValueError, OSError):
        export.problems.append(f"{name} could not be read")


def load_export(folder: Path) -> NotificationExport:
    """The notification objects found in `folder`; missing files are simply empty."""
    if not folder.is_dir():
        raise ValueError(f"{folder} is not a folder")
    export = NotificationExport()

    def policies(payload: Any) -> None:
        export.policy = parse_policy_tree(payload)

    _load(
        export,
        folder,
        FILE_CONTACT_POINTS,
        lambda p: export.contact_points.extend(read_contact_points(p)),
    )
    _load(export, folder, FILE_POLICIES, policies)
    _load(
        export,
        folder,
        FILE_MUTE_TIMINGS,
        lambda p: export.mute_timings.extend(parse_mute_timings(p)),
    )
    _load(export, folder, FILE_TEMPLATES, lambda p: export.templates.extend(read_templates(p)))
    _load(
        export, folder, FILE_DATASOURCES, lambda p: export.datasources.extend(read_datasources(p))
    )
    if (folder / RULE_FILES).is_dir():
        _rule_files(folder / RULE_FILES, export)
    mark_overridden(export.contact_points, export.templates)
    return export
