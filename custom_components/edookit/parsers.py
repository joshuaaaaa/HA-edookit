"""HTML parsers for the Edookit parent/student portal (``<school>.edookit.net``).

The portal has no public JSON API for parents, so everything here is
scraping. Selectors for the inbox, the "requires action" widget, upcoming
events and detail pages mirror ones used by other open-source Edookit tools
in production. Tables and the timetable use defensive heuristics that work
from column headers and text patterns rather than exact markup.

This module must not import Home Assistant so it can be unit tested alone.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
import re
from typing import Any

from bs4 import BeautifulSoup, Tag

# --------------------------------------------------------------------------
# Generic helpers
# --------------------------------------------------------------------------

_WS_RE = re.compile(r"[\s   ]+")
_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})\.\s*(\d{1,2})\.(?:\s*(\d{4}))?")
_TIME_RE = re.compile(r"(?<![\d:])(\d{1,2}):(\d{2})(?![\d])")
_TIME_RANGE_RE = re.compile(r"(?<![\d:])(\d{1,2}):(\d{2})\s*(?:-|–|—|až|to|do)\s*(\d{1,2}):(\d{2})(?![\d])")
_ONCLICK_URL_RE = re.compile(r"""(?:window\.)?location(?:\.href)?\s*=\s*["']([^"']+)["']""")
_WEEKDAY_PREFIX_RE = re.compile(
    r"^(?:po|út|ut|st|čt|ct|pá|pa|so|ne|mo|tu|we|th|fr|sa|su)[a-zěščřžýáíéůú]*\.?\s+",
    re.IGNORECASE,
)

TODAY_WORDS = ("dnes", "today")
YESTERDAY_WORDS = ("včera", "vcera", "yesterday")
TOMORROW_WORDS = ("zítra", "zitra", "tomorrow")

GRADE_WORDS = {
    "výborně": 1,
    "chvalitebně": 2,
    "dobře": 3,
    "dostatečně": 4,
    "nedostatečně": 5,
}


def clean(text: str | None) -> str:
    """Collapse whitespace (including thin / non-breaking spaces)."""
    if not text:
        return ""
    return _WS_RE.sub(" ", text).strip()


def soupify(html: str | BeautifulSoup) -> BeautifulSoup:
    """Return a BeautifulSoup for the given HTML."""
    if isinstance(html, BeautifulSoup):
        return html
    return BeautifulSoup(html or "", "html.parser")


def normalize_url(url: str | None) -> str | None:
    """Return a site-relative URL with a leading slash (or an absolute one)."""
    if not url:
        return None
    url = url.replace("&amp;", "&").strip()
    if url.startswith(("http://", "https://", "javascript:", "#")):
        return None if url.startswith(("javascript:", "#")) else url
    if not url.startswith("/"):
        url = "/" + url
    return url


def element_url(el: Tag) -> str | None:
    """Extract the navigation target of a portal element (onclick or href)."""
    onclick = el.get("onclick") or ""
    match = _ONCLICK_URL_RE.search(onclick)
    if match:
        return normalize_url(match.group(1))
    href = el.get("href")
    if href:
        return normalize_url(href)
    link = el.find("a", href=True)
    if link:
        return normalize_url(link["href"])
    return None


def parse_date(text: str | None, today: date | None = None) -> date | None:
    """Parse Czech/English portal dates such as ``Po 2. 2. 2026`` or ``Dnes``."""
    if not text:
        return None
    today = today or date.today()
    value = clean(text).lower()
    if any(w in value for w in TODAY_WORDS):
        return today
    if any(w in value for w in YESTERDAY_WORDS):
        return today - timedelta(days=1)
    if any(w in value for w in TOMORROW_WORDS):
        return today + timedelta(days=1)
    iso = re.search(r"(\d{4})-(\d{2})-(\d{2})", value)
    if iso:
        try:
            return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        except ValueError:
            return None
    match = _DATE_RE.search(value)
    if not match:
        return None
    day, month = int(match.group(1)), int(match.group(2))
    try:
        if match.group(3):
            return date(int(match.group(3)), month, day)
        # No year: pick the occurrence closest to today.
        candidates = [date(today.year + off, month, day) for off in (-1, 0, 1)]
    except ValueError:
        return None
    return min(candidates, key=lambda d: abs((d - today).days))


def parse_datetime(text: str | None, today: date | None = None) -> datetime | None:
    """Parse ``15. 4. 2026, 12:22`` / ``Dnes, 8:15`` into a naive datetime."""
    day = parse_date(text, today)
    if day is None:
        return None
    match = _TIME_RE.search(clean(text))
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        if hour < 24 and minute < 60:
            return datetime.combine(day, time(hour, minute))
    return datetime.combine(day, time(0, 0))


def parse_time(text: str | None) -> time | None:
    """Return the first HH:MM found in text."""
    match = _TIME_RE.search(clean(text))
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)


def parse_grade_value(text: str | None) -> float | None:
    """Return a numeric grade (1-5, also ``2-`` / ``1+``) or None."""
    value = clean(text).lower()
    if not value:
        return None
    for word, grade in GRADE_WORDS.items():
        if value.startswith(word):
            return float(grade)
    match = re.fullmatch(r"([1-5])\s*([+\-−*]?)", value)
    if not match:
        match = re.fullmatch(r"([1-5])\s*[,.]\s*(\d)", value)
        if match:
            return float(f"{match.group(1)}.{match.group(2)}")
        return None
    grade = float(match.group(1))
    if match.group(2) == "-" or match.group(2) == "−":
        grade += 0.5
    elif match.group(2) == "+":
        grade -= 0.25
    return grade


def is_login_page(html: str | BeautifulSoup, url: str | None = None) -> bool:
    """Return True if the page is the login page (= session expired)."""
    if url and ("/user/login" in url or "uuidentity.plus4u.net" in url):
        return True
    soup = soupify(html)
    title = soup.find("title")
    logged_in_marker = soup.select_one("#menu-icon, .fullname, #headerAvatarMobile, .logout")
    if title and re.search(r"přihlašovací|přihlášení|login", title.get_text(), re.I) and not logged_in_marker:
        return True
    return bool(soup.select_one("form[id*='loginForm'], form[action*='loginForm'], #plus4ULoginButton"))


def parse_student_name(html: str | BeautifulSoup) -> str | None:
    """Return the logged-in user's / student's display name if present."""
    soup = soupify(html)
    for selector in (".fullname", ".student-name", ".user-name", "#headerAvatar .name"):
        el = soup.select_one(selector)
        if el and clean(el.get_text()):
            return clean(el.get_text())
    return None


_CHILD_PARAM_RE = re.compile(r"[?&]([\w-]*?(?:student|child|person|pupil|zak|dite|kid)[\w-]*?)=(\d+)", re.I)
_SELECTED_CLASSES = {"selected", "active", "current", "checked"}
# A person's name: 2-4 capitalised words ("Anna Hrubá", "Jan Petr Novák").
_UPPER = "A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ"
_PERSON_NAME_RE = re.compile(rf"^[{_UPPER}][\w'’.-]*(?:\s+[{_UPPER}][\w'’.-]*){{1,3}}$")


def parse_children(html: str | BeautifulSoup) -> list[dict[str, Any]]:
    """Find the child switcher a parent with several children sees at the top of the portal.

    The portal lists the children's names as links that select the child for the
    session (like the school-year selector). We look for links whose URL carries a
    student/person/child id parameter and keep the parameter that appears with the
    most distinct ids. Returns ``[]`` when there is no switcher (one child).
    """
    soup = soupify(html)
    groups: dict[str, dict[str, dict[str, Any]]] = {}
    for el in soup.find_all(["a", "li", "div", "span", "button"]):
        raw = el.get("href") or el.get("data-href")
        match = _ONCLICK_URL_RE.search(el.get("onclick") or "")
        if not raw and match:
            raw = match.group(1)
        if not raw or "/detail" in raw or "termSelector" in raw:
            continue
        raw = raw.replace("&amp;", "&")
        found = _CHILD_PARAM_RE.search(raw)
        if not found:
            continue
        # Only state-switching links count (a Nette signal or a *Selector parameter);
        # ordinary links such as "Přejít na hodnocení po předmětech?student=1" do not.
        if "do=" not in raw and not re.search(r"selector|switch", found.group(1), re.I):
            continue
        name = display_name(clean(el.get_text(" "))) or ""
        if not _PERSON_NAME_RE.match(name):
            name = display_name(clean(el.get("title") or "")) or ""
        if not _PERSON_NAME_RE.match(name):
            continue
        classes = set(el.get("class", []))
        if el.parent is not None:
            classes |= set(el.parent.get("class", []))
        group = groups.setdefault(found.group(1).lower(), {})
        group.setdefault(
            found.group(2),
            {
                "id": found.group(2),
                "name": name,
                "url": normalize_url(raw),
                "selected": bool(classes & _SELECTED_CLASSES) or el.has_attr("selected"),
            },
        )
    best = max(groups.values(), key=len, default={})
    return list(best.values()) if len(best) >= 2 else []


def display_name(name: str | None) -> str | None:
    """Drop the ``(account, Plus4U id)`` suffix the portal adds to names."""
    if not name:
        return name
    return re.sub(r"\s*\([^()]*\)\s*$", "", name).strip() or name


def parse_school_year(html: str | BeautifulSoup) -> str | None:
    """Return the selected term label (``2025/26``) from the term selector."""
    soup = soupify(html)
    el = soup.select_one("#selected-term") or soup.select_one("a.selected[href*='termSelector']")
    return clean(el.get_text()) if el else None


# --------------------------------------------------------------------------
# Inbox (/overview/updates)
# --------------------------------------------------------------------------

INBOX_TYPES = (
    "assignment",
    "actionRequired",
    "inboxMessage",
    "message",
    "exam",
    "poll",
    "event",
    "evaluation",
    "material",
)


def parse_inbox(html: str | BeautifulSoup, today: date | None = None) -> list[dict[str, Any]]:
    """Parse inbox items (messages, grades, homework, exams, events, polls)."""
    soup = soupify(html)
    items: list[dict[str, Any]] = []
    for div in soup.find_all("div", class_="item"):
        classes = div.get("class", [])
        if "archive-notification" in classes or "inbox-help" in classes:
            continue
        item_type = next((t for t in INBOX_TYPES if t in classes), None)
        if item_type is None:
            continue
        if item_type == "message":
            item_type = "inboxMessage"
        name_el = div.find(class_="object-name")
        title = clean(name_el.get_text(" ")) if name_el else ""
        if not title:
            continue
        creator_el = div.find(class_="creator")
        time_el = div.find(class_="time")
        desc_el = div.find(class_="description")
        grade_el = div.find("span", class_="evaluation")
        raw_time = clean(time_el.get_text(" ")) if time_el else ""
        timestamp = parse_datetime(raw_time, today)
        grade = clean(grade_el.get_text()) if grade_el else ""
        items.append(
            {
                "type": item_type,
                "title": title,
                "url": element_url(div),
                "creator": clean(creator_el.get_text(" ")) if creator_el else "",
                "time": raw_time,
                "timestamp": timestamp.isoformat() if timestamp else None,
                "description": clean(desc_el.get_text(" ")) if desc_el else "",
                "grade": grade,
                "unread": "unread" in classes,
            }
        )
    return items


def parse_action_items(html: str | BeautifulSoup) -> list[dict[str, Any]]:
    """Parse the dashboard "Requires action" widget (payments, polls, consents)."""
    soup = soupify(html)
    container = soup.find(id="requires-action-container")
    if not container:
        return []
    result = []
    for item_div in container.select("div.item-container"):
        link = item_div.find("a", class_="item") or item_div.find("a")
        if not link:
            continue
        name = link.find(class_="name")
        info = link.find(class_="additional-info")
        icon = link.find(class_="icon")
        icon_type = ""
        if icon:
            icon_classes = [c for c in icon.get("class", []) if c != "icon"]
            icon_type = icon_classes[0] if icon_classes else ""
        result.append(
            {
                "name": clean(name.get_text(" ")) if name else clean(link.get_text(" ")),
                "info": clean(info.get_text(" ")) if info else "",
                "url": normalize_url(link.get("href")),
                "kind": icon_type,
            }
        )
    return result


# --------------------------------------------------------------------------
# Generic Edookit tables (ul.table_row / li cells, or <table>)
# --------------------------------------------------------------------------


@dataclass
class Row:
    """A parsed table row."""

    cells: list[str]
    columns: dict[str, str] = field(default_factory=dict)
    url: str | None = None
    classes: list[str] = field(default_factory=list)
    element: Tag | None = None


def _header_like(row: Tag) -> bool:
    classes = " ".join(row.get("class", [])).lower()
    if "header" in classes or "head" in classes:
        return True
    return bool(row.find("th")) and not row.find("td")


def parse_tables(html: str | BeautifulSoup) -> list[list[Row]]:
    """Return all list-tables on the page as lists of rows keyed by header text."""
    soup = soupify(html)
    tables: list[list[Row]] = []

    # Edookit "ul.table_row" tables (grouped by their parent container).
    parents: dict[int, Tag] = {}
    for ul in soup.find_all("ul", class_="table_row"):
        parents.setdefault(id(ul.parent), ul.parent)
    for parent in parents.values():
        headers: list[str] = []
        rows: list[Row] = []
        for ul in parent.find_all("ul", class_="table_row", recursive=False) or parent.find_all(
            "ul", class_="table_row"
        ):
            cells = [clean(li.get_text(" ")) for li in ul.find_all("li", recursive=False)]
            if not cells:
                continue
            url = element_url(ul) if (ul.get("onclick") or ul.find("a", href=True)) else None
            if not headers and (_header_like(ul) or (not ul.get("onclick") and not rows)):
                headers = cells
                continue
            rows.append(_make_row(cells, headers, url, ul))
        if rows:
            tables.append(rows)

    # Classic HTML tables.
    for table in soup.find_all("table"):
        headers = [clean(th.get_text(" ")) for th in table.find_all("th")]
        rows = []
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if not tds:
                continue
            cells = [clean(td.get_text(" ")) for td in tds]
            rows.append(_make_row(cells, headers, element_url(tr), tr))
        if rows:
            tables.append(rows)
    return tables


def _make_row(cells: list[str], headers: list[str], url: str | None, el: Tag) -> Row:
    columns = {}
    for idx, value in enumerate(cells):
        key = headers[idx] if idx < len(headers) and headers[idx] else f"col{idx + 1}"
        columns[key] = value
    return Row(cells=cells, columns=columns, url=url, classes=list(el.get("class", [])), element=el)


def _pick(columns: dict[str, str], *keywords: str) -> str:
    for key, value in columns.items():
        low = key.lower()
        if any(k in low for k in keywords):
            return value
    return ""


# --------------------------------------------------------------------------
# Evaluations / grades
# --------------------------------------------------------------------------


def parse_evaluations(html: str | BeautifulSoup, today: date | None = None) -> list[dict[str, Any]]:
    """Parse ``/evaluation/list`` (Předmět a téma | Hodnocení | Vytvořeno)."""
    result: list[dict[str, Any]] = []
    for table in parse_tables(html):
        for row in table:
            cols = row.columns
            subject_topic = _pick(cols, "předmět", "subject", "kurz", "course") or (row.cells[0] if row.cells else "")
            grade = _pick(cols, "hodnocení", "známka", "evaluation", "grade", "mark")
            if not grade and len(row.cells) >= 2:
                grade = row.cells[1]
            created = _pick(cols, "vytvořeno", "datum", "created", "date")
            if not created and len(row.cells) >= 3:
                created = row.cells[-1]
            if not subject_topic or not grade:
                continue
            weight = None
            if row.element is not None:
                weight_el = row.element.find(attrs={"title": re.compile(r"(Váha|Weight)", re.I)})
                if weight_el:
                    weight = _to_float(re.sub(r"[^\d.,]", "", weight_el["title"]))
            subject, _, topic = subject_topic.partition(" - ")
            stamp = parse_datetime(created, today)
            result.append(
                {
                    "subject": clean(subject),
                    "topic": clean(topic),
                    "grade": grade,
                    "value": parse_grade_value(grade.split(" ")[0]),
                    "weight": weight,
                    "date": stamp.isoformat() if stamp else None,
                    "url": row.url,
                }
            )
    return result


def _text(el: Tag | None) -> str:
    return clean(el.get_text(" ")) if el is not None else ""


def _to_float(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value.replace(",", "."))
    except ValueError:
        return None


def grade_averages(grades: list[dict[str, Any]]) -> dict[str, float]:
    """Weighted averages per subject for numeric grades."""
    totals: dict[str, list[float]] = {}
    for grade in grades:
        value = grade.get("value")
        if value is None:
            continue
        weight = grade.get("weight")
        weight = 1.0 if weight is None else float(weight)
        if weight <= 0:
            continue
        subject = grade.get("subject") or "?"
        acc = totals.setdefault(subject, [0.0, 0.0])
        acc[0] += value * weight
        acc[1] += weight
    return {s: round(t / w, 2) for s, (t, w) in sorted(totals.items()) if w}


# --------------------------------------------------------------------------
# Assignments / exams / generic lists
# --------------------------------------------------------------------------


def parse_assignments(html: str | BeautifulSoup, today: date | None = None) -> list[dict[str, Any]]:
    """Parse ``/assignments/`` (homework)."""
    result = []
    for table in parse_tables(html):
        for row in table:
            cols = row.columns
            due_raw = _pick(cols, "termín", "odevzd", "due", "deadline") or (row.cells[0] if row.cells else "")
            due = parse_datetime(due_raw, today)
            course = _pick(cols, "kurz", "předmět", "course", "subject")
            name = _pick(cols, "název", "úkol", "name", "assignment") or (row.cells[2] if len(row.cells) > 2 else "")
            if not name and not course:
                continue
            result.append(
                {
                    "title": name or course,
                    "subject": course,
                    "due": due.isoformat() if due else None,
                    "due_raw": due_raw,
                    "description": _pick(cols, "popis", "description"),
                    "teacher": _pick(cols, "vytvořil", "učitel", "creator", "teacher", "vytvořeno"),
                    "done": any(c in row.classes for c in ("done", "completed", "submitted")),
                    "url": row.url,
                }
            )
    if not result:
        result = _parse_boxes(html, today, date_keys=(".date", ".due-date", "time"))
    return result


def parse_exams(html: str | BeautifulSoup, today: date | None = None) -> list[dict[str, Any]]:
    """Parse ``/exams/`` (written tests)."""
    result = []
    for table in parse_tables(html):
        for row in table:
            cols = row.columns
            when_raw = _pick(cols, "datum", "termín", "date", "kdy") or (row.cells[0] if row.cells else "")
            when = parse_datetime(when_raw, today)
            name = _pick(cols, "název", "téma", "name", "topic") or (row.cells[1] if len(row.cells) > 1 else "")
            if not name:
                continue
            result.append(
                {
                    "title": name,
                    "subject": _pick(cols, "kurz", "předmět", "course", "subject"),
                    "date": when.isoformat() if when else None,
                    "date_raw": when_raw,
                    "description": _pick(cols, "popis", "description"),
                    "url": row.url,
                }
            )
    if not result:
        result = _parse_boxes(html, today, date_keys=(".date", "time"))
    return result


def _parse_boxes(html: str | BeautifulSoup, today: date | None, date_keys: tuple[str, ...]) -> list[dict[str, Any]]:
    soup = soupify(html)
    result = []
    for box in soup.select(".box"):
        title_el = box.select_one(".name, .title, h3, h4")
        if not title_el:
            continue
        date_el = None
        for key in date_keys:
            date_el = box.select_one(key)
            if date_el:
                break
        when = parse_datetime(date_el.get_text(" ") if date_el else None, today)
        result.append(
            {
                "title": clean(title_el.get_text(" ")),
                "subject": clean((box.select_one(".subject, .course") or title_el).get_text(" ")),
                "date": when.isoformat() if when else None,
                "description": _text(box.select_one(".description, .content")),
                "url": element_url(box),
            }
        )
    return result


def parse_upcoming_events(html: str | BeautifulSoup, today: date | None = None) -> list[dict[str, Any]]:
    """Parse ``/timetable/upcoming`` school events (trips, holidays, meetings)."""
    soup = soupify(html)
    table = soup.find("div", class_="events_table")
    if not table:
        return []
    events = []
    for row in table.find_all("ul", class_="table_row"):
        if not row.get("onclick"):
            continue
        cols = row.find_all("li", recursive=False)
        if len(cols) < 2:
            continue
        bold = cols[0].find("b")
        date_raw = clean(bold.get_text(" ") if bold else cols[0].get_text(" "))
        title_el = cols[1].find("span")
        paragraphs = cols[1].find_all("p")
        creator = ""
        if len(cols) > 2:
            creator_el = cols[2].find("b")
            creator = clean(creator_el.get_text() if creator_el else cols[2].get_text(" "))
        start = parse_datetime(date_raw, today)
        end = None
        # "od 2. 4. do 6. 4." / "from 2. 4. to 6. 4." / "22. 4., od 9:00 do 11:30"
        dates = list(_DATE_RE.finditer(date_raw))
        if len(dates) >= 2:
            end = parse_date(dates[1].group(0), today)
        times = _TIME_RANGE_RE.search(date_raw)
        start_time = end_time = None
        if times:
            start_time = f"{int(times.group(1)):02d}:{times.group(2)}"
            end_time = f"{int(times.group(3)):02d}:{times.group(4)}"
        events.append(
            {
                "title": clean(title_el.get_text(" ")) if title_el else clean(cols[1].get_text(" ")),
                "description": clean(paragraphs[-1].get_text(" ")) if len(paragraphs) >= 2 else "",
                "date_raw": date_raw,
                "start": start.date().isoformat() if start else None,
                "end": end.isoformat() if end else None,
                "start_time": start_time,
                "end_time": end_time,
                "creator": creator,
                "url": element_url(row),
            }
        )
    return events


def parse_attendance(html: str | BeautifulSoup, today: date | None = None) -> dict[str, Any]:
    """Parse attendance pages into a list of absence rows plus summary numbers."""
    soup = soupify(html)
    records = []
    for table in parse_tables(soup):
        for row in table:
            cols = row.columns
            when = parse_date(_pick(cols, "datum", "date", "den", "day") or row.cells[0], today)
            if when is None:
                continue
            records.append(
                {
                    "date": when.isoformat(),
                    "lesson": _pick(cols, "hodin", "lesson", "vyuč"),
                    "subject": _pick(cols, "kurz", "předmět", "course", "subject"),
                    "status": _pick(cols, "stav", "status", "typ", "type"),
                    "excuse": _pick(cols, "omluv", "excuse", "důvod", "reason"),
                    "columns": cols,
                }
            )
    stats: dict[str, str] = {}
    for el in soup.select(".stat, .stats-item, .summary-item"):
        label = el.select_one(".label, .title")
        value = el.select_one(".value, .count")
        if label and value:
            stats[clean(label.get_text())] = clean(value.get_text())
    unexcused = sum(1 for r in records if re.search(r"neomluv|unexcused", (r["status"] + r["excuse"]).lower()))
    return {"records": records, "stats": stats, "unexcused": unexcused}


def parse_payments(html: str | BeautifulSoup) -> dict[str, Any]:
    """Parse ``/payments/`` into a list of payments and an outstanding total."""
    soup = soupify(html)
    payments = []
    outstanding = 0.0
    for table in parse_tables(soup):
        for row in table:
            cols = row.columns
            amount_raw = _pick(cols, "částka", "amount", "suma", "cena", "k úhradě")
            amount = _parse_money(amount_raw)
            if amount is None:
                continue
            status = _pick(cols, "stav", "status")
            low = status.lower()
            paid = bool(re.search(r"zaplac|uhrazen|paid", low)) and not re.search(
                r"nezaplac|neuhrazen|unpaid|čeká", low
            )
            due = parse_date(_pick(cols, "splatnost", "due", "datum"))
            payments.append(
                {
                    "title": _pick(cols, "název", "popis", "platba", "name", "description")
                    or (row.cells[0] if row.cells else ""),
                    "amount": amount,
                    "status": status,
                    "paid": paid,
                    "due": due.isoformat() if due else None,
                    "variable_symbol": _pick(cols, "variabil", "vs"),
                    "url": row.url,
                }
            )
            if not paid:
                outstanding += amount
    return {"payments": payments, "outstanding": round(outstanding, 2)}


def _parse_money(text: str | None) -> float | None:
    if not text:
        return None
    match = re.search(r"-?\d[\d\s  ]*(?:[,.]\d{1,2})?", text)
    if not match:
        return None
    return _to_float(re.sub(r"[\s  ]", "", match.group(0)))


def parse_detail_page(html: str | BeautifulSoup) -> dict[str, Any]:
    """Extract label/value pairs, rich text and attachments from a detail page."""
    soup = soupify(html)
    fields: dict[str, Any] = {}
    name = soup.find("span", class_="detail-object-name")
    if name:
        fields["name"] = clean(name.get_text(" "))
    for row in soup.find_all("div", class_="ft_row"):
        label_el = row.find("span", class_="ft_c1")
        if not label_el:
            continue
        label = clean(label_el.get_text()).rstrip(":")
        if not label:
            continue
        rich = row.find("div", class_="rich_content")
        if rich:
            fields["description"] = "\n".join(clean(p.get_text(" ")) for p in rich.find_all("p")) or clean(
                rich.get_text(" ")
            )
            continue
        value = row.find(["span", "div"], class_="ft_c2")
        if value:
            fields[label] = clean(value.get_text(" "))
    attachments = []
    files = soup.find("div", class_="files_table")
    if files:
        for row in files.find_all("ul", class_="table_row"):
            link = row.find("a", class_="more")
            dl = row.find("a", class_="downloadLink")
            if link and dl and dl.get("href"):
                attachments.append({"name": clean(link.get_text()), "url": normalize_url(dl["href"])})
    fields["attachments"] = attachments
    return fields


# --------------------------------------------------------------------------
# Timetable
# --------------------------------------------------------------------------


@dataclass
class Lesson:
    """One timetable entry."""

    date: str
    start: str | None
    end: str | None
    subject: str
    subject_short: str | None = None
    teacher: str | None = None
    room: str | None = None
    group: str | None = None
    topic: str | None = None
    period: int | None = None
    changed: bool = False
    cancelled: bool = False
    note: str | None = None
    kind: str = "lesson"  # lesson | event | exam
    url: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON friendly dict."""
        return asdict(self)


