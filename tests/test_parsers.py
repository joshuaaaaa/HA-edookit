"""Tests for the HTML parsers."""

from datetime import date

from custom_components.edookit import parsers
from custom_components.edookit.ical import ical_to_lessons, parse_ical

from .conftest import load

TODAY = date(2026, 9, 30)


def test_parse_date_variants():
    assert parsers.parse_date("Po 2. 2. 2026", TODAY) == date(2026, 2, 2)
    assert parsers.parse_date("14. 9. 2026, 10:47", TODAY) == date(2026, 9, 14)
    assert parsers.parse_date("Dnes, 8:15", TODAY) == TODAY
    assert parsers.parse_date("Včera", TODAY) == date(2026, 9, 29)
    assert parsers.parse_date("zítra", TODAY) == date(2026, 10, 1)
    # Without a year: nearest occurrence.
    assert parsers.parse_date("3. 1.", TODAY) == date(2027, 1, 3)
    assert parsers.parse_date("2026-10-05", TODAY) == date(2026, 10, 5)
    assert parsers.parse_date("nonsense", TODAY) is None


def test_parse_grade_value():
    assert parsers.parse_grade_value("1") == 1.0
    assert parsers.parse_grade_value("2-") == 2.5
    assert parsers.parse_grade_value("1+") == 0.75
    assert parsers.parse_grade_value("výborně") == 1.0
    assert parsers.parse_grade_value("Splnil") is None
    assert parsers.parse_grade_value("") is None


def test_login_page_detection():
    assert parsers.is_login_page(load("login.html"))
    assert parsers.is_login_page("<html></html>", "https://x.edookit.net/user/login?back=1")
    assert not parsers.is_login_page(load("dashboard.html"), "https://x.edookit.net/")


def test_parse_inbox():
    items = parsers.parse_inbox(load("inbox.html"), TODAY)
    assert [i["type"] for i in items] == ["inboxMessage", "evaluation", "assignment"]
    message = items[0]
    assert message["title"] == "Třídní schůzky"
    assert message["unread"] is True
    assert message["url"] == "/messages/detail?message=123"
    assert message["timestamp"] == "2026-09-30T08:15:00"
    grade = items[1]
    assert grade["grade"] == "2-"
    assert grade["url"] == "/evaluation/detail?evaluationId=55"
    assert grade["timestamp"] == "2026-09-14T10:47:00"
    assert items[2]["timestamp"] == "2026-09-29T13:00:00"


def test_dashboard():
    html = load("dashboard.html")
    assert parsers.parse_student_name(html) == "Jan Novák"
    assert parsers.parse_school_year(html) == "2026/27"
    actions = parsers.parse_action_items(html)
    assert actions[0] == {
        "name": "Školní výlet",
        "info": "450 Kč do 30. 9.",
        "url": "/payments/detail?payment=7",
        "kind": "payment",
    }
    assert actions[1]["url"] == "/polls/detail?poll=3"


def test_upcoming_events():
    events = parsers.parse_upcoming_events(load("upcoming.html"), TODAY)
    assert len(events) == 2
    trip, meeting = events
    assert trip["title"] == "Ozdravný pobyt"
    assert trip["start"] == "2026-10-02"
    assert trip["end"] == "2026-10-04"
    assert trip["description"] == "Odjezd v 8:00 od školy"
    assert trip["creator"] == "Mgr. Petra Svobodová"
    assert meeting["start"] == "2026-10-15"
    assert (meeting["start_time"], meeting["end_time"]) == ("16:00", "18:00")
    assert meeting["url"] == "/timetable/detail?event=78"


def test_evaluations_and_averages():
    grades = parsers.parse_evaluations(load("evaluation_list.html"), TODAY)
    assert len(grades) == 4
    assert grades[0]["subject"] == "Matematika"
    assert grades[0]["topic"] == "Zlomky"
    assert grades[0]["weight"] == 2.0
    assert grades[1]["date"] == "2026-09-29T00:00:00"
    averages = parsers.grade_averages(grades)
    # (1*2 + 3*1) / 3
    assert averages["Matematika"] == 1.67
    assert averages["Český jazyk"] == 2.5
    assert "Tělesná výchova" not in averages


