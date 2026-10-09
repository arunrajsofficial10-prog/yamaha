import { api } from "./api.js";
import { barChart, lineChart, rankChart } from "./charts.js";
import { Player } from "./player.js";
import { initStyle, renderStyle, stopPreview } from "./style-ui.js";
import { Waveform, fmtTime } from "./waveform.js";

const $ = (id) => document.getElementById(id);
const PCS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
const KEY_SPELL = {
  major: ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"],
  minor: ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "G#", "A", "Bb", "B"],
};
const TIME_SIGS = ["2/4", "3/4", "4/4", "5/4", "6/8", "7/8", "9/8", "12/8"];
const LOW = 0.6;

const STEPS = [
  { id: "upload", label: "Upload song" },
  { id: "analyze", label: "Analyze" },
  { id: "analysis", label: "Check tempo & key" },
  { id: "midi", label: "Drum groove found" },
  { id: "style", label: "Choose your keyboard" },
  { id: "preview", label: "Preview the style" },
  { id: "export", label: "Download & load" },
];

const state = { project: null, analysis: null, effective: null, tab: "bpm", polling: null, audioFor: null, taps: [] };

const player = new Player();
const wave = new Waveform($("wave"), { onSeek: (t) => { player.seek(t); tick(true); } });
wave.onView = syncScroll;

// ---------- helpers ----------
function toast(msg, isError = false) {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast" + (isError ? " error" : "");
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.hidden = true), isError ? 6000 : 3000);
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function confHtml(v, corrected) {
  if (corrected) return `<span class="corrected-tag">corrected</span>`;
  if (v == null) return "";
  const color = v >= 0.8 ? "var(--good)" : v >= LOW ? "var(--warn)" : "var(--bad)";
  return `<span class="pill"><i style="width:${Math.round(v * 100)}%;background:${color}"></i></span>${Math.round(v * 100)}% confidence`;
}

function keyLabel(tonic, mode) {
  return tonic == null ? "–" : `${KEY_SPELL[mode][tonic]} ${mode === "major" ? "Major" : "Minor"}`;
}

function fmtBytes(n) { return n > 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.round(n / 1e3)} KB`; }

// ---------- routing / loading ----------
async function openProject(id, { pushHistory = true } = {}) {
  try {
    const data = await api.get(id);
    applyPayload(data);
    if (pushHistory) history.pushState({ id }, "", `#/project/${id}`);
    if (data.job) pollJob(data.job.id);
    refreshRecent();
    loadAudio();
  } catch (e) {
    toast(e.message, true);
    showUpload();
    refreshRecent();
  }
}

function applyPayload({ project, analysis, effective }) {
  state.project = project;
  state.analysis = analysis;
  state.effective = effective;
  render();
}

function showUpload() {
  stopPolling();
  player.stop();
  stopPreview();
  state.project = state.analysis = state.effective = null;
  state.audioFor = null;
  history.replaceState(null, "", "#/");
  render();
}

async function loadAudio() {
  const p = state.project;
  if (!p || state.audioFor === p.id) return;
  state.audioFor = p.id;
  $("waveLoading").hidden = false;
  $("waveLoading").textContent = "Loading audio…";
  try {
    const buf = await player.load(api.audioUrl(p.id));
    if (state.project?.id !== p.id) return;
    wave.setAudio(buf);
    $("waveLoading").hidden = true;
    syncBeats();
    tick(true);
  } catch {
    $("waveLoading").textContent = "Your browser can't play this file, but it can still be analyzed.";
  }
}

async function refreshRecent() {
  try {
    const { projects } = await api.list();
    const ul = $("recent");
    if (!projects.length) { ul.innerHTML = `<li class="muted small">No projects yet</li>`; return; }
    ul.innerHTML = projects.slice(0, 15).map((p) => `
      <li class="${p.id === state.project?.id ? "current" : ""}">
        <button data-id="${p.id}"><span class="r-name">${esc(p.projectName)}</span>
        <span class="r-meta">${fmtTime(p.duration, false)} · ${p.analysisStatus === "done" ? "analyzed" : esc(p.analysisStatus)}</span></button>
      </li>`).join("");
  } catch { /* sidebar is non-critical */ }
}

