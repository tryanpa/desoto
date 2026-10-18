"use strict";
// Admin app. Student names come from the roster/CSV and are treated as untrusted: every value goes
// into the page through textContent / text nodes (see h()), never innerHTML.

// ------------------------------------------------------------------ helpers
function h(tag, props, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v === true) e.setAttribute(k, "");
    else if (v !== false && v != null) e.setAttribute(k, v);
  }
  kids.flat().forEach(c => { if (c != null && c !== false) e.append(c); });
  return e;
}
// replaceChildren() would print a null argument as the text "null", so drop empties first.
const setKids = (el, ...kids) => el.replaceChildren(...kids.flat().filter(k => k != null && k !== false));
const store = {
  get: k => { try { return sessionStorage.getItem(k) || ""; } catch (e) { return ""; } },
  set: (k, v) => { try { sessionStorage.setItem(k, v); } catch (e) {} },
};
const pad = n => String(n).padStart(2, "0");
const isoDay = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const dstr = ts => isoDay(new Date(ts * 1000));
const tstr = ts => ts == null ? "—" : new Date(ts * 1000).toLocaleTimeString([], {hour: "numeric", minute: "2-digit", second: "2-digit"});
const mmss = sec => { const a = Math.max(0, Math.round(sec)); return `${Math.floor(a / 60)}:${pad(a % 60)}`; };
const hm = sec => sec >= 3600 ? `${Math.floor(sec / 3600)}h ${Math.round((sec % 3600) / 60)}m` : `${Math.round(sec / 60)}m`;
const shortDate = iso => new Date(iso + "T12:00:00").toLocaleDateString([], {month: "short", day: "numeric"});
const longDate = iso => new Date(iso + "T12:00:00").toLocaleDateString([], {weekday: "short", month: "short", day: "numeric", year: "numeric"});
const qs = o => { const p = new URLSearchParams(); for (const [k, v] of Object.entries(o)) if (v !== "" && v != null) p.set(k, v); return p.toString(); };
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : w.endsWith("s") ? "es" : "s"}`;

let showIds = store.get("ids") === "1";
const idText = id => !id ? "—" : showIds ? id : "•".repeat(Math.max(0, id.length - 3)) + id.slice(-3);

// ---------------------------------------------------------------------- api
let PIN = store.get("pin");
class ApiError extends Error {
  constructor(status, data) {
    super(data && typeof data.detail === "string" ? data.detail : `Request failed (${status})`);
    this.status = status; this.data = data;
  }
}
async function rawFetch(path, opts, retry = true) {
  const r = await fetch(path, {...opts, headers: {"Content-Type": "application/json", "X-Admin-Pin": PIN}});
  if (r.status === 401 && retry) {
    const p = prompt("Admin PIN");
    if (p === null) throw new ApiError(401, {detail: "Admin PIN required"});
    PIN = p; store.set("pin", p);
    return rawFetch(path, opts, false);
  }
  return r;
}
async function api(path, {method = "GET", body} = {}) {
  const r = await rawFetch(path, {method, body: body ? JSON.stringify(body) : undefined});
  const data = await r.json().catch(() => null);
  if (!r.ok) throw new ApiError(r.status, data);
  return data;
}
async function download(path, filename) {
  const r = await rawFetch(path, {});
  if (!r.ok) throw new ApiError(r.status, null);
  const url = URL.createObjectURL(await r.blob());
  const a = h("a", {href: url, download: filename}); document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}
let rosterCache = null;
const getRoster = async () => rosterCache || (rosterCache = await api("/api/admin/roster"));
const periodKeys = r => Object.keys(r).sort((a, b) => (isNaN(a) || isNaN(b)) ? a.localeCompare(b) : a - b);
const errText = e => e instanceof ApiError && e.data && e.data.errors ? e.data.errors.join(" ") : e.message;

// -------------------------------------------------------------- UI building
function stat(label) {
  const v = h("div", {class: "v"}, "—"), s = h("div", {class: "s"}), el = h("div", {class: "stat"}, v, h("div", {class: "k"}, label), s);
  return {el, set(value, sub, cls) { v.textContent = value; v.className = "v " + (cls || ""); s.textContent = sub || ""; }};
}
function card(title, ...rest) { return h("div", {class: "card"}, h("header", null, h("div", {class: "label"}, title), ...rest)); }
function badge(text, cls, title) { return h("span", {class: "badge " + (cls || ""), title}, text); }
function empty(text) { return h("div", {class: "empty"}, text); }
function btn(text, onclick, cls) { return h("button", {class: "btn " + (cls || ""), onclick, type: "button"}, text); }
function table(cols, rows, opts = {}) {
  const head = h("tr", null, cols.map(c => {
    const th = h("th", {class: (c.num ? "num " : "") + (c.sort ? "sort" : ""), onclick: c.sort ? () => opts.onSort(c.key) : null},
      c.label + (c.key && opts.sortKey === c.key ? (opts.sortDir > 0 ? " ▲" : " ▼") : ""));
    return th;
  }));
  return h("table", {class: "t"}, h("thead", null, head), h("tbody", null, rows));
}

// Date range + period (+ optional search). f is the filter state object; onChange re-queries.
function rangeOf(f) {
  const t = new Date(), back = n => { const d = new Date(t); d.setDate(d.getDate() - n); return isoDay(d); };
  if (f.preset === "today") return {start: isoDay(t), end: isoDay(t)};
  if (f.preset === "7") return {start: back(6), end: isoDay(t)};
  if (f.preset === "30") return {start: back(29), end: isoDay(t)};
  if (f.preset === "custom") return {start: f.start, end: f.end};
  return {start: "", end: ""};
}
async function filterBar(f, onChange, {search} = {}) {
  const bar = h("div", {class: "filters"});
  const presets = [["today", "Today"], ["7", "7 days"], ["30", "30 days"], ["all", "All time"], ["custom", "Custom"]];
  const seg = h("div", {class: "seg"});
  const custom = h("div", {class: "row"});
  const draw = () => {
    seg.replaceChildren(...presets.map(([k, label]) => h("button", {class: f.preset === k ? "on" : "", type: "button", onclick: () => { f.preset = k; f.page = 0; draw(); onChange(); }}, label)));
    custom.replaceChildren(...(f.preset === "custom" ? [
      h("input", {type: "date", value: f.start, "aria-label": "From", onchange: e => { f.start = e.target.value; f.page = 0; onChange(); }}),
      h("span", {class: "help"}, "to"),
      h("input", {type: "date", value: f.end, "aria-label": "To", onchange: e => { f.end = e.target.value; f.page = 0; onChange(); }})] : []));
  };
  draw();
  const roster = await getRoster();
  const sel = h("select", {"aria-label": "Period", onchange: e => { f.period = e.target.value; f.page = 0; onChange(); }},
    h("option", {value: ""}, "All periods"),
    periodKeys(roster.roster).map(p => h("option", {value: p, selected: f.period === p}, "Period " + p)));
  bar.append(seg, custom, sel);
  if (search) {
    let t = null;
    bar.append(h("input", {type: "search", placeholder: "Search name or ID", value: f.q || "", "aria-label": "Search",
      oninput: e => { f.q = e.target.value; f.page = 0; clearTimeout(t); t = setTimeout(onChange, 250); }}));
  }
  return bar;
}

// ------------------------------------------------------------------- router
let current = {}, token = 0, latest = null;
const views = {};
async function route() {
  const name = (location.hash || "#live").slice(1);
  const view = views[name] ? name : "live";
  document.querySelectorAll("#nav a").forEach(a => a.classList.toggle("on", a.getAttribute("href") === "#" + view));
  const my = ++token;
  current = {name: view, redraws: []};
  $("view").replaceChildren();
  try { await views[view](() => my === token); }
  catch (e) { if (my === token) $("view").replaceChildren(h("div", {class: "errbox"}, "Couldn't load this page: " + errText(e))); }
}
window.addEventListener("hashchange", route);
let resizeT = null;
window.addEventListener("resize", () => { clearTimeout(resizeT); resizeT = setTimeout(() => (current.redraws || []).forEach(f => f()), 150); });

// --------------------------------------------------------------------- Live
views.live = async alive => {
  const view = $("view");
  const tWait = stat("Waiting"), tOut = stat("Out now"), tOver = stat("Time over"), tToday = stat("Trips today");
  const outBody = h("div"), queueBody = h("div"), msg = h("span", {class: "help"});
  const clearBtn = btn("Clear queue", async () => {
    const n = latest ? latest.queue.length : 0;
    if (!n || !confirm(`Remove all ${plural(n, "student")} from the queue?`)) return;
    try { const r = await api("/api/admin/queue/clear", {method: "POST"}); msg.textContent = `Cleared ${r.cleared}.`; setTimeout(() => msg.textContent = "", 3000); }
    catch (e) { msg.textContent = errText(e); }
  }, "danger sm");
  view.append(h("h2", null, "Live"), h("div", {class: "lead"}, "What the kiosk and hallway display are showing right now"),
    h("div", {class: "tiles"}, tWait.el, tOut.el, tOver.el, tToday.el),
    card("Currently out").appendChild(outBody).parentNode,
    card("Queue", msg, clearBtn).appendChild(queueBody).parentNode);
  let outKey = null, qKey = "", sinceCell = null, leftCell = null, todayKey = "init";
  async function refreshToday() {
    try {
      const d = await api("/api/admin/stats?" + qs(rangeOf({preset: "today"})));
      if (alive()) tToday.set(String(d.overall.trips), d.overall.over ? `${d.overall.over} over time` : "none over time");
    } catch (e) { /* tile stays as is */ }
  }
  current.onState = (s, left) => {
    tWait.set(String(s.queue.length), s.queue.length ? `Next: ${s.queue[0].student}` : "Queue is empty");
    tOut.set(s.out ? mmss(left) : "—", s.out ? s.out.student : "Pass available", s.out && left < 0 ? "red" : "");
    tOver.set(s.out && left < 0 ? "+" + mmss(-left) : "—", "", s.out && left < 0 ? "red" : "");
    const ok = s.out ? s.out.started_at + "|" + s.out.student : "";
    if (ok !== outKey) {
      outKey = ok;
      if (s.out) {
        sinceCell = h("td", {class: "mono"}, tstr(s.out.started_at)); leftCell = h("td", {class: "num"});
        outBody.replaceChildren(table([{label: "Student"}, {label: "Period"}, {label: "Out since"}, {label: "Time left", num: true}, {label: ""}],
          h("tr", null, h("td", {class: "name green"}, s.out.student), h("td", null, s.out.period ? "Period " + s.out.period : "—"), sinceCell, leftCell,
            h("td", {class: "num"}, btn("Mark returned", async () => {
              if (confirm("Mark the pass as returned? Use this only if the sensor missed it.")) try { await api("/api/admin/pass/return", {method: "POST"}); } catch (e) { alert(errText(e)); }
            }, "sm")))));
      } else outBody.replaceChildren(empty("The pass is available."));
    }
    if (s.out && leftCell) { leftCell.textContent = left < 0 ? "+" + mmss(-left) : mmss(left); leftCell.style.color = left < 0 ? "var(--over)" : ""; }
    const qk = JSON.stringify(s.queue);
    if (qk !== qKey) {
      qKey = qk;
      clearBtn.disabled = !s.queue.length;
      queueBody.replaceChildren(s.queue.length ? table([{label: "#", num: true}, {label: "Student"}, {label: "Period"}, {label: ""}],
        s.queue.map((q, i) => h("tr", null, h("td", {class: "num mono"}, i + 1), h("td", {class: "name"}, q.student), h("td", null, "Period " + q.period),
          h("td", {class: "num"}, btn("Remove", async () => {
            try { await fetch(`/api/queue/${encodeURIComponent(q.period)}/${encodeURIComponent(q.student)}`, {method: "DELETE"}); } catch (e) { /* ws refreshes */ }
          }, "sm"))))) : empty("Nobody is waiting."));
    }
    const tk = s.out ? s.out.started_at : "idle";
    if (tk !== todayKey) { todayKey = tk; refreshToday(); }
  };
  if (latest) current.onState(latest, window.leftNow);
};

// ------------------------------------------------------------------- Roster
views.roster = async alive => {
  const view = $("view");
  rosterCache = null;
  let data = await getRoster();
  if (!alive()) return;
  const f = {q: "", period: ""};

  // --- import
  const imp = {text: "", mode: "replace"};
  const fileIn = h("input", {type: "file", accept: ".csv,.tsv,.txt,text/csv", "aria-label": "CSV file"});
  const defIn = h("input", {placeholder: "e.g. 3", size: 6, "aria-label": "Period for the whole file", oninput: () => preview()});
  const modeSeg = h("div", {class: "seg"});
  const drawMode = () => modeSeg.replaceChildren(...[["replace", "Replace roster"], ["merge", "Add & update"]].map(([k, l]) =>
    h("button", {type: "button", class: imp.mode === k ? "on" : "", onclick: () => { imp.mode = k; drawMode(); preview(); }}, l)));
  drawMode();
  const out = h("div");
  fileIn.addEventListener("change", async () => {
    const file = fileIn.files[0];
    if (!file) return;
    imp.text = await file.text();
    preview();
  });
  async function preview() {
    if (!imp.text) return;
    out.replaceChildren(h("div", {class: "help"}, "Checking file…"));
    try {
      const r = await api("/api/admin/roster/import", {method: "POST", body: {csv: imp.text, mode: imp.mode, default_period: defIn.value, preview: true}});
      if (alive()) renderPreview(r);
    } catch (e) {
      const warn = e.data && e.data.warnings && e.data.warnings.length ? warnList(e.data.warnings) : null;
      setKids(out, h("div", {class: "errbox"}, errText(e)), warn);
    }
  }
  function warnList(w) {
    return h("div", {class: "warnbox"}, `${plural(w.length, "note")} about this file:`,
      h("ul", null, w.slice(0, 8).map(x => h("li", null, x)), w.length > 8 ? h("li", null, `…and ${w.length - 8} more`) : null));
  }
  function renderPreview(r) {
    const apply = btn(imp.mode === "replace" ? "Replace roster" : "Add & update", async () => {
      if (imp.mode === "replace" && r.removed && !confirm(`This removes ${plural(r.removed, "student")} who are not in the file, and any of them waiting in the queue. Continue?`)) return;
      apply.disabled = true;
      try {
        const done = await api("/api/admin/roster/import", {method: "POST", body: {csv: imp.text, mode: imp.mode, default_period: defIn.value, preview: false}});
        imp.text = ""; fileIn.value = ""; rosterCache = null; data = await getRoster();
        out.replaceChildren(h("div", {class: "okbox"}, `Roster updated: ${plural(done.total, "student")} in ${plural(done.periods.length, "period")}.`));
        drawTable();
      } catch (e) { out.replaceChildren(h("div", {class: "errbox"}, errText(e))); }
    }, "primary");
    setKids(out,
      h("div", {class: "row", style: "margin-top:14px"},
        h("div", null, h("b", null, plural(r.total, "student")), ` in ${plural(r.periods.length, "period")}`, " · ",
          h("span", {class: "green"}, `${r.added} new`), " · ", h("span", {class: r.removed ? "red" : ""}, `${r.removed} removed`)),
        h("div", {class: "grow"}), apply),
      r.warnings.length ? warnList(r.warnings) : null,
      h("div", {class: "scroll", style: "max-height:240px;margin-top:12px"}, table([{label: "Period"}, {label: "Students", num: true}, {label: "First few"}],
        r.periods.map(p => h("tr", null, h("td", null, "Period " + p.period), h("td", {class: "num"}, p.count),
          h("td", {class: "mono"}, p.sample.map(s => s.name).join(", ") + (p.count > p.sample.length ? ", …" : "")))))));
  }
  const importCard = card("Import from CSV");
  importCard.append(h("div", {class: "body"},
    h("div", {class: "help"}, "A header row plus one student per row. Columns (any order): ", h("code", null, "Name"), " (or ", h("code", null, "First"), " and ", h("code", null, "Last"), "), ",
      h("code", null, "ID"), ", ", h("code", null, "Period"), ". \"Last, First\" names are flipped to \"First Last\". Nothing changes until you press the green button."),
    h("div", {class: "row", style: "margin-top:14px"}, h("label", {class: "field"}, h("span", null, "CSV file"), fileIn),
      h("label", {class: "field"}, h("span", null, "Period, if the file has no Period column"), defIn),
      h("div", {class: "field"}, h("span", null, "When importing"), modeSeg)),
    out));

  // --- add one
  const aP = h("input", {placeholder: "Period", size: 6, "aria-label": "Period"}), aN = h("input", {placeholder: "Name", "aria-label": "Name"}), aI = h("input", {placeholder: "ID", size: 10, "aria-label": "ID"});
  const aMsg = h("span", {class: "help"});
  const addCard = card("Add a student", aMsg);
  addCard.append(h("div", {class: "body row"}, aP, aN, aI, btn("Add", async () => {
    try {
      await api("/api/admin/roster/student", {method: "POST", body: {period: aP.value, name: aN.value, id: aI.value}});
      aMsg.textContent = `Added ${aN.value.trim()}.`; aN.value = aI.value = "";
      rosterCache = null; data = await getRoster(); drawTable();
    } catch (e) { aMsg.textContent = errText(e); }
  }, "primary")));

  // --- list
  const body = h("div", {class: "scroll", style: "max-height:520px"}), count = h("span", {class: "help"});
  const listCard = card("Students", count);
  const search = h("input", {type: "search", placeholder: "Search name or ID", "aria-label": "Search", oninput: e => { f.q = e.target.value.toLowerCase(); drawTable(); }});
  const psel = h("select", {"aria-label": "Period", onchange: e => { f.period = e.target.value; drawTable(); }});
  listCard.querySelector("header").append(search, psel);
  listCard.append(body);
  function drawTable() {
    psel.replaceChildren(h("option", {value: ""}, "All periods"), periodKeys(data.roster).map(p => h("option", {value: p, selected: f.period === p}, "Period " + p)));
    const rows = [];
    for (const p of periodKeys(data.roster)) {
      if (f.period && p !== f.period) continue;
      for (const s of data.roster[p]) {
        if (f.q && !(s.name.toLowerCase().includes(f.q) || s.id.includes(f.q))) continue;
        rows.push(h("tr", null, h("td", {class: "name"}, s.name), h("td", null, "Period " + p), h("td", {class: "mono"}, idText(s.id)),
          h("td", {class: "num"}, btn("Remove", async () => {
            if (!confirm(`Remove ${s.name} from period ${p}?`)) return;
            try { await api(`/api/admin/roster/student?${qs({period: p, name: s.name})}`, {method: "DELETE"}); rosterCache = null; data = await getRoster(); drawTable(); }
            catch (e) { alert(errText(e)); }
          }, "sm danger"))));
      }
    }
    count.textContent = `${rows.length} shown · ${data.count} total`;
    body.replaceChildren(rows.length ? table([{label: "Name"}, {label: "Period"}, {label: "ID"}, {label: ""}], rows) : empty("No students match."));
  }
  current.onIds = drawTable;
  drawTable();
  view.append(h("h2", null, "Roster"), h("div", {class: "lead"}, "Who appears on the kiosk. Student IDs are only ever shown on these admin pages."), importCard, addCard, listCard);
};

// ------------------------------------------------------------- Activity log
const fAct = {preset: "7", start: "", end: "", period: "", q: "", page: 0};
views.activity = async alive => {
  const view = $("view"), PAGE = 50;
  const body = h("div"), pager = h("div", {class: "pager"}), listCard = card("Passes");
  let rows = [], total = 0;
  async function load() {
    listCard.classList.add("loading");
    try {
      const r = await api("/api/admin/log?" + qs({...rangeOf(fAct), period: fAct.period, q: fAct.q, limit: PAGE, offset: fAct.page * PAGE}));
      if (!alive()) return;
      rows = r.rows; total = r.total; draw();
    } catch (e) { body.replaceChildren(h("div", {class: "errbox", style: "margin:16px"}, errText(e))); }
    listCard.classList.remove("loading");
  }
  function draw() {
    body.replaceChildren(rows.length ? h("div", {class: "scroll"}, table(
      [{label: "Date"}, {label: "Student"}, {label: "Period"}, {label: "ID"}, {label: "Out"}, {label: "In"}, {label: "Time", num: true}, {label: "Result"}],
      rows.map(r => {
        let result;
        if (r.status === "out") result = badge("Out now", "out");
        else if (r.status === "incomplete") result = badge("No return logged", "warn", "The server restarted before the pass came back");
        else result = r.over ? badge("Over +" + mmss(r.seconds - r.allowed), "over") : badge("On time", "ok");
        return h("tr", null, h("td", {class: "mono"}, dstr(r.out_at)),
          h("td", {class: "name"}, r.unqueued ? h("span", {style: "color:var(--muted)"}, "Not queued") : r.name),
          h("td", null, r.period ? "Period " + r.period : "—"), h("td", {class: "mono"}, idText(r.student_id)),
          h("td", {class: "mono"}, tstr(r.out_at)), h("td", {class: "mono"}, tstr(r.in_at)),
          h("td", {class: "num"}, r.seconds == null ? "—" : mmss(r.seconds)),
          h("td", null, result, r.source === "manual" ? [" ", badge("Manual", "warn", "Marked returned by an admin")] : null));
      }))) : empty("No passes in this range."));
    const pages = Math.max(1, Math.ceil(total / PAGE));
    pager.replaceChildren(btn("← Prev", () => { fAct.page--; load(); }, "sm"), h("span", null, `Page ${fAct.page + 1} of ${pages} · ${plural(total, "pass")}`),
      btn("Next →", () => { fAct.page++; load(); }, "sm"));
    pager.children[0].disabled = fAct.page === 0; pager.children[2].disabled = fAct.page + 1 >= pages;
  }
  const exportBtn = btn("Export CSV", async () => {
    try { await download("/api/admin/log.csv?" + qs({...rangeOf(fAct), period: fAct.period, q: fAct.q}), "desoto-activity.csv"); } catch (e) { alert(errText(e)); }
  });
  listCard.querySelector("header").append(exportBtn);
  listCard.append(body, pager);
  view.append(h("h2", null, "Activity Log"), h("div", {class: "lead"}, "Every trip: who, when the pass went out, and when it came back"),
    await filterBar(fAct, load, {search: true}), listCard);
  current.onIds = draw;
  await load();
};

// --------------------------------------------------------------------- Data
const fData = {preset: "30", start: "", end: "", period: ""};
views.data = async alive => {
  const view = $("view");
  const tiles = h("div", {class: "tiles"}), note = h("div"), charts = h("div", {class: "charts"}), studentsCard = card("Per student");
  const studentsBody = h("div");
  let d = null, sort = {key: "trips", dir: -1}, sq = "";
  const limit = () => ((latest && latest.duration) || 420);

  function chartCard(title, legend, makeChart, makeTable) {
    const body = h("div", {class: "body"}), c = h("div", {class: "card"});
    let asTable = false;
    const toggle = btn("Table", () => { asTable = !asTable; toggle.textContent = asTable ? "Chart" : "Table"; draw(); }, "sm");
    c.append(h("header", null, h("div", {class: "label"}, title), toggle), body);
    function draw() {
      if (asTable) { body.replaceChildren(h("div", {class: "scroll", style: "max-height:300px"}, makeTable())); return; }
      const wrap = h("div", {class: "chart"});
      setKids(body, legend ? legendEl(legend) : null, wrap);
      wrap.append(makeChart(Math.max(320, wrap.clientWidth || body.clientWidth || 560)));
    }
    current.redraws.push(() => { if (!asTable && c.isConnected) draw(); });
    c._draw = draw;
    return c;
  }
  const legendEl = items => h("div", {class: "legend"}, items.map(i => h("span", null, h("i", {style: `background:${i.color}`}), i.name)));
  const STACK = [{name: "Trips", color: CH.blue}, {name: "Over time", color: CH.over}];
  const stackLegend = [{name: "Within limit", color: CH.blue}, {name: "Over time", color: CH.over}];

  function render() {
    const o = d.overall;
    const mk = (label, v, s, cls) => { const t = stat(label); t.set(v, s, cls); return t.el; };
    tiles.replaceChildren(
      mk("Trips", String(o.trips), `${plural(o.unique_students, "student")} went`),
      mk("Average trip", mmss(o.avg_seconds), `median ${mmss(o.median_seconds)}`),
      mk("Longest trip", mmss(o.longest_seconds)),
      mk("Over time", String(o.over), o.trips ? `${o.over_pct}% of trips` : "", o.over ? "red" : ""),
      mk("Total time out", hm(o.total_seconds)),
      mk("Not queued", String(o.unqueued), "pass taken without queuing"));
    const notes = [];
    if (o.incomplete) notes.push(`${plural(o.incomplete, "trip")} with no return logged ${o.incomplete === 1 ? "is" : "are"} left out of these numbers.`);
    if (o.manual_returns) notes.push(`${plural(o.manual_returns, "trip")} ${o.manual_returns === 1 ? "was" : "were"} marked returned by an admin, so the time may be off.`);
    note.replaceChildren(...notes.map(n => h("div", {class: "warnbox", style: "margin:0 0 16px"}, n)));

    charts.replaceChildren();
    if (!o.trips && !d.students.length) { charts.append(empty("No data in this range yet.")); studentsCard.hidden = true; return; }
    studentsCard.hidden = false;
    const daily = d.daily;
    charts.append(
      chartCard("Trips per day", stackLegend,
        w => columnChart({width: w, series: STACK.map((s, i) => ({...s, name: stackLegend[i].name})), aria: "Trips per day",
          data: daily.map(x => ({label: shortDate(x.date), title: longDate(x.date), values: [x.trips - x.over, x.over]}))}),
        () => table([{label: "Date"}, {label: "Trips", num: true}, {label: "Over time", num: true}], daily.map(x => h("tr", null, h("td", null, longDate(x.date)), h("td", {class: "num"}, x.trips), h("td", {class: "num"}, x.over))))),
      chartCard("Average trip length", null,
        w => lineChart({width: w, name: "Average", aria: "Average trip length per day", yFmt: v => (Math.round(v * 10) / 10) + "m",
          ref: {value: limit() / 60, label: `${limit() / 60} min limit`},
          data: daily.map(x => ({label: shortDate(x.date), title: longDate(x.date), y: x.avg_seconds / 60}))}),
        () => table([{label: "Date"}, {label: "Average", num: true}], daily.map(x => h("tr", null, h("td", null, longDate(x.date)), h("td", {class: "num"}, mmss(x.avg_seconds)))))),
      (() => {
        const act = d.hourly.filter(x => x.trips), lo = Math.min(7, ...act.map(x => x.hour)), hi = Math.max(15, ...act.map(x => x.hour));
        const hrs = d.hourly.filter(x => x.hour >= lo && x.hour <= hi), lab = x => ((x.hour + 11) % 12 + 1) + (x.hour < 12 ? "a" : "p");
        return chartCard("Trips by time of day", stackLegend,
          w => columnChart({width: w, series: STACK.map((s, i) => ({...s, name: stackLegend[i].name})), aria: "Trips by hour",
            data: hrs.map(x => ({label: lab(x), title: `${lab(x)} hour`, values: [x.trips - x.over, x.over]}))}),
          () => table([{label: "Hour"}, {label: "Trips", num: true}, {label: "Over time", num: true}], hrs.map(x => h("tr", null, h("td", null, lab(x)), h("td", {class: "num"}, x.trips), h("td", {class: "num"}, x.over)))));
      })(),
      chartCard("Trips by period", null,
        w => hbarChart({width: w, aria: "Trips by period", data: d.by_period.map(p => ({label: "Period " + p.period, value: p.trips,
          tip: [{label: "Trips", value: String(p.trips)}, {label: "Students", value: String(p.students)}, {label: "Average", value: mmss(p.avg_seconds)}, {label: "Over time", value: String(p.over)}]}))}),
        () => table([{label: "Period"}, {label: "Trips", num: true}, {label: "Students", num: true}, {label: "Average", num: true}, {label: "Over time", num: true}],
          d.by_period.map(p => h("tr", null, h("td", null, "Period " + p.period), h("td", {class: "num"}, p.trips), h("td", {class: "num"}, p.students), h("td", {class: "num"}, mmss(p.avg_seconds)), h("td", {class: "num"}, p.over))))));
    charts.querySelectorAll(".card").forEach(c => c._draw());
    drawStudents();
  }

  function drawStudents() {
    const max = Math.max(1, ...d.students.map(s => s.trips));
    const val = {name: s => s.name.toLowerCase(), period: s => (isNaN(s.period) ? 1e6 : +s.period), trips: s => s.trips, total_seconds: s => s.total_seconds,
      avg_seconds: s => s.avg_seconds, longest_seconds: s => s.longest_seconds, over: s => s.over, over_seconds: s => s.over_seconds, last_out: s => s.last_out || 0}[sort.key];
    const list = d.students.filter(s => !sq || s.name.toLowerCase().includes(sq) || (s.id || "").includes(sq))
      .sort((a, b) => (val(a) > val(b) ? 1 : val(a) < val(b) ? -1 : a.name.localeCompare(b.name)) * sort.dir);
    const cols = [{label: "Student", key: "name", sort: true}, {label: "Period", key: "period", sort: true}, showIds ? {label: "ID"} : null,
      {label: "Trips", key: "trips", sort: true, num: true}, {label: "Total", key: "total_seconds", sort: true, num: true}, {label: "Average", key: "avg_seconds", sort: true, num: true},
      {label: "Longest", key: "longest_seconds", sort: true, num: true}, {label: "Over time", key: "over", sort: true, num: true}, {label: "Time over", key: "over_seconds", sort: true, num: true},
      {label: "Last trip", key: "last_out", sort: true}, {label: "12-week trend"}].filter(Boolean);
    const tr = s => h("tr", null, h("td", {class: "name"}, s.name), h("td", null, "Period " + s.period), showIds ? h("td", {class: "mono"}, idText(s.id)) : null,
      h("td", {class: "num"}, s.trips, h("span", {class: "bar-in", style: `width:${Math.round(48 * s.trips / max)}px`})),
      h("td", {class: "num"}, s.trips ? hm(s.total_seconds) : "—"), h("td", {class: "num"}, s.trips ? mmss(s.avg_seconds) : "—"), h("td", {class: "num"}, s.trips ? mmss(s.longest_seconds) : "—"),
      h("td", {class: "num", style: s.over ? "color:var(--over)" : ""}, s.over ? "▲ " + s.over : "0"), h("td", {class: "num"}, s.over_seconds ? mmss(s.over_seconds) : "—"),
      h("td", {class: "mono"}, s.last_out ? dstr(s.last_out) : "—"),
      h("td", null, sparkline(s.weekly, `Trips per week, last 12 weeks (week of ${shortDate(d.week_starts[0])} to ${shortDate(d.week_starts[11])}): ${s.weekly.join(", ")}`)));
    studentsBody.replaceChildren(list.length ? h("div", {class: "scroll", style: "max-height:640px"}, table(cols, list.map(tr), {sortKey: sort.key, sortDir: sort.dir,
      onSort: k => { sort = {key: k, dir: sort.key === k ? -sort.dir : (k === "name" || k === "period" ? 1 : -1)}; drawStudents(); }})) : empty("No students match."));
  }

  let busy = 0;
  async function load() {
    const my = ++busy;
    [tiles, charts, studentsCard].forEach(x => x.classList.add("loading"));
    try {
      const r = await api("/api/admin/stats?" + qs({...rangeOf(fData), period: fData.period}));
      if (!alive() || my !== busy) return;
      d = r; render();
    } catch (e) { tiles.replaceChildren(h("div", {class: "errbox"}, errText(e))); }
    [tiles, charts, studentsCard].forEach(x => x.classList.remove("loading"));
  }
  const exp = btn("Export CSV", async () => { try { await download("/api/admin/stats.csv?" + qs({...rangeOf(fData), period: fData.period}), "desoto-student-data.csv"); } catch (e) { alert(errText(e)); } });
  const sIn = h("input", {type: "search", placeholder: "Search student", "aria-label": "Search student", oninput: e => { sq = e.target.value.toLowerCase(); if (d) drawStudents(); }});
  studentsCard.querySelector("header").append(sIn, exp);
  studentsCard.append(studentsBody);
  view.append(h("h2", null, "Data"), h("div", {class: "lead"}, "Counts, durations and trends for the selected dates"),
    await filterBar(fData, load), tiles, note, charts, h("div", {style: "height:22px"}), studentsCard);
  current.onIds = () => { if (d) drawStudents(); };
  await load();
};

// ----------------------------------------------------------------- Settings
views.settings = async alive => {
  const view = $("view"), msg = h("div", {hidden: true});
  const note = (text, cls) => { msg.hidden = false; msg.className = cls; msg.textContent = text; if (cls === "okbox") setTimeout(() => { msg.hidden = true; }, 2500); };
  const groups = [];
  // One card of mutually exclusive options. options: [key, title, subtitle, preview?]
  function group(title, field, options, extra) {
    const opts = h("div", {class: "opts"}), c = card(title);
    options.forEach(([v, t, sub, prev]) => opts.append(h("button", {class: "opt", type: "button", "data-v": v, onclick: async () => {
      try { await api("/api/admin/settings", {method: "PUT", body: {[field]: v}}); note("Saved", "okbox"); }
      catch (e) { note(errText(e), "errbox"); }
    }}, prev, h("b", null, t), h("small", null, sub))));
    c.append(opts); if (extra) c.append(extra);
    groups.push(s => opts.querySelectorAll(".opt").forEach(o => {
      o.classList.toggle("on", o.dataset.v === s.settings[field]);
      if (field === "simulator" && o.dataset.v === "off") { o.disabled = !s.sensor; o.style.opacity = s.sensor ? "" : ".45"; }
    }));
    return c;
  }
  const icon = t => h("div", {class: "prev"}, h("div", {class: "d", style: "font-size:1.6rem"}, t));
  const simNote = h("div", {class: "body help"});
  view.append(h("h2", null, "Settings"), h("div", {class: "lead"}, "Changes apply to the kiosk and hallway display immediately"),
    group("Pass input", "simulator", [
      ["off", "Sensor", "The physical pass and hall sensor control the pass", icon("🧲 Sensor")],
      ["on", "Simulator", "For testing: the kiosk shows Take pass / Return pass buttons and the sensor is ignored", icon("🖱 Simulator")]], simNote),
    group("Countdown style", "bar_style", [
      ["thick", "Thick bar", "Large digits with a thick progress bar beneath", h("div", {class: "prev"}, h("div", {class: "d"}, "4:32"), h("div", {class: "b"}, h("i")))],
      ["pill", "Pill", "One large pill with the time inside the bar", h("div", {class: "prev"}, h("div", {class: "p"}, h("i"), h("span", null, "4:32")))]]),
    msg);
  current.onState = s => {
    groups.forEach(g => g(s));
    simNote.textContent = s.sensor ? "While the simulator is on, the real sensor is ignored. Switching back to Sensor takes over from wherever the pass is."
      : "No sensor is set up on this computer (start with DESOTO_GPIO_PIN), so the simulator is the only way to control the pass.";
  };
  if (latest) current.onState(latest);
};

// --------------------------------------------------------------------- boot
startClock($("clock"));
$("showIds").checked = showIds;
$("showIds").addEventListener("change", e => { showIds = e.target.checked; store.set("ids", showIds ? "1" : ""); if (current.onIds) current.onIds(); });
connect((s, left) => { latest = s; window.leftNow = left; setPill(s); if (current.onState) current.onState(s, left); });
(async () => {
  try {
    const a = await (await fetch("/api/auth")).json();
    $("pinNote").textContent = a.pin_required ? "Protected by the admin PIN." : "No admin PIN is set. Anyone on this network can open this page and see student IDs. Start the server with DESOTO_ADMIN_PIN to require one.";
    $("pinNote").classList.toggle("warn", !a.pin_required);
    if (a.pin_required && !PIN) await api("/api/admin/ping");
  } catch (e) { /* views report their own errors */ }
  route();
})();
