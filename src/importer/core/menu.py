"""The numbered menu: pick a source, then check -> read -> convert and preview -> optional create.

Every step calls an existing command through `runner`, so the menu adds no new way to talk to a
cloud or to New Relic. All questions go through a `Console`, so tests drive it with scripted
answers. Nothing is created without the word `yes` typed after a message that names the New Relic
account. No browser or report opens unless that exact option is chosen.
"""

import os
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from importer.core.apply import GuardError, check_guards
from importer.core.console import Console, InputEnded, confirm_yes, ensure_secret
from importer.core.run_rollback import ClientFactory, real_client, rollback_run
from importer.core.runs import RunRecord, RunStore

Runner = Callable[[list[str]], int]
SAMPLE_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "gcp"


@dataclass(frozen=True)
class Spec:
    key: str  # menu number
    label: str
    source: str  # value of `convert --source`
    doctor: tuple[str, ...]
    visible_env: tuple[str, ...]  # settings that are not secret
    secret_env: tuple[str, ...]
    fix: str  # one plain sentence for a failed check


SPECS: dict[str, Spec] = {
    "1": Spec(
        "1",
        "GCP",
        "gcp",
        ("--source", "gcp"),
        (),
        (),
        "Sign in to Google by running 'gcloud auth application-default login', then pick this "
        "again.",
    ),
    "2": Spec(
        "2",
        "AWS",
        "cloudwatch",
        (),
        (),
        (),
        "Sign in to AWS (for example 'aws sso login' or set AWS_PROFILE), then pick this again.",
    ),
    "3": Spec(
        "3",
        "Grafana",
        "grafana",
        ("--source", "grafana"),
        ("GRAFANA_URL",),
        ("GRAFANA_SA_TOKEN",),
        "Check that GRAFANA_URL and GRAFANA_SA_TOKEN in your .env are right, then pick this again.",
    ),
    "4": Spec(
        "4",
        "Dynatrace",
        "dynatrace",
        ("--source", "dynatrace"),
        ("DYNATRACE_ENV_URL",),
        ("DYNATRACE_API_TOKEN",),
        "Check DYNATRACE_ENV_URL and DYNATRACE_API_TOKEN in your .env, then pick this again.",
    ),
}
MAIN = (
    "\nConquest Importer",
    "  1  GCP",
    "  2  AWS",
    "  3  Grafana",
    "  4  Dynatrace",
    "  5  Roll back a run",
    "  6  Check my setup",
    "  7  Try on sample data",
    "  0  Quit",
)
# What each kind of run creates, and how it ends up in New Relic.
WHAT = {
    "alerts": ("alert conditions", "disabled"),
    "dashboards": ("dashboards", "private"),
    "notifications": ("destinations, channels and workflows", "workflows disabled"),
}


def kind_of(source: str) -> str:
    """alerts, dashboards or notifications, from a run's source label such as grafana-dashboards."""
    for kind in ("dashboards", "notifications"):
        if source.endswith("-" + kind):
            return kind
    return "alerts"


def command_for(source: str) -> str:
    return {"alerts": "apply", "dashboards": "apply-dashboards"}.get(
        kind_of(source), "apply-notifications"
    )


def preview_args(command: str, folder: Path, account: int) -> list[str]:
    return [command, "--in", str(folder), "--account-id", str(account)]


@dataclass(frozen=True)
class Dirs:
    export: Path  # where this flow's own export goes
    out: Path  # where its converted files go
    raw: Path  # the alert export already read
    alerts: Path  # the alerts already converted


Step = tuple[str, list[str]]
StepBuilder = Callable[[Dirs], list[Step]]
READ_ONLY, LOCAL = "reading (read-only)", "converting (local files only)"


def _grafana_dashboards(d: Dirs) -> list[Step]:
    return [
        (READ_ONLY, ["extract-grafana-dashboards", "--out", str(d.export)]),
        (LOCAL, ["convert-grafana-dashboards", "--in", str(d.export), "--out", str(d.out)]),
    ]


def _grafana_notifications(d: Dirs) -> list[Step]:
    convert = ["convert-grafana-notifications", "--in", str(d.export)]
    return [
        (READ_ONLY, ["extract-grafana-notifications", "--out", str(d.export)]),
        (LOCAL, [*convert, "--alerts", str(d.alerts), "--out", str(d.out)]),
    ]