// ---------- upload ----------
async function uploadFile(file) {
  if (!file) return;
  const ext = file.name.split(".").pop().toLowerCase();
  if (!["mp3", "wav", "flac", "ogg"].includes(ext)) {
    toast(`“.${ext}” files aren't supported. Please upload MP3, WAV, FLAC or OGG.`, true);
    return;
  }
  $("uploadProgress").hidden = false;
  const bar = $("uploadProgress").querySelector(".bar span");
  const status = $("uploadStatus");
  try {
    status.textContent = `Uploading ${file.name}…`;
    const data = await api.upload(file, (f) => {
      bar.style.width = `${Math.round(f * 100)}%`;
      if (f >= 1) status.textContent = "Validating audio…";
    });
    $("uploadProgress").hidden = true;
    bar.style.width = "0";
    applyPayload(data);
    history.pushState({ id: data.project.id }, "", `#/project/${data.project.id}`);
    refreshRecent();
    loadAudio();
    startAnalysis(); // analysis is the natural next step
  } catch (e) {
    $("uploadProgress").hidden = true;
    toast(e.message, true);
  }
}

// ---------- analysis job ----------
async function startAnalysis() {
  const p = state.project;
  try {
    const job = await api.analyze(p.id);
    state.project.analysisStatus = "running";
    render();
    pollJob(job.id);
  } catch (e) { toast(e.message, true); }
}

function stopPolling() { clearTimeout(state.polling); state.polling = null; }

function pollJob(jobId) {
  stopPolling();
  const pid = state.project.id;
  const step = async () => {
    if (state.project?.id !== pid) return;
    let j;
    try { j = await api.job(jobId); } catch (e) { toast(e.message, true); return; }
    $("jobPanel").hidden = false;
    $("jobMessage").textContent = j.message || "Analyzing…";
    $("jobPct").textContent = `${Math.round(j.progress * 100)}%`;
    $("jobBar").style.width = `${Math.round(j.progress * 100)}%`;
    if (j.status === "done") {
      $("jobPanel").hidden = true;
      applyPayload(await api.get(pid));
      syncBeats();
      refreshRecent();
      toast("Analysis complete.");
    } else if (j.status === "failed") {
      $("jobPanel").hidden = true;
      applyPayload(await api.get(pid));
      toast(j.error?.message || "Analysis failed.", true);
      refreshRecent();
    } else {
      state.polling = setTimeout(step, 500);
    }
  };
  step();
}

async function correct(patch) {
  try {
    const busy = "bpm" in patch && patch.bpm != null;
    if (busy) toast("Re-tracking beats at the new tempo…");
    applyPayload(await api.correct(state.project.id, patch));
    syncBeats();
    if (busy) toast("Beat grid updated.");
  } catch (e) { toast(e.message, true); }
}

function syncBeats() {
  const beats = state.effective?.beats || [];
  wave.setBeats(beats);
  player.setBeats(beats);
}

// ---------- rendering ----------
function render() {
  const p = state.project;
  $("uploadView").hidden = !!p;
  $("projectView").hidden = !p;
  $("topbarProject").hidden = !p;
  renderSteps();
  if (!p) return;

  if (document.activeElement !== $("projectName")) $("projectName").value = p.projectName;
  const a = state.analysis, eff = state.effective, c = p.corrections || {};
  const running = p.analysisStatus === "running";

  $("sumSong").textContent = p.audioFile;
  $("sumSong").title = p.audioFile;
  $("sumDur").textContent = fmtTime(p.metadata.duration, false);
  $("sumBpm").textContent = eff?.bpm != null ? (+eff.bpm).toFixed(1) : "–";
  $("sumBpmConf").innerHTML = a ? confHtml(a.tempo.bpmConfidence, "bpm" in c) : "";
  $("sumKey").textContent = eff?.key ?? "–";
  $("sumKeyConf").innerHTML = a ? confHtml(a.key.keyConfidence, "tonic" in c || "mode" in c) : "";
  $("sumTime").textContent = eff?.timeSignature ?? "–";
  $("sumTimeConf").innerHTML = a ? confHtml(a.tempo.timeSignatureConfidence, "timeSignature" in c || "downbeatPhase" in c) : "";

  $("analyzeBtn").disabled = running;
  $("analyzeBtn").textContent = running ? "Analyzing…" : a ? "Re-analyze" : "Analyze song";
  if (!running) $("jobPanel").hidden = true;

  const warns = [...(a?.warnings || [])];
  if (p.analysisStatus === "failed" && p.analysisError) warns.unshift({ severity: "error", message: p.analysisError });
  $("warnings").innerHTML = warns.map((w) => `
    <div class="warning warning-${w.severity}" role="alert"><span class="ico">${w.severity === "info" ? "ℹ" : "!"}</span><span>${esc(w.message)}</span></div>`).join("");

  $("dlAnalysis").href = api.analysisUrl(p.id);
  $("dlAnalysis").toggleAttribute("aria-disabled", !a);
  $("dlAnalysis").style.pointerEvents = a ? "" : "none";
  $("dlAnalysis").style.opacity = a ? "" : ".45";
  $("dlProject").href = api.exportUrl(p.id);

  renderTab();
  renderStyle();
}

