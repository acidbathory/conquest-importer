"""Roll back one recorded run, with a preview, a typed `yes`, a read-back and a record update.

Reuses `core.rollback.rollback`, which re-reads each object and deletes it only if its id still
has the recorded name. On top of that this module deletes only what is in the run record and whose
name starts with an allowed prefix, so a record can never lead to deleting something that is not
clearly this tool's.
"""

import os
import time
from collections.abc import Callable
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Any

from importer.core.apply import GuardError, NewRelicAPIError, NewRelicClient, check_guards
from importer.core.console import Console, confirm_yes, ensure_secret
from importer.core.manifest import CreatedObject, Manifest
from importer.core.rollback import current, rollback
from importer.core.runs import RunRecord, RunStore
from importer.core.secrets import MissingSecretError, require_env
from importer.core.text import printable

# `ckg-` marks sandbox objects; the second is the policy name `apply` uses by default; the third is
# the default prefix of the notification objects the menu creates.
ALLOWED_PREFIXES = ("ckg-", "Imported by Conquest Importer", "Imported:")


ClientFactory = Callable[[str], AbstractContextManager[Any]]


@contextmanager
def real_client(region: str) -> Any:
    with NewRelicClient(require_env("NEW_RELIC_API_KEY"), region) as client:
        yield client


def is_ours_by_name(obj: CreatedObject) -> bool:
    """A dashboard keeps the title it had in the source, so it is accepted by kind: it is in the
    record only because this tool created it (existing names are skipped), and `rollback` still
    re-reads it and deletes it only if the id has the recorded name."""
    return obj.kind == "dashboard" or obj.name.startswith(ALLOWED_PREFIXES)


def _merge_interrupted(console: Console, store: RunStore, record: RunRecord) -> None:
    """A run that stopped part-way may have created objects its record does not list yet."""
    manifest = Path(record.out_dir) / "manifest.json"
    if not manifest.is_file():
        return
    try:
        store.absorb_manifest(record, manifest)
    except (OSError, ValueError) as exc:
        console.say(f"Could not read {manifest}: {printable(str(exc), 150)}")


def _preview(console: Console, record: RunRecord, ours: list[CreatedObject]) -> None:
    console.say(f"\nRun {record.run_id} ({record.source}), New Relic account {record.account_id}.")
    console.say("These will be deleted:")
    for o in ours:
        console.say(f"  {o.kind:11} {o.id}  {printable(o.name, 60)}")


def _read_back(
    client: Any, account: int, deleted: list[CreatedObject]
) -> tuple[list[CreatedObject], list[CreatedObject]]:
    """Ask New Relic again; split into (confirmed gone, still there)."""
    gone: list[CreatedObject] = []
    left: list[CreatedObject] = []
    for o in deleted:
        try:
            # Dashboards stay listed for a while after deletion; the delete answer was the proof.
            present = o.kind != "dashboard" and current(client, account, o) is not None
        except NewRelicAPIError:
            present = True
        (left if present else gone).append(o)
    return gone, left


def _finish(store: RunStore, record: RunRecord, gone: list[CreatedObject]) -> None:
    record.rolled_back += [record.key(o) for o in gone if record.key(o) not in record.rolled_back]
    record.status = "rolled-back" if not record.pending() else "partly-rolled-back"
    store.save(record)


def rollback_run(
    console: Console,
    store: RunStore,
    record: RunRecord,
    client_factory: ClientFactory = real_client,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    configured = os.environ.get("NEW_RELIC_ACCOUNT_ID", "").strip()
    if configured != str(record.account_id):
        console.say(
            f"This run is for New Relic account {record.account_id} but your settings say "
            f"account {printable(configured or 'none', 20)}. Set NEW_RELIC_ACCOUNT_ID to "
            f"{record.account_id} to roll it back."
        )
        return
    _merge_interrupted(console, store, record)
    pending = record.pending()
    ours = [o for o in pending if is_ours_by_name(o)]
    for o in pending:
        if not is_ours_by_name(o):
            console.say(f"  Not touching {o.kind} {o.id} {printable(o.name, 40)}: name not ours.")
    if not ours:
        console.say("There is nothing left in this run that can be deleted.")
        return
    _preview(console, record, ours)
    if not confirm_yes(
        console, f"Delete these {len(ours)} objects from account {record.account_id}?"
    ):
        console.say("Cancelled. Nothing was deleted.")
        return
    try:
        check_guards(
            record.account_id, record.account_id, os.environ.get("NEW_RELIC_ALLOWED_ACCOUNT_IDS")
        )
        if not ensure_secret(console, "NEW_RELIC_API_KEY", "delete these objects"):
            console.say("No New Relic key, so nothing was deleted.")
            return
        with client_factory(record.region) as client:
            result = rollback(
                client,
                record.account_id,
                Manifest(account_id=record.account_id, region=record.region, objects=ours),
                apply=True,
                sleep=sleep,
            )
            gone, left = _read_back(client, record.account_id, result.deleted)
    except (GuardError, MissingSecretError, NewRelicAPIError, OSError) as exc:
        console.say(f"Stopped: {printable(str(exc), 250)}")
        return
    for o in left:
        console.say(f"  {o.kind} {o.id} still exists after delete; left in the record.")
    for o, why in result.skipped + result.failed:
        console.say(f"  {o.kind} {o.id} {printable(o.name, 40)} not deleted: {printable(why, 120)}")
    _finish(store, record, gone)
    console.say(f"Checked again in New Relic: {len(gone)} of {len(ours)} are gone.")
