// Tiny SVG charts for the admin Data tab. Dark surface only. All text goes in via textContent.
const CH = {blue: "#4596d6", over: "#e0533f", grid: "#303030", surface: "#222222"};
const NS = "http://www.w3.org/2000/svg";
function svg(tag, attrs, ...kids) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v);
  kids.forEach(k => e.append(k));
  return e;
}
function niceScale(max, integer) {
  if (max <= 0) return {max: 4, step: 1};
  const raw = max / 4, mag = Math.pow(10, Math.floor(Math.log10(raw))), norm = raw / mag;
  let step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10) * mag;
  if (integer) step = Math.max(1, Math.round(step));
  return {step, max: Math.ceil(max / step) * step};
}
// Path for a bar with only the top (data end) corners rounded; the baseline end stays square.
function barPath(x, y, w, h, r, round) {
  r = round ? Math.min(r, w / 2, h) : 0;
  return `M${x},${y + h}V${y + r}` + (r ? `Q${x},${y} ${x + r},${y}` : "") + `H${x + w - r}` +
    (r ? `Q${x + w},${y} ${x + w},${y + r}` : "") + `V${y + h}Z`;
}

let tipEl = null;
function showTip(ev, title, rows) {
  if (!tipEl) { tipEl = document.createElement("div"); tipEl.className = "tip"; document.body.append(tipEl); }
  tipEl.replaceChildren();
  if (title) { const t = document.createElement("div"); t.className = "ti"; t.textContent = title; tipEl.append(t); }
  for (const r of rows) {
    const row = document.createElement("div"); row.className = "tr";
    if (r.color) { const k = document.createElement("i"); k.style.background = r.color; row.append(k); }
    const l = document.createElement("span"); l.textContent = r.label;
    const v = document.createElement("b"); v.textContent = r.value;
    row.append(l, v); tipEl.append(row);
  }
  tipEl.style.display = "block";
  const x = ev.clientX == null ? 0 : ev.clientX, y = ev.clientY == null ? 0 : ev.clientY;
  const w = tipEl.offsetWidth, h = tipEl.offsetHeight;
  tipEl.style.left = Math.max(8, Math.min(innerWidth - w - 8, x + 14)) + "px";
  tipEl.style.top = Math.max(8, Math.min(innerHeight - h - 8, y + 14)) + "px";
}
const hideTip = () => { if (tipEl) tipEl.style.display = "none"; };
function hookTip(el, fn) {
  el.addEventListener("pointermove", e => fn(e));
  el.addEventListener("pointerleave", hideTip);
  el.addEventListener("focus", () => { const b = el.getBoundingClientRect(); fn({clientX: b.left + b.width / 2, clientY: b.top}); });
  el.addEventListener("blur", hideTip);
}

function frame(width, height, m, yMax, step, yFmt) {
  const root = svg("svg", {viewBox: `0 0 ${width} ${height}`, role: "img"});
  const ph = height - m.t - m.b, y = v => m.t + ph - (v / yMax) * ph;
  for (let v = 0; v <= yMax + 1e-9; v += step) {
    root.append(svg("line", {x1: m.l, x2: width - m.r, y1: y(v), y2: y(v), stroke: CH.grid, "stroke-width": 1}));
    const t = svg("text", {x: m.l - 8, y: y(v) + 4, "text-anchor": "end"}); t.textContent = yFmt(v); root.append(t);
  }
  return {root, y, pw: width - m.l - m.r, ph};
}
function xLabels(root, labels, xAt, width, m, height) {
  let last = -1e9;
  labels.forEach((lab, i) => {
    const x = xAt(i);
    if (x - last < 58) return;
    last = x;
    const t = svg("text", {x, y: height - 6, "text-anchor": "middle"}); t.textContent = lab; root.append(t);
  });
}

// Stacked columns. opts: {width, height, series:[{name,color}], data:[{label, values:[..], title}], valueFmt, aria}
function columnChart(o) {
  const W = o.width || 640, H = o.height || 240, m = {l: 40, r: 8, t: 10, b: 26};
  const totals = o.data.map(d => d.values.reduce((a, b) => a + b, 0));
  const sc = niceScale(Math.max(0, ...totals), true);
  const {root, y, pw} = frame(W, H, m, sc.max, sc.step, v => String(v));
  root.setAttribute("aria-label", o.aria || "Column chart");
  const n = o.data.length, slot = pw / Math.max(n, 1), bw = Math.max(2, Math.min(24, slot - 2));
  const xAt = i => m.l + slot * i + slot / 2;
  o.data.forEach((d, i) => {
    const g = svg("g", {class: "col"});
    let top = y(0);
    const topmost = d.values.map((v, k) => v > 0 ? k : -1).filter(k => k >= 0).pop();
    d.values.forEach((v, k) => {
      if (v <= 0) return;
      const h = Math.max(y(0) - y(v), 1), yy = top - (top < y(0) ? 2 : 0) - h;  // 2px surface gap between segments
      g.append(svg("path", {d: barPath(xAt(i) - bw / 2, yy, bw, h, 4, k === topmost), fill: o.series[k].color}));
      top = yy;
    });
    root.append(g);
    const hit = svg("rect", {x: m.l + slot * i, y: m.t, width: slot, height: H - m.t - m.b, fill: "transparent", tabindex: 0, "aria-label": `${d.title}: ${totals[i]}`});
    hookTip(hit, e => { g.style.opacity = .8; showTip(e, d.title, o.series.map((s, k) => ({color: s.color, label: s.name, value: String(d.values[k])}))); });
    hit.addEventListener("pointerleave", () => g.style.opacity = 1); hit.addEventListener("blur", () => g.style.opacity = 1);
    root.append(hit);
  });
  xLabels(root, o.data.map(d => d.label), xAt, W, m, H);
  return root;
}

