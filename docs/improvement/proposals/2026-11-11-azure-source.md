# Proposal: add the Azure source to the improvement baseline

Branch `improve/azure-source`. Nothing pinned was regenerated. The owner decides.

## What changed that the pinned files could notice

- `docs/improvement/baseline.json` has no entry for `azure`. The new source registers its fixture
  folders with `register_fixtures`, so `importer improve-measure` now measures `azure` too
  (`tests/fixtures/azure`, plus `tests/fixtures/azure-live` when it exists). The gate compares against
  the old baseline and passes because a source that is missing from a baseline cannot regress.
- No golden file or hash file changed (`tests/golden`, `tests/test_emitter_byte_identity.py` and the
  dashboard identity tests pass unchanged). The shared emitter has one new optional hook: a monitor
  whose `unmapped` holds `description_note` gets that text in parentheses in its description. No
  existing source sets it, so their output is byte for byte what it was.
- The guided menu shows one more line, `10  Azure`. Two tests that used `10` as an invalid choice
  and counted four sources in "Check my setup" were changed (11 and five).

## What to approve

1. Record the Azure line in `docs/improvement/baseline.json` with
   `importer improve-measure --update-baseline` (or the loop's usual command). Expected content,
   from [the baseline note](../../research/azure-baseline.md): official examples only, 61 distinct
   alert rules, 6 converted, 48 manual, 7 unsupported. The measure also reads the 18 hostile
   synthetic files in the same folder, so its own counts are higher than the note's; the note
   excludes them on purpose.
2. Decide whether the synthetic files belong in the measured folder. If not, register
   `tests/fixtures/azure/metric_alerts` and the four sibling alert folders instead of the root.

Nothing else needs a decision.