# Selector groups tried in order; the first group that yields lessons wins.
_LESSON_SELECTOR_GROUPS = (
    ".lesson-entry, .lessonRow, .timetable-lesson, .tt-lesson, .lesson",
    "[onclick*='timetable/detail'], a[href*='timetable/detail'], [onclick*='lesson='], a[href*='lesson=']",
    "[class*='lesson']",
)
_PART_CLASS_RE = re.compile(
    r"lesson[-_]?(name|time|room|teacher|info|title|label|header|wrapper|container|list|number|nr)"
    r"|lessons\b",
    re.I,
)
_CANCEL_RE = re.compile(r"zrušen|odpadá|cancel", re.I)
_CHANGE_RE = re.compile(r"suplov|změn|přesun|substitut|changed|moved", re.I)
_ROOM_RE = re.compile(r"(?:učebna|uč\.|místnost|room|mist\.)\s*:?\s*([\w./\- ]{1,20}?)(?=,|;|\||$|\s{2})", re.I)
_TEACHER_RE = re.compile(r"(?:vyučující|učitel|teacher)\s*:?\s*([^,;|]+)", re.I)
_CODE_RE = re.compile(r"\(([^()]{1,30})\)\s*$")


def _period_of(el: Tag) -> int | None:
    for attr in ("data-period", "data-hour", "data-lesson-number"):
        if el.get(attr) and str(el.get(attr)).isdigit():
            return int(el[attr])
    return None


