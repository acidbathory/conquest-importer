# Repository hygiene gates

Mechanical checks that keep secrets, bad licences and copied code out of the repository. CI
(`.github/workflows/ci.yml`, jobs `secret-scan` and `supply-chain`) and `.pre-commit-config.yaml`
run them; the existing `check` job is unchanged.

## Secret scanning

- `gitleaks.toml` extends the default rules. Allowlist entries match on the **value** and only
  cover obviously fake strings already used in tests (`AKIAIOSFODNN7EXAMPLE`,
  `glsa_AbCdEf123456_...`, `dt0s16.ABCDEFGH.secretsecret` and similar). Each entry has a
  description saying why it is safe. Never allowlist a directory; never add a real token.
- Locally: `gitleaks git -c gitleaks.toml --redact --no-banner .` (history) and the pre-commit
  hook (staged changes). CI downloads a pinned release and verifies its sha256; when bumping
  the version, update `GITLEAKS_VERSION` and `GITLEAKS_SHA256` together from the release's
  checksums file.
- A finding in a real file: rotate the credential first, then remove it. Do not paste the value
  into an issue, a commit message or a chat.

## Pre-push scan

`uv run python scripts/improve/prepush_scan.py` reads the values in `.env` and the lines of the
git-ignored `.prepush-identities` (one personal name, email or company string per line) and
searches `git log -p origin/main..HEAD` and every tracked file. It prints only the `.env` key
name (or "identity #N") and the commit or file, never the value, and exits 1 on a hit. Run it
before every push.

## Licence gate and clean-room rule

`uv run python scripts/improve/license_check.py` (`--only deps|sources|clean-room`):

- every installed distribution needs a permissive licence (MIT, BSD-2/3, Apache-2.0, ISC,
  MPL-2.0, PSF, Unlicense, 0BSD, zlib, CC0) or an entry with a reason in
  `license-exceptions.toml`;
- every `tests/fixtures/**/SOURCES.md` must record a licence;
- no tracked file outside `docs/` may carry a marker of the Elastic licence, and no `SOURCES.md`
  or `manifest.json` may cite the Elastic repositories. We read competitor research for ideas
  and write our own code.

## SBOM

CI builds a CycloneDX 1.6 SBOM of the runtime dependencies (`uv export --no-dev` piped into
`cyclonedx-py requirements`) and uploads it as the `sbom-cyclonedx` artifact. `cyclonedx-bom` is
a pinned dev dependency only.
