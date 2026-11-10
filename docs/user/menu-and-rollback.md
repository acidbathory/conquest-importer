# The guided menu and rollback

Run `importer menu` in a terminal. It needs a person at the keyboard: with piped input it prints
a one-line message and exits.

```
1 GCP   2 AWS   3 Grafana   4 Dynatrace
5 Roll back a run   6 Check my setup   7 Try on sample data   0 Quit
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

## Runs and rollback

Before a create, the menu writes a run record to `out/runs/<run id>.json` (folder `0700`, files
`0600`, written atomically; override the folder with `IMPORTER_RUNS_DIR`). After the create it
adds what was made: type, id, name, account. `importer runs` lists them.

Item 5 (or `importer rollback-run`) lists runs, shows exactly what would be deleted, and asks you
to type `yes`. It then:

- deletes only objects in that record whose name starts with `ckg-` or is the default policy name
  `Imported by Conquest Importer` (anything else is listed and left alone);
- reuses `core/rollback.py`: each object is re-read and deleted only if its id still has the
  recorded name; a policy with conditions that are not ours stays;
- reads each object back from New Relic and records only those that are gone;
- updates the record (`rolled-back` or `partly-rolled-back`).

A run whose create was interrupted shows as `in-progress`. Choosing it offers "continue" (the
create runs again; existing items are skipped) or "roll back" (the run's `manifest.json` is merged
in first, so objects made before the interruption are covered).

The existing `importer rollback --manifest` command is unchanged.