function renderSteps() {
  const p = state.project, done = p?.analysisStatus === "done";
  const status = {
    upload: p ? "done" : "active",
    analyze: done ? "done" : p ? "active" : "locked",
    analysis: done ? "done" : "locked",
  };
  if (done && state.analysis?.drums?.status === "ok") {
    Object.assign(status, { midi: "done", style: "active", preview: "active", export: "active" });
  } else if (done) {
    status.analysis = "active";
  }
  $("steps").innerHTML = STEPS.map((s, i) => {
    const st = status[s.id] || "locked";
    return `<li class="${st}"><span class="n">${st === "done" ? "✓" : i + 1}</span>${s.label}</li>`;
  }).join("");
}

function renderTab() {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === state.tab));
  const body = $("tabBody");
  const a = state.analysis;
  if (state.tab === "file") return renderFileTab(body);
  if (!a) {
    body.innerHTML = `<div class="placeholder">${state.project.analysisStatus === "running" ? "Analysis in progress…" : "Run the analysis to see results."}</div>`;
    return;
  }
  ({ bpm: renderBpmTab, key: renderKeyTab, beats: renderBeatsTab })[state.tab](body);
}

function renderBpmTab(body) {
  const t = state.analysis.tempo, eff = state.effective, c = state.project.corrections || {};
  if (t.status === "no_beat") {
    body.innerHTML = `<div class="tab-grid"><div><div class="big">–</div><p class="muted">No steady beat detected.</p></div>
      <div>${manualBpmHtml(eff.bpm)}</div></div>`;
    bindManualBpm();
    return;
  }
  body.innerHTML = `
    <div class="tab-grid">
      <div>
        <div class="big">${(+eff.bpm).toFixed(1)} <span class="muted" style="font-size:16px">BPM</span></div>
        <div class="conf" style="margin:6px 0 14px">${confHtml(t.bpmConfidence, "bpm" in c)}</div>
        <dl class="kv">
          <dt>Detected</dt><dd class="mono">${t.bpm.toFixed(2)} BPM</dd>
          <dt>Beat length</dt><dd class="mono">${(60 / eff.bpm).toFixed(4)} s</dd>
          <dt>Bar length</dt><dd class="mono">${((60 / eff.bpm) * eff.beatsPerBar).toFixed(3)} s</dd>
          <dt>Tempo stability</dt><dd>${Math.round(t.tempoStability * 100)}%</dd>
          <dt>Beats tracked</dt><dd>${eff.beats.length}</dd>
        </dl>
      </div>
      <div>
        <div class="block"><h4>Half / double time</h4>
          <div class="cand">${t.bpmCandidates.map((cd) => `
            <button class="btn sm ${Math.abs(cd.bpm - eff.bpm) < 0.05 ? "selected" : ""}" data-bpm="${cd.bpm}" title="Score ${cd.score}">
              ${cd.bpm.toFixed(1)} <span class="muted">· ${cd.relation === "detected" ? "detected" : cd.relation === "double" ? "×2" : "÷2"}</span></button>`).join("")}
          </div>
        </div>
        <div class="block">${manualBpmHtml(eff.bpm, "bpm" in c)}</div>
        <div class="block"><h4>Tempo over time</h4><div class="chart" id="tempoChart"></div></div>
      </div>
    </div>`;
  body.querySelectorAll("[data-bpm]").forEach((b) => b.addEventListener("click", () => correct({ bpm: +b.dataset.bpm })));
  bindManualBpm();
  const beatLen = 60 / t.bpm;
  lineChart($("tempoChart"), (t.localTempo || []).map((v, i) => ({ x: i * 4 * beatLen, y: v, tip: `${fmtTime(i * 4 * beatLen, false)}: ${v.toFixed(1)} BPM` })),
    { xFormat: (v) => fmtTime(v, false), yFormat: (v) => v.toFixed(0) });
}

