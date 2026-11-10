"""The numbered menu: pick a source, then one pass: check, read everything, convert everything,
one preview, one typed `yes`, create in a safe order, one combined run record.

Every step calls an existing command through `runner`, so the menu adds no new way to talk to a
cloud or to New Relic. All questions go through a `Console`, so tests drive it with scripted
answers. Nothing is created without the word `yes` typed after a message that names the New Relic
account and the counts.

The HTML report opens in the default browser twice per source: before the question (the plan) and
after a successful create (what was made). The path is always printed too. Opening is skipped
without a terminal, with IMPORTER_NO_BROWSER=1 or `--no-open`; a failure to open never stops the
flow, and only the report file inside the run's own folder is ever opened.
"""

import os
import webbrowser
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from importer.core.apply import GuardError, check_guards, get_account_name
from importer.core.console import Console, InputEnded, confirm_yes, ensure_secret, has_terminal
from importer.core.menu_flow import (
    APPLY,
    KINDS,
    LABEL,
    PLANS,
    STATE,
    KindPlan,
    Layout,
    Summary,
    build_kinds,
    extra_plans,
    read_int,
    summarise,
    triage_buckets,
)
from importer.core.migration_summary import load_summary
from importer.core.report_html import PROFILES, RunInfo, render_report
from importer.core.run_rollback import (
    ClientFactory,
    is_sandbox_account,
    real_client,
    rollback_run,
)
from importer.core.runs import RunRecord, RunStore

Runner = Callable[[list[str]], int]
PRIVATE_KINDS = ("condition", "workflow", "dashboard")  # created disabled or private
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
    "  8  What did I just do?",
    "  0  Quit",
)


def kind_of(source: str) -> str:
    """alerts, dashboards or notifications, from a run's source label such as grafana-dashboards."""
    for kind in ("dashboards", "notifications"):
        if source.endswith("-" + kind):
            return kind
    return "alerts"


def command_for(source: str) -> str:
    return APPLY[kind_of(source)]


def preview_args(command: str, folder: Path, account: int) -> list[str]:
    return [command, "--in", str(folder), "--account-id", str(account)]


BAD_CHOICE = "Please type a number from the list."


