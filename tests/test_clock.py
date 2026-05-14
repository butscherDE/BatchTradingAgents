"""Unit tests for service.clock module."""

import datetime

from service import clock


def test_production_mode_returns_real_time():
    clock.reset()
    before = datetime.datetime.utcnow()
    result = clock.now()
    after = datetime.datetime.utcnow()
    assert before <= result <= after


def test_production_mode_is_sim_false():
    clock.reset()
    assert clock.is_sim() is False


def test_set_time_enables_sim():
    clock.reset()
    t = datetime.datetime(2025, 6, 1, 12, 0, 0)
    clock.set_time(t)
    assert clock.is_sim() is True
    assert clock.now() == t


def test_today_returns_date_string():
    clock.reset()
    clock.set_time(datetime.datetime(2025, 3, 15, 10, 30, 0))
    assert clock.today() == "2025-03-15"


def test_monotonicity_rejects_backward():
    clock.reset()
    t1 = datetime.datetime(2025, 6, 1, 12, 0, 0)
    t2 = datetime.datetime(2025, 5, 1, 12, 0, 0)  # earlier
    clock.set_time(t1)
    clock.set_time(t2)
    assert clock.now() == t1  # didn't go backward


def test_monotonicity_allows_forward():
    clock.reset()
    t1 = datetime.datetime(2025, 6, 1, 12, 0, 0)
    t2 = datetime.datetime(2025, 6, 2, 12, 0, 0)
    clock.set_time(t1)
    clock.set_time(t2)
    assert clock.now() == t2


def test_reset_returns_to_real_time():
    clock.set_time(datetime.datetime(2025, 1, 1))
    assert clock.is_sim() is True
    clock.reset()
    assert clock.is_sim() is False
    # now() should be close to real time
    diff = abs((clock.now() - datetime.datetime.utcnow()).total_seconds())
    assert diff < 1.0


def test_first_set_time_from_none():
    clock.reset()
    t = datetime.datetime(2025, 6, 1, 12, 0, 0)
    clock.set_time(t)
    assert clock.now() == t