function manualBpmHtml(bpm, corrected) {
  return `<h4>Manual tempo</h4><div class="row">
    <input class="input num mono" id="bpmInput" type="number" min="30" max="300" step="0.1" value="${bpm ?? ""}" aria-label="BPM">
    <button class="btn sm primary" id="bpmApply">Apply</button>
    <button class="btn sm" id="tapBtn" title="Tap along to the beat">Tap tempo</button>
    ${corrected ? `<button class="btn sm ghost" id="bpmReset">Reset to detected</button>` : ""}
  </div>`;
}

function bindManualBpm() {
  $("bpmApply").addEventListener("click", () => {
    const v = parseFloat($("bpmInput").value);
    if (!(v >= 30 && v <= 300)) return toast("BPM must be between 30 and 300.", true);
    correct({ bpm: v });
  });
  $("bpmReset")?.addEventListener("click", () => correct({ bpm: null }));
  $("tapBtn").addEventListener("click", () => {
    const now = performance.now();
    state.taps = state.taps.filter((t) => now - t < 3000).concat(now);
    if (state.taps.length >= 3) {
      const d = (state.taps.at(-1) - state.taps[0]) / (state.taps.length - 1);
      $("bpmInput").value = (60000 / d).toFixed(1);
    }
  });
}

function renderKeyTab(body) {
  const k = state.analysis.key, eff = state.effective, c = state.project.corrections || {};
  if (k.status !== "ok") {
    body.innerHTML = `<div class="tab-grid"><div><div class="big">–</div><p class="muted">No tonal content detected.</p></div><div>${keyFormHtml(eff)}</div></div>`;
    bindKeyForm();
    return;
  }
  body.innerHTML = `
    <div class="tab-grid">
      <div>
        <div class="big">${esc(eff.key)}</div>
        <div class="conf" style="margin:6px 0 14px">${confHtml(k.keyConfidence, "tonic" in c || "mode" in c)}</div>
        <dl class="kv">
          <dt>Detected</dt><dd>${esc(k.key)}</dd>
          <dt>Key stability</dt><dd>${k.keyStability == null ? "–" : Math.round(k.keyStability * 100) + "%"}</dd>
          <dt>Tuning</dt><dd class="mono">${k.tuningCents > 0 ? "+" : ""}${k.tuningCents} cents vs A440</dd>
          <dt>Key signature</dt><dd>${eff.keySignature === 0 ? "none" : Math.abs(eff.keySignature) + (eff.keySignature > 0 ? " ♯" : " ♭")}</dd>
        </dl>
        <div class="block">${keyFormHtml(eff, "tonic" in c || "mode" in c)}</div>
      </div>
      <div>
        <div class="block"><h4>Top candidates (template correlation)</h4><div class="chart" id="keyCands"></div></div>
        <div class="block"><h4>Pitch-class profile</h4><div class="chart" id="pcp"></div></div>
        ${k.windows?.length ? `<details><summary>Key per ${Math.round(k.windows[0].end - k.windows[0].start)} s window</summary>
          <table class="data"><tr><th>From</th><th>To</th><th>Key</th><th>Score</th></tr>
          ${k.windows.map((w) => `<tr><td>${fmtTime(w.start, false)}</td><td>${fmtTime(w.end, false)}</td><td>${esc(w.key)}</td><td class="mono">${w.score}</td></tr>`).join("")}
          </table></details>` : ""}
      </div>
    </div>`;
  bindKeyForm();
  rankChart($("keyCands"), k.candidates.map((cd) => ({
    label: cd.key, value: Math.max(cd.score, 0), highlight: cd.tonic === eff.tonic && cd.mode === eff.mode,
    tip: `${cd.key}: r = ${cd.score.toFixed(3)} (click to use)`,
  })));
  // Scale-tone highlight for the effective key
  const scale = eff.mode === "major" ? [0, 2, 4, 5, 7, 9, 11] : [0, 2, 3, 5, 7, 8, 10];
  const inKey = new Set(scale.map((s) => (s + eff.tonic) % 12));
  barChart($("pcp"), k.pitchClassProfile.map((v, i) => ({
    label: PCS[i], value: v, highlight: inKey.has(i),
    tip: `${PCS[i]}: ${(v * 100).toFixed(0)}% ${inKey.has(i) ? "(in key)" : "(outside key)"}`,
  })), { max: 1, format: (v) => `${(v * 100).toFixed(0)}%` });
  $("keyCands").querySelectorAll(".hit").forEach((h, i) => {
    h.style.cursor = "pointer";
    h.addEventListener("click", () => correct({ tonic: k.candidates[i].tonic, mode: k.candidates[i].mode }));
  });
}

