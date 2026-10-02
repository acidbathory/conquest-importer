# Real CloudWatch export (sanitized)

`describe-alarms.json` is genuine output of `aws cloudwatch describe-alarms` for 9 test alarms
created in a real AWS account on 2026-10-02 and deleted afterwards. The only edit is replacing
the account id with 123456789012. The alarms are not tied to real resources (dummy instance,
database and function names).
