"""Tests for the smart refresh planner."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from custom_components.edookit.scheduler import plan_next_refresh

TZ = ZoneInfo("Europe/Prague")


def at(day: int, hhmm: str) -> datetime:
    hour, minute = map(int, hhmm.split(":"))
    return datetime(2026, 10, day, hour, minute, tzinfo=TZ)


# Thursday 1.10.: lessons end 8:45 … 13:30; Friday 2.10.: first ends 8:45
ENDS = [at(1, t) for t in ("08:45", "09:40", "10:45", "11:40", "12:35", "13:30")] + [at(2, "08:45")]
OPTS = {
    "delay": timedelta(minutes=5),
    "off_school": timedelta(minutes=180),
    "quiet_start": time(22, 0),
    "quiet_end": time(6, 0),
}


def test_during_school_after_each_lesson():
    plan = plan_next_refresh(at(1, "09:00"), ENDS, **OPTS)
    assert plan.when == at(1, "09:45")
    assert plan.after_lesson and not plan.last_of_day


def test_last_lesson_of_the_day():
    plan = plan_next_refresh(at(1, "13:00"), ENDS, **OPTS)
    assert plan.when == at(1, "13:35")
    assert plan.last_of_day


def test_afternoon_uses_long_interval():
    plan = plan_next_refresh(at(1, "14:00"), ENDS, **OPTS)
    assert plan.when == at(1, "17:00")
    assert not plan.after_lesson


def test_night_is_quiet():
    plan = plan_next_refresh(at(1, "20:00"), ENDS, **OPTS)
    assert plan.when == at(2, "06:00")


def test_morning_waits_for_first_lesson():
    plan = plan_next_refresh(at(2, "06:00"), ENDS, **OPTS)
    assert plan.when == at(2, "08:50")
    assert plan.after_lesson and plan.last_of_day  # Friday has only one lesson in this list


def test_no_timetable_at_all():
    plan = plan_next_refresh(at(3, "10:00"), [], **OPTS)
    assert plan.when == at(3, "13:00")
