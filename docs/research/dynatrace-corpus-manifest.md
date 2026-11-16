# Dynatrace public corpus manifest (2026-11-16)

Shallow clones under /tmp/community/dynatrace-src/<name>/repo, read as text only. Built set: /tmp/community/dynatrace-corpus (builder: /tmp/community-tools/build.py; Monaco {{ .x }} template placeholders replaced by the literal TPL so templates parse). Converter output: /tmp/community/dynatrace-out. Converter worktree commit: 53cbfbc441ab732e30a019bfc5b4bbac4de482e3.

| Repo | Licence (LICENSE file) | Commit |
|---|---|---|
| Dynatrace/dynatrace-configuration-as-code | Apache-2.0 | 3c9d487b9d388769874dbbbecf7d6b8b28e872fa |
| Dynatrace/dynatrace-configuration-as-code-samples | Apache-2.0 | 82b43c66bab0fcfe85cdf2f9edcd94b703ca7820 |
| Dynatrace/Dynatrace-workflow-samples | Apache-2.0 | a6319c5c4ad2d68fb7e8d6d013ec6ae91c14d24f |
| dynatrace-oss/terraform-provider-dynatrace | Apache-2.0 (LICENSE file; the brief said MPL-2.0, the file says Apache) | b1760a6cf3b2ad83ecbffa86aa82db332aa237b8 |
| dynatrace-oss/nvidia-observability | Apache-2.0 | 2de34717645e56c4bd4e77b8a27cbd9591f81f6e |
| dynatrace-oss/nutanix-observability | Apache-2.0 | bc7094815b1fee117aa2700ef3c4a91c64c4d6d9 |
| dynatrace-oss/redhat-observability | Apache-2.0 | ce09b2b45c5ec823876dc6a72625d38513c29ad7 |
| dynatrace-oss/dynatrace-snowflake-observability-agent | MIT | 2c3e8196e49161f468042527475608dc798e01c8 |
| dynatrace-oss/DynatraceDashboardPowerups | Apache-2.0 | 847549479b411b2cec2d2efa977cd951e4a203a6 |
| Dynatrace/BizOpsConfigurator | Apache-2.0 | 61bd337d46237f0ff9cb6ca6de38967853072f37 |
| Dynatrace/obslab-llm-observability | Apache-2.0 | a4bf97e4c48c87ba5ace3986322bb35e0525e0ea |
| Dynatrace/demo-cert-manager-observability | Apache-2.0 | dc5e0e3e5cbd880d6a26566345becdd093bdc482 |
| Dynatrace/livedebugger-file-coverage-dashboard | Apache-2.0 | 6af91ffccd90e29fcf698adaa67f7b6394d5d616 |
| dynatrace-oss/batch-configurator | Apache-2.0 | 2bb3f489eeb1826b6e13dcdc5f09ba154b1a56c8 |

Note: the old dynatrace-oss/dynatrace-configuration-as-code* names no longer resolve; they live under the Dynatrace org.

Built corpus (deduplicated): 38 Gen3 dashboards (454 data tiles, 229 markdown), 12 classic dashboards (9 planned, 17 data tiles), 19 workflows, 302 Settings objects (96 management zones, 20 SLO, 7 alerting profiles, 5 notifications, 4 maintenance windows, 3 metric events, 1 Davis detector, rest unrelated config). Not measured: HCL examples (terraform provider testdata, about 830 .tf files) because our converters read JSON only.
