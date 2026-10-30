"""Validation must accept good data and catch each kind of bad data."""
from src import config
from src.data_loader import load_all, read_raw
from src.validation import ERROR, WARNING, validate_dataset, validate_relationships


def _messages(issues):
    return " | ".join(issue.message for issue in issues)


def test_generated_demo_data_is_valid(demo_bundle):
    assert demo_bundle.issues == []


def test_missing_column_is_an_error(demo_dir):
    raw = read_raw("orders", demo_dir).drop(columns=["status"])
    issues = validate_dataset("orders", raw)
    assert any(i.severity == ERROR and i.column == "status" for i in issues)


def test_blank_required_value_is_an_error(demo_dir):
    raw = read_raw("shipments", demo_dir)
    raw.loc[0, "owner"] = ""
    issues = validate_dataset("shipments", raw)
    assert "blank 'owner'" in _messages(issues)


def test_blank_optional_value_is_allowed(demo_dir):
    raw = read_raw("tickets", demo_dir)
    raw["resolved_at"] = ""
    assert validate_dataset("tickets", raw) == []


def test_invalid_date_is_an_error(demo_dir):
    raw = read_raw("shipments", demo_dir)
    raw.loc[0, "expected_delivery_date"] = "2026-13-45"
    issues = validate_dataset("shipments", raw)
    assert "not valid dates" in _messages(issues)


def test_non_numeric_and_negative_values_are_errors(demo_dir):
    raw = read_raw("products", demo_dir)
    raw.loc[0, "stock_on_hand"] = "twelve"
    raw.loc[1, "min_stock"] = "-5"
    messages = _messages(validate_dataset("products", raw))
    assert "not numbers" in messages
    assert "negative" in messages


def test_unknown_status_is_an_error(demo_dir):
    raw = read_raw("tickets", demo_dir)
    raw.loc[0, "priority"] = "Urgent!!"
    issues = validate_dataset("tickets", raw)
    assert "Urgent!!" in _messages(issues)


def test_duplicate_ids_are_errors(demo_dir):
    raw = read_raw("orders", demo_dir)
    raw.loc[1, "order_id"] = raw.loc[0, "order_id"]
    issues = validate_dataset("orders", raw)
    assert any(i.column == "order_id" and "Duplicate" in i.message for i in issues)


def test_broken_reference_is_a_warning(demo_bundle):
    frames = dict(demo_bundle.frames)
    shipments = frames["shipments"].copy()
    shipments.loc[0, "order_id"] = "ORD-DOES-NOT-EXIST"
    frames["shipments"] = shipments
    issues = validate_relationships(frames)
    assert len(issues) == 1 and issues[0].severity == WARNING


def test_missing_file_is_reported_not_crashed(demo_dir):
    (demo_dir / config.DATASET_FILES["payments"]).unlink()
    bundle = load_all(demo_dir, mode="demo")
    assert bundle.has_errors
    assert bundle.frames["payments"].empty
