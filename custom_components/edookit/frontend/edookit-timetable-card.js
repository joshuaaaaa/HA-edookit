/*
 * Edookit timetable card for Home Assistant.
 *
 * Shows the timetable stored in the attributes of the Edookit "Rozvrh"
 * sensor (sensor.*_rozvrh / sensor.*_timetable): a week grid (days x
 * periods) or a single-day list. The integration downloads the timetable
 * once a day at the time set in its options; the card just renders it.
 *
 *   type: custom:edookit-timetable-card
 *   entity: sensor.jan_novak_rozvrh
 */

const CARD_VERSION = "0.1.0";

const STRINGS = {
  cs: {
    noEntity: "Vyberte entitu rozvrhu Edookit",
    notFound: "Entita nenalezena",
    noData: "Rozvrh zatím není načten",
    free: "Volno 🎉",
    period: ".",
    updated: "Aktualizováno",
    cancelled: "zrušeno",
    changed: "změna",
    today: "Dnes",
    tomorrow: "Zítra",
    week: "Týden",
    prev: "Předchozí",
    next: "Další",
    now: "Teď",
    lessons: (n) => (n === 1 ? "1 hodina" : n >= 2 && n <= 4 ? `${n} hodiny` : `${n} hodin`),
  },
  en: {
    noEntity: "Select an Edookit timetable entity",
    notFound: "Entity not found",
    noData: "Timetable not loaded yet",
    free: "No lessons 🎉",
    period: ".",
    updated: "Updated",
    cancelled: "cancelled",
    changed: "changed",
    today: "Today",
    tomorrow: "Tomorrow",
    week: "Week",
    prev: "Previous",
    next: "Next",
    now: "Now",
    lessons: (n) => (n === 1 ? "1 lesson" : `${n} lessons`),
  },
};

const DEFAULTS = {
  view: "auto", // auto | week | day
  show_room: true,
  show_teacher: false,
  show_times: true,
  show_footer: true,
  short_names: "auto", // auto | true | false
  highlight_now: true,
  next_week_from: "friday_after_school", // when to jump to the next week: friday_after_school | saturday | never
  subject_colors: {},
};

const pad = (n) => String(n).padStart(2, "0");
const isoDate = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const toMinutes = (hhmm) => {
  if (!hhmm) return null;
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
};
const escapeHtml = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function subjectHue(name) {
  let hash = 0;
  for (const ch of name || "") hash = (hash * 31 + ch.codePointAt(0)) >>> 0;
  return hash % 360;
}

