# The guided menu and rollback

Run `importer menu` in a terminal, or just `importer` with no arguments. The menu needs a person at
the keyboard: `importer menu` with piped input prints a one-line message and exits, and a bare
`importer` with piped input (or `importer --help`) behaves as it always did and shows the help.

```
1 GCP   2 AWS   3 Grafana   4 Dynatrace
5 Roll back a run   6 Check my setup   7 Try on sample data
8 What did I just do?   0 Quit
```

## A source (1 to 4): everything in one pass

Picking a source does the whole job in one go. There is no "create these or open the report"
choice. The menu calls the same commands you would type:

1. Check setup (`doctor`). A failure prints one sentence saying what to do next.
2. Read everything the source offers, read-only: alert rules, notifications and dashboards
   (`extract-*`).
3. Convert everything, local files only, and write the reports and the remediation plan
   (`plan-remediation`, called for you). For Grafana it also writes the data-path guide
   (`plan-grafana-datapath`), the dashboard ranking (`triage-grafana-dashboards`) and the
   quickstart match (`plan-grafana-quickstarts`).
4. One preview in plain language: for alerts, notifications and dashboards, "N will be created, M
   need your attention"; the New Relic account id and name; "everything is created disabled or
   private"; the top five reasons items need attention, with counts, and the path to the
   remediation plan; for Grafana and Dynatrace a line that your metrics must reach New Relic
   (when the data-path plan exists); and the quickstart and ranking results when they were
   produced. All numbers are counted from the converter's own output files, never estimated.
5. One confirmation. It names the account and lists the counts of each kind. Only the word `yes`
   continues; anything else leaves nothing created.
6. Create in a safe order: alert conditions, then notifications, then dashboards. The whole thing
   is one run record (for example `grafana`), so one rollback undoes all of it.
7. A short "Done" screen: what was created, where the reports are (a path; no browser is ever
   opened), the remediation plan, how to undo (pick 5 Roll back a run) and what to do next (turn
   on the alert conditions one by one, make sure your metrics reach New Relic).

| | Alerts | Notifications | Dashboards |
|---|---|---|---|
| GCP | `extract-gcp`, `convert` | `convert-gcp-notifications` (manual steps, nothing created) | `convert-dashboards --source gcp`, `apply-dashboards` |
| AWS | `extract-cloudwatch`, `convert` | `convert-notifications`, `apply-notifications` | `convert-dashboards`, `apply-dashboards` (if the account has any) |
| Grafana | `extract-grafana`, `convert` | `extract-grafana-notifications`, `convert-grafana-notifications`, `apply-notifications` | `extract-grafana-dashboards`, `convert-grafana-dashboards`, `apply-dashboards` |
| Dynatrace | `extract-dynatrace`, `convert` | `convert-notifications --source dynatrace`, `apply-notifications` (plans marked "needs review" are counted as needing attention, not created) | planned only (`extract-dynatrace-inventory`, `plan-dynatrace-dashboards`); nothing is created |

If the alerts cannot be read or converted, the flow stops and nothing was changed. If only the
notifications or the dashboards fail to read or convert, they are left out and the rest goes on.
If a create fails part-way, the menu stops, says what was created so far, and offers to roll that
back, to continue (items that already exist are skipped), or to leave it for later.

Missing settings are asked for. A token is asked with a hidden prompt, kept in this process only,
and never written to disk or to a report. Creating needs the New Relic account id (from
`NEW_RELIC_ACCOUNT_ID`), that account in `NEW_RELIC_ALLOWED_ACCOUNT_IDS`, and you typing `yes`
after a message that names the account. It only creates, never updates. Conditions and workflows
are disabled; dashboards are private. The menu never opens a browser: the markdown and HTML
reports are written and their folder is printed.

## What did I just do? (item 8)

Shows the last run: its id, source, New Relic account, status, how many objects of each kind it
created, how many are still in New Relic, what is private or disabled, and how to undo it
(item 5, or `importer rollback-run`). It reads only the local run record.

## Runs and rollback

Before a create, the menu writes a run record to `out/runs/<run id>.json` (folder `0700`, files
`0600`, written atomically; override the folder with `IMPORTER_RUNS_DIR`). After the create it
adds what was made: type, id, name, account. One source flow is one run (for example `grafana`) holding the conditions, workflows and dashboards it made, so one rollback undoes all of it. Runs recorded by earlier versions, such as `grafana-dashboards`, still list and roll back. `importer runs` lists them.

Item 5 (or `importer rollback-run`) lists runs, shows exactly what would be deleted, and asks you
to type `yes`. It then:

- deletes only objects in that record whose name starts with `ckg-`, with `Imported:` (the default
  prefix of notification objects), or is the default policy name `Imported by Conquest Importer`;
  anything else is listed and left alone (see the next section for dashboards);
- reuses `core/rollback.py`: each object is re-read and deleted only if its id still has the
  recorded name; a policy with conditions that are not ours stays;
- reads each object back from New Relic and records only those that are gone;
- updates the record (`rolled-back` or `partly-rolled-back`).

A run whose create was interrupted shows as `in-progress`. Choosing it offers "continue" (the
create runs again; existing items are skipped) or "roll back" (the run's `manifest.json` is merged
in first, so objects made before the interruption are covered).

### Sandbox runs and real migrations

Each run record stores a `sandbox` flag. It is true when the New Relic account was in
`NEW_RELIC_ALLOWED_ACCOUNT_IDS` when the run was made, and also when that list is not set (when in
doubt the strict rule applies). Records from before this flag count as sandbox.

- A sandbox record is strict: only names starting with `ckg-` or the prefixes above are deleted,
  dashboards included. A dashboard with its normal source title is listed and left alone.
- A real migration (`sandbox` is false) keeps the source title on its dashboards, so there rollback
  accepts a dashboard by kind, but only if you run `importer rollback-run --real-migration` and
  the record entry has the New Relic guid and the creation marker the tool wrote: the description
  it sent ("Imported from a Grafana dashboard. Review it." and any notes). The run id is not in
  the description, because that would change the converted output.
- Just before deleting, each such dashboard is read again. If its name or description is no longer
  the recorded one (someone renamed or edited it), or it cannot be read, it is refused with the
  reason and left alone.
- Without `--real-migration`, a real-migration record is treated as strictly as a sandbox one.

The existing `importer rollback --manifest` command is unchanged.
