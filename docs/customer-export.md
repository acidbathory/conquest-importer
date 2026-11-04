# Sharing results safely

The importer writes two kinds of output, and they differ a lot in what they reveal:

| File | Contains | Share it? |
|---|---|---|
| `importer stats <folder>` output | Conversion rates and failure reasons, with names, ARNs and account ids stripped (a test enforces it) | Yes. Paste it in a [feedback issue](../../../issues/new/choose) |
| `report.html`, `export/`, `report.md` | Real alarm names, resource names, ARNs, account ids and email addresses | No, not until you have reviewed it and removed anything sensitive |

Never share keys or an `.env` file.

## If someone else owns the AWS account

They only need Python. Send them [permissions](permissions.md) and this:

```sh
pip install .
export AWS_ACCESS_KEY_ID=...  AWS_SECRET_ACCESS_KEY=...
importer extract-cloudwatch --account-id <12-digit id> --region <region> --out export
```

It only reads, and stops if the keys belong to a different account. They review `export/`, then
send it to you to run `importer convert`, `importer report` and `importer stats` on.