class Menu:
    def __init__(
        self,
        console: Console,
        runner: Runner,
        store: RunStore,
        *,
        work_dir: Path = Path("out") / "guided",
        sample_dir: Path = SAMPLE_DIR,
        client_factory: ClientFactory | None = None,
        real_migration: bool = False,
        opener: Callable[[str], object] | None = None,
        open_reports: bool = True,
        terminal: Callable[[], bool] = has_terminal,
    ) -> None:
        self.c = console
        self.runner = runner
        self.store = store
        self.work_dir = work_dir
        self.sample_dir = sample_dir
        self.client_factory = client_factory or real_client
        self.real_migration = real_migration
        self.opener = opener or webbrowser.open
        self.open_reports = open_reports
        self.terminal = terminal
        self._opened: set[tuple[Path, str]] = set()

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
            elif choice == "8":
                self.last_run_summary()
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
    def _ask_inputs(self, spec: Spec) -> tuple[list[str], list[str]] | None:
        """Returns (extract args without --out, extra doctor args), or None to go back."""
        if spec.key == "1":
            project = self.c.ask("Google Cloud project id (Enter to go back):")
            return (["extract-gcp", "--project", project], []) if project else None
        if spec.key == "2":
            account = self.c.ask("AWS account id, 12 digits (Enter to go back):")
            if not account:
                return None
            region = self.c.ask("AWS region [us-east-1]:") or "us-east-1"
            args = ["extract-cloudwatch", "--account-id", account, "--region", region]
            return args, ["--region", region]
        return [("extract-grafana" if spec.key == "3" else "extract-dynatrace")], []

    def _nr_account(self) -> int | None:
        text = os.environ.get("NEW_RELIC_ACCOUNT_ID", "").strip()
        if not text.isdigit():
            text = self.c.ask("New Relic account id to create in (Enter to skip creating):")
            if not text.isdigit():
                return None
            os.environ["NEW_RELIC_ACCOUNT_ID"] = text
        return int(text)

    def _run_all(self, steps: list[list[str]]) -> bool:
        return all(self.runner(step) == 0 for step in steps)

    def _source(self, spec: Spec) -> None:
        if not self._ensure_env(spec):
            return
        inputs = self._ask_inputs(spec)
        if inputs is None:
            return
        extract, doctor_extra = inputs
        self.c.say("\nStep 1 of 4: checking your setup")
        if not self._doctor(spec, tuple(doctor_extra)):
            return
        lay = Layout(self.work_dir / f"{spec.source}-{datetime.now(UTC):%Y%m%d-%H%M%S}")
        kinds = build_kinds(spec.key, spec.source, extract, lay)
        self.c.say(f"\nStep 2 of 4: reading everything {spec.label} offers (read-only)")
        kinds = self._stage(kinds, "read", "Reading")
        if not kinds:
            return
        self.c.say("\nStep 3 of 4: converting everything (local files only)")
        kinds = self._stage(kinds, "convert", "Converting")
        if not kinds:
            return
        self._side_plans(spec, lay)
        summary = summarise(lay, kinds)
        account = self._nr_account()
        self.c.say("\nStep 4 of 4: preview")
        name = self._account_name(account) if account is not None else None
        self._preview(spec, lay, summary, account, name)
        self._show_report(lay, "before", self._write_report(spec, lay, account))
        if account is None:
            self.c.say(
                "Set NEW_RELIC_ACCOUNT_ID in .env (or enter it) to create. Nothing was created."
            )
            return
        if summary.total == 0:
            self.c.say("There is nothing to create from this source. Nothing was created.")
            return
        self._create_all(spec, lay, summary, account, name)

    def _stage(self, kinds: list[KindPlan], which: str, verb: str) -> list[KindPlan]:
        """Run one stage for every kind. Alerts failing ends the flow; other kinds are dropped."""
        kept: list[KindPlan] = []
        for kp in kinds:
            if self._run_all(kp.read if which == "read" else kp.convert):
                kept.append(kp)
            elif kp.kind == "alerts":
                self.c.say(f"{verb} the alerts failed; see the message above. Nothing was changed.")
                return []
            else:
                self.c.say(
                    f"{verb} the {kp.kind} did not work (see above), so they are left out. "
                    "Nothing was changed."
                )
        return kept

    def _side_plans(self, spec: Spec, lay: Layout) -> None:
        """Plans written next to the conversion. A failure here never stops the flow."""
        for args in extra_plans(spec.key, lay):
            self.runner(args)
        self.runner(["plan-remediation", "--in", str(lay.converted), "--out", str(lay.remediation)])

    # -- the HTML report ---------------------------------------------------------------------
    def _write_report(
        self,
        spec: Spec,
        lay: Layout,
        account: int | None,
        record: RunRecord | None = None,
        plan: Summary | None = None,
    ) -> Path | None:
        """Write the plan report (before) or the created report (after) into the run folder."""
        if spec.source not in PROFILES:
            return None
        try:
            summary = load_summary(lay.converted)
            info = None
            if record is not None:
                summary.created = dict(Counter(o.kind for o in record.objects))
                private = sum(o.kind in PRIVATE_KINDS for o in record.pending())
                info = RunInfo(record.run_id, private, self._rows(record, plan))
            path = lay.folder / ("report-created.html" if record else "report.html")
            html = render_report(
                summary,
                nr_account=account,
                source=spec.source,
                info=info,
                guided=True,
                links=self._links(lay),
            )
            path.write_text(html)
        except (OSError, ValueError) as exc:
            self.c.say(f"The HTML report could not be written: {exc}")
            return None
        return path

    @staticmethod
    def _rows(
        record: RunRecord, plan: Summary | None
    ) -> tuple[tuple[str, int, int | None, int], ...]:
        """Per kind: created (run record), planned and need attention (from the preview)."""
        made: Counter[str] = Counter(str(o.kind) for o in record.objects)
        kind_of_object = {"condition": "alerts", "workflow": "notifications"}
        kind_of_object |= {"dashboard": "dashboards"}
        per_kind = Counter({k: made[o] for o, k in kind_of_object.items()})
        rows = []
        for kind in KINDS:
            found = plan.kinds.get(kind) if plan else None
            if per_kind[kind] == 0 and (found is None or found.create == 0):
                continue
            rows.append(
                (
                    LABEL[kind].capitalize(),
                    per_kind[kind],
                    found.create if found else None,
                    found.attention if found else 0,
                )
            )
        return tuple(rows)

    @staticmethod
    def _links(lay: Layout) -> list[tuple[str, str]]:
        wanted = [
            ("Remediation plan: what to do for each item", lay.remediation / "remediation-plan.md"),
            ("Data-path guide: get your metrics into New Relic", lay.datapath / "datapath-plan.md"),
            ("Dashboard ranking", lay.triage / "triage-report.md"),
        ]
        return [(t, p.relative_to(lay.folder).as_posix()) for t, p in wanted if p.is_file()]

    def _show_report(self, lay: Layout, phase: str, path: Path | None) -> None:
        """Print the report's file:// path; open it once per phase when a person is here."""
        if path is None:
            return
        try:
            target = path.resolve()
            if not target.is_file() or not target.is_relative_to(lay.folder.resolve()):
                return  # only ever a file inside this run's own folder
            uri = target.as_uri()
        except (OSError, ValueError):
            return
        self.c.say(f"HTML report: {uri}")
        key = (lay.folder, phase)
        quiet = os.environ.get("IMPORTER_NO_BROWSER") == "1"
        if key in self._opened or quiet or not self.open_reports or not self.terminal():
            return
        self._opened.add(key)
        try:
            self.opener(uri)
        except Exception:  # a missing browser must never stop the migration
            self.c.say("The browser could not be opened; open the file above yourself.")

    def _account_name(self, account: int) -> str | None:
        """The account's name when a key is already set; any failure just leaves the id."""
        if not os.environ.get("NEW_RELIC_API_KEY", "").strip():
            return None
        try:
            with self.client_factory("us") as client:
                return get_account_name(client, account)
        except Exception:  # cosmetic lookup: the id is shown anyway
            return None

    def _preview(
        self, spec: Spec, lay: Layout, summary: Summary, account: int | None, name: str | None
    ) -> None:
        who = "(no New Relic account chosen yet)"
        if account is not None:
            who = f"New Relic account {account}" + (f" ({name})" if name else "")
        self.c.say(f"\nWhat would be created in {who}:")
        for kind in KINDS:
            found = summary.kinds.get(kind)
            if found is None:
                continue
            line = f"  {LABEL[kind].capitalize()}: {found.create} will be created, "
            line += f"{found.attention} need your attention"
            if kind == "dashboards" and spec.key == "4":
                line += " (Dynatrace dashboards are planned only, nothing is created)"
            if kind == "notifications" and spec.key == "1":
                line += " (Google channels are manual steps, nothing is created)"
            self.c.say(line)
        self.c.say("Everything is created disabled or private; nothing is turned on.")
        if summary.reasons:
            self.c.say("\nMost common reasons items need your attention:")
            for title, count in summary.reasons:
                self.c.say(f"  {count}  {title}")
        plan = lay.remediation / "remediation-plan.md"
        if plan.is_file():
            self.c.say(f"Remediation plan (what to do for each): {plan}")
        if spec.key in ("3", "4") and (lay.datapath / "datapath-plan.md").is_file():
            self.c.say(
                f"Your metrics must reach New Relic; see {lay.datapath / 'datapath-plan.md'}"
            )
        self._dashboard_plans(lay)
        self.c.say(f"Reports: {lay.converted}")

    def _dashboard_plans(self, lay: Layout) -> None:
        quick = read_int(lay.quickstarts / "quickstart-plan.json", "replace")
        if quick:
            self.c.say(f"{quick} dashboards have a New Relic quickstart replacement.")
        buckets = triage_buckets(lay.triage / "triage-report.json")
        if buckets:
            ranked = ", ".join(f"{n} {b}" for b, n in sorted(buckets.items()))
            self.c.say(f"Dashboards ranked: {ranked} (see {lay.triage / 'triage-report.md'}).")

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

    def _create_all(
        self, spec: Spec, lay: Layout, summary: Summary, account: int, name: str | None
    ) -> None:
        if not self._guards_ok(account):
            return
        parts = [f"{summary.create(k)} {LABEL[k]} ({STATE[k]})" for k in KINDS if summary.create(k)]
        who = f"{account} ({name})" if name else str(account)
        question = (
            f"\nThis creates {', '.join(parts)} in New Relic account {who}, named from your "
            "source. Nothing is turned on. You can undo all of it from 'Roll back a run'."
        )
        if not confirm_yes(self.c, question):
            self.c.say("Nothing was created.")
            return
        if not ensure_secret(self.c, "NEW_RELIC_API_KEY", "create objects"):
            self.c.say("No New Relic key, so nothing was created.")
            return
        record = self.store.start(
            source=spec.source,
            account_id=account,
            region="us",
            out_dir=lay.converted,
            sandbox=is_sandbox_account(account, os.environ.get("NEW_RELIC_ALLOWED_ACCOUNT_IDS")),
        )
        if self._apply_until_done(record):
            self._done(lay, record)
            self._show_report(lay, "after", self._write_report(spec, lay, account, record, summary))

    def _targets(self, record: RunRecord) -> list[tuple[str, Path]]:
        """The converted folders this run creates from, in the safe order."""
        out = Path(record.out_dir)
        marker = {"alerts": "ir", "notifications": PLANS, "dashboards": "dashboard-ir"}
        found = [(k, out / k) for k in KINDS if (out / k / marker[k]).exists()]
        if found:
            return found
        return [(kind_of(record.source), out)]  # a run recorded by an older menu

    def _apply_until_done(self, record: RunRecord) -> bool:
        """Create every kind; on the first failure say what exists and offer undo or continue."""
        while True:
            failed = self._apply_all(record)
            if failed is None:
                return True
            self.c.say(f"\n{failed.capitalize()} could not all be created; I stopped there.")
            self.c.say(f"Created so far: {self._made(record)}.")
            self.c.say("  1  Roll back what was created")
            self.c.say("  2  Continue (already created items are skipped)")
            self.c.say("  0  Leave it as it is (undo later from item 5)")
            choice = self.c.ask("Your choice:")
            if choice == "1":
                rollback_run(
                    self.c,
                    self.store,
                    record,
                    self.client_factory,
                    real_migration=self.real_migration,
                )
                return False
            if choice != "2":
                return False

    def _apply_all(self, record: RunRecord) -> str | None:
        """Returns the kind that failed, or None when everything was created."""
        for kind, folder in self._targets(record):
            manifest = folder / "manifest.json"
            code = self.runner(
                [
                    APPLY[kind],
                    "--in",
                    str(folder),
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
            if code != 0:
                record.status = "in-progress"
                self.store.save(record)
                return LABEL[kind]
        record.status = "applied"
        self.store.save(record)
        return None

    @staticmethod
    def _made(record: RunRecord) -> str:
        made = Counter(o.kind for o in record.objects)
        return ", ".join(f"{n} {kind}" for kind, n in sorted(made.items())) or "nothing"

    def _done(self, lay: Layout, record: RunRecord) -> None:
        self.c.say(f"\nDone. Run {record.run_id}: created {self._made(record)} in account "
                   f"{record.account_id}.")  # fmt: skip
        self.c.say(f"Reports are in {lay.converted}")
        plan = lay.remediation / "remediation-plan.md"
        if plan.is_file():
            self.c.say(f"Remediation plan: {plan}")
        self.c.say("To undo all of it: pick 5 Roll back a run.")
        self.c.say(
            "Next: turn on the alert conditions one by one in New Relic, and make sure your "
            "metrics reach New Relic."
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
        rollback_run(
            self.c, self.store, record, self.client_factory, real_migration=self.real_migration
        )

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
        self._apply_until_done(record)

    # -- what did I just do --------------------------------------------------------------------
    def last_run_summary(self) -> None:
        runs = self.store.list()
        if not runs:
            self.c.say("\nNothing yet: no run has created anything in New Relic.")
            return
        r = runs[0]
        left = r.pending()
        self.c.say(f"\nLast run: {r.run_id} ({r.source}), New Relic account {r.account_id}.")
        self.c.say(f"Started {r.started or 'unknown'}; status: {r.status}.")
        if not r.objects:
            self.c.say("It recorded no objects, so there is nothing in New Relic to undo.")
            return
        made = Counter(o.kind for o in r.objects)
        self.c.say("Created: " + ", ".join(f"{n} {kind}" for kind, n in sorted(made.items())) + ".")
        self.c.say(f"{len(left)} still in New Relic; {len(r.objects) - len(left)} removed.")
        notes = (
            (("dashboard",), "Dashboards are private: only you can see them."),
            (("condition", "policy"), "Alert conditions are created disabled."),
            (("workflow",), "Workflows are created disabled, so nothing is sent yet."),
        )
        for kinds, note in notes:
            if any(o.kind in kinds for o in left):
                self.c.say(note)
        if left:
            self.c.say(
                "To undo it: pick 'Roll back a run' (5) here, or run 'importer rollback-run'. "
                "You see exactly what will be deleted and must type yes."
            )

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
        self.c.say(f"Done. The summary is in {out / 'report.md'}. Nothing was sent anywhere.")