function keyFormHtml(eff, corrected) {
  const mode = eff.mode || "major";
  return `<h4>Correct key</h4><div class="row">
    <label class="field">Key<select class="input" id="keyTonic">${KEY_SPELL[mode].map((n, i) => `<option value="${i}" ${i === eff.tonic ? "selected" : ""}>${n}</option>`).join("")}</select></label>
    <label class="field">Mode<select class="input" id="keyMode"><option value="major" ${mode === "major" ? "selected" : ""}>Major</option><option value="minor" ${mode === "minor" ? "selected" : ""}>Minor</option></select></label>
    <span style="align-self:flex-end" class="row"><button class="btn sm primary" id="keyApply">Apply</button>
    ${corrected ? `<button class="btn sm ghost" id="keyReset">Reset</button>` : ""}</span>
  </div>`;
}

function bindKeyForm() {
  $("keyApply").addEventListener("click", () => correct({ tonic: +$("keyTonic").value, mode: $("keyMode").value }));
  $("keyReset")?.addEventListener("click", () => correct({ tonic: null, mode: null }));
}

function renderBeatsTab(body) {
  const t = state.analysis.tempo, eff = state.effective, c = state.project.corrections || {};
  const md = t.meterDetail?.candidates;
  body.innerHTML = `
    <div class="tab-grid">
      <div>
        <div class="big">${esc(eff.timeSignature)}</div>
        <div class="conf" style="margin:6px 0 14px">${confHtml(t.timeSignatureConfidence, "timeSignature" in c)}</div>
        <dl class="kv">
          <dt>Bars</dt><dd>${eff.bars}</dd>
          <dt>Beats per bar</dt><dd>${eff.beatsPerBar}</dd>
          <dt>First downbeat</dt><dd class="mono">${eff.downbeats.length ? fmtTime(eff.downbeats[0]) : "–"}</dd>
          <dt>Pickup beats</dt><dd>${eff.downbeatPhase}</dd>
          <dt>Downbeat clarity</dt><dd>${Math.round((t.downbeatConfidence || 0) * 100)}%</dd>
        </dl>
        <p class="small muted">Play the song with the metronome on. The high click should land on every “1”.</p>
      </div>
      <div>
        <div class="block"><h4>Time signature</h4><div class="row">
          <select class="input" id="tsSelect">${TIME_SIGS.map((s) => `<option ${s === eff.timeSignature ? "selected" : ""}>${s}</option>`).join("")}</select>
          <button class="btn sm primary" id="tsApply">Apply</button>
          ${"timeSignature" in c ? `<button class="btn sm ghost" id="tsReset">Reset</button>` : ""}
        </div></div>
        <div class="block"><h4>Downbeat position</h4><div class="row">
          <button class="btn sm" id="dbPrev">◀ Earlier beat</button>
          <button class="btn sm" id="dbNext">Later beat ▶</button>
          ${"downbeatPhase" in c ? `<button class="btn sm ghost" id="dbReset">Reset</button>` : ""}
        </div></div>
        <div class="block"><h4>Grid offset</h4><div class="row">
          <input class="input num mono" id="offInput" type="number" min="-500" max="500" step="5" value="${c.beatOffsetMs ?? 0}"> ms
          <button class="btn sm primary" id="offApply">Apply</button>
          <span class="small muted">Shift every beat if clicks sound early or late</span>
        </div></div>
        ${md ? `<details><summary>Meter evidence</summary><table class="data">
          <tr><th>Meter</th><th>Downbeat accent</th><th>Bar periodicity</th><th>Score</th></tr>
          ${[4, 3].map((m) => `<tr><td>${m}/4</td><td class="mono">${md[m].accent.toFixed(3)}</td><td class="mono">${md[m].period.toFixed(3)}</td><td class="mono">${md[m].score.toFixed(3)}</td></tr>`).join("")}
        </table></details>` : ""}
      </div>
    </div>`;
  $("tsApply").addEventListener("click", () => correct({ timeSignature: $("tsSelect").value }));
  $("tsReset")?.addEventListener("click", () => correct({ timeSignature: null }));
  const shift = (d) => correct({ downbeatPhase: ((eff.downbeatPhase + d) % eff.beatsPerBar + eff.beatsPerBar) % eff.beatsPerBar });
  $("dbPrev").addEventListener("click", () => shift(1));
  $("dbNext").addEventListener("click", () => shift(-1));
  $("dbReset")?.addEventListener("click", () => correct({ downbeatPhase: null }));
  $("offApply").addEventListener("click", () => correct({ beatOffsetMs: parseFloat($("offInput").value) || 0 }));
}

