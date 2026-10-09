// Keyboard-style panel: model picker, variation/style switcher, groove summary, section step grids
// with preview, downloads.
import { styleApi } from "./api.js";
import { DrumPreview } from "./drumsynth.js";

const $ = (id) => document.getElementById(id);
const LANES = [["crash", "Crash"], ["open_hat", "O.Hat"], ["closed_hat", "C.Hat"], ["snare", "Snare"], ["kick", "Kick"]];
const KIND = {
  extracted: ["song", "from the song"],
  fallback: ["check", "copied"],
  generated: ["check", "added"],
};
const store = {
  get(k, d) { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
};

const ui = {
  keyboards: [], kb: store.get("lak.keyboard", "psr-sx700"), q: 0, part: 1,
  report: null, key: null, seq: 0, playing: null,
  editing: false, draft: null,
};
const preview = new DrumPreview();
preview.onStop = () => { ui.playing = null; markPlaying(); };

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

const fourVariations = (k) => k?.sections.includes("Main C");

/** Names as printed on the keyboard panel rather than the file's internal markers. */
function displayName(name, k) {
  const m = name.match(/^(Intro|Ending) ([A-D])$/);
  if (m) return `${m[1]} ${"ABCD".indexOf(m[2]) + 1}`;
  if (name === "Fill In BA") return fourVariations(k) ? "Break" : "Fill B → A";
  if (name === "Fill In AB") return "Fill A → B";
  const f = name.match(/^Fill In ([A-D])\1$/);
  if (f) return `Fill ${f[1]}`;
  return name;
}

function sectionType(name, k) {
  if (name.startsWith("Intro")) return "intro";
  if (name.startsWith("Ending")) return "ending";
  if (name === "Fill In BA" && fourVariations(k)) return "break";
  if (name.startsWith("Fill")) return "fill";
  return "main";
}

export async function initStyle(getState, toast) {
  ui.getState = getState;
  ui.toast = toast;
  try {
    ui.keyboards = (await styleApi.keyboards()).keyboards;
  } catch (e) { toast(e.message, true); }
  if (!ui.keyboards.some((k) => k.id === ui.kb)) ui.kb = ui.keyboards[0]?.id;
  const series = [...new Set(ui.keyboards.map((k) => k.series))];
  $("kbSelect").innerHTML = series.map((s) => `<optgroup label="${esc(s)} series">${ui.keyboards.filter((k) => k.series === s)
    .map((k) => `<option value="${k.id}">${esc(k.name)}${k.can_load_styles ? "" : " (no style loading)"}</option>`).join("")}</optgroup>`).join("");
  $("kbSelect").value = ui.kb;
  $("kbSelect").addEventListener("change", (e) => {
    ui.kb = e.target.value;
    ui.part = 1;
    closeEditor();
    store.set("lak.keyboard", ui.kb);
    renderStyle(true);
  });
  $("quantSeg").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-q]");
    if (!b) return;
    ui.q = +b.dataset.q;
    $("quantSeg").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
    renderStyle(true);
  });
  $("varSwitch").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-part]");
    if (!b || +b.dataset.part === ui.part) return;
    ui.part = +b.dataset.part;
    renderStyle(true);
  });
  $("sectionsGrid").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-sec]");
    if (b) togglePreview(b.dataset.sec);
  });
  $("editVarBtn").addEventListener("click", () => {
    if (ui.editing) return closeEditor();
    if (!ui.report) return;
    ui.editing = true;
    ui.draft = ui.report.mapping.map((row) => [...row]);
    renderEditor();
  });
  $("varEditor").addEventListener("change", (e) => {
    const s = e.target.closest("select[data-row]");
    if (!s) return;
    ui.draft[+s.dataset.row][+s.dataset.col] = s.value;
    renderEditor();
  });
  $("varEditor").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-act]");
    if (!b) return;
    const act = b.dataset.act;
    if (act === "play") togglePreview(`groove:${b.dataset.g}`);
    else if (act === "add") { ui.draft.push(nextRow()); renderEditor(); }
    else if (act === "remove") { ui.draft.splice(+b.dataset.row, 1); renderEditor(); }
    else if (act === "save") saveVariations(ui.draft);
    else if (act === "reset") saveVariations(null);
    else if (act === "cancel") closeEditor();
  });
}