def _date_context(el: Tag, today: date) -> date | None:
    """Find a date for an element: own attributes first, then nearest day header."""
    for attr in ("data-date", "data-day", "data-datetime", "datetime"):
        if el.get(attr):
            found = parse_date(str(el[attr]), today)
            if found:
                return found
    node: Tag | None = el
    for _ in range(8):
        if node is None or node.parent is None:
            break
        parent = node.parent
        for attr in ("data-date", "data-day"):
            if parent.get(attr):
                found = parse_date(str(parent[attr]), today)
                if found:
                    return found
        # A day header is usually a preceding sibling or the first child of the row.
        for sib in list(node.find_previous_siblings(limit=4)):
            if not isinstance(sib, Tag):
                continue
            text = clean(sib.get_text(" "))
            if len(text) <= 40:
                found = _DATE_RE.search(text)
                if found:
                    return parse_date(found.group(0), today)
        header = parent.find(class_=re.compile(r"(day|date|den|datum)", re.I), recursive=False)
        if header and header is not el:
            found = parse_date(clean(header.get_text(" "))[:40], today)
            if found:
                return found
        node = parent
    return None


def _lesson_from_text(blob: str, today: date) -> dict[str, Any]:
    """Pull date/time/subject/room/teacher out of free text."""
    info: dict[str, Any] = {}
    match = _DATE_RE.search(blob)
    if match:
        info["date"] = parse_date(match.group(0), today)
    rng = _TIME_RANGE_RE.search(blob)
    if rng:
        info["start"] = f"{int(rng.group(1)):02d}:{rng.group(2)}"
        info["end"] = f"{int(rng.group(3)):02d}:{rng.group(4)}"
    else:
        single = _TIME_RE.search(blob)
        if single:
            info["start"] = f"{int(single.group(1)):02d}:{single.group(2)}"
    room = _ROOM_RE.search(blob)
    if room:
        info["room"] = clean(room.group(1))
    teacher = _TEACHER_RE.search(blob)
    if teacher:
        info["teacher"] = clean(teacher.group(1))
    return info