function mondayOf(dateStr) {
  const d = new Date(`${dateStr}T12:00:00`);
  const wd = (d.getDay() + 6) % 7;
  d.setDate(d.getDate() - wd);
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
    // Re-render when the entity changes or at most once a minute (current-lesson highlight).
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
    return this._effectiveView() === "week" ? 6 : 4;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 4, rows: "auto" };
  }

  get _t() {
    const lang = (this._hass?.language || "cs").slice(0, 2);
    return STRINGS[lang] || STRINGS.cs;
  }

  _effectiveView() {
    const view = this._config?.view || "auto";
    if (view !== "auto") return view;
    return (this._width || this.clientWidth || 800) >= 560 ? "week" : "day";
  }

  _weeks(days) {
    const weeks = [];
    for (const day of days) {
      const monday = mondayOf(day.date);
      let week = weeks.find((w) => w.monday === monday);
      if (!week) {
        week = { monday, days: [] };
        weeks.push(week);
      }
      week.days.push(day);
    }
    return weeks;
  }

  _autoWeekIndex(weeks, attrs) {
    const now = new Date();
    const today = isoDate(now);
    const thisMonday = mondayOf(today);
    let idx = weeks.findIndex((w) => w.monday === thisMonday);
    if (idx < 0) idx = weeks.findIndex((w) => w.monday > thisMonday);
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
        const last = ends.length ? Math.max(...ends) : 12 * 60;
        jump = now.getHours() * 60 + now.getMinutes() >= last;
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
        if (l.period && !known.has(l.period)) {
          known.add(l.period);
          bell.push({ period: l.period, start: l.start, end: l.end });
        }
    bell.sort((a, b) => a.period - b.period);
    // Hide trailing/leading periods that are empty in the shown week.
    const used = new Set(days.flatMap((d) => (d.lessons || []).map((l) => l.period)));
    const usedList = bell.filter((b) => used.has(b.period));
    if (!usedList.length) return [];
    const first = usedList[0].period;
    const last = usedList[usedList.length - 1].period;
    return bell.filter((b) => b.period >= first && b.period <= last);
  }

  _color(subject) {
    const custom = this._config.subject_colors?.[subject];
    if (custom) return { bg: `color-mix(in srgb, ${custom} 22%, transparent)`, border: custom };
    const hue = subjectHue(subject);
    return { bg: `hsla(${hue}, 70%, 55%, 0.16)`, border: `hsl(${hue}, 60%, 48%)` };
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

  _lessonHtml(lesson, dateStr, compact, wide = false) {
    const c = this._color(lesson.subject);
    const classes = ["lesson"];
    if (lesson.cancelled) classes.push("cancelled");
    else if (lesson.changed) classes.push("changed");
    if (this._isNow(lesson, dateStr)) classes.push("now");
    const name = compact ? lesson.subject_short || lesson.subject : lesson.subject;
    const meta = [];
    if (this._config.show_room && lesson.room) meta.push(`<span class="room">${escapeHtml(lesson.room)}</span>`);
    if (this._config.show_teacher && lesson.teacher) meta.push(`<span class="teacher">${escapeHtml(lesson.teacher)}</span>`);
    if (wide && (lesson.cancelled || lesson.changed))
      meta.push(`<span class="badge">${lesson.cancelled ? this._t.cancelled : this._t.changed}</span>`);
    const tooltip = [
      lesson.subject,
      lesson.start && `${lesson.start}${lesson.end ? "–" + lesson.end : ""}`,
      lesson.room,
      lesson.teacher,
      lesson.group,
      lesson.cancelled ? this._t.cancelled : lesson.changed ? this._t.changed : "",
      lesson.note,
    ]
      .filter(Boolean)
      .join("\n");
    if (wide) classes.push("wide");
    return `<div class="${classes.join(" ")}" style="--bg:${c.bg};--accent:${c.border}" title="${escapeHtml(tooltip)}">
        <div class="subject">${escapeHtml(name)}</div>
        ${meta.length ? `<div class="meta">${meta.join(" · ")}</div>` : ""}
      </div>`;
  }

  _weekHtml(week, attrs) {
    const t = this._t;
    const days = week.days;
    const periods = this._periods(attrs, days);
    if (!periods.length) return `<div class="empty">${t.free}</div>`;
    const compact =
      this._config.short_names === true ||
      (this._config.short_names === "auto" && (this._width || 800) / (periods.length + 1) < 95);
    const today = isoDate(new Date());
    const head = periods
      .map(
        (p) => `<div class="ph">
          <div class="pn">${p.period}${t.period}</div>
          ${this._config.show_times && p.start ? `<div class="pt">${p.start}${p.end ? "–" + p.end : ""}</div>` : ""}
        </div>`
      )
      .join("");
    const rows = days
      .map((day) => {
        const d = new Date(`${day.date}T12:00:00`);
        const cells = periods
          .map((p) => {
            const lessons = (day.lessons || []).filter((l) => l.period === p.period);
            return `<div class="cell">${lessons.map((l) => this._lessonHtml(l, day.date, compact)).join("")}</div>`;
          })
          .join("");
        return `<div class="dh ${day.date === today ? "today" : ""}">
            <div class="dn">${escapeHtml(day.weekday_short)}</div>
            <div class="dd">${d.getDate()}. ${d.getMonth() + 1}.</div>
          </div>${cells}`;
      })
      .join("");
    return `<div class="grid" style="grid-template-columns: 52px repeat(${periods.length}, minmax(0, 1fr));">
        <div class="corner"></div>${head}${rows}
      </div>`;
  }

  _dayHtml(day) {
    const t = this._t;
    const lessons = day?.lessons || [];
    if (!lessons.length) return `<div class="empty">${t.free}</div>`;
    return `<div class="daylist">${lessons
      .map((l) => {
        const now = this._isNow(l, day.date);
        return `<div class="row ${now ? "is-now" : ""}">
          <div class="when">
            <div class="pn">${l.period ? l.period + t.period : ""}</div>
            ${this._config.show_times && l.start ? `<div class="pt">${l.start}${l.end ? "<br>" + l.end : ""}</div>` : ""}
          </div>
          ${this._lessonHtml(l, day.date, false, true)}
        </div>`;
      })
      .join("")}</div>`;
  }

  _render() {
    if (!this._config) return;
    const t = this._t;
    const cfg = this._config;
    const st = this._hass?.states[cfg.entity];
    let body = "";
    let nav = "";
    let footer = "";
    let title = cfg.title ?? "";

    if (!cfg.entity) body = `<div class="empty">${t.noEntity}</div>`;
    else if (!st) body = `<div class="empty">${t.notFound}: ${escapeHtml(cfg.entity)}</div>`;
    else {
      const attrs = st.attributes || {};
      const days = attrs.days || [];
      if (cfg.title === undefined) title = attrs.student ? `${attrs.student}` : attrs.friendly_name || "Rozvrh";
      if (!days.length) body = `<div class="empty">${t.noData}</div>`;
      else if (this._effectiveView() === "week") {
        const weeks = this._weeks(days);
        const auto = this._autoWeekIndex(weeks, attrs);
        const idx = Math.min(Math.max(this._weekIndex ?? auto, 0), weeks.length - 1);
        const week = weeks[idx];
        const first = new Date(`${week.days[0].date}T12:00:00`);
        const last = new Date(`${week.days[week.days.length - 1].date}T12:00:00`);
        nav = `<div class="nav">
            <button class="prev" ${idx === 0 ? "disabled" : ""} title="${t.prev}">‹</button>
            <span class="label">${first.getDate()}. ${first.getMonth() + 1}. – ${last.getDate()}. ${last.getMonth() + 1}.</span>
            <button class="next" ${idx >= weeks.length - 1 ? "disabled" : ""} title="${t.next}">›</button>
          </div>`;
        body = this._weekHtml(week, attrs);
        this._navTarget = { kind: "week", idx, auto, max: weeks.length - 1 };
      } else {
        const today = isoDate(new Date());
        // Default day: today, or the next day with lessons once today's lessons are over.
        let baseIdx = days.findIndex((d) => d.date >= today);
        if (baseIdx < 0) baseIdx = days.length - 1;
        const todayEntry = days[baseIdx];
        if (todayEntry?.date === today) {
          const ends = (todayEntry.lessons || []).map((l) => toMinutes(l.end || l.start)).filter((m) => m !== null);
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
        const count = (day.lessons || []).filter((l) => !l.cancelled).length;
        nav = `<div class="nav">
            <button class="prev" ${idx === 0 ? "disabled" : ""} title="${t.prev}">‹</button>
            <span class="label">${rel ? `<b>${rel}</b> · ` : ""}${escapeHtml(day.weekday)} ${d.getDate()}. ${d.getMonth() + 1}.
              <span class="count">${count ? t.lessons(count) : ""}</span></span>
            <button class="next" ${idx >= days.length - 1 ? "disabled" : ""} title="${t.next}">›</button>
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

    this.shadowRoot.innerHTML = `
      <style>${EdookitTimetableCard.styles}</style>
      <ha-card>
        ${title || nav ? `<div class="header">
          ${title ? `<div class="title">${escapeHtml(title)}</div>` : ""}
          ${nav}
        </div>` : ""}
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
    this.shadowRoot.querySelector(".nav .label")?.addEventListener("click", () => {
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
      :host { display: block; }
      ha-card { overflow: hidden; }
      .header { display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap;
        padding: 12px 16px 4px; }
      .title { font-size: 1.15em; font-weight: 500; cursor: pointer; color: var(--primary-text-color); }
      .nav { display: flex; align-items: center; gap: 6px; margin-left: auto; }
      .nav .label { font-size: 0.92em; color: var(--secondary-text-color); cursor: pointer; text-align: center; }
      .nav .count { opacity: 0.8; margin-left: 4px; }
      .nav button { border: none; background: var(--secondary-background-color, rgba(127,127,127,.12));
        color: var(--primary-text-color); width: 30px; height: 30px; border-radius: 50%; font-size: 18px;
        line-height: 1; cursor: pointer; }
      .nav button:disabled { opacity: 0.3; cursor: default; }
      .content { padding: 8px 12px 12px; }
      .empty { padding: 24px 8px; text-align: center; color: var(--secondary-text-color); }
      .grid { display: grid; gap: 4px; }
      .corner { }
      .ph { text-align: center; padding: 2px 0 4px; border-bottom: 1px solid var(--divider-color); }
      .pn { font-weight: 600; font-size: 0.9em; color: var(--primary-text-color); }
      .pt { font-size: 0.68em; color: var(--secondary-text-color); white-space: nowrap; }
      .dh { display: flex; flex-direction: column; justify-content: center; align-items: center; border-radius: 8px;
        padding: 2px 0; color: var(--secondary-text-color); }
      .dh .dn { font-weight: 600; color: var(--primary-text-color); }
      .dh .dd { font-size: 0.72em; }
      .dh.today { background: var(--primary-color); color: var(--text-primary-color, #fff); }
      .dh.today .dn { color: var(--text-primary-color, #fff); }
      .cell { display: flex; flex-direction: column; gap: 2px; min-height: 42px; }
      .lesson { flex: 1; background: var(--bg); border-left: 3px solid var(--accent); border-radius: 6px;
        padding: 3px 5px; min-width: 0; display: flex; flex-direction: column; justify-content: center; }
      .lesson .subject { font-weight: 600; font-size: 0.85em; line-height: 1.15; color: var(--primary-text-color);
        overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .lesson .meta { font-size: 0.7em; color: var(--secondary-text-color); overflow: hidden; text-overflow: ellipsis;
        white-space: nowrap; }
      .lesson.changed { outline: 2px dashed var(--warning-color, #ff9800); outline-offset: -2px; }
      .lesson.cancelled { background: rgba(127,127,127,.12); border-left-color: var(--error-color, #db4437); }
      .lesson.cancelled .subject { text-decoration: line-through; color: var(--secondary-text-color); }
      .lesson.now { box-shadow: 0 0 0 2px var(--primary-color); }
      .daylist { display: flex; flex-direction: column; gap: 6px; }
      .row { display: flex; gap: 10px; align-items: stretch; }
      .row .when { width: 46px; flex: none; text-align: center; display: flex; flex-direction: column; justify-content: center; }
      .row .when .pt { font-size: 0.72em; line-height: 1.25; }
      .lesson.wide { padding: 6px 10px; }
      .lesson.wide .subject { font-size: 1em; white-space: normal; }
      .lesson.wide .meta { font-size: 0.8em; white-space: normal; }
      .row.is-now .when .pn { color: var(--primary-color); }
      .badge { color: var(--warning-color, #ff9800); font-weight: 600; text-transform: uppercase; font-size: 0.85em; }
      .lesson.cancelled .badge { color: var(--error-color, #db4437); }
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
              { value: "auto", label: "Automaticky (podle šířky)" },
              { value: "week", label: "Týden (mřížka)" },
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
    documentationURL: "https://github.com/joshuaaaaa/HA-edookit",
  });
  console.info(`%c EDOOKIT-TIMETABLE-CARD %c ${CARD_VERSION} `, "background:#3f51b5;color:#fff", "background:#ddd;color:#000");
}