function closeEditor() {
  ui.editing = false;
  ui.draft = null;
  $("varEditor").hidden = true;
  $("editVarBtn").textContent = "✎ Edit variations";
}

/** A sensible second style: grooves not yet used on the first, else repeat the first. */
function nextRow() {
  const used = new Set(ui.draft.flat());
  const free = ui.report.grooves.map((g) => g.groove).filter((g) => !used.has(g));
  return ui.report.buttons.map((_, i) => free[i] ?? free[0] ?? ui.draft[0][i]);
}

async function saveVariations(styles) {
  const { project } = ui.getState();
  try {
    await styleApi.saveVariations(project.id, ui.report.layoutKind, styles);
    ui.toast(styles ? "Variations saved." : "Back to the automatic variations.");
    closeEditor();
    if (ui.part > (styles ? styles.length : 2)) ui.part = 1;
    ui.key = null;
    await renderStyle(true);
  } catch (e) { ui.toast(e.message, true); }
}

function grooveLabel(g, i, total) {
  const feel = total === 1 ? "" : i === 0 ? "lightest" : i === total - 1 ? "busiest" : "";
  return `Groove ${g.groove}${feel ? ` (${feel})` : ""}`;
}

function renderEditor() {
  const r = ui.report;
  if (!ui.editing || !r) return;
  $("editVarBtn").textContent = "✕ Close editor";
  const kb = profile();
  const two = r.layoutKind === "two";
  const total = r.grooves.length;
  const grooveCards = r.grooves.map((g, i) => `
    <div class="groove-card g-${g.groove}" data-name="groove:${g.groove}">
      <div class="gc-head"><span class="gtile g-${g.groove}">${g.groove}</span>
        <span><span class="gc-name">${esc(grooveLabel(g, i, total))}</span><br>
        <span class="gc-meta">${g.repetitions ? `${g.repetitions}× in the song · ` : ""}${g.bars} bar${g.bars > 1 ? "s" : ""} · ${g.hits} hits</span></span>
        <button class="icon-btn" data-act="play" data-g="${g.groove}" aria-label="Preview groove ${g.groove}" title="Preview">▶</button></div>
      ${stepGrid({ bars: g.bars, events: g.events }, r)}
    </div>`).join("");
  const options = (sel) => r.grooves.map((g, i) =>
    `<option value="${g.groove}" ${g.groove === sel ? "selected" : ""}>${esc(grooveLabel(g, i, total))}</option>`).join("");
  const rows = ui.draft.map((row, ri) => `
    <div class="assign-row">
      <span class="ar-name">${two ? `Style ${ri + 1}` : esc(kb?.name || "Style")}</span>
      ${r.buttons.map((btn, ci) => `
        <label class="assign-slot"><span class="btn-label">Button ${btn}</span>
          <span class="gtile g-${row[ci]}">${row[ci]}</span>
          <select class="input" data-row="${ri}" data-col="${ci}" aria-label="${two ? `Style ${ri + 1} ` : ""}button ${btn}">${options(row[ci])}</select>
        </label>`).join("")}
      ${two && ui.draft.length > 1 ? `<button class="btn sm ghost danger" data-act="remove" data-row="${ri}">Remove style</button>` : ""}
    </div>`).join("");
  $("varEditor").hidden = false;
  $("varEditor").innerHTML = `
    <div class="ve-head"><h4>SONG GROOVES</h4><span class="ve-help">The different drum patterns found in your song. Press ▶ to hear one.</span></div>
    <div class="groove-list">${grooveCards}</div>
    <div class="ve-head"><h4>YOUR KEYBOARD BUTTONS</h4><span class="ve-help">${two
      ? `Your ${esc(kb?.name)} has buttons A and B. Pick the groove for each button; add a second style for more grooves.`
      : `Pick which song groove plays on each variation button.`} Each groove brings its own fill.</span></div>
    <div class="assign-rows">${rows}</div>
    <div class="ve-actions">
      ${two && ui.draft.length < r.maxStyles ? `<button class="btn" data-act="add">＋ Add a second style</button>` : ""}
      <span class="spacer"></span>
      <button class="btn ghost" data-act="cancel">Cancel</button>
      <button class="btn" data-act="reset" ${r.mappingCustom ? "" : "disabled"} title="Let the app choose">Reset to automatic</button>
      <button class="btn primary" data-act="save">Save variations</button>
    </div>`;
  markPlaying();
}

