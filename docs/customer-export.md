# Sharing results safely

Two kinds of output, with very different privacy:

| File | Contains | Share it? |
|---|---|---|
| `importer stats <folder>` output | Conversion rates and failure reasons, with names, ARNs and account ids stripped (a test enforces it) | **Yes.** Paste it in a [feedback issue](../../../issues/new/choose) |
| `report.html`, `export/`, `report.md` | Real alarm names, resource names, ARNs, account ids and email addresses | **No**, not without reviewing and removing anything sensitive |

Never share keys or an `.env` file.

## If someone else owns the AWS account

They do not need to install anything but Python. Send them [permissions](permissions.md) and
this:

```sh
pip install .
export AWS_ACCESS_KEY_ID=...  AWS_SECRET_ACCESS_KEY=...
importer extract-cloudwatch --account-id <12-digit id> --region <region> --out export
```

It only reads, and stops if the keys belong to a different account. They review `export/`, then
send it to you to run `importer convert`, `importer report` and `importer stats` on.
