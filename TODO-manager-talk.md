# Conquest Importer: notes for the conversation with my manager

## The one-line pitch
A tool that reads a customer's Datadog monitors and produces the equivalent New Relic alert conditions (NerdGraph + Terraform), with a report of what converted and what needs manual work. It removes the biggest reason customers stay with a competitor: the effort of rebuilding their alerting.

## Where things stand
- The conversion pipeline is built and tested offline (67 automated tests, strict type checks, security checks).
- On 20 synthetic Datadog monitors: **9 convert automatically (45%)**: 5 exact, 4 approximate, and the other 11 are labelled with the reason they need manual work. Target for the proof of concept is 70% or more.
- It has **not** been run against a real Datadog account, because of the blocker below.

## The blocker
`api.datadoghq.com` is blocked on our network (Cloudflare Gateway shows "This website is blocked"). I can't create a Datadog trial's monitors or read them back until it is allowed.

## What I need from my manager (decisions)
1. **Approval to use a self-created Datadog trial account** with synthetic data only (no customer data), and to call Datadog's public API.
2. **A network exception** for `api.datadoghq.com` (and `app.datadoghq.com` for the web UI), or the right person to ask in IT / security.
3. **If an exception is not possible:** is it OK to run the Datadog part from a non-corporate machine? Or should I continue with hand-written sample data only?
4. **Legal / policy check:** any concern with reading a competitor's API using our own trial account? Any review needed before a demo?
5. **A New Relic test account** (or sub-account) where generated alerts can be created safely. Who provisions it?
6. **Sponsorship / audience:** who should see the demo, and is this a prototype for internal review only?

## Suggested message for IT / security
> Request to allowlist api.datadoghq.com and app.datadoghq.com for a competitive-migration prototype. It uses a self-created Datadog trial account with synthetic monitors only, via Datadog's public API, with no customer data. Read-only keys for extraction and a temporary write key to create the sample monitors, deleted afterwards.

## My to-dos
- [ ] Talk to manager (decisions 1-6 above)
- [ ] Get Datadog access unblocked (or an approved alternative)
- [ ] Recreate the Datadog API and Application keys. The current ones were pasted into a chat, so delete them in Datadog and create new ones. Type them into `.env` directly, not into any chat
- [ ] Create a read-only Application key (monitors_read) for extraction, and a temporary write key (monitors_write) for creating sample monitors. Delete the write key afterwards
- [ ] Set `DD_SITE` in `.env` to match the Datadog address bar (for example `datadoghq.com`, `us5.datadoghq.com`)
- [ ] Set up the New Relic test account and a limited user key (alert write only)
- [ ] Install Terraform (`brew install terraform`) so the generated files can be validated
- [ ] Run `uv run python scripts/create_sample_monitors.py` (dry run), then with `--apply`
- [ ] Run `uv run importer extract`, then `uv run importer convert`, and review `out/converted/report.md`
- [ ] Udemy Python course (ongoing)

## My to-dos for Claude (next build steps)
- [ ] Hand-written Datadog fixtures so work can continue without live access
- [ ] Translate arithmetic queries, log monitors and service checks (raises the conversion rate)
- [ ] Verify and extend the metric mapping file against a real New Relic account
- [ ] Add the guarded `--apply` step (test-account allow-list, diff first)
- [ ] First git commit

## Risks to mention honestly
- The metric and tag mappings are unverified against real New Relic data.
- Datadog's "average over 5 minutes" and New Relic's "1-minute values breaching for 5 minutes" are close but not identical. The report says so, and conditions are created disabled.
- Notification channels are not migrated, only listed.
- The conversion rate (45% today) depends on how typical the sample monitors are. Real customer accounts may differ.

## Not for sharing
This file contains no keys. Never paste API keys into chat, email or tickets.