def _strip_meta(text: str) -> str:
    text = _WEEKDAY_PREFIX_RE.sub("", text)
    text = _DATE_RE.sub(" ", text)
    text = _TIME_RANGE_RE.sub(" ", text)
    text = _TIME_RE.sub(" ", text)
    text = _ROOM_RE.sub(" ", text)
    text = _TEACHER_RE.sub(" ", text)
    text = re.sub(r"zrušeno|odpadá|suplování", " ", text, flags=re.I)
    return clean(text.strip(" ,;|-–"))


def parse_timetable(html: str | BeautifulSoup, today: date | None = None) -> list[dict[str, Any]]:
    """Parse lessons from the portal timetable or dashboard timetable widget.

    The portal renders the week as a grid with one element per lesson. We
    find lesson elements by class/link patterns, then read date, time,
    subject, room and teacher from their attributes (``title``,
    ``data-*``), child elements and text.
    """
    soup = soupify(html)
    today = today or date.today()
    root = soup.select_one(".timetable-container, #timetable, .timetable") or soup

    for selector in _LESSON_SELECTOR_GROUPS:
        found = [el for el in root.select(selector) if not _PART_CLASS_RE.search(" ".join(el.get("class", [])))]
        # Keep the outermost match so the lesson's children (room, teacher) stay in scope.
        found_ids = {id(el) for el in found}
        outer = [el for el in found if not any(id(p) in found_ids for p in el.parents)]
        lessons = _lessons_from_elements(outer, today)
        if lessons:
            return [ls.as_dict() for ls in sort_lessons(lessons)]
    return []


