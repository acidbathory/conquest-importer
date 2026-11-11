# Azure notifications: baseline on the fixtures

Measured 2026-11-11 offline with `convert_notifications` over all of `tests/fixtures/azure/` (action
groups: the official examples plus the three synthetic ones, deduplicated by resource id: 3 groups
and 21 receivers; processing rules: 9 after deduplication, the 2019 preview examples collapse into
the 2021 ones of the same id). Nothing was called in Azure or New Relic. A receiver or rule counts as
converted only when a real destination or muting rule is in the plan; masked placeholders such as
`<serviceUri>` and `<httpTriggerUrl>` (which the official example responses return) count as manual.

## Receivers

| Type | Converted | of which approximate | Manual (a New Relic type exists) | No New Relic destination |
|---|---|---|---|---|
| email | 3 | 0 | 0 | 0 |
| webhook | 1 | 1 | 3 (Entra auth, credentials in URL, no URL) | 0 |
| logic app | 2 | 2 | 0 | 0 |
| Azure function | 1 | 1 | 1 (placeholder URL) | 0 |
| automation runbook | 1 | 1 | 1 (placeholder URL) | 0 |
| ITSM | 0 | 0 | 1 | 0 |
| app push | 0 | 0 | 1 | 0 |
| SMS | 0 | 0 | 0 | 3 |
| voice | 0 | 0 | 0 | 1 |
| ARM role | 0 | 0 | 0 | 1 |
| Event Hub | 0 | 0 | 0 | 1 |
| **all (21)** | **8** | **5** | **7** | **6** |

Approximate means the webhook body is a New Relic template laid out like the Azure common alert
schema, not the schema itself. Three of the 8 converted destinations hold a URL secret as a
`SecretRef` (`nr_secret_azure_*`); the rest need none.

## Alert processing rules (9)

| Outcome | Without tags on the converted conditions | With the optional tags (`docs/dev/azure-notification-link.md`) |
|---|---|---|
| Converted to muting rules | 1 (2 muting rules) | 2 (3 muting rules) |
| Nothing to mute (no converted condition matches) | 1 | 1 |
| Manual | 7 | 6 |

Manual reasons: 4 `AddActionGroups` rules (no equivalent; workflow workaround), 2 rules that need a
resource group, resource or resource type tag the converted conditions do not yet carry (one of
them converts once the tags exist), 1 unknown action type (synthetic). The converted rule is a
daily plus a weekly window in `America/New_York`.

The two measurements used two stand-in converted conditions; with the real alert conversion the
numbers change with its tags. Placeholders are never counted.

## Not measured

Real list responses (whether secret URLs are masked), the Windows time zone names Azure returns,
that the `azure.actionGroup` tag reaches New Relic issues, and that a muting rule with no schedule is
accepted are UNVERIFIED; the live fixtures another agent adds under `tests/fixtures/azure-live/`
settle them.
