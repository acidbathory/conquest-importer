# Sources of the live Azure fixtures

These files are REAL responses, read on 2026-11-11 from one free-trial subscription with a service
principal holding Reader, Monitoring Reader (subscription) and Contributor (one resource group).
The objects were created by `scripts/live-test/azure-estate/estate.py` (all named `ckg-`, alert
rules disabled). They differ from `tests/fixtures/azure/`, which holds Microsoft's official
examples.

## Layout

`<kind>/<api-version>__list.json` is the body of the subscription-scope list call, with the value
array reduced to `ckg-` objects (nothing else in the subscription was kept). `<kind>/<api-version>__get_<name>.json`
is a GET of the first object. `_meta.json` lists every call: status, total items before filtering,
how many were `ckg-`, the envelope keys and whether `nextLink` was present.

The api-versions of the main files are those of `docs/research/azure-monitor-alerting.md`. The
older versions (`2018-03-01`, `2023-12-01`, `2021-08-01`, `2020-10-01`, `2019-05-05-preview`,
`2021-09-01`) were read to see what an old client would get.

## Sanitising (`readback.py: sanitise`)

- Subscription id, tenant id: `SUBSCRIPTION_ID`, `TENANT_ID`. Resource group: `RESOURCE_GROUP`.
- Every other GUID (principal ids, customer ids, etags, role ids): `GUID-NN`, numbered per file, so the
  same value in one file has the same placeholder but numbers do not match across files. The
  `roleId` in the ARM role receiver is Microsoft's public Monitoring Contributor role id; it is
  masked too.
- Query values named `sig`, `code`, `token`, `key`, `signature` become `MASKED` (none occurred:
  no URL receiver could be created, see `docs/research/azure-live-vs-official.md`).
- No secret or token was ever written. Addresses are `ckg-test@example.invalid`.
- Zero GUID `00000000-...` is kept (it was sent as a placeholder).

Resource names (`ckg-*`, `ckgstore<random>`) and timestamps are real.

## Licence

These files are our own data, read from our own trial subscription (object names `ckg-`, no
third-party material); they are released with the repository under its Apache-2.0 licence.