def _lessons_from_elements(elements: list[Tag], today: date) -> list[Lesson]:
    lessons: list[Lesson] = []
    seen: set[tuple] = set()
    for el in elements:
        title_attr = clean(el.get("title") or el.get("data-title") or el.get("data-tooltip") or "")
        text = clean(el.get_text(" "))
        if not text and not title_attr:
            continue
        blob = f"{title_attr} | {text}" if title_attr else text
        info = _lesson_from_text(blob, today)

        day = info.get("date") or _date_context(el, today)
        if day is None:
            continue

        subject_el = el.select_one(".subject, .course, .name, .lesson-name, b, strong")
        subject = clean(subject_el.get_text(" ")) if subject_el else ""
        if not subject:
            subject = _strip_meta(text) or _strip_meta(title_attr)
        if not subject:
            continue
        code = None
        code_match = _CODE_RE.search(subject)
        if code_match:
            code = clean(code_match.group(1))
            subject = clean(subject[: code_match.start()])

        room_el = el.select_one(".room, .classroom, .ucebna")
        teacher_el = el.select_one(".teacher, .lecturer, .ucitel")
        group_el = el.select_one(".group, .class, .skupina")
        classes = " ".join(el.get("class", []))
        cancelled = bool(_CANCEL_RE.search(classes) or _CANCEL_RE.search(blob))
        changed = bool(_CHANGE_RE.search(classes) or _CHANGE_RE.search(blob)) or cancelled

        lesson = Lesson(
            date=day.isoformat(),
            start=info.get("start"),
            end=info.get("end"),
            subject=subject,
            subject_short=code or _short_name(subject),
            teacher=clean(teacher_el.get_text(" ")) if teacher_el else info.get("teacher"),
            room=clean(room_el.get_text(" ")) if room_el else info.get("room"),
            group=clean(group_el.get_text(" ")) if group_el else None,
            period=_period_of(el),
            changed=changed,
            cancelled=cancelled,
            note=title_attr if title_attr and title_attr != text else None,
            kind="event" if "event" in classes.lower() else "lesson",
            url=element_url(el),
        )
        key = (lesson.date, lesson.start, lesson.subject)
        if key in seen:
            continue
        seen.add(key)
        lessons.append(lesson)
    return lessons


