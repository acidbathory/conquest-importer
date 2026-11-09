"""The files a conversion writes, as text in memory first and then to disk with mode 0600.

Layout (compatible with `apply-notifications --in <out>`, which reads `notification-plans/`):

    notification-plans/topic-0001.json   one NotificationPlan each (the sensitive fields are in it)
    muting-rules/rule-0001.json          one MutingRule each
    notifications.tf                     destinations, channels, workflows, muting rules
    secrets.tfvars.example               every sensitive variable, with a placeholder
    report.md                            counts, steps, secrets to supply (no addresses)

The plan files and the Terraform hold email addresses, so every file is written readable by the
owner only. Nothing is overwritten.
"""

import os
from pathlib import Path

from importer.core.emit.dashboard import TERRAFORM_HEADER
from importer.core.emit.muting import muting_terraform
from importer.core.emit.notifications import (
    all_destinations,
    notifications_terraform,
    secret_names,
    secret_variable,
)
from importer.core.notification_ir import plan_json
from importer.sources.grafana.notify_convert.convert import Conversion
from importer.sources.grafana.notify_convert.report import render_report

PLANS = "notification-plans"
MUTING = "muting-rules"
PLACEHOLDER = "REPLACE_WITH_THE_SECRET"


def output_files(c: Conversion) -> dict[str, str]:
    """Relative path -> text, for every file of the conversion."""
    files: dict[str, str] = {}
    for n, plan in enumerate(c.plans, start=1):
        files[f"{PLANS}/topic-{n:04d}.json"] = plan_json(plan)
    for n, rule in enumerate(c.muting_rules, start=1):
        files[f"{MUTING}/rule-{n:04d}.json"] = rule.model_dump_json(indent=2) + "\n"
    terraform = TERRAFORM_HEADER + "\n" + notifications_terraform(c.plans)
    if c.muting_rules:
        terraform += "\n" + muting_terraform(c.muting_rules)
    files["notifications.tf"] = terraform
    names = secret_names([d for p in c.plans for d in all_destinations(p)])
    files["secrets.tfvars.example"] = "".join(
        f'{secret_variable(n)} = "{PLACEHOLDER}"\n' for n in names
    )
    files["report.md"] = render_report(c)
    return files


def write_files(files: dict[str, str], out: Path) -> None:
    """Write every file under `out`, mode 0600. Refuses to overwrite anything."""
    targets = {out / rel: text for rel, text in files.items()}
    clash = next((t for t in targets if t.exists()), None)
    if clash is not None:
        raise ValueError(f"{clash.name} already exists in {out}; pass a new --out folder")
    for path, text in targets.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