export function stopPreview() { preview.stop(); }

function profile() { return ui.keyboards.find((k) => k.id === ui.kb); }

function renderKbInfo(k) {
  const chip = $("seriesChip");
  if (!k) { $("kbInfo").innerHTML = ""; chip.innerHTML = ""; return; }
  chip.innerHTML = !k.can_load_styles ? `<span class="series-chip series-none">No style loading</span>`
    : fourVariations(k) ? `<span class="series-chip series-arranger">4 variations · A B C D</span>`
      : `<span class="series-chip series-portable">2 variations · A B</span>`;
  const status = !k.can_load_styles ? `<span class="badge no">can't load styles</span>`
    : k.verified ? `<span class="badge ok">confirmed</span>` : `<span class="badge check">check your manual</span>`;
  $("kbInfo").innerHTML = k.can_load_styles ? `
    <div><div class="label">Keyboard</div><div class="val">${esc(k.name)} ${status}</div></div>
    <div><div class="label">File type</div><div class="val">${esc(k.format)} · ${esc(k.extension)}</div></div>
    <div><div class="label">Size limit</div><div class="val">${k.max_bytes ? Math.round(k.max_bytes / 1024) + " KB" : "No limit"}</div></div>
    <div><div class="label">User style slots</div><div class="val">${esc(k.user_slots || "–")}</div></div>
    <div class="kb-note"><b>How to load:</b> ${esc(k.load_method)}</div>`
    : `<div><div class="label">Keyboard</div><div class="val">${esc(k.name)} ${status}</div></div>
       <div class="kb-note">This keyboard can't load style files. Download the drum MIDI file below and play it as a song instead.</div>`;
}

function clearReport() {
  $("feelInfo").innerHTML = "";
  $("sectionsGrid").innerHTML = "";
  $("styleSize").textContent = "";
  $("varSwitch").hidden = true;
  $("laneLegend").hidden = true;
  $("dlZip").hidden = true;
  $("editVarBtn").hidden = true;
  closeEditor();
}

export async function renderStyle(force = false) {
  const { project, analysis } = ui.getState();
  if (!project) return;
  const k = profile();
  renderKbInfo(k);
  $("dlMidi").href = styleApi.midiUrl(project.id);
  const drums = analysis?.drums;
  const blocked = (msg) => {
    ui.report = null;
    ui.key = null;
    $("styleStatus").innerHTML = `<div class="style-empty">${msg}</div>`;
    clearReport();
    $("dlStyle").hidden = true;
    toggleLink($("dlMidi"), false);
  };
  if (!analysis) return blocked("Analyze the song first. The style is built from what the analysis finds.");
  if (!drums) return blocked("This song was analyzed with an older version. Click <b>Re-analyze</b> to find its drum groove.");
  if (drums.status !== "ok") return blocked(`No drum groove found. ${esc(drums.reason || "")}`);
  toggleLink($("dlMidi"), true);
  if (!k?.can_load_styles) {
    $("dlStyle").hidden = true;
    $("styleStatus").innerHTML = "";
    clearReport();
    return;
  }
  $("dlStyle").hidden = false;

  const key = `${project.id}|${project.updatedAt}|${ui.kb}|${ui.q}|${ui.part}`;
  if (!force && key === ui.key && ui.report) return;
  ui.key = key;
  const seq = ++ui.seq;
  $("styleStatus").textContent = "Building your style…";
  toggleLink($("dlStyle"), false);
  try {
    const r = await styleApi.report(project.id, ui.kb, ui.q, ui.part);
    if (seq !== ui.seq) return;
    ui.report = r;
    preview.stop();
    $("styleStatus").innerHTML = r.adjustments.length
      ? `<span class="badge check">made smaller</span> ${esc(r.adjustments.join("; "))}` : "";
    renderVariations(r, k);
    renderFeel(r, drums);
    renderSections(r, k);
    $("editVarBtn").hidden = !r.grooves.length;
    if (ui.editing) renderEditor();
    $("laneLegend").hidden = false;
    $("dlStyle").href = styleApi.styleUrl(project.id, ui.kb, ui.q, ui.part);
    $("dlStyle").textContent = `⬇ Download ${r.fileName}`;
    toggleLink($("dlStyle"), true);
    $("dlZip").hidden = r.styleFiles.length < 2;
    $("dlZip").href = styleApi.zipUrl(project.id, ui.kb, ui.q);
    $("styleSize").textContent = `${(r.bytes / 1024).toFixed(1)} KB${r.budget ? ` of ${Math.round(r.budget / 1024)} KB` : ""} · drums`;
  } catch (e) {
    if (seq !== ui.seq) return;
    ui.report = null;
    $("styleStatus").innerHTML = `<div class="style-empty">${esc(e.message)}</div>`;
    clearReport();
  }
}

