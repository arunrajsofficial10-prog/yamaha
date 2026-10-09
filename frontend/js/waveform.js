// Zoomable waveform with beat / downbeat / bar overlay and playhead.

const BLOCK = 256; // samples per precomputed min/max block

export class Waveform {
  constructor(canvas, { onSeek } = {}) {
    this.canvas = canvas;
    this.onSeek = onSeek;
    this.blocks = null;   // Float32Array [min, max, min, max, ...]
    this.sr = 44100;
    this.duration = 0;
    this.beats = [];
    this.view = { start: 0, span: 0 };
    this.playhead = 0;
    this.keySegments = [];
    this._resize = new ResizeObserver(() => this.draw());
    this._resize.observe(canvas);
    canvas.addEventListener("click", (e) => {
      const r = canvas.getBoundingClientRect();
      this.onSeek?.(this.view.start + ((e.clientX - r.left) / r.width) * this.view.span);
    });
    canvas.addEventListener("wheel", (e) => {
      if (!this.duration) return;
      e.preventDefault();
      const r = canvas.getBoundingClientRect();
      const anchor = this.view.start + ((e.clientX - r.left) / r.width) * this.view.span;
      if (e.ctrlKey || Math.abs(e.deltaY) > Math.abs(e.deltaX)) this.zoom(e.deltaY < 0 ? 1.25 : 0.8, anchor);
      else this.scrollBy(e.deltaX / r.width * this.view.span);
    }, { passive: false });
  }

  setAudio(buffer) {
    const ch0 = buffer.getChannelData(0);
    const ch1 = buffer.numberOfChannels > 1 ? buffer.getChannelData(1) : null;
    const n = Math.ceil(ch0.length / BLOCK);
    const blocks = new Float32Array(n * 2);
    for (let b = 0; b < n; b++) {
      let lo = 1, hi = -1;
      const end = Math.min(ch0.length, (b + 1) * BLOCK);
      for (let i = b * BLOCK; i < end; i++) {
        const v = ch1 ? (ch0[i] + ch1[i]) * 0.5 : ch0[i];
        if (v < lo) lo = v;
        if (v > hi) hi = v;
      }
      blocks[2 * b] = lo; blocks[2 * b + 1] = hi;
    }
    this.blocks = blocks;
    this.sr = buffer.sampleRate;
    this.duration = buffer.duration;
    this.view = { start: 0, span: buffer.duration };
    this.draw();
  }

  setBeats(beats) { this.beats = beats || []; this.draw(); }
  setPlayhead(t) { this.playhead = t; this.draw(); }

  zoom(factor, anchor = this.view.start + this.view.span / 2) {
    const span = Math.min(this.duration, Math.max(2, this.view.span / factor));
    const rel = (anchor - this.view.start) / this.view.span;
    this.view.span = span;
    this.view.start = anchor - rel * span;
    this._clamp();
    this.draw();
    this.onView?.();
  }

  scrollTo(start) { this.view.start = start; this._clamp(); this.draw(); }
  scrollBy(dt) { this.scrollTo(this.view.start + dt); this.onView?.(); }

  follow(t) {
    if (t < this.view.start || t > this.view.start + this.view.span * 0.9) {
      this.view.start = t - this.view.span * 0.1;
      this._clamp();
      this.onView?.();
    }
  }

  _clamp() {
    this.view.start = Math.max(0, Math.min(this.view.start, this.duration - this.view.span));
  }

  draw() {
    const c = this.canvas;
    const dpr = window.devicePixelRatio || 1;
    const W = c.clientWidth, H = c.clientHeight;
    if (!W || !H) return;
    if (c.width !== W * dpr || c.height !== H * dpr) { c.width = W * dpr; c.height = H * dpr; }
    const g = c.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.fillStyle = "#141127";
    g.fillRect(0, 0, W, H);
    if (!this.blocks) return;

    const { start, span } = this.view;
    const x = (t) => ((t - start) / span) * W;
    const pxPerSec = W / span;
    const top = 18, mid = top + (H - top) / 2, amp = (H - top) / 2 - 4;

    // beats & bars
    const showBeats = pxPerSec * (this._beatInterval() || 1) > 6;
    const showBarNums = this.beats.length && pxPerSec * (this._beatInterval() || 1) * 4 > 28;
    g.font = "11px ui-monospace, Consolas, monospace";
    for (const b of this.beats) {
      if (b.time < start || b.time > start + span) continue;
      const bx = Math.round(x(b.time)) + 0.5;
      if (b.beat === 1) {
        g.strokeStyle = "rgba(251,191,36,0.6)";
        g.beginPath(); g.moveTo(bx, top); g.lineTo(bx, H); g.stroke();
        if (showBarNums && b.bar > 0) { g.fillStyle = "#fbbf24"; g.fillText(String(b.bar), bx + 3, 12); }
      } else if (showBeats) {
        g.strokeStyle = "rgba(196,181,253,0.12)";
        g.beginPath(); g.moveTo(bx, top); g.lineTo(bx, H); g.stroke();
      }
    }

    // waveform (min/max per pixel column)
    const blocksPerSec = this.sr / BLOCK;
    const nBlocks = this.blocks.length / 2;
    const grad = g.createLinearGradient(0, top, 0, H);
    grad.addColorStop(0, "#22d3ee");
    grad.addColorStop(0.5, "#8b5cf6");
    grad.addColorStop(1, "#ec4899");
    g.fillStyle = grad;
    for (let px = 0; px < W; px++) {
      const b0 = Math.floor((start + (px / W) * span) * blocksPerSec);
      const b1 = Math.max(b0 + 1, Math.floor((start + ((px + 1) / W) * span) * blocksPerSec));
      let lo = 0, hi = 0;
      for (let b = Math.max(0, b0); b < Math.min(nBlocks, b1); b++) {
        if (this.blocks[2 * b] < lo) lo = this.blocks[2 * b];
        if (this.blocks[2 * b + 1] > hi) hi = this.blocks[2 * b + 1];
      }
      const y0 = mid - hi * amp, y1 = mid - lo * amp;
      g.fillRect(px, y0, 1, Math.max(1, y1 - y0));
    }

    // time ruler
    g.fillStyle = "#8a83ad";
    const step = [1, 2, 5, 10, 15, 30, 60, 120].find((s) => s * pxPerSec > 70) || 300;
    for (let t = Math.ceil(start / step) * step; t < start + span; t += step) {
      const tx = x(t);
      g.fillRect(tx, H - 5, 1, 5);
      if (!showBarNums) g.fillText(fmtTime(t, false), tx + 3, 12);
    }

    // playhead
    if (this.playhead >= start && this.playhead <= start + span) {
      g.fillStyle = "#ffffff";
      g.fillRect(Math.round(x(this.playhead)), 0, 2, H);
    }
  }

  _beatInterval() {
    if (this.beats.length < 2) return 0;
    return (this.beats[this.beats.length - 1].time - this.beats[0].time) / (this.beats.length - 1);
  }
}

export function fmtTime(t, tenths = true) {
  if (t == null || !isFinite(t)) return "–";
  const m = Math.floor(t / 60);
  const s = t - m * 60;
  return tenths ? `${m}:${s.toFixed(1).padStart(4, "0")}` : `${m}:${String(Math.floor(s)).padStart(2, "0")}`;
}