function renderFileTab(body) {
  const m = state.project.metadata, a = state.analysis;
  body.innerHTML = `<div class="tab-grid"><div><dl class="kv">
      <dt>File</dt><dd>${esc(state.project.audioFile)}</dd>
      ${m.title ? `<dt>Title</dt><dd>${esc(m.title)}</dd>` : ""}
      ${m.artist ? `<dt>Artist</dt><dd>${esc(m.artist)}</dd>` : ""}
      ${m.album ? `<dt>Album</dt><dd>${esc(m.album)}</dd>` : ""}
      <dt>Format</dt><dd>${esc(m.format)}${m.codec ? ` · ${esc(m.codec)}` : ""}</dd>
      <dt>Sample rate</dt><dd>${m.sampleRate} Hz</dd>
      <dt>Channels</dt><dd>${m.channels === 1 ? "Mono" : m.channels === 2 ? "Stereo" : m.channels}</dd>
      <dt>Bitrate</dt><dd>${m.bitrateKbps ? `~${m.bitrateKbps} kbps` : "–"}</dd>
      <dt>Size</dt><dd>${fmtBytes(m.fileSize)}</dd>
      <dt>Duration</dt><dd class="mono">${fmtTime(m.duration)}</dd>
    </dl></div><div>${a ? `<dl class="kv">
      <dt>Analyzed</dt><dd>${new Date(a.analyzedAt).toLocaleString()}</dd>
      <dt>Engine</dt><dd>${esc(a.engine.name)} ${esc(a.engine.version)} @ ${a.engine.sampleRate} Hz</dd>
      <dt>Timings</dt><dd class="mono">${Object.entries(a.timings).map(([k, v]) => `${k} ${v}s`).join(" · ")}</dd>
      <dt>Analysis</dt><dd>${a.stages.filter((s) => s.status === "done").map((s) => `✓ ${esc(s.label)}`).join("<br>")}</dd>
    </dl>` : `<p class="muted">Not analyzed yet.</p>`}</div></div>`;
}

