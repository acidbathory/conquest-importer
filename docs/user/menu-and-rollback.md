# The guided menu and rollback

Run `importer menu` in a terminal, or just `importer` with no arguments. The menu needs a person at
the keyboard: `importer menu` with piped input prints a one-line message and exits, and a bare
`importer` with piped input (or `importer --help`) behaves as it always did and shows the help.

```
1 GCP   2 AWS   3 Grafana   4 Dynatrace
5 Roll back a run   6 Check my setup   7 Try on sample data
8 What did I just do?   0 Quit
```

## A source (1 to 4)

The menu calls the same commands you would type, in order:

1. Check setup (`doctor`). A failure prints one sentence saying what to do next.
2. Read from the source (`extract-*`, read-only).
3. Convert and preview (`convert`, then `apply` without `--apply`). Local files only.
4. Optional: create in New Relic, or open the HTML report.

Missing settings are asked for. A token is asked with a hidden prompt, kept in this process only,
and never written to disk or to a report. Creating needs the New Relic account id (from
`NEW_RELIC_ACCOUNT_ID`), that account in `NEW_RELIC_ALLOWED_ACCOUNT_IDS`, and you typing `yes`
after a message that names the account. Nothing opens a browser unless you choose "Open the HTML
report".

## Dashboards and notifications (Grafana and Dynatrace)

After the alert conditions have been converted, the "what next?" step of Grafana and Dynatrace also
offers `3 Also bring over the dashboards` and `4 Also bring over the notifications`. Each one runs
read (read-only), convert (local files), preview (a dry run that names the New Relic account),
then optionally create. It reuses `apply-dashboards` and `apply-notifications` and their manifests.

| | Grafana | Dynatrace |
|---|---|---|
| Dashboards | `extract-grafana-dashboards`, `convert-grafana-dashboards`, `apply-dashboards` | planned only (`extract-dynatrace-inventory`, `plan-dynatrace-dashboards`); nothing is created |
| Notifications | `extract-grafana-notifications`, `convert-grafana-notifications` (uses the converted alerts), `apply-notifications` | `convert-notifications --source dynatrace` on the export already read, then `apply-notifications`; asks whether to include plans marked "needs review" |

Creating asks for the same things as for alerts: the account in `NEW_RELIC_ALLOWED_ACCOUNT_IDS`, a
confirmation message that names the account, and you typing `yes`. It only creates, never updates.
Dashboards are private; workflows are disabled. No report or browser is opened.

## What did I just do? (item 8)

Shows the last run: its id, source, New Relic account, status, how many objects of each kind it
created, how many are still in New Relic, what is private or disabled, and how to undo it
(item 5, or `importer rollback-run`). It reads only the local run record.

## Runs and rollback

Before a create, the menu writes a run record to `out/runs/<run id>.json` (folder `0700`, files
`0600`, written atomically; override the folder with `IMPORTER_RUNS_DIR`). After the create it
adds what was made: type, id, name, account. Dashboard and notification creates are separate runs (for example `grafana-dashboards`), so each can be undone on its own. `importer runs` lists them.

Item 5 (or `importer rollback-run`) lists runs, shows exactly what would be deleted, and asks you
to type `yes`. It then:

- deletes only objects in that record whose name starts with `ckg-`, with `Imported:` (the default
  prefix of notification objects), or is the default policy name `Imported by Conquest Importer`,
  plus recorded dashboards, which keep their source title (anything else is listed and left alone);
- reuses `core/rollback.py`: each object is re-read and deleted only if its id still has the
  recorded name; a policy with conditions that are not ours stays;
- reads each object back from New Relic and records only those that are gone;
- updates the record (`rolled-back` or `partly-rolled-back`).

A run whose create was interrupted shows as `in-progress`. Choosing it offers "continue" (the
create runs again; existing items are skipped) or "roll back" (the run's `manifest.json` is merged
in first, so objects made before the interruption are covered).

The existing `importer rollback --manifest` command is unchanged.
