"""When to refresh Edookit data ("smart" refresh).

During school the data is refreshed shortly after every lesson ends (teachers
enter grades, homework and notes after the lesson). Outside school hours a
longer interval is used and at night nothing is downloaded at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta


@dataclass(frozen=True)
class RefreshPlan:
    """The next refresh."""

    when: datetime
    after_lesson: bool  # triggered by the end of a lesson
    last_of_day: bool  # ... by the end of the day's last lesson (also refresh the timetable)


def _in_quiet(moment: datetime, start: time, end: time) -> bool:
    now = moment.time()
    if start == end:
        return False
    if start < end:
        return start <= now < end
    return now >= start or now < end  # e.g. 22:00-06:00 across midnight


def _quiet_end_after(moment: datetime, start: time, end: time) -> datetime:
    """First moment at or after ``moment`` that is outside the quiet hours."""
    candidate = moment.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
    if candidate <= moment:
        candidate += timedelta(days=1)
    return candidate


def plan_next_refresh(
    now: datetime,
    lesson_ends: list[datetime],
    *,
    delay: timedelta,
    off_school: timedelta,
    quiet_start: time,
    quiet_end: time,
) -> RefreshPlan:
    """Pick the next refresh time.

    ``lesson_ends`` are the end times of the (not cancelled) lessons and timed
    events of all children; duplicates are fine.
    """
    after_lessons = sorted({end + delay for end in lesson_ends if end + delay > now})
    regular = now + off_school
    if after_lessons and after_lessons[0] <= regular:
        when = after_lessons[0]
        day_ends = [end + delay for end in lesson_ends if end.date() == when.date()]
        plan = RefreshPlan(when, True, when >= max(day_ends))
    else:
        plan = RefreshPlan(regular, False, False)
    if _in_quiet(plan.when, quiet_start, quiet_end):
        resume = _quiet_end_after(plan.when, quiet_start, quiet_end)
        # A lesson may end before the quiet hours are over (rare, e.g. 0:00-0:00 settings).
        upcoming = [t for t in after_lessons if t > plan.when and not _in_quiet(t, quiet_start, quiet_end)]
        if upcoming and upcoming[0] < resume:
            return RefreshPlan(upcoming[0], True, False)
        return RefreshPlan(resume, False, False)
    return plan