def test_payments():
    payments = parsers.parse_payments(load("payments.html"))
    assert payments["outstanding"] == 1250.0
    assert payments["payments"][0]["variable_symbol"] == "2026001"
    assert payments["payments"][1]["paid"] is True


def test_timetable_grid():
    lessons = parsers.parse_timetable(load("timetable_grid.html"), TODAY)
    assert [(ls["date"], ls["start"], ls["subject"]) for ls in lessons] == [
        ("2026-09-28", "08:00", "M"),
        ("2026-09-28", "08:55", "ČJ"),
        ("2026-09-29", "08:00", "AJ"),
        ("2026-09-29", "08:55", "PŘ"),
    ]
    first = lessons[0]
    assert first["end"] == "08:45"
    assert first["room"] == "12"
    assert first["teacher"] == "Karel Dvořák"
    assert first["url"] == "/timetable/detail?lesson=1"
    assert lessons[1]["changed"] is True
    assert lessons[2]["cancelled"] is True
    assert lessons[3]["teacher"] == "Novotná"

    parsers.assign_periods(lessons)
    assert [ls["period"] for ls in lessons] == [1, 2, 1, 2]
    assert parsers.bell_schedule(lessons) == [
        {"period": 1, "start": "08:00", "end": "08:45"},
        {"period": 2, "start": "08:55", "end": "09:40"},
    ]


def test_timetable_text_entries():
    lessons = parsers.parse_timetable(load("timetable_entries.html"), TODAY)
    assert len(lessons) == 3
    assert lessons[0]["subject"] == "Svět češtiny"
    assert lessons[0]["subject_short"] == "CZE - 1.Q"
    assert lessons[0]["date"] == "2026-09-01"
    assert lessons[1]["cancelled"] is True
    assert lessons[1]["subject"] == "Matematika"
    assert (lessons[2]["start"], lessons[2]["end"]) == ("08:30", "09:15")


def test_ical():
    events = parse_ical(load("timetable.ics"))
    assert len(events) == 3
    lessons = ical_to_lessons(events)
    assert len(lessons) == 2  # all-day event is skipped
    assert lessons[0]["subject"] == "Matematika"
    assert lessons[0]["room"] == "Učebna 12"
    assert lessons[0]["teacher"] == "Karel Dvořák"
    assert (lessons[1]["start"], lessons[1]["end"]) == ("08:55", "09:40")  # UTC -> Prague
    assert lessons[1]["cancelled"] is True


def test_children_switcher():
    html = """<div class="top"><a href="/?mainMenu-studentSelector-new=11&do=x" class="active">Anna Hrubá</a>
      <a href="/?mainMenu-studentSelector-new=12&do=x">Petr Hrubý</a>
      <a href="/evaluation/detail?student=11">detail link is ignored</a></div>"""
    children = parsers.parse_children(html)
    assert [(c["id"], c["name"], c["selected"]) for c in children] == [
        ("11", "Anna Hrubá", True),
        ("12", "Petr Hrubý", False),
    ]
    assert children[1]["url"] == "/?mainMenu-studentSelector-new=12&do=x"
    assert parsers.parse_children(load("dashboard.html")) == []
    # Per-child dashboard links are not a switcher (this created bogus "children" before).
    links = """<a href="/evaluation/?student=11">Přejít na hodnocení po předmětech</a>
      <a href="/evaluation/?student=12">Přejít na hodnocení po předmětech</a>"""
    assert parsers.parse_children(links) == []
    assert parsers.display_name("Jakub Hrubý (Jarmila Šuláková, 9549-3740-1)") == "Jakub Hrubý"


# ---------------------------------------------------------------- real portal layout
# Fixtures below are anonymised copies of a real parent portal (two children, VI.B).


def test_family_timetable_real_layout():
    children = parsers.parse_family_timetables(load("family_timetable.html"))
    assert [(c["id"], c["name"], c["class_name"]) for c in children] == [
        ("102", "Anna Nováková", "VI.B"),
        ("101", "Petr Novák", "VI.B"),
    ]
    lessons = children[0]["lessons"]
    assert len(lessons) == 32
    first = lessons[0]
    assert {k: first[k] for k in ("date", "period", "start", "end", "subject", "subject_short", "room", "kind")} == {
        "date": "2026-11-02",
        "period": 1,
        "start": "08:00",
        "end": "08:45",
        "subject": "Matematika",
        "subject_short": "M",
        "room": "M 6.B",
        "kind": "lesson",
    }
    assert first["teacher"].startswith("Učitel")
    assert {ls["date"] for ls in lessons} == {"2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06"}
    assert max(ls["period"] for ls in lessons) <= 8