// Line with end dots, one series. opts: {data:[{label,y,title}], yFmt, ref:{value,label}, color}
function lineChart(o) {
  const W = o.width || 640, H = o.height || 240, m = {l: 44, r: 12, t: 10, b: 26};
  const sc = niceScale(Math.max(0, ...o.data.map(d => d.y), o.ref ? o.ref.value : 0), false);
  const {root, y, pw} = frame(W, H, m, sc.max, sc.step, o.yFmt);
  root.setAttribute("aria-label", o.aria || "Line chart");
  const n = o.data.length, xAt = i => n === 1 ? m.l + pw / 2 : m.l + (pw * i) / (n - 1);
  if (o.ref) {
    root.append(svg("line", {x1: m.l, x2: W - m.r, y1: y(o.ref.value), y2: y(o.ref.value), stroke: "#8a8a8a", "stroke-width": 1}));
    const t = svg("text", {x: W - m.r, y: y(o.ref.value) - 5, "text-anchor": "end"}); t.textContent = o.ref.label; root.append(t);
  }
  root.append(svg("polyline", {points: o.data.map((d, i) => `${xAt(i)},${y(d.y)}`).join(" "), fill: "none", stroke: o.color || CH.blue, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round"}));
  const cross = svg("line", {y1: m.t, y2: H - m.b, stroke: "#6a6a6a", "stroke-width": 1, visibility: "hidden"});
  const dot = svg("circle", {r: 5, fill: o.color || CH.blue, stroke: CH.surface, "stroke-width": 2, visibility: "hidden"});
  root.append(cross, dot);
  if (n) { const k = n - 1; root.append(svg("circle", {cx: xAt(k), cy: y(o.data[k].y), r: 4, fill: o.color || CH.blue, stroke: CH.surface, "stroke-width": 2})); }
  xLabels(root, o.data.map(d => d.label), xAt, W, m, H);
  const hit = svg("rect", {x: m.l, y: m.t, width: pw, height: H - m.t - m.b, fill: "transparent"});
  hit.addEventListener("pointermove", e => {
    const b = root.getBoundingClientRect(), px = (e.clientX - b.left) * (W / b.width);
    const i = Math.max(0, Math.min(n - 1, Math.round(n === 1 ? 0 : ((px - m.l) / pw) * (n - 1))));
    cross.setAttribute("x1", xAt(i)); cross.setAttribute("x2", xAt(i)); cross.setAttribute("visibility", "visible");
    dot.setAttribute("cx", xAt(i)); dot.setAttribute("cy", y(o.data[i].y)); dot.setAttribute("visibility", "visible");
    showTip(e, o.data[i].title, [{color: o.color || CH.blue, label: o.name, value: o.yFmt(o.data[i].y)}]);
  });
  hit.addEventListener("pointerleave", () => { hideTip(); cross.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); });
  root.append(hit);
  return root;
}

// Horizontal bars, one series, value at the tip. opts: {data:[{label,value,tip:[{label,value}]}]}
function hbarChart(o) {
  const W = o.width || 640, row = 34, H = Math.max(row * o.data.length + 8, 40), lw = 76, rw = 56;
  const root = svg("svg", {viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": o.aria || "Bar chart"});
  const max = Math.max(1, ...o.data.map(d => d.value));
  o.data.forEach((d, i) => {
    const cy = 4 + row * i + row / 2, w = Math.max(2, ((W - lw - rw) * d.value) / max);
    const lab = svg("text", {x: lw - 10, y: cy + 4, "text-anchor": "end"}); lab.textContent = d.label;
    const bar = svg("path", {d: `M${lw},${cy - 10}H${lw + w - 4}Q${lw + w},${cy - 10} ${lw + w},${cy - 6}V${cy + 6}Q${lw + w},${cy + 10} ${lw + w - 4},${cy + 10}H${lw}Z`, fill: CH.blue});
    const val = svg("text", {x: lw + w + 8, y: cy + 4}); val.textContent = String(d.value);
    const hit = svg("rect", {x: 0, y: cy - row / 2, width: W, height: row, fill: "transparent", tabindex: 0, "aria-label": `${d.label}: ${d.value}`});
    hookTip(hit, e => { bar.style.opacity = .8; showTip(e, d.label, d.tip); });
    hit.addEventListener("pointerleave", () => bar.style.opacity = 1); hit.addEventListener("blur", () => bar.style.opacity = 1);
    root.append(lab, bar, val, hit);
  });
  return root;
}

function sparkline(values, label) {
  const W = 96, H = 26, max = Math.max(1, ...values), n = values.length;
  const pts = values.map((v, i) => [2 + (W - 8) * i / Math.max(n - 1, 1), H - 4 - (H - 8) * v / max]);
  const flat = values.every(v => v === 0);
  const root = svg("svg", {viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img", "aria-label": label});
  const t = svg("title"); t.textContent = label; root.append(t);
  root.append(svg("polyline", {points: pts.map(p => p.join(",")).join(" "), fill: "none", stroke: flat ? "#4a4a4a" : CH.blue, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round"}));
  if (!flat) { const p = pts[pts.length - 1]; root.append(svg("circle", {cx: p[0], cy: p[1], r: 3, fill: CH.blue, stroke: CH.surface, "stroke-width": 2})); }
  return root;
}