function toggleLink(a, on) {
  a.style.pointerEvents = on ? "" : "none";
  a.style.opacity = on ? "" : ".45";
  a.setAttribute("aria-disabled", String(!on));
}

function renderVariations(r, k) {
  const box = $("varSwitch");
  const custom = r.mappingCustom ? `<span class="badge song">your choice</span>` : `<span class="badge ok">automatic</span>`;
  if (fourVariations(k)) {
    box.hidden = false;
    box.innerHTML = `<div class="small muted">Variation buttons ${custom}</div>
      <div class="var-tabs"><div class="var-tab on" style="cursor:default"><span><span class="vn">${esc(r.fileName)}</span><br>
      <span class="vs">Buttons A B C D play song grooves ${r.mapping[0].join(" · ")}</span></span>
      <span class="var-letters">${r.mapping[0].map((g) => `<span class="g-${g}">${g}</span>`).join("")}</span></div></div>`;
    return;
  }
  box.hidden = false;
  const grooves = r.distinctGrooves;
  if (r.styleFiles.length < 2) {
    box.innerHTML = `<div class="small muted">Your ${esc(k.name)} has two variation buttons (A and B). ${custom}
      ${r.mappingCustom ? "" : `This song has ${grooves} groove${grooves === 1 ? "" : "s"}, so everything fits in one style.`}</div>
      <div class="var-tabs"><div class="var-tab on" style="cursor:default"><span><span class="vn">${esc(r.fileName)}</span><br>
      <span class="vs">Button A = groove ${r.mapping[0][0]}, button B = groove ${r.mapping[0][1]}</span></span>
      <span class="var-letters">${r.mapping[0].map((g) => `<span class="g-${g}">${g}</span>`).join("")}</span></div></div>`;
    return;
  }
  box.innerHTML = `<div class="small muted">Your ${esc(k.name)} has two variation buttons (A and B). You get
      <b>two styles</b>; load both into user style slots. ${custom}</div>
    <div class="var-tabs">${r.styleFiles.map((f) => {
      const [x, y] = f.grooves.split(" + ");
      return `<button class="var-tab ${f.part === r.part ? "on" : ""}" data-part="${f.part}" aria-pressed="${f.part === r.part}">
        <span><span class="vn">Style ${f.part}</span><br><span class="vs">${esc(f.fileName)} · song grooves ${x} &amp; ${y}</span></span>
        <span class="var-letters" title="Keyboard buttons A and B play song grooves ${x} and ${y}"><span class="g-${x}">${x}</span><span class="g-${y}">${y}</span></span>
      </button>`;
    }).join("")}</div>`;
}

function renderFeel(r, drums) {
  const f = r.feel;
  const spread = (f.timingSpreadBeats * 60000 / r.bpm).toFixed(0);
  $("feelInfo").innerHTML = `
    <span>🥁 Drum kit <b>${Math.round(drums.kitConfidence * 100)}%</b></span>
    <span>Feel <b>${f.subdivision === "triplet" ? "Shuffle" : "Straight"}</b></span>
    ${f.subdivision === "straight" ? `<span>Swing <b>${Math.max(0, f.swingPercent).toFixed(0)}%</b></span>` : ""}
    <span>Human timing <b>±${spread} ms</b> ${r.quantize ? `· ${Math.round(r.quantize * 100)}% quantized` : "· kept"}</span>
    <span>Grooves in song <b>${r.distinctGrooves}</b></span>
    <span>Pattern <b>${r.patternBars} bar${r.patternBars > 1 ? "s" : ""}</b></span>`;
}