// ---------- transport ----------
function tick(force = false) {
  const t = player.position;
  if (player.playing) wave.follow(t);
  wave.setPlayhead(t);
  $("timeDisplay").textContent = `${fmtTime(t)} / ${fmtTime(player.duration, false)}`;
  const beats = state.effective?.beats;
  if (beats?.length) {
    let lo = 0, hi = beats.length - 1, i = -1;
    while (lo <= hi) { const m = (lo + hi) >> 1; if (beats[m].time <= t + 0.02) { i = m; lo = m + 1; } else hi = m - 1; }
    $("barDisplay").textContent = i >= 0 ? `Bar ${beats[i].bar} · ${beats[i].beat}` : "";
  } else $("barDisplay").textContent = "";
  if (!force) syncScroll();
  $("playBtn").textContent = player.playing ? "❚❚" : "▶";
  $("playBtn").setAttribute("aria-label", player.playing ? "Pause" : "Play");
}

function syncScroll() {
  const s = $("scroll");
  const free = wave.duration - wave.view.span;
  s.disabled = free <= 0.01;
  s.value = free > 0 ? Math.round((wave.view.start / free) * 1000) : 0;
}

function loop() {
  if (player.playing) tick();
  requestAnimationFrame(loop);
}

function togglePlay() {
  if (player.playing) player.pause();
  else { stopPreview(); player.play(); }
  setTimeout(() => tick(true), 50);
}

// ---------- events ----------
$("fileInput").addEventListener("change", (e) => { uploadFile(e.target.files[0]); e.target.value = ""; });
const dz = $("dropzone");
dz.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("fileInput").click(); } });
["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("over"); }));
dz.addEventListener("drop", (e) => uploadFile(e.dataTransfer.files[0]));

$("newProjectBtn").addEventListener("click", showUpload);
$("analyzeBtn").addEventListener("click", startAnalysis);
$("recent").addEventListener("click", (e) => { const b = e.target.closest("button[data-id]"); if (b) openProject(b.dataset.id); });
document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => {
  state.tab = t.dataset.tab;
  if (state.project) history.replaceState({ id: state.project.id }, "", `#/project/${state.project.id}/${state.tab}`);
  renderTab();
}));

$("projectName").addEventListener("change", async (e) => {
  const v = e.target.value.trim();
  if (!v || v === state.project.projectName) { e.target.value = state.project.projectName; return; }
  try { applyPayload(await api.correct(state.project.id, { projectName: v })); refreshRecent(); } catch (err) { toast(err.message, true); }
});

$("deleteBtn").addEventListener("click", async () => {
  if (!confirm(`Delete “${state.project.projectName}” and its uploaded audio? This can't be undone.`)) return;
  try { await api.remove(state.project.id); showUpload(); refreshRecent(); toast("Project deleted."); } catch (e) { toast(e.message, true); }
});

$("playBtn").addEventListener("click", togglePlay);
$("stopBtn").addEventListener("click", () => { player.stop(); wave.scrollTo(0); tick(true); syncScroll(); });
$("metroToggle").addEventListener("change", (e) => (player.metronome = e.target.checked));
$("songVol").addEventListener("input", (e) => player.setSongVolume(+e.target.value));
$("clickVol").addEventListener("input", (e) => player.setClickVolume(+e.target.value));
$("zoomIn").addEventListener("click", () => wave.zoom(1.6, player.playing ? player.position : undefined));
$("zoomOut").addEventListener("click", () => wave.zoom(1 / 1.6));
$("scroll").addEventListener("input", (e) => wave.scrollTo((e.target.value / 1000) * (wave.duration - wave.view.span)));
player.onEnded = () => tick(true);

document.addEventListener("keydown", (e) => {
  if (e.code !== "Space" || !state.project || ["INPUT", "SELECT", "TEXTAREA", "BUTTON"].includes(e.target.tagName)) return;
  e.preventDefault();
  togglePlay();
});

window.addEventListener("popstate", () => route(false));

function route(push) {
  const m = location.hash.match(/^#\/project\/([0-9a-f]{32})(?:\/(bpm|key|beats|chords|file))?$/);
  if (m?.[2]) state.tab = m[2];
  if (m) openProject(m[1], { pushHistory: push });
  else { showUpload(); refreshRecent(); }
}

initStyle(() => ({ project: state.project, analysis: state.analysis, onPreview: () => { player.pause(); tick(true); } }), toast)
  .then(() => renderStyle());
route(false);
requestAnimationFrame(loop);