def _short_name(subject: str) -> str:
    words = [w for w in re.split(r"[\s\-]+", subject) if w]
    if len(words) >= 2:
        return "".join(w[0].upper() for w in words[:3])
    return subject[:3].upper() if subject else ""


def sort_lessons(lessons: list[Lesson]) -> list[Lesson]:
    """Sort lessons chronologically."""
    return sorted(lessons, key=lambda ls: (ls.date, ls.start or "99:99", ls.subject))


def assign_periods(lessons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fill in missing period numbers from the distinct start times (bell schedule)."""
    starts = sorted({ls["start"] for ls in lessons if ls.get("start") and ls.get("kind", "lesson") == "lesson"})
    # Merge start times closer than 10 minutes (e.g. 8:00 and 8:05 for different groups).
    bell: list[str] = []
    for start in starts:
        if bell and _minutes(start) - _minutes(bell[-1]) < 10:
            continue
        bell.append(start)
    for ls in lessons:
        if ls.get("period") is None and ls.get("start"):
            mins = _minutes(ls["start"])
            idx = min(range(len(bell)), key=lambda i: abs(_minutes(bell[i]) - mins)) if bell else None
            ls["period"] = idx + 1 if idx is not None else None
    return lessons


def bell_schedule(lessons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return ``[{period, start, end}]`` derived from lessons."""
    periods: dict[int, dict[str, Any]] = {}
    for ls in lessons:
        period = ls.get("period")
        if not period or not ls.get("start"):
            continue
        current = periods.setdefault(period, {"period": period, "start": ls["start"], "end": ls.get("end")})
        if _minutes(ls["start"]) < _minutes(current["start"]):
            current["start"] = ls["start"]
        if ls.get("end") and (not current["end"] or _minutes(ls["end"]) > _minutes(current["end"])):
            current["end"] = ls["end"]
    return [periods[k] for k in sorted(periods)]


def _minutes(hhmm: str) -> int:
    hour, minute = hhmm.split(":")[:2]
    return int(hour) * 60 + int(minute)


# --------------------------------------------------------------------------
# Family timetable / dashboard (verified against a real parent portal)
# --------------------------------------------------------------------------
#
# A parent account shows every child at once:
#   /timetable/  <h2 class="heading">Name <span class="class-name">VI.B</span>
#                <a class="print-pdf" href="/timetable/pdf?personId=2554&…">  followed by
#                <div id="snippet-familyTimetable-personTimetable-2554"> with the week grid.
#   /            <div class="student-container" id="student-container-2554"> with the
#                child's grades, current-week timetable (incl. topics) and absences.
# The grid: ".period-time" headers (title "1. 08:00–08:45", style left:X%), lessons
# "a.lesson" positioned by data-posLeft/data-size with <span data-day="2026_11_02"
# data-lesson-id="174483">, rows ".lessonRow" (short subject, teacher, room) and a
# hidden "div.lesson-info#lesson-info-<id>" with date, times, "M - VI.B", subject,
# "Nč (Nečasová L.)", room and "Učivo: …". Cancelled lessons contain "Zrušeno",
# events have class "event" (data-allDayStart/End="1" for whole-day ones).

_PERIOD_TITLE_RE = re.compile(r"(\d+)\.\s*(\d{1,2}:\d{2})\s*[–\-]\s*(\d{1,2}:\d{2})")
_LEFT_RE = re.compile(r"left:\s*([\d.]+)%")


def _hhmm(value: str | None) -> str | None:
    if not value:
        return None
    match = _TIME_RE.search(value)
    return f"{int(match.group(1)):02d}:{match.group(2)}" if match else None


def _grid_periods(node: Tag) -> list[dict[str, Any]]:
    periods: dict[int, dict[str, Any]] = {}
    for header in node.select(".period-time"):
        match = _PERIOD_TITLE_RE.search(clean(header.get("title") or header.get_text(" ")))
        left = _LEFT_RE.search(header.get("style", ""))
        if not match or not left:
            continue
        num = int(match.group(1))
        periods.setdefault(
            num,
            {"period": num, "start": _hhmm(match.group(2)), "end": _hhmm(match.group(3)), "left": float(left.group(1))},
        )
    return sorted(periods.values(), key=lambda p: p["left"])


def _lesson_info(div: Tag | None) -> dict[str, Any]:
    """Read a hidden ``div.lesson-info`` block."""
    if div is None:
        return {}
    rows = [row for row in div.find_all("div", recursive=False)]
    texts = [clean(row.get_text(" ")) for row in rows]
    info: dict[str, Any] = {}
    if rows:
        times = [_hhmm(t.get_text()) for t in rows[0].select(".date_time")]
        if len(times) >= 2:
            info["start"], info["end"] = times[0], times[1]
    if len(texts) > 1 and texts[1]:
        code, _, group = texts[1].partition(" - ")
        info["code"] = clean(code)
        info["group"] = clean(group) or None
    if len(texts) > 2 and texts[2]:
        info["subject"] = texts[2]
    if len(texts) > 3 and texts[3]:
        short, _, full = texts[3].partition(" (")
        info["teacher"] = full.rstrip(")") or short
        info["teacher_short"] = clean(short) or None
    if len(texts) > 4 and texts[4] and not texts[4].startswith("Učivo"):
        info["room"] = texts[4]
    for text in texts[4:]:
        if text.startswith("Učivo"):
            info["topic"] = clean(text.partition(":")[2]) or None
    return info


def _parse_grid(node: Tag) -> list[dict[str, Any]]:
    periods = _grid_periods(node)
    infos = {div["id"][len("lesson-info-") :]: div for div in node.select("div.lesson-info[id^='lesson-info-']")}

    def period_at(left: float) -> dict[str, Any] | None:
        if not periods:
            return None
        best = min(periods, key=lambda p: abs(p["left"] - left))
        return best if abs(best["left"] - left) < 2 else None

    lessons: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for el in node.select(".lesson"):
        classes = el.get("class", [])
        day_el = el.find(attrs={"data-day": True})
        if not day_el:
            continue
        try:
            year, month, day = (int(x) for x in day_el["data-day"].split("_")[:3])
            when = date(year, month, day)
        except ValueError:
            continue
        lesson_id = (day_el.get("data-lesson-id") or "").split("-")[0] or None
        left = _to_float(el.get("data-posleft")) or 0.0
        size = _to_float(el.get("data-size")) or 0.0
        rows = [clean(r.get_text(" ")) for r in el.select(".lessonRow")]
        text = clean(el.get_text(" "))
        cancelled = bool(_CANCEL_RE.search(text) or _CANCEL_RE.search(" ".join(classes)))
        period = period_at(left)

        if "event" in classes:
            covered = [p for p in periods if left - 1 <= p["left"] < left + size - 1] or ([period] if period else [])
            all_day = el.get("data-alldaystart") == "1" and el.get("data-alldayend") == "1"
            lesson = {
                "date": when.isoformat(),
                "start": None if all_day or not covered else covered[0]["start"],
                "end": None if all_day or not covered else covered[-1]["end"],
                "subject": text,
                "subject_short": text,
                "teacher": None,
                "room": None,
                "group": None,
                "topic": None,
                "period": None if all_day or not covered else covered[0]["period"],
                "period_end": None if all_day or not covered else covered[-1]["period"],
                "all_day": all_day,
                "changed": False,
                "cancelled": False,
                "note": None,
                "kind": "event",
                "url": normalize_url(el.get("href")),
                "id": lesson_id,
            }
        else:
            info = _lesson_info(infos.get(lesson_id or ""))
            # Status label in orange: "Zrušeno" (cancelled) or "Událost" (replaced by an
            # event); orange text without a label marks a change (room, teacher, …).
            orange = el.find("span", style=re.compile(r"ea8400", re.I))
            status_el = orange.find("b") if orange else None
            status = clean(status_el.get_text()) if status_el else None
            if status:
                cancelled = True
            struck = el.find("s")
            short = (
                clean(struck.get_text(" ")) if struck else re.sub(r"(?i)zrušeno|odpadá", " ", rows[0] if rows else text)
            )
            short = clean(short) or None
            badges = [
                clean(b.get_text(" "))
                for b in el.find_all("span", style=re.compile(r"border-radius"))
                if clean(b.get_text(" "))
            ]
            exam = next(
                (
                    clean(re.sub(r"^(Pís|Zk|Test)\w*\.?\s*-?\s*", "", b))
                    for b in badges
                    if re.match(r"(Pís|Zk|Test)", b)
                ),
                None,
            )
            lesson = {
                "date": when.isoformat(),
                "start": info.get("start") or (period or {}).get("start"),
                "end": info.get("end") or (period or {}).get("end"),
                "subject": info.get("subject") or short or text,
                # What the portal shows in the box ("Tv"), not the code with the group ("Tv d").
                "subject_short": short or info.get("code"),
                "teacher": info.get("teacher") or (rows[1] if len(rows) > 1 and rows[1] else None),
                "teacher_short": info.get("teacher_short") or (rows[1] if len(rows) > 1 and rows[1] else None),
                "room": info.get("room") or (rows[2] if len(rows) > 2 and rows[2] else None),
                "group": info.get("group"),
                "topic": info.get("topic"),
                "period": (period or {}).get("period"),
                "changed": cancelled or orange is not None or bool(_CHANGE_RE.search(" ".join(classes))),
                "cancelled": cancelled,
                "status": status or ("Zrušeno" if cancelled else None),
                "exam": exam,
                "badges": badges,
                "note": None,
                "kind": "lesson",
                "url": None,
                "id": lesson_id,
            }
        key = (lesson["date"], lesson["start"], lesson["subject"], lesson["kind"])
        if key in seen:
            continue
        seen.add(key)
        lessons.append(lesson)
    lessons.sort(key=lambda ls: (ls["date"], ls["start"] or "00:00"))
    return lessons


def parse_family_timetables(html: str | BeautifulSoup) -> list[dict[str, Any]]:
    """Per-child timetables from ``/timetable/`` (parent view, all children on one page)."""
    soup = soupify(html)
    result = []
    for heading in soup.select("h2.heading"):
        link = heading.find("a", href=re.compile(r"personId=\d+"))
        if not link:
            continue
        person_id = re.search(r"personId=(\d+)", link["href"]).group(1)
        node = soup.find(id=f"snippet-familyTimetable-personTimetable-{person_id}")
        if node is None:
            continue
        class_el = heading.select_one(".class-name")
        result.append(
            {
                "id": person_id,
                "name": clean(" ".join(heading.find_all(string=True, recursive=False))),
                "class_name": clean(class_el.get_text()) if class_el else None,
                "lessons": _parse_grid(node),
            }
        )
    if not result:
        # A student account (or another layout): one grid for the whole page.
        node = soup.select_one(".timetable") or soup
        if node.select(".lesson [data-day]"):
            result.append({"id": None, "name": None, "class_name": None, "lessons": _parse_grid(soup)})
    return result


def _widget_grades(container: Tag, today: date) -> list[dict[str, Any]]:
    grades = []
    for item in container.select(".evaluation-widget-container a.item"):
        course = clean((item.select_one(".course") or item).get_text(" "))
        when = parse_date(clean((item.select_one(".date") or Tag(name="x")).get_text(" ")), today)
        mark = item.select_one(".mark")
        weight_el = mark.find(attrs={"title": re.compile(r"Váha", re.I)}) if mark else None
        value_text = clean(mark.get_text(" ")) if mark else ""
        not_evaluated = bool(mark and mark.select_one(".not-evaluated"))
        grades.append(
            {
                "subject": course,
                "topic": "",
                "grade": value_text,
                "value": None if not_evaluated else parse_grade_value(value_text),
                "weight": _to_float(re.sub(r"[^\d.,]", "", weight_el["title"])) if weight_el else None,
                "date": when.isoformat() + "T00:00:00" if when else None,
                "url": normalize_url(item.get("href")),
            }
        )
    return grades


def parse_dashboard_children(html: str | BeautifulSoup, today: date | None = None) -> list[dict[str, Any]]:
    """Per-child widgets of the dashboard: name, class, grades, timetable, absences."""
    soup = soupify(html)
    today = today or date.today()
    result = []
    for container in soup.select("div.student-container[id^='student-container-']"):
        person_id = container["id"].rsplit("-", 1)[1]
        header = container.select_one(".student-header-container")
        name_el = header.select_one(".name") if header else None
        class_el = header.select_one(".class") if header else None
        teacher_el = header.select_one(".message-class-teacher") if header else None
        timetable = container.select_one(".timetable-widget-container")
        absences: dict[str, int] = {}
        for cell in container.select(".attendance-container td[title]"):
            number = cell.select_one(".number")
            if number and clean(number.get_text()).isdigit():
                absences[clean(cell["title"])] = int(clean(number.get_text()))
        lessons = _parse_grid(timetable) if timetable else []
        result.append(
            {
                "id": person_id,
                "name": clean(name_el.get_text(" ")) if name_el else None,
                "class_name": clean(class_el.get_text(" ")) if class_el else None,
                "class_teacher": clean(teacher_el.get_text(" ")) if teacher_el else None,
                "grades": _widget_grades(container, today),
                "absences": absences,
                "lessons": lessons,
                "subjects": {
                    ls["subject_short"]: ls["subject"]
                    for ls in lessons
                    if ls["kind"] == "lesson" and ls.get("subject_short") and ls["subject"] != ls["subject_short"]
                },
            }
        )
    return result
