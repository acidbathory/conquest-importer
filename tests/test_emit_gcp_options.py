"""Emitter settings the Google Cloud converter uses: description note, prediction, expiration."""

from test_emit import convert

from importer.core.emit.nerdgraph import nerdgraph_payload
from importer.core.emit.terraform import resource_block
from importer.core.ir import EmitOptions, IRMonitor


def with_emit(monitor: IRMonitor, **options: object) -> IRMonitor:
    monitor.emit = EmitOptions.model_validate(options)
    return monitor


def condition(monitor: IRMonitor) -> dict[str, object]:
    cond = nerdgraph_payload(monitor)["variables"]["condition"]
    assert isinstance(cond, dict)
    return cond


def test_description_note_follows_the_source_line_and_is_cleaned() -> None:
    m = with_emit(convert(), description_note="Subject: disk\x1b[31m full")
    assert condition(m)["description"] == "Imported from AWS CloudWatch 7\nSubject: disk?[31m full"
    assert '"Imported from AWS CloudWatch 7\\nSubject: disk?[31m full"' in resource_block(m)


def test_description_is_capped() -> None:
    m = with_emit(convert(), description_note="x" * 5000)
    text = condition(m)["description"]
    assert isinstance(text, str) and len(text) == 1000 and text.endswith("...")


def test_prediction_goes_on_every_term() -> None:
    m = with_emit(convert(warning=70), predict_by_seconds=14400)
    terms = condition(m)["terms"]
    assert isinstance(terms, list)
    assert all(t["prediction"] == {"predictBy": 14400} for t in terms)
    tf = resource_block(m)
    assert tf.count("    prediction {\n      predict_by = 14400\n    }") == 2


def test_no_options_changes_nothing() -> None:
    m = convert()
    assert "prediction" not in str(condition(m)["terms"])
    assert "prediction" not in resource_block(m)


def test_close_on_loss_of_signal() -> None:
    m = with_emit(
        convert(notify_no_data=True),
        open_violation_on_expiration=False,
        close_violations_on_expiration=True,
    )
    assert condition(m)["expiration"] == {
        "expirationDuration": 600,
        "openViolationOnExpiration": False,
        "closeViolationsOnExpiration": True,
    }
    tf = resource_block(m)
    assert "open_violation_on_expiration   = false" in tf
    assert "close_violations_on_expiration = true" in tf
