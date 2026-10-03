from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine

from app.models.ai_usage_reservation import Operation, ReservationState as State
from app.services.ai_quota import (QuotaLimits, QuotaService, QuotaUnavailable,
                                  canonical_fingerprint, retry_after, utc_bounds, validate_key)

T = datetime(2026, 10, 3, 12, tzinfo=UTC)


@pytest.mark.parametrize("field", ["user_window", "window_seconds", "user_daily", "global_daily"])
@pytest.mark.parametrize("value", [0, -1, True, 1.5, "5"])
def test_limits_are_positive_integers(field, value):
    with pytest.raises(ValidationError):
        QuotaLimits(**{field: value})


@pytest.mark.parametrize("key", ["", "x" * 129, "中文", 42])
def test_invalid_keys(key):
    with pytest.raises(ValueError):
        validate_key(key)


def test_keys_and_canonical_fingerprints():
    assert validate_key(None) != validate_key(None)
    assert validate_key("x" * 128) == "x" * 128
    assert canonical_fingerprint(Operation.RAG_QUERY, {"question": "x", "course_id": 1}) == canonical_fingerprint(Operation.RAG_QUERY, {"course_id": 1, "question": "x"})
    assert canonical_fingerprint(Operation.RAG_QUERY, {"course_id": 1}) != canonical_fingerprint(Operation.RAG_QUERY, {"course_id": 2})
    assert canonical_fingerprint(Operation.EXTRACTION, {"text": "x"}) != canonical_fingerprint(Operation.RAG_QUERY, {"text": "x"})


def test_utc_and_reduced_limits():
    from datetime import timezone
    assert utc_bounds(T) == utc_bounds(T.astimezone(timezone(timedelta(hours=8))))
    records = [T - timedelta(seconds=s) for s in [50, 40, 30, 20, 10]]
    assert retry_after(T, records, 5, 5, QuotaLimits(user_window=2)) == 40
    assert retry_after(T, records, 20, 5, QuotaLimits(user_window=2)) == 43200
    assert retry_after(T, [], 0, 100, QuotaLimits()) == 43200
    assert retry_after(T, [], 0, 0, QuotaLimits()) is None
    with pytest.raises(ValueError):
        utc_bounds(T.replace(tzinfo=None))


def test_ties_and_fractional_expiry():
    assert retry_after(T, [T - timedelta(seconds=59.9)] * 3, 0, 0, QuotaLimits(user_window=2)) == 1


def test_no_sqlite_fallback():
    service = QuotaService(create_engine("sqlite://"))
    with pytest.raises(QuotaUnavailable):
        service.reserve(1, Operation.EXTRACTION, "a" * 64)


def test_best_effort_preserves_result_and_safe_log(caplog):
    service = QuotaService(create_engine("sqlite://"))
    service.settle = Mock(side_effect=QuotaUnavailable())
    assert service.settle_best_effort(Mock(), State.SUCCEEDED) is False
    assert len(caplog.records) == 1
    assert caplog.records[0].event == "ai_quota_settlement_failed"
    assert not caplog.records[0].exc_info
    with pytest.raises(ValueError):
        QuotaService(create_engine("sqlite://")).settle(Mock(), State.CANCELLED)
