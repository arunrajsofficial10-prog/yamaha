// Minimal single-series SVG charts: thin marks, rounded data ends, recessive axes, hover tooltips.

const NS = "http://www.w3.org/2000/svg";
const tip = () => document.getElementById("chartTip");

function el(name, attrs = {}, parent) {
  const n = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  parent?.appendChild(n);
  return n;
}

function showTip(e, html) {
  const t = tip();
  t.innerHTML = html;
  t.hidden = false;
  t.style.left = `${e.clientX + 12}px`;
  t.style.top = `${e.clientY - 30}px`;
}
const hideTip = () => { tip().hidden = true; };

// Top-rounded bar anchored to the baseline (4px radius at the data end only).
function barPath(x, y, w, h, r = 4) {
  r = Math.min(r, w / 2, h);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

/** data: [{label, value, tip, highlight}] — vertical bars, values in [0, max]. */
export function barChart(container, data, { height = 150, max = null, format = (v) => v.toFixed(2) } = {}) {
  container.innerHTML = "";
  const W = Math.max(240, container.clientWidth || 480), H = height, padL = 4, padB = 20, padT = 8;
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img" }, container);
  const m = max ?? Math.max(...data.map((d) => d.value), 1e-9);
  const slot = (W - padL) / data.length;
  const bw = Math.max(4, Math.min(28, slot - 6));
  const plotH = H - padB - padT;
  el("line", { x1: 0, x2: W, y1: H - padB + 0.5, y2: H - padB + 0.5, class: "gridline" }, svg);

  data.forEach((d, i) => {
    const h = Math.max(1, (Math.max(0, d.value) / m) * plotH);
    const x = padL + i * slot + (slot - bw) / 2;
    const y = H - padB - h;
    const hit = el("rect", { x: padL + i * slot, y: padT, width: slot, height: plotH, class: "hit" }, svg);
    const mark = el("path", { d: barPath(x, y, bw, h), class: d.highlight === false ? "mark dim" : "mark" }, svg);
    const t = el("text", { x: x + bw / 2, y: H - 5, "text-anchor": "middle", class: "axis" }, svg);
    t.textContent = d.label;
    hit.addEventListener("mousemove", (e) => { mark.classList.add("hover"); showTip(e, d.tip ?? `${d.label}: ${format(d.value)}`); });
    hit.addEventListener("mouseleave", () => { mark.classList.remove("hover"); hideTip(); });
  });
}

/** Horizontal bars for ranked candidates. data: [{label, value, tip, highlight}] */
export function rankChart(container, data, { format = (v) => v.toFixed(3) } = {}) {
  container.innerHTML = "";
  const rowH = 22, W = Math.max(240, container.clientWidth || 480), labelW = 90, valW = 50;
  const H = data.length * rowH + 4;
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img" }, container);
  const m = Math.max(...data.map((d) => d.value), 1e-9);
  data.forEach((d, i) => {
    const y = i * rowH + 3;
    const w = Math.max(2, (Math.max(0, d.value) / m) * (W - labelW - valW - 8));
    const lab = el("text", { x: 0, y: y + 13, class: "axis" }, svg);
    lab.textContent = d.label;
    if (d.highlight) lab.setAttribute("style", "fill: var(--text)");
    const hit = el("rect", { x: 0, y: y - 2, width: W, height: rowH, class: "hit" }, svg);
    // right-rounded bar anchored at the label edge
    const r = Math.min(4, w / 2);
    const x0 = labelW, h = 12, yy = y + 2;
    const mark = el("path", {
      d: `M${x0},${yy}H${x0 + w - r}Q${x0 + w},${yy} ${x0 + w},${yy + r}V${yy + h - r}Q${x0 + w},${yy + h} ${x0 + w - r},${yy + h}H${x0}Z`,
      class: d.highlight ? "mark" : "mark dim",
    }, svg);
    const val = el("text", { x: labelW + w + 6, y: y + 13, class: "axis" }, svg);
    val.textContent = format(d.value);
    hit.addEventListener("mousemove", (e) => { mark.classList.add("hover"); showTip(e, d.tip ?? `${d.label}: ${format(d.value)}`); });
    hit.addEventListener("mouseleave", () => { mark.classList.remove("hover"); hideTip(); });
  });
}

/** points: [{x, y, tip}] — 2px line with crosshair hover. */
export function lineChart(container, points, { height = 120, yFormat = (v) => v.toFixed(1), xFormat = (v) => v } = {}) {
  container.innerHTML = "";
  if (points.length < 2) { container.textContent = "Not enough data."; return; }
  const W = Math.max(240, container.clientWidth || 480), H = height, padL = 40, padB = 18, padT = 8, padR = 6;
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img" }, container);
  const xs = points.map((p) => p.x), ys = points.map((p) => p.y);
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  let y0 = Math.min(...ys), y1 = Math.max(...ys);
  const pad = Math.max((y1 - y0) * 0.2, 1);
  y0 -= pad; y1 += pad;
  const sx = (v) => padL + ((v - x0) / (x1 - x0 || 1)) * (W - padL - padR);
  const sy = (v) => padT + (1 - (v - y0) / (y1 - y0)) * (H - padT - padB);

  for (const v of [y0 + pad, (y0 + y1) / 2, y1 - pad]) {
    el("line", { x1: padL, x2: W - padR, y1: sy(v), y2: sy(v), class: "gridline" }, svg);
    const t = el("text", { x: padL - 6, y: sy(v) + 4, "text-anchor": "end", class: "axis" }, svg);
    t.textContent = yFormat(v);
  }
  for (const v of [x0, x1]) {
    const t = el("text", { x: sx(v), y: H - 3, "text-anchor": v === x0 ? "start" : "end", class: "axis" }, svg);
    t.textContent = xFormat(v);
  }
  el("path", { d: points.map((p, i) => `${i ? "L" : "M"}${sx(p.x)},${sy(p.y)}`).join(""), class: "line" }, svg);

  const cross = el("line", { y1: padT, y2: H - padB, stroke: "var(--text-2)", "stroke-width": 1, visibility: "hidden" }, svg);
  const dot = el("circle", { r: 4, fill: "var(--accent)", stroke: "var(--panel)", "stroke-width": 2, visibility: "hidden" }, svg);
  const hit = el("rect", { x: padL, y: 0, width: W - padL - padR, height: H, class: "hit" }, svg);
  hit.addEventListener("mousemove", (e) => {
    const r = svg.getBoundingClientRect();
    const vx = x0 + ((e.clientX - r.left) / r.width * W - padL) / (W - padL - padR) * (x1 - x0);
    let best = points[0];
    for (const p of points) if (Math.abs(p.x - vx) < Math.abs(best.x - vx)) best = p;
    cross.setAttribute("x1", sx(best.x)); cross.setAttribute("x2", sx(best.x));
    dot.setAttribute("cx", sx(best.x)); dot.setAttribute("cy", sy(best.y));
    cross.setAttribute("visibility", "visible"); dot.setAttribute("visibility", "visible");
    showTip(e, best.tip ?? `${xFormat(best.x)}: ${yFormat(best.y)}`);
  });
  hit.addEventListener("mouseleave", () => { cross.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); hideTip(); });
}