function stepGrid(sec, r) {
  const n = r.subdivision, bpb = r.beatsPerBar;
  const cols = sec.bars * bpb * n;
  const cw = 9, ch = 9, lw = 34, gap = 1;
  const W = lw + cols * (cw + gap), H = LANES.length * (ch + gap);
  let svg = `<svg class="steps-svg" width="${W * 1.25}" height="${H * 1.25}" viewBox="0 0 ${W} ${H}" role="img" aria-label="Drum pattern">`;
  LANES.forEach(([, label], li) => {
    const y = li * (ch + gap);
    svg += `<text class="lane" x="0" y="${y + ch - 1}">${label}</text>`;
    for (let c = 0; c < cols; c++) {
      svg += `<rect class="${c % n === 0 ? "beatcell" : "cell"}" x="${lw + c * (cw + gap)}" y="${y}" width="${cw}" height="${ch}" rx="1.5"/>`;
    }
  });
  const q = r.quantize || 0;
  for (const e of sec.events) {
    const li = LANES.findIndex(([i]) => i === e.inst);
    if (li < 0) continue;
    const grid = e.bar * bpb + e.slot / n;
    const pos = e.pos - q * (e.pos - grid);            // micro-timing shows as an offset from the cell
    const x = lw + pos * n * (cw + gap);
    if (x < lw - cw || x > W) continue;
    const op = (0.4 + 0.6 * (e.velocity / 127)).toFixed(2);
    svg += `<rect class="hit hit-${e.inst}" x="${x.toFixed(1)}" y="${li * (ch + gap)}" width="${cw}" height="${ch}" rx="1.5" opacity="${op}"><title>${LANES[li][1]} · velocity ${e.velocity}</title></rect>`;
  }
  return svg + "</svg>";
}

function renderSections(r, k) {
  const two = !fourVariations(k);
  $("sectionsGrid").innerHTML = r.sections.map((s) => {
    const [cls, label] = KIND[s.kind] || ["check", s.kind];
    const showOrigin = s.origin && (s.name.startsWith("Main") || s.name.startsWith("Fill")) && (two || r.mappingCustom);
    const origin = showOrigin ? `<span class="origin g-${s.origin}" title="Song groove ${s.origin}">groove ${s.origin}</span>` : "";
    return `<div class="sec-card t-${sectionType(s.name, k)}" data-name="${esc(s.name)}">
      <div class="sec-head"><span class="nm" title="${esc(s.name)}">${esc(displayName(s.name, k))}</span>${origin}
        <span class="bars">${s.bars} bar${s.bars > 1 ? "s" : ""}</span>
        <span class="badge ${cls}">${label}</span>
        <button class="icon-btn" data-sec="${esc(s.name)}" aria-label="Preview ${esc(displayName(s.name, k))}" title="Preview">▶</button></div>
      <div class="sec-src" title="${esc(s.source)}">${esc(s.source)}</div>
      ${stepGrid(s, r)}
    </div>`;
  }).join("");
  markPlaying();
}

function togglePreview(name) {
  if (ui.playing === name) { preview.stop(); return; }
  const s = name.startsWith("groove:")
    ? ui.report?.grooves.find((g) => g.groove === name.slice(7))
    : ui.report?.sections.find((x) => x.name === name);
  if (!s) return;
  ui.getState().onPreview?.();
  const grid = (e) => e.bar * ui.report.beatsPerBar + e.slot / ui.report.subdivision;
  const events = s.events.map((e) => ({ ...e, pos: e.pos - ui.q * (e.pos - grid(e)) }));
  preview.play(events, s.bars * ui.report.beatsPerBar, ui.report.bpm);
  ui.playing = name;
  markPlaying();
}

function markPlaying() {
  document.querySelectorAll(".sec-card, .groove-card").forEach((c) => {
    const on = c.dataset.name === ui.playing;
    c.classList.toggle("playing", on);
    const b = c.querySelector("button[data-sec], button[data-act=play]");
    if (b) b.textContent = on ? "■" : "▶";
  });
}
