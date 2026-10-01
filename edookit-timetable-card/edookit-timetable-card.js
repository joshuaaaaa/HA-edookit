/*
 * Edookit timetable card for Home Assistant.
 *
 * Renders the timetable stored in the attributes of the Edookit "Rozvrh"
 * sensor in the look of the Edookit parent portal ("Rozvrh žáků"): days as
 * rows, periods as columns, lesson boxes with subject, teacher and room,
 * "Zrušeno" / "Událost" in orange, written tests as green badges and school
 * events as a purple bar under the day. A day list is available for narrow
 * cards. The integration downloads the timetable once a day; the card only
 * renders it.
 *
 *   type: custom:edookit-timetable-card
 *   entity: sensor.edookit_laura_hruba_rozvrh
 */

const CARD_VERSION = "0.3.0";

const STRINGS = {
  cs: {
    noEntity: "Vyberte entitu rozvrhu Edookit",
    notFound: "Entita nenalezena",
    unavailable: "Entita není dostupná – zkontrolujte, že je integrace načtená a entita existuje.",
    noData: "Rozvrh zatím není načten",
    empty:
      "Rozvrh je prázdný. Integrace nenašla žádné hodiny – zkontrolujte senzor Poslední aktualizace (atribut errors) nebo zavolejte službu edookit.dump_pages.",
    free: "Volno 🎉",
    week: (a, b) => `Týden od ${a} do ${b}`,
    currentWeek: "Aktuální týden",
    today: "Dnes",
    tomorrow: "Zítra",
    updated: "Aktualizováno",
    cancelled: "Zrušeno",
    changed: "Změna",
    exam: "Pís.",
    topic: "Učivo",
    prev: "Předchozí",
    next: "Další",
    lessons: (n) => (n === 1 ? "1 hodina" : n >= 2 && n <= 4 ? `${n} hodiny` : `${n} hodin`),
  },
  en: {
    noEntity: "Select an Edookit timetable entity",
    notFound: "Entity not found",
    unavailable: "Entity unavailable – check that the integration is loaded and the entity exists.",
    noData: "Timetable not loaded yet",
    empty:
      "The timetable is empty. The integration found no lessons – check the Last update sensor (errors attribute) or call the edookit.dump_pages service.",
    free: "No lessons 🎉",
    week: (a, b) => `Week ${a} – ${b}`,
    currentWeek: "This week",
    today: "Today",
    tomorrow: "Tomorrow",
    updated: "Updated",
    cancelled: "Cancelled",
    changed: "Changed",
    exam: "Test",
    topic: "Topic",
    prev: "Previous",
    next: "Next",
    lessons: (n) => (n === 1 ? "1 lesson" : `${n} lessons`),
  },
};

const DEFAULTS = {
  view: "week", // week | day | auto (auto = day list when the card is narrower than 400 px)
  show_room: true,
  show_teacher: true,
  show_times: true,
  show_footer: true,
  show_exams: true,
  show_header: true,
  short_names: true, // big subject abbreviation like the portal (false = full subject names)
  highlight_now: true,
  next_week_from: "friday_after_school", // friday_after_school | saturday | never
  colorize: false, // tint lessons by subject instead of the portal's grey boxes
  subject_colors: {},
};

const pad = (n) => String(n).padStart(2, "0");
const isoDate = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const toMinutes = (hhmm) => {
  if (!hhmm) return null;
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
};
const dm = (d) => `${d.getDate()}. ${d.getMonth() + 1}.`;
const escapeHtml = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function subjectHue(name) {
  let hash = 0;
  for (const ch of name || "") hash = (hash * 31 + ch.codePointAt(0)) >>> 0;
  return hash % 360;
}

function mondayOf(dateStr) {
  const d = new Date(`${dateStr}T12:00:00`);
  d.setDate(d.getDate() - ((d.getDay() + 6) % 7));
  return isoDate(d);
}

class EdookitTimetableCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._weekIndex = null; // null = automatic
    this._dayOffset = 0;
    this._lastKey = "";
    this._width = 0;
  }

  static getConfigElement() {
    return document.createElement("edookit-timetable-card-editor");
  }

  static getStubConfig(hass) {
    const entity = Object.keys(hass.states).find(
      (id) => id.startsWith("sensor.") && Array.isArray(hass.states[id].attributes.days) && hass.states[id].attributes.bell
    );
    return { entity: entity || "" };
  }

  setConfig(config) {
    if (!config) throw new Error("Invalid configuration");
    this._config = { ...DEFAULTS, ...config };
    this._lastKey = "";
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    const st = this._config && hass.states[this._config.entity];
    // Re-render when the entity changes or once a minute (current lesson highlight).
    const key = `${st?.last_updated}|${st?.state}|${Math.floor(Date.now() / 60000)}|${hass.language}`;
    if (key !== this._lastKey) {
      this._lastKey = key;
      this._render();
    }
  }

  connectedCallback() {
    if (!this._resizeObserver && window.ResizeObserver) {
      this._resizeObserver = new ResizeObserver((entries) => {
        const width = Math.round(entries[0].contentRect.width);
        if (Math.abs(width - this._width) > 20) {
          this._width = width;
          this._render();
        }
      });
      this._resizeObserver.observe(this);
    }
  }

  disconnectedCallback() {
    this._resizeObserver?.disconnect();
    this._resizeObserver = undefined;
  }

  getCardSize() {
    return this._effectiveView() === "week" ? 7 : 5;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 4, rows: "auto" };
  }

  get _t() {
    const lang = (this._hass?.language || "cs").slice(0, 2);
    return STRINGS[lang] || STRINGS.cs;
  }

  _effectiveView() {
    const view = this._config?.view || "week";
    if (view !== "auto") return view;
    return (this._width || this.clientWidth || 800) >= 400 ? "week" : "day";
  }

  _weeks(days) {
    const weeks = [];
    for (const day of days) {
      const monday = mondayOf(day.date);
      let week = weeks.find((w) => w.monday === monday);
      if (!week) weeks.push((week = { monday, days: [] }));
      week.days.push(day);
    }
    return weeks;
  }

  _autoWeekIndex(weeks, attrs) {
    const now = new Date();
    const today = isoDate(now);
    let idx = weeks.findIndex((w) => w.monday === mondayOf(today));
    if (idx < 0) idx = weeks.findIndex((w) => w.monday > mondayOf(today));
    if (idx < 0) return Math.max(0, weeks.length - 1);
    const mode = this._config.next_week_from;
    const wd = (now.getDay() + 6) % 7;
    let jump = false;
    if (mode === "saturday") jump = wd >= 5;
    else if (mode === "friday_after_school") {
      if (wd >= 5) jump = true;
      else if (wd === 4) {
        const todays = (attrs.days || []).find((d) => d.date === today)?.lessons || [];
        const ends = todays.map((l) => toMinutes(l.end || l.start)).filter((m) => m !== null);
        jump = now.getHours() * 60 + now.getMinutes() >= (ends.length ? Math.max(...ends) : 12 * 60);
      }
    }
    if (jump && idx + 1 < weeks.length) idx += 1;
    return idx;
  }

  _periods(attrs, days) {
    const bell = Array.isArray(attrs.bell) ? [...attrs.bell] : [];
    const known = new Set(bell.map((b) => b.period));
    for (const day of days)
      for (const l of day.lessons || [])
        if (l.period && l.kind !== "event" && !known.has(l.period)) {
          known.add(l.period);
          bell.push({ period: l.period, start: l.start, end: l.end });
        }
    bell.sort((a, b) => a.period - b.period);
    const used = new Set();
    for (const d of days)
      for (const l of d.lessons || []) {
        if (!l.period) continue;
        for (let p = l.period; p <= (l.kind === "event" && l.period_end ? l.period_end : l.period); p++) used.add(p);
      }
    if (!used.size) return [];
    const first = Math.min(...used);
    const last = Math.max(...used);
    // Like the portal: from the 1st period (or the first used one) to the last used one.
    return bell.filter((b) => b.period >= Math.min(first, 1) && b.period <= last);
  }

  _isNow(lesson, dateStr) {
    if (!this._config.highlight_now) return false;
    const now = new Date();
    if (isoDate(now) !== dateStr) return false;
    const mins = now.getHours() * 60 + now.getMinutes();
    const start = toMinutes(lesson.start);
    const end = toMinutes(lesson.end) ?? (start !== null ? start + 45 : null);
    return start !== null && mins >= start && mins < end;
  }

  _tooltip(l) {
    const t = this._t;
    return [
      l.subject_short && l.subject && l.subject_short !== l.subject ? `${l.subject_short} (${l.subject})` : l.subject,
      l.teacher,
      l.room,
      l.start && `${l.start}–${l.end || ""}`,
      l.status || (l.changed ? t.changed : ""),
      l.exam && `${t.exam} – ${l.exam}`,
      l.topic && `${t.topic}: ${l.topic}`,
    ]
      .filter(Boolean)
      .join("\n");
  }

  _style(l) {
    if (!this._config.colorize) return "";
    const custom = this._config.subject_colors?.[l.subject] || this._config.subject_colors?.[l.subject_short];
    const hue = subjectHue(l.subject);
    const color = custom || `hsl(${hue}, 60%, 48%)`;
    return `style="--lesson-bg: color-mix(in srgb, ${color} 16%, var(--card-background-color, #fff)); --lesson-border: ${color};"`;
  }

  _lessonBox(l, dateStr) {
    const t = this._t;
    const cfg = this._config;
    const classes = ["box"];
    if (cfg.show_exams && l.exam) classes.push("has-exam");
    if (l.cancelled) classes.push("cancelled");
    else if (l.changed) classes.push("changed");
    if (this._isNow(l, dateStr)) classes.push("now");
    const name = cfg.short_names ? l.subject_short || l.subject : l.subject;
    const label = l.cancelled
      ? `<span class="status">${escapeHtml(l.status || t.cancelled)}</span> <s>${escapeHtml(name)}</s>`
      : escapeHtml(name);
    const exam =
      cfg.show_exams && l.exam ? `<span class="exam" title="${escapeHtml(l.exam)}">${t.exam} - ${escapeHtml(l.exam)}</span>` : "";
    const teacher = cfg.show_teacher && !l.cancelled ? escapeHtml(l.teacher_short || l.teacher || "") : "";
    const room = cfg.show_room && !l.cancelled ? escapeHtml(l.room || "") : "";
    return `<div class="${classes.join(" ")}" ${this._style(l)} title="${escapeHtml(this._tooltip(l))}">
        ${exam}
        <div class="name ${l.cancelled ? "small" : ""}">${label}</div>
        ${teacher || room ? `<div class="foot"><span>${teacher}</span><span>${room}</span></div>` : ""}
      </div>`;
  }

  _weekHtml(week, attrs) {
    const t = this._t;
    const days = week.days;
    const periods = this._periods(attrs, days);
    if (!periods.length) return `<div class="empty">${t.free}</div>`;
    const today = isoDate(new Date());
    const col = (period) => periods.findIndex((p) => p.period === period) + 2; // column 1 = day names

    const head = periods
      .map(
        (p) => `<div class="ph"><b>${p.period}.</b>${
          this._config.show_times && p.start ? `<span>${p.start}–${p.end || ""}</span>` : ""
        }</div>`
      )
      .join("");

    let row = 2;
    const body = days
      .map((day) => {
        const all = day.lessons || [];
        const lessons = all.filter((l) => l.kind !== "event");
        const events = all.filter((l) => l.kind === "event");
        const d = new Date(`${day.date}T12:00:00`);
        const rows = events.length ? 2 : 1;
        let html = `<div class="dh ${day.date === today ? "today" : ""}" style="grid-row:${row} / span ${rows}">
            <b>${escapeHtml(day.weekday_short)}</b><span>${dm(d)}</span></div>`;
        for (const p of periods) {
          const here = lessons.filter((l) => l.period === p.period);
          html += `<div class="cell" style="grid-row:${row};grid-column:${col(p.period)}">${here
            .map((l) => this._lessonBox(l, day.date))
            .join("")}</div>`;
        }
        // School events: a purple bar under the day's lessons, over the periods they cover.
        for (const ev of events) {
          const from = ev.all_day || !ev.period ? 2 : Math.max(2, col(ev.period));
          const to = ev.all_day || !ev.period ? periods.length + 2 : Math.max(from + 1, col(ev.period_end || ev.period) + 1);
          html += `<div class="event" style="grid-row:${row + 1};grid-column:${from} / ${to}" title="${escapeHtml(
            [ev.subject, ev.start && `${ev.start}–${ev.end || ""}`].filter(Boolean).join("\n")
          )}">${escapeHtml(ev.subject)}</div>`;
        }
        row += rows;
        return html;
      })
      .join("");

    return `<div class="scroll"><div class="grid" style="grid-template-columns: 46px repeat(${periods.length}, minmax(48px, 1fr)); min-width: ${
      46 + periods.length * 52
    }px;"><div class="corner"></div>${head}${body}</div></div>`;
  }

  _dayHtml(day) {
    const t = this._t;
    const all = day?.lessons || [];
    if (!all.length) return `<div class="empty">${t.free}</div>`;
    const events = all.filter((l) => l.kind === "event");
    const lessons = all.filter((l) => l.kind !== "event");
    return `<div class="daylist">
      ${events
        .map(
          (ev) =>
            `<div class="event">${escapeHtml(ev.subject)}${ev.start && !ev.all_day ? ` · ${ev.start}–${ev.end || ""}` : ""}</div>`
        )
        .join("")}
      ${lessons
        .map((l) => {
          const now = this._isNow(l, day.date);
          const meta = [
            this._config.show_teacher && l.teacher,
            this._config.show_room && l.room,
            l.topic && `${t.topic}: ${l.topic}`,
          ].filter(Boolean);
          return `<div class="row ${now ? "is-now" : ""} ${l.cancelled ? "cancelled" : l.changed ? "changed" : ""}">
            <div class="when"><b>${l.period ? l.period + "." : ""}</b>${
              this._config.show_times && l.start ? `<span>${l.start}<br>${l.end || ""}</span>` : ""
            }</div>
            <div class="box wide" ${this._style(l)} title="${escapeHtml(this._tooltip(l))}">
              ${this._config.show_exams && l.exam ? `<span class="exam">${t.exam} - ${escapeHtml(l.exam)}</span>` : ""}
              <div class="name">${
                l.cancelled ? `<span class="status">${escapeHtml(l.status || t.cancelled)}</span> <s>${escapeHtml(l.subject)}</s>` : escapeHtml(l.subject)
              }</div>
              ${meta.length ? `<div class="meta">${meta.map(escapeHtml).join(" · ")}</div>` : ""}
            </div>
          </div>`;
        })
        .join("")}
    </div>`;
  }

  _render() {
    if (!this._config) return;
    const t = this._t;
    const cfg = this._config;
    const st = this._hass?.states[cfg.entity];
    let body = "";
    let nav = "";
    let footer = "";
    let title = "";
    let subtitle = "";

    if (!cfg.entity) body = `<div class="empty">${t.noEntity}</div>`;
    else if (!st) body = `<div class="empty">${t.notFound}: ${escapeHtml(cfg.entity)}</div>`;
    else if (st.state === "unavailable") body = `<div class="empty">${t.unavailable}</div>`;
    else {
      const attrs = st.attributes || {};
      const days = attrs.days || [];
      title = cfg.title ?? (attrs.student ? `${attrs.student}`.replace(/\s*\(.*\)\s*$/, "") : attrs.friendly_name || "Rozvrh");
      subtitle = cfg.title === undefined ? attrs.class_name || "" : "";
      const total = days.reduce((n, d) => n + (d.lessons || []).length, 0);
      if (!days.length) body = `<div class="empty">${t.noData}</div>`;
      else if (!total) body = `<div class="empty">${t.empty}</div>`;
      else if (this._effectiveView() === "week") {
        const weeks = this._weeks(days);
        const auto = this._autoWeekIndex(weeks, attrs);
        const idx = Math.min(Math.max(this._weekIndex ?? auto, 0), weeks.length - 1);
        const week = weeks[idx];
        const monday = new Date(`${week.monday}T12:00:00`);
        const sunday = new Date(monday);
        sunday.setDate(sunday.getDate() + 6);
        nav = `<div class="nav">
            <button class="prev" ${idx === 0 ? "disabled" : ""} title="${t.prev}">←</button>
            <b>${t.week(dm(monday), dm(sunday))}</b>
            <button class="next" ${idx >= weeks.length - 1 ? "disabled" : ""} title="${t.next}">→</button>
            ${idx !== auto ? `<a class="reset">${t.currentWeek}</a>` : ""}
          </div>`;
        body = this._weekHtml(week, attrs);
        this._navTarget = { kind: "week", idx, max: weeks.length - 1 };
      } else {
        const today = isoDate(new Date());
        let baseIdx = days.findIndex((d) => d.date >= today);
        if (baseIdx < 0) baseIdx = days.length - 1;
        if (days[baseIdx]?.date === today) {
          // After the last lesson, show the next day with lessons.
          const ends = (days[baseIdx].lessons || []).map((l) => toMinutes(l.end || l.start)).filter((m) => m !== null);
          const now = new Date();
          if (!ends.length || now.getHours() * 60 + now.getMinutes() >= Math.max(...ends)) {
            const nextIdx = days.findIndex((d, i) => i > baseIdx && (d.lessons || []).length);
            if (nextIdx >= 0) baseIdx = nextIdx;
          }
        }
        const idx = Math.min(Math.max(baseIdx + this._dayOffset, 0), days.length - 1);
        const day = days[idx];
        const d = new Date(`${day.date}T12:00:00`);
        const tomorrow = new Date();
        tomorrow.setDate(tomorrow.getDate() + 1);
        const rel = day.date === today ? t.today : day.date === isoDate(tomorrow) ? t.tomorrow : "";
        const count = (day.lessons || []).filter((l) => !l.cancelled && l.kind !== "event").length;
        nav = `<div class="nav">
            <button class="prev" ${idx === 0 ? "disabled" : ""} title="${t.prev}">←</button>
            <b>${rel ? `${rel} · ` : ""}${escapeHtml(day.weekday)} ${dm(d)}</b>
            <span class="count">${count ? t.lessons(count) : ""}</span>
            <button class="next" ${idx >= days.length - 1 ? "disabled" : ""} title="${t.next}">→</button>
            ${idx !== baseIdx ? `<a class="reset">${t.today}</a>` : ""}
          </div>`;
        body = this._dayHtml(day);
        this._navTarget = { kind: "day", idx, base: baseIdx, max: days.length - 1 };
      }
      if (cfg.show_footer && attrs.last_update) {
        const upd = new Date(attrs.last_update);
        footer = `<div class="footer">${t.updated}: ${upd.toLocaleString(this._hass.locale?.language || "cs", {
          day: "numeric",
          month: "numeric",
          hour: "2-digit",
          minute: "2-digit",
        })}</div>`;
      }
    }

    const header =
      cfg.show_header && (title || nav)
        ? `<div class="header">
            ${title ? `<div class="title">${escapeHtml(title)} ${subtitle ? `<span class="class">${escapeHtml(subtitle)}</span>` : ""}</div>` : ""}
            ${nav}
          </div>`
        : nav;

    this.shadowRoot.innerHTML = `
      <style>${EdookitTimetableCard.styles}</style>
      <ha-card>
        ${header}
        <div class="content">${body}</div>
        ${footer}
      </ha-card>`;

    this.shadowRoot.querySelector(".title")?.addEventListener("click", () => {
      const ev = new Event("hass-more-info", { bubbles: true, composed: true });
      ev.detail = { entityId: cfg.entity };
      this.dispatchEvent(ev);
    });
    this.shadowRoot.querySelector(".prev")?.addEventListener("click", () => this._move(-1));
    this.shadowRoot.querySelector(".next")?.addEventListener("click", () => this._move(1));
    this.shadowRoot.querySelector(".reset")?.addEventListener("click", () => {
      this._weekIndex = null;
      this._dayOffset = 0;
      this._render();
    });
  }

  _move(delta) {
    const nav = this._navTarget;
    if (!nav) return;
    if (nav.kind === "week") this._weekIndex = Math.min(Math.max(nav.idx + delta, 0), nav.max);
    else this._dayOffset = Math.min(Math.max(nav.idx + delta, 0), nav.max) - nav.base;
    this._render();
  }

  static get styles() {
    return `
      :host {
        display: block;
        --edoo-box: var(--secondary-background-color, #f7f7f7);
        --edoo-border: var(--divider-color, #c1c1c1);
        --edoo-orange: #ea8400;
        --edoo-green: #38d07c;
        --edoo-event: rgba(196, 140, 230, 0.35);
        --edoo-event-border: rgba(150, 90, 200, 0.6);
        --edoo-link: var(--primary-color, #377fea);
      }
      ha-card { overflow: hidden; }
      .header { padding: 14px 16px 6px; }
      .title { font-size: 1.35em; color: var(--primary-text-color); cursor: pointer; }
      .title .class { color: var(--secondary-text-color); font-size: 0.8em; margin-left: 10px; }
      .nav { display: flex; align-items: center; gap: 10px; margin-top: 10px; color: var(--primary-text-color); flex-wrap: wrap; }
      .nav b { font-weight: 600; }
      .nav .count { color: var(--secondary-text-color); font-size: 0.9em; }
      .nav button { border: none; background: none; color: var(--edoo-link); font-size: 20px; cursor: pointer; padding: 0 4px; line-height: 1; }
      .nav button:disabled { opacity: 0.25; cursor: default; }
      .nav .reset { margin-left: auto; color: var(--edoo-link); cursor: pointer; }
      .content { padding: 4px 12px 12px; }
      .empty { padding: 24px 8px; text-align: center; color: var(--secondary-text-color); }
      .scroll { overflow-x: auto; }
      .grid { display: grid; gap: 0; border-top: 1px solid var(--edoo-border); }
      .corner, .ph, .dh, .cell, .event { border-bottom: 1px solid var(--edoo-border); }
      .ph { padding: 4px 4px 3px; font-size: 0.72em; color: var(--primary-text-color); border-left: 1px solid var(--edoo-border); line-height: 1.25; }
      .ph b { display: block; }
      .ph span { color: var(--secondary-text-color); white-space: nowrap; }
      .dh { padding: 6px 4px; display: flex; flex-direction: column; color: var(--primary-text-color); }
      .dh b { font-weight: 500; font-size: 1.05em; }
      .dh span { font-size: 0.65em; color: var(--secondary-text-color); }
      .dh.today b, .dh.today span { color: var(--edoo-link); font-weight: 700; }
      .cell { padding: 3px; border-left: 1px solid var(--edoo-border); display: flex; flex-direction: column; gap: 3px; min-height: 52px; min-width: 0; }
      .box {
        position: relative; flex: 1; min-width: 0; min-height: 46px;
        background: var(--lesson-bg, var(--edoo-box)); border: 1px solid var(--lesson-border, var(--edoo-border));
        border-radius: 7px; display: flex; flex-direction: column; justify-content: center; padding: 2px 4px;
        color: var(--primary-text-color); box-sizing: border-box;
      }
      .box .name { text-align: center; font-size: 1.35em; line-height: 1.2; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; padding-top: 4px; }
      .box .name.small { font-size: 0.85em; font-weight: 600; }
      .box .foot { display: flex; justify-content: space-between; gap: 4px; font-size: 0.62em; color: var(--secondary-text-color); white-space: nowrap; }
      .box .foot span { overflow: hidden; text-overflow: ellipsis; }
      .box .exam {
        position: absolute; top: 2px; right: 3px; max-width: calc(100% - 6px);
        background: var(--edoo-green); color: #fff; border-radius: 9px; padding: 0 5px;
        font-size: 0.55em; line-height: 1.5; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
      }
      .box.has-exam .name { padding-top: 13px; }
      .box.cancelled { border-color: var(--edoo-orange); }
      .box .status { color: var(--edoo-orange); font-weight: 700; }
      .box.cancelled s { color: var(--secondary-text-color); }
      .box.changed { border-color: var(--edoo-orange); }
      .box.changed .name { color: var(--edoo-orange); }
      .box.now { box-shadow: 0 0 0 2px var(--edoo-link); }
      .event {
        margin: 0 3px 3px; background: var(--edoo-event); border: 1px solid var(--edoo-event-border); border-radius: 7px;
        text-align: center; font-weight: 600; font-size: 0.8em; padding: 4px 6px; color: var(--primary-text-color);
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
      }
      .daylist { display: flex; flex-direction: column; gap: 6px; padding-top: 6px; }
      .daylist .event { margin: 0; white-space: normal; }
      .row { display: flex; gap: 10px; align-items: stretch; }
      .row .when { color: var(--primary-text-color); width: 44px; flex: none; text-align: center; display: flex; flex-direction: column; justify-content: center; font-size: 0.8em; }
      .row .when span { color: var(--secondary-text-color); font-size: 0.9em; line-height: 1.25; }
      .row.is-now .when b { color: var(--edoo-link); }
      .box.wide { padding: 8px 10px; }
      .box.wide.has-exam .name, .box.wide .name { padding-top: 0; }
      .box.wide .name { text-align: left; font-size: 1.05em; font-weight: 600; white-space: normal; padding-top: 0; }
      .box.wide .meta { font-size: 0.8em; color: var(--secondary-text-color); margin-top: 2px; }
      .box.wide .exam { position: static; display: inline-block; align-self: flex-start; font-size: 0.72em; margin-bottom: 3px; }
      .row.cancelled .box { border-color: var(--edoo-orange); }
      .row.changed .box { border-color: var(--edoo-orange); }
      .footer { padding: 0 16px 10px; font-size: 0.72em; color: var(--secondary-text-color); text-align: right; }
    `;
  }
}

class EdookitTimetableCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = { ...config };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    if (this._form) this._form.hass = hass;
    else this._render();
  }

  _schema() {
    return [
      { name: "entity", required: true, selector: { entity: { domain: "sensor", integration: "edookit" } } },
      { name: "title", selector: { text: {} } },
      {
        name: "view",
        selector: {
          select: {
            mode: "dropdown",
            options: [
              { value: "week", label: "Týden (mřížka jako na portálu)" },
              { value: "auto", label: "Automaticky (podle šířky)" },
              { value: "day", label: "Den (seznam)" },
            ],
          },
        },
      },
      {
        name: "next_week_from",
        selector: {
          select: {
            mode: "dropdown",
            options: [
              { value: "friday_after_school", label: "Pátek po vyučování" },
              { value: "saturday", label: "Sobota" },
              { value: "never", label: "Nikdy" },
            ],
          },
        },
      },
      {
        type: "grid",
        name: "",
        schema: [
          { name: "show_room", selector: { boolean: {} } },
          { name: "show_teacher", selector: { boolean: {} } },
          { name: "show_exams", selector: { boolean: {} } },
          { name: "short_names", selector: { boolean: {} } },
          { name: "colorize", selector: { boolean: {} } },
          { name: "show_times", selector: { boolean: {} } },
          { name: "show_footer", selector: { boolean: {} } },
          { name: "highlight_now", selector: { boolean: {} } },
        ],
      },
    ];
  }

  _render() {
    if (!this._hass || !this._config) return;
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.computeLabel = (s) =>
        ({
          entity: "Entita rozvrhu",
          title: "Nadpis",
          view: "Zobrazení",
          next_week_from: "Přepnout na další týden",
          show_room: "Učebna",
          show_teacher: "Vyučující",
          show_exams: "Písemky",
          short_names: "Zkratky předmětů (jako na portálu)",
          colorize: "Barevně podle předmětu",
          show_times: "Časy hodin",
          show_footer: "Čas aktualizace",
          highlight_now: "Zvýraznit probíhající hodinu",
        })[s.name] || s.name;
      this._form.addEventListener("value-changed", (ev) => {
        this._config = ev.detail.value;
        this.dispatchEvent(new CustomEvent("config-changed", { detail: { config: this._config }, bubbles: true, composed: true }));
      });
      this.appendChild(this._form);
    }
    this._form.hass = this._hass;
    this._form.schema = this._schema();
    this._form.data = { ...DEFAULTS, ...this._config };
  }
}

if (!customElements.get("edookit-timetable-card")) {
  customElements.define("edookit-timetable-card", EdookitTimetableCard);
  customElements.define("edookit-timetable-card-editor", EdookitTimetableCardEditor);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "edookit-timetable-card",
    name: "Edookit rozvrh",
    description: "Rozvrh hodin z integrace Edookit (týdenní mřížka nebo denní seznam).",
    preview: true,
    documentationURL: "https://github.com/joshuaaaaa/HA-edookit/tree/main/edookit-timetable-card",
  });
  console.info(`%c EDOOKIT-TIMETABLE-CARD %c ${CARD_VERSION} `, "background:#3f51b5;color:#fff", "background:#ddd;color:#000");
}
