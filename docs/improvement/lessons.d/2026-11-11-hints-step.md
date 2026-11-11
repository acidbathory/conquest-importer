## 2026-11-11: cycle `hints-step`

| Defect class | How it hid | Check that catches the class |
|---|---|---|
| A converter input that only a separate command produces (hints) was never offered in the guided flow | `shape.sum_of_gauge` stayed the top blocker because nobody ran `grafana-prom-hints` by hand | `tests/test_menu_hints.py` asserts the flow runs hints before the dashboard conversion and passes `--hints`; the skipped path says what is lost |
| A read-only client tied to one transport (Grafana proxy) | A person with only a Prometheus URL could not use it | Direct getter shares the allow-list check with the proxy one; `tests/test_grafana_prom_hints_direct.py` parametrizes the refused paths |