def _dynatrace_dashboards(d: Dirs) -> list[Step]:
    return [
        (READ_ONLY, ["extract-dynatrace-inventory", "--out", str(d.export)]),
        (
            "planning (local files only)",
            ["plan-dynatrace-dashboards", "--in", str(d.export), "--out", str(d.out)],
        ),
    ]


def _dynatrace_notifications(d: Dirs) -> list[Step]:
    convert = ["convert-notifications", "--source", "dynatrace", "--topics", str(d.raw)]
    return [(LOCAL, [*convert, "--out", str(d.out)])]


EXTRA: dict[str, dict[str, StepBuilder]] = {
    "3": {"dashboards": _grafana_dashboards, "notifications": _grafana_notifications},
    "4": {"dashboards": _dynatrace_dashboards, "notifications": _dynatrace_notifications},
}
BAD_CHOICE = "Please type a number from the list."


class Menu:
    def __init__(
        self,
        console: Console,
        runner: Runner,
        store: RunStore,
        *,
        work_dir: Path = Path("out") / "guided",
        opener: Callable[[str], object] = webbrowser.open,
        sample_dir: Path = SAMPLE_DIR,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self.c = console
        self.runner = runner
        self.store = store
        self.work_dir = work_dir
        self.opener = opener
        self.sample_dir = sample_dir
        self.client_factory = client_factory or real_client

    # -- main loop ---------------------------------------------------------------------------
    def run(self) -> int:
        try:
            return self._loop()
        except InputEnded:
            self.c.say("\nInput ended, so the menu is closing. Nothing more was changed.")
            return 1
        except KeyboardInterrupt:
            self.c.say(
                "\nStopped. If a create was running, pick 'Roll back a run' to continue or undo it."
            )
            return 130

    def _loop(self) -> int:
        while True:
            for line in MAIN:
                self.c.say(line)
            choice = self.c.ask("Your choice:")
            if choice == "0":
                return 0
            if choice in SPECS:
                self._source(SPECS[choice])
            elif choice == "5":
                self.rollback_menu()
            elif choice == "6":
                self._check_all()
            elif choice == "7":
                self._sample()
            else:
                self.c.say(BAD_CHOICE)

    # -- setup check -------------------------------------------------------------------------
    def _doctor(self, spec: Spec, extra: tuple[str, ...] = ()) -> bool:
        if self.runner(["doctor", *spec.doctor, *extra]) == 0:
            return True
        self.c.say(f"Next: {spec.fix}")
        return False

    def _check_all(self) -> None:
        for spec in SPECS.values():
            self.c.say(f"\n{spec.label}:")
            if self._doctor(spec):
                self.c.say("Ready.")

    def _ensure_env(self, spec: Spec) -> bool:
        for name in spec.visible_env:
            if not os.environ.get(name, "").strip():
                value = self.c.ask(f"{name} is not set. Enter it (or press Enter to stop):")
                if not value:
                    self.c.say(f"Set {name} in your .env file or type it here, then try again.")
                    return False
                os.environ[name] = value
        for name in spec.secret_env:
            if not ensure_secret(self.c, name, f"read from {spec.label} (kept in memory only)"):
                self.c.say(f"{name} is needed to read from {spec.label}; add it to .env and retry.")
                return False
        return True

    # -- a source ------------------------------------------------------------------------------
    def _ask_inputs(self, spec: Spec) -> tuple[list[str], list[str], str] | None:
        """Returns (extract args without --out, extra doctor args, label for messages)."""
        if spec.key == "1":
            project = self.c.ask("Google Cloud project id (Enter to go back):")
            return (["extract-gcp", "--project", project], [], project) if project else None
        if spec.key == "2":
            account = self.c.ask("AWS account id, 12 digits (Enter to go back):")
            if not account:
                return None
            region = self.c.ask("AWS region [us-east-1]:") or "us-east-1"
            args = ["extract-cloudwatch", "--account-id", account, "--region", region]
            return args, ["--region", region], account
        extract = "extract-grafana" if spec.key == "3" else "extract-dynatrace"
        return [extract], [], spec.label

    def _nr_account(self) -> int | None:
        text = os.environ.get("NEW_RELIC_ACCOUNT_ID", "").strip()
        if not text.isdigit():
            text = self.c.ask("New Relic account id to create in (Enter to skip creating):")
            if not text.isdigit():
                return None
            os.environ["NEW_RELIC_ACCOUNT_ID"] = text
        return int(text)

    def _source(self, spec: Spec) -> None:
        if not self._ensure_env(spec):
            return
        inputs = self._ask_inputs(spec)
        if inputs is None:
            return
        extract, doctor_extra, _label = inputs
        self.c.say("\nStep 1 of 4: checking your setup")
        if not self._doctor(spec, tuple(doctor_extra)):
            return
        folder = self.work_dir / f"{spec.source}-{datetime.now(UTC):%Y%m%d-%H%M%S}"
        raw, alerts = folder / "export", folder / "alerts"
        self.c.say("\nStep 2 of 4: reading from the source (read-only)")
        if self.runner([*extract, "--out", str(raw)]) != 0:
            self.c.say("Reading failed; see the message above. Nothing was changed anywhere.")
            return
        self.c.say("\nStep 3 of 4: converting and previewing (local files only)")
        convert_in = raw / "policies" if spec.key == "1" else raw
        if (
            self.runner(
                ["convert", "--source", spec.source, "--in", str(convert_in), "--out", str(alerts)]
            )
            != 0
        ):
            self.c.say("Converting failed; see the message above. Nothing was changed anywhere.")
            return
        self._after_convert(spec, alerts, raw)

    def _after_convert(self, spec: Spec, alerts: Path, raw: Path) -> None:
        account = self._nr_account()
        if account is not None:
            self.c.say(f"\nPreview of what would be created in New Relic account {account}:")
            self.runner(preview_args("apply", alerts, account))
        while True:
            self.c.say("\nStep 4 of 4: what next?")
            self.c.say("  1  Create these in New Relic (asks you to confirm)")
            self.c.say("  2  Open the HTML report in my browser")
            if spec.key in EXTRA:
                self.c.say("  3  Also bring over the dashboards")
                self.c.say("  4  Also bring over the notifications")
            self.c.say("  0  Back to the main menu")
            choice = self.c.ask("Your choice:")
            if choice == "0":
                return
            if choice == "2":
                self._open_report(spec, alerts)
            elif choice in ("3", "4") and spec.key in EXTRA:
                self._extra_flow(
                    spec, "dashboards" if choice == "3" else "notifications", raw, alerts
                )
            elif choice == "1":
                if account is None:
                    self.c.say("Set NEW_RELIC_ACCOUNT_ID in .env (or enter it) to create.")
                elif self._create(spec, alerts, account):
                    return
            else:
                self.c.say(BAD_CHOICE)

    def _open_report(self, spec: Spec, alerts: Path) -> None:
        if self.runner(["report", str(alerts), "--source", spec.source]) != 0:
            self.c.say("The report could not be written; see the message above.")
            return
        path = (alerts / "report.html").resolve()
        self.opener(path.as_uri())
        self.c.say(f"Opened {path}")

    # -- create ------------------------------------------------------------------------------
    def _guards_ok(self, account: int) -> bool:
        try:
            check_guards(account, account, os.environ.get("NEW_RELIC_ALLOWED_ACCOUNT_IDS"))
        except GuardError:
            self.c.say(
                f"Add {account} to NEW_RELIC_ALLOWED_ACCOUNT_IDS in your .env file to allow "
                "creating there, then try again."
            )
            return False
        return True

    def _create(
        self, spec: Spec, out: Path, account: int, kind: str = "alerts", review: bool = False
    ) -> bool:
        """Returns True when the flow is finished (created, or something went wrong)."""
        if not self._guards_ok(account):
            return False
        what, state = WHAT[kind]
        question = (
            f"This creates {what} in New Relic account {account} ({state}), named from your "
            "source. You can undo it from 'Roll back a run'."
        )
        if not confirm_yes(self.c, question):
            self.c.say("Nothing was created.")
            return False
        if not ensure_secret(self.c, "NEW_RELIC_API_KEY", "create objects"):
            self.c.say("No New Relic key, so nothing was created.")
            return False
        source = spec.source if kind == "alerts" else f"{spec.source}-{kind}"
        record = self.store.start(
            source=source, account_id=account, region="us", out_dir=out, include_review=review
        )
        self._apply(record)
        return True

    # -- dashboards and notifications (Grafana, Dynatrace) -------------------------------------
    def _extra_flow(self, spec: Spec, kind: str, raw: Path, alerts: Path) -> None:
        """Read -> convert -> preview -> optional create for dashboards or notifications."""
        folder = alerts.parent
        dirs = Dirs(folder / f"{kind}-export", folder / kind, raw, alerts)
        steps = EXTRA[spec.key][kind](dirs)
        for i, (title, args) in enumerate(steps, 1):
            self.c.say(f"\n{kind.capitalize()}, step {i} of {len(steps)}: {title}")
            if self.runner(args) != 0:
                self.c.say("That step failed; see the message above. Nothing was created.")
                return
        if spec.key == "4" and kind == "dashboards":
            self.c.say("Dynatrace dashboards are planned only; there is nothing to create.")
            return
        account = self._nr_account()
        if account is None:
            self.c.say("Set NEW_RELIC_ACCOUNT_ID in .env (or enter it) to preview and create.")
            return
        self.c.say(f"\nPreview of what would be created in New Relic account {account}:")
        self.runner(preview_args(command_for(f"x-{kind}"), dirs.out, account))
        self.c.say(f"  1  Create these {kind} in New Relic account {account} (asks you to confirm)")
        self.c.say("  0  Back")
        if self.c.ask("Your choice:") != "1":
            return
        review = spec.key == "4" and confirm_yes(
            self.c, "Dynatrace plans are marked 'needs review'. Include them anyway?"
        )
        self._create(spec, dirs.out, account, kind, review)

    def _apply(self, record: RunRecord) -> None:
        alerts = Path(record.out_dir)
        manifest = alerts / "manifest.json"
        command = command_for(record.source)
        code = self.runner(
            [
                command,
                "--in",
                str(alerts),
                "--account-id",
                str(record.account_id),
                "--region",
                record.region,
                "--apply",
                "--confirm-account",
                str(record.account_id),
                "--manifest",
                str(manifest),
                *(["--include-needs-review"] if record.include_review else []),
            ]
        )
        if manifest.is_file():
            try:
                record = self.store.absorb_manifest(record, manifest)
            except (OSError, ValueError) as exc:
                self.c.say(f"Could not record the result: {exc}")
        self.c.say(
            f"\nRun {record.run_id}: {len(record.objects)} object(s) recorded in account "
            f"{record.account_id}."
            + ("" if code == 0 else " Something failed; roll back or continue from item 5.")
        )

    # -- roll back -----------------------------------------------------------------------------
    def rollback_menu(self) -> None:
        runs = [r for r in self.store.list() if r.status != "rolled-back"]
        if not runs:
            self.c.say("\nThere are no recorded runs to roll back.")
            return
        self.c.say("\nRecorded runs (newest first):")
        for i, r in enumerate(runs, 1):
            note = " (interrupted)" if r.status == "in-progress" else ""
            self.c.say(
                f"  {i}  {r.run_id}  {r.source}  account {r.account_id}  "
                f"{len(r.pending())} object(s){note}"
            )
        self.c.say("  0  Back")
        choice = self.c.ask("Which run?")
        if not choice.isdigit() or not 1 <= int(choice) <= len(runs):
            if choice != "0":
                self.c.say(BAD_CHOICE)
            return
        self.roll_back_or_continue(runs[int(choice) - 1])

    def roll_back_or_continue(self, record: RunRecord) -> None:
        if record.status == "in-progress":
            self.c.say("\nThis run was interrupted before it finished.")
            self.c.say("  1  Continue it (already created items are skipped)")
            self.c.say("  2  Roll back what it created")
            self.c.say("  0  Back")
            choice = self.c.ask("Your choice:")
            if choice == "1":
                self._continue(record)
                return
            if choice != "2":
                return
        rollback_run(self.c, self.store, record, self.client_factory)

    def _continue(self, record: RunRecord) -> None:
        if not self._guards_ok(record.account_id):
            return
        if not confirm_yes(
            self.c, f"This creates the rest in New Relic account {record.account_id}."
        ):
            self.c.say("Nothing was created.")
            return
        if not ensure_secret(self.c, "NEW_RELIC_API_KEY", "create objects"):
            return
        self._apply(record)

    # -- sample ------------------------------------------------------------------------------
    def _sample(self) -> None:
        source = self.sample_dir
        if not source.exists():
            self.c.say("The sample data ships with the source checkout and is not installed here.")
            return
        out = self.work_dir / "sample"
        self.c.say("\nConverting the built-in sample (local files only, nothing is sent anywhere).")
        if self.runner(["convert", "--source", "gcp", "--in", str(source), "--out", str(out)]) != 0:
            self.c.say("The sample could not be converted; see the message above.")
            return
        self.c.say(f"Done. The summary is in {out / 'report.md'}.")
        while True:
            self.c.say("  1  Open the HTML report in my browser")
            self.c.say("  0  Back to the main menu")
            choice = self.c.ask("Your choice:")
            if choice == "1":
                self._open_report(SPECS["1"], out)
            elif choice == "0":
                return
            else:
                self.c.say(BAD_CHOICE)