def test_family_dashboard_real_layout():
    children = parsers.parse_dashboard_children(load("family_dashboard.html"), date(2026, 10, 1))
    assert [c["name"] for c in children] == ["Anna Nováková", "Petr Novák"]
    petr = children[1]
    assert petr["id"] == "101"
    assert petr["absences"] == {"Absence omluvená": 5}
    assert petr["class_teacher"].startswith("Učitel")
    assert petr["subjects"]["M"] == "Matematika"
    # Grades with weights; "N" (not evaluated) and comment-only entries have no value.
    grades = {(g["subject"], g["grade"], g["weight"]) for g in petr["grades"]}
    assert ("M", "3", 1.0) in grades
    assert ("D", "1", 0.5) in grades
    assert any(g["grade"] == "N" and g["value"] is None for g in petr["grades"])

    lessons = petr["lessons"]
    holiday = [ls for ls in lessons if ls["date"] == "2026-09-28"]
    assert holiday[0]["kind"] == "event" and holiday[0]["subject"] == "Státní svátek" and holiday[0]["all_day"]
    assert all(ls["cancelled"] for ls in holiday if ls["kind"] == "lesson")
    trip = next(ls for ls in lessons if ls["kind"] == "event" and ls["date"] == "2026-10-02")
    assert (trip["subject"], trip["start"], trip["end"], trip["period"]) == ("Školní výlet", "08:00", "13:30", 1)
    today = [ls for ls in lessons if ls["date"] == "2026-10-01"]
    assert [ls["subject_short"] for ls in today] == ["D", "Čj", "Z", "SM", "Vv", "Vv"]
    assert today[1]["topic"] == "Sloh: inzerát."


def test_family_dashboard_states_and_exams():
    petr = parsers.parse_dashboard_children(load("family_dashboard.html"), date(2026, 10, 1))[1]
    by_key = {(ls["date"], ls["period"]): ls for ls in petr["lessons"] if ls["kind"] == "lesson"}
    # Written tests ("Pís. - …" badges)
    assert by_key[("2026-09-30", 2)]["exam"] == "Pravopisné cvičení - září"
    assert by_key[("2026-09-30", 3)]["exam"] == "Opakování 5. třídy"
    assert by_key[("2026-10-01", 4)]["exam"] == "Geometrické značky"
    # "Zrušeno" (holiday) and "Událost" (replaced by the trip), shown struck through
    assert by_key[("2026-09-28", 1)]["status"] == "Zrušeno"
    assert by_key[("2026-10-02", 1)]["status"] == "Událost"
    assert by_key[("2026-10-02", 1)]["cancelled"]
    # Orange subject without a label = change (other room)
    changed = by_key[("2026-09-30", 8)]
    assert changed["changed"] and not changed["cancelled"] and changed["room"] == "PC 2. patro"
    # Short name as shown in the portal box, teacher abbreviation for the box corner
    assert by_key[("2026-09-29", 3)]["subject_short"] == "Tv"
    assert by_key[("2026-09-29", 1)]["teacher_short"] == "Še"


def test_message_detail():
    detail = parsers.parse_message_detail(load("message_detail.html"))
    assert detail["subject"] == "Třídní schůzky"
    assert detail["from"] == "Mgr. Petra Svobodová"
    assert detail["text"].splitlines() == [
        "Vážení rodiče,",
        "zveme vás na třídní schůzky ve čtvrtek 8. 10. od 17:00 ve třídě 6.B.",
        "S pozdravem P. Svobodová",
    ]
    assert detail["attachments"] == [{"name": "pozvanka.pdf", "url": "/files/download?id=5"}]


def test_message_detail_fallback_without_rich_text():
    html = '<html><body><div id="content"><h2>Zpráva</h2><p>První řádek</p><p>Druhý řádek</p></div></body></html>'
    assert parsers.parse_message_detail(html)["text"] == "První řádek\nDruhý řádek"
