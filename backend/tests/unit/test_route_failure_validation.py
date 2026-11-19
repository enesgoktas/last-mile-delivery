import pytest
from fastapi import HTTPException

from app.models.enums import FailureReason
from app.services.route_service import validate_stop_failure_details


def test_other_failure_requires_note():
    with pytest.raises(HTTPException) as exc_info:
        validate_stop_failure_details(FailureReason.OTHER, None)
    assert exc_info.value.status_code == 400


def test_other_failure_allows_non_empty_note():
    validate_stop_failure_details(FailureReason.OTHER, "Customer asked to reschedule")


def test_non_other_failure_allows_empty_note():
    validate_stop_failure_details(FailureReason.NOT_HOME, None)


def test_custom_reason_label_requires_other_reason():
    with pytest.raises(HTTPException) as exc_info:
        validate_stop_failure_details(
            FailureReason.NOT_HOME,
            None,
            custom_reason_label="Gate locked",
        )

    assert exc_info.value.status_code == 400


def test_custom_reason_label_must_be_enabled():
    with pytest.raises(HTTPException) as exc_info:
        validate_stop_failure_details(
            FailureReason.OTHER,
            None,
            custom_reason_label="Gate locked",
            allowed_custom_reason_labels=["Business closed"],
        )

    assert exc_info.value.status_code == 400


def test_enabled_custom_reason_label_skips_other_note_requirement():
    validate_stop_failure_details(
        FailureReason.OTHER,
        None,
        custom_reason_label="Gate locked",
        allowed_custom_reason_labels=["Gate locked"],
    )
