# AWS CloudWatch corpus manifest (2026-11-16)

Shallow clones (depth 1) under /tmp/community/aws-src/. Read as text only; nothing executed. Licence column is a coarse read of the LICENSE head (Apache-2.0, MIT, MIT-0 shown as "Permission is hereby" = MIT or MIT-0, to be confirmed per repo); "none" means no LICENSE file was found.

Extractor: /tmp/community-tools/awsextract.py (python3 -I). Output corpus: /tmp/community/aws-corpus/{alarms,dash}; non-mzazon subset: /tmp/community/aws-corpus-nomz. Converter output: /tmp/community/aws-out, /tmp/community/aws-out-nomz. Scoreboards: /tmp/community/aws-sb, /tmp/community/aws-sb-nomz.
Resolution rules: Ref to a parameter with a Default, AWS::Region=us-east-1, AWS::AccountId=123456789012; alarm Threshold/Namespace/Period/etc. must be literal, else the alarm is dropped. Dimension values and expression text that cannot be resolved keep the reference name (not structural). Terraform: literal attributes and one-level dimensions only; metric_query alarms dropped.
Dropped: 479 CloudFormation alarms and 325 Terraform alarms unresolved or math-in-HCL; 5 dashboards (3 JSON decode, 2 unresolved). 1 composite alarm seen, not run.

| Repo | Commit | Licence (coarse) |
|---|---|---|
| https://github.com/aws-cloudformation/aws-cloudformation-templates | a0f43bc6d20813052892546f445037cf84c75b54 | Apache License |
| https://github.com/aws-observability/observability-best-practices | 81b568ff929ade02ac9a2ccf2684c51cd0da879b | Permission is hereby |
| https://github.com/aws-samples/amazon-cloudwatch-auto-alarms | 50afdf6c59356e39a4de9f2fd491a10f34424814 | Permission is hereby |
| https://github.com/aws-samples/amazon-cloudwatch-dashboard-automation | d0a1a15781eed73470cafaa5e4c6fb6b37e36cb5 | Permission is hereby |
| https://github.com/aws-samples/amazon-connect-instance-dashboard | 3a1416d792e2977efcefe25368e6d31bfcf572a3 | Permission is hereby |
| https://github.com/aws-samples/aws-batch-runtime-monitoring | 524e08838c67543614ab86edde8be219fbfbdcb0 | Permission is hereby |
| https://github.com/aws-samples/aws-cloudwatch-building-dashboard-outside-aws-console | d25271bd69e5507dfa1f64ba1f54bf7515d4a22c | Apache License |
| https://github.com/aws-samples/aws-cloudwatch-dashboard-generator | 8e7b2df09907e4fd4074c5a545a00989c332f2f9 | Permission is hereby |
| https://github.com/aws-samples/aws-gamelift-and-serverless-backend-sample | 5173e1fa37de71eb732f17bb91fbea7dab400a59 | Apache License |
| https://github.com/aws-samples/aws-medialive-mediapackage-cloudwatch-dashboard | 4de9002d308ce231516eefc441b8df8298cb868c | Apache License |
| https://github.com/aws-samples/aws-refarch-moodle | dbb43e629d207b148ab4c69dd9c42283f6549702 | Apache License |
| https://github.com/aws-samples/cfn101-workshop | 2f01dbb8d58b9cda6ed2749b72f5651fc32c1d54 | Permission is hereby |
| https://github.com/aws-samples/cloudwatch-alarm-management-for-asg | 2913fffea8c2f489106624b9175fc2f9fc6906ec | Permission is hereby |
| https://github.com/aws-samples/cloudwatch-custom-widgets-samples | e337b8de4d0f5edf906d7ac2b17d609248301cc8 | Permission is hereby |
| https://github.com/aws-samples/cloudwatch-dashboards-cloudformation-sample | e7302237c9996d3fe2590c84d4d9902e0a60782d | Apache License |
| https://github.com/aws-samples/ec2-spot-interruption-dashboard | e7e2ecb1ad0a555b14f07435aa6dd92a6f7bb260 | Permission is hereby |
| https://github.com/aws-samples/mwaa-dashboard | e95799e4a83511f8bd45eb1ad4abd335b878bf1c | Permission is hereby |
| https://github.com/aws-samples/sample-quota-dashboard-for-amazon-bedrock | 3abec9862c56bf1d24d58e7e95257b75607de5ca | MIT No Attribution |
| https://github.com/aws-samples/sample-recommended-msk-cloudwatch-alarms | 41754f0f4c0602073bfe5461b1894e108a4a2424 | MIT No Attribution |
| https://github.com/aws-samples/sample-sns-sqs-multi-region | 01901fbf0dfdc528ffe8a04e121c76a5d6ba4ed4 | MIT License |
| https://github.com/aws-samples/serverless-patterns | 9a221abbf3382ee94e02449fb830d2b9780918d2 | Permission is hereby |
| https://github.com/aws-samples/serverless-samples | d8b80a19a88dbc596d2e2ead2c466fc2780f1eb0 | Permission is hereby |
| https://github.com/aws-samples/universal-cloudwatch-alarm-template | 15a69a6f5efdc7293885371ed6202ab064ae3290 | Permission is hereby |
| https://github.com/awslabs/cloudwatch-xray-apm-demo | ffb1935d68924be2df88154f502196ad69569a8f | Apache License |
| https://github.com/awslabs/ec2-spot-labs | 545190ba692d300e0c2f4ed176f26975edc8c8c3 | none |
| https://github.com/awstut-an-r/awstut-fa | c798853df91216d28d2159de862a0a0ef3fa5bd2 | none |
| https://github.com/boto/botocore | e250abbe956da791c16086575e6ddb1f2da81a78 | Apache License |
| https://github.com/cdk-patterns/serverless | 08e31630e738f1b463c640cad1de6ca73d85f1c2 | MIT License |
| https://github.com/awsdocs/amazon-cloudwatch-user-guide | 3e2afd415f0d55a826df87e8a4a857b5aaf5e817 | none (stale 2023 stub, unused) |
| https://github.com/govuk-one-login/ipv-core-back | 0484eff92c7e58aace28025709f749119f4ea29b | MIT License |
| https://github.com/govuk-one-login/txma-ticf-integration | 9d5903bec99c1a2da4a1dcb93adff8722a0704ba | MIT License |
| https://github.com/guardian/gateway | 68ec695db5dff76de9fec0985c38460edff145f9 | none |
| https://github.com/mzazon/cloud-projects | 7b63ce57f310642ed4a9f040787d51450600abb6 | none |
| https://github.com/superwerker/superwerker | af33aec8a3ae5a006100cf199ab9a9f8895aa9ea | MIT License |
| https://github.com/takeshimiao/aws-cicd | 73964765f6698ca1fda277edf591a2b66b0366bb | Apache License |
| https://github.com/terraform-aws-modules/terraform-aws-cloudwatch | f6dff9f79a5f4d2653b359182364f30db638ad48 | Apache License |
| https://github.com/widdix/aws-cf-templates | f4c49a4c4648dd6047939b1a98281ce4748b739e | Apache License |
| https://github.com/newrelic/docs-website (reference only) | a9e157231289b0cb3880e942b36c0ab4a39ff811 | CC-BY-4.0 docs (LICENSE) |

Corpus skew: mzazon/cloud-projects supplies 591 of 983 alarms and 224 of 271 dashboards (AI-style recipe repo, licence not confirmed). All numbers are given with and without it.
Counts: 980 alarms read (695 converted), 271 dashboards (1019 data widgets, 411 converted). Without mzazon: 390 alarms (305), 47 dashboards (239 data widgets, 75).
