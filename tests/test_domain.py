from datetime import datetime

import pytest

from app.domain import TZ, Agenda, Clock

NOW = datetime(2026, 9, 28, 8, 0, tzinfo=TZ)  # lunes


@pytest.fixture
def agenda() -> Agenda:
    return Agenda(clock=Clock(NOW))


@pytest.mark.parametrize(
    "service,start,reason",
    [
        ("limpieza", datetime(2026, 9, 29, 10, 0, tzinfo=TZ), "ok"),
        ("limpieza", datetime(2026, 9, 27, 10, 0, tzinfo=TZ), "in_past"),
        ("limpieza", datetime(2026, 10, 4, 10, 0, tzinfo=TZ), "closed"),  # domingo
        ("limpieza", datetime(2026, 9, 29, 14, 0, tzinfo=TZ), "closed"),  # mediodía
        ("blanqueamiento", datetime(2026, 9, 29, 13, 30, tzinfo=TZ), "closed"),  # 60 min no cabe
        ("limpieza", datetime(2026, 9, 29, 10, 15, tzinfo=TZ), "not_on_grid"),
        ("cirugia", datetime(2026, 9, 29, 10, 0, tzinfo=TZ), "unknown_service"),
        ("limpieza", datetime(2027, 1, 12, 10, 0, tzinfo=TZ), "too_far"),
    ],
)
def test_is_bookable(agenda, service, start, reason):
    assert agenda.is_bookable(service, start)[1] == reason


def test_overlap_is_busy(agenda):
    agenda.book("blanqueamiento", datetime(2026, 9, 29, 10, 0, tzinfo=TZ), "A", "s")
    assert agenda.is_bookable("limpieza", datetime(2026, 9, 29, 10, 30, tzinfo=TZ))[1] == "busy"
    assert agenda.is_bookable("limpieza", datetime(2026, 9, 29, 11, 0, tzinfo=TZ))[1] == "ok"


def test_book_rejects_invalid(agenda):
    with pytest.raises(ValueError):
        agenda.book("limpieza", datetime(2026, 10, 4, 10, 0, tzinfo=TZ), "A", "s")
