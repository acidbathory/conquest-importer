"""Roll back one recorded run, with a preview, a typed `yes`, a read-back and a record update.

Reuses `core.rollback.rollback`, which re-reads each object and deletes it only if its id still
has the recorded name. On top of that this module deletes only what is in the run record and whose
name starts with an allowed prefix, so a record can never lead to deleting something that is not
clearly this tool's.

The one exception is a dashboard of a real migration (record.sandbox is False, and the person gave
`--real-migration`): it keeps its source title, so it is accepted by kind when the record entry has
its New Relic guid and the creation marker (the description the tool sent), and a fresh read still
shows the same name and description.
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


MARKER_START = "Imported from a "  # what the dashboard emitter writes at the start of a description
DASHBOARD_DETAIL = (
    "query($g: EntityGuid!) { actor { entity(guid: $g) { guid name "
    "... on DashboardEntity { description } } } }"
)

ClientFactory = Callable[[str], AbstractContextManager[Any]]


@contextmanager
def real_client(region: str) -> Any:
    with NewRelicClient(require_env("NEW_RELIC_API_KEY"), region) as client:
        yield client


def is_sandbox_account(account_id: int, allowed: str | None) -> bool:
    """True when the account is in NEW_RELIC_ALLOWED_ACCOUNT_IDS. With no list at all the answer
    is also True: when in doubt a run counts as sandbox, which is the strict rule."""
    ids = {part.strip() for part in (allowed or "").split(",") if part.strip()}
    return not ids or str(account_id) in ids


def is_ours_by_name(obj: CreatedObject) -> bool:
    return obj.name.startswith(ALLOWED_PREFIXES)


def has_marker(obj: CreatedObject) -> bool:
    """A dashboard entry that carries a guid and the description this tool wrote."""
    return bool(obj.id) and obj.description.startswith(MARKER_START)


def refusal(record: RunRecord, obj: CreatedObject, real_migration: bool) -> str | None:
    """Why this recorded object may not be deleted, or None when it may (before the live read)."""
    if is_ours_by_name(obj):
        return None
    if obj.kind != "dashboard" or record.sandbox:
        return "name not ours"
    if not real_migration:
        return "name not ours (use --real-migration for dashboards of a real migration)"
    if not has_marker(obj):
        return "the record has no guid and creation marker for it"
    return None


def _live_mismatch(client: Any, obj: CreatedObject) -> str | None:
    """Re-read a by-kind dashboard; say what no longer matches the record, else None."""
    if is_ours_by_name(obj):
        return None
    entity = (client.run(DASHBOARD_DETAIL, {"g": obj.id}, is_write=False).get("actor") or {}).get(
        "entity"
    )
    if not isinstance(entity, dict):
        return "it can no longer be read"
    if entity.get("name") != obj.name:
        return "its name changed since it was created"
    if entity.get("description") != obj.description:
        return "its description changed since it was created"
    return None


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


def _drop_mismatched(
    console: Console, client: Any, ours: list[CreatedObject]
) -> list[CreatedObject]:
    keep: list[CreatedObject] = []
    for o in ours:
        try:
            why = _live_mismatch(client, o)
        except NewRelicAPIError as exc:
            why = f"it could not be read ({printable(str(exc), 80)})"
        if why is None:
            keep.append(o)
        else:
            console.say(f"  Refusing {o.kind} {o.id} {printable(o.name, 40)}: {why}; left alone.")
    return keep


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
    real_migration: bool = False,
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
    ours: list[CreatedObject] = []
    for o in pending:
        why = refusal(record, o, real_migration)
        if why is None:
            ours.append(o)
        else:
            console.say(f"  Not touching {o.kind} {o.id} {printable(o.name, 40)}: {why}.")
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
        if record.sandbox:
            check_guards(
                record.account_id,
                record.account_id,
                os.environ.get("NEW_RELIC_ALLOWED_ACCOUNT_IDS"),
            )
        if not ensure_secret(console, "NEW_RELIC_API_KEY", "delete these objects"):
            console.say("No New Relic key, so nothing was deleted.")
            return
        with client_factory(record.region) as client:
            ours = _drop_mismatched(console, client, ours)
            if not ours:
                console.say("Nothing was deleted.")
                return
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
