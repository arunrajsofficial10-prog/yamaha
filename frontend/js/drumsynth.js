// Synthesised drum kit for previewing extracted style sections in the browser. It is a sketch
// of the groove (timing + velocity), not the keyboard's own drum sounds.

const LOOKAHEAD = 0.15;

export class DrumPreview {
  constructor() {
    this.ctx = null;
    this.timer = null;
    this.onStop = null;
  }

  _ensure() {
    if (!this.ctx) {
      this.ctx = new AudioContext();
      this.out = this.ctx.createGain();
      this.out.gain.value = 0.8;
      this.out.connect(this.ctx.destination);
      const len = this.ctx.sampleRate;
      this.noise = this.ctx.createBuffer(1, len, this.ctx.sampleRate);
      const d = this.noise.getChannelData(0);
      for (let i = 0; i < len; i++) d[i] = Math.random() * 2 - 1;
    }
    return this.ctx;
  }

  get playing() { return this.timer !== null; }

  /** events: [{inst, velocity, pos}] with pos in beats; loops lengthBeats at bpm. */
  async play(events, lengthBeats, bpm) {
    this.stop();
    const ctx = this._ensure();
    if (ctx.state === "suspended") await ctx.resume();
    const spb = 60 / bpm;
    const loopLen = lengthBeats * spb;
    const sorted = [...events].sort((a, b) => a.pos - b.pos);
    let loopStart = ctx.currentTime + 0.05;
    let i = 0;
    const tick = () => {
      const horizon = ctx.currentTime + LOOKAHEAD;
      while (true) {
        if (i >= sorted.length) { i = 0; loopStart += loopLen; }
        const e = sorted[i];
        const when = loopStart + Math.max(0, e.pos) * spb;
        if (when > horizon) break;
        if (when >= ctx.currentTime - 0.01) this._hit(e.inst, when, e.velocity / 127);
        i++;
        if (!sorted.length) break;
      }
    };
    if (!sorted.length) return;
    this.timer = setInterval(tick, 25);
    tick();
  }

  stop() {
    if (this.timer !== null) {
      clearInterval(this.timer);
      this.timer = null;
      this.onStop?.();
    }
  }

  _noiseHit(when, vel, { type, freq, q = 1, decay }) {
    const src = this.ctx.createBufferSource();
    src.buffer = this.noise;
    const f = this.ctx.createBiquadFilter();
    f.type = type; f.frequency.value = freq; f.Q.value = q;
    const g = this.ctx.createGain();
    g.gain.setValueAtTime(vel, when);
    g.gain.exponentialRampToValueAtTime(0.001, when + decay);
    src.connect(f).connect(g).connect(this.out);
    src.start(when, Math.random() * 0.5);
    src.stop(when + decay + 0.02);
  }

  _hit(inst, when, vel) {
    const ctx = this.ctx;
    if (inst === "kick") {
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.frequency.setValueAtTime(140, when);
      o.frequency.exponentialRampToValueAtTime(45, when + 0.12);
      g.gain.setValueAtTime(vel, when);
      g.gain.exponentialRampToValueAtTime(0.001, when + 0.35);
      o.connect(g).connect(this.out);
      o.start(when); o.stop(when + 0.4);
    } else if (inst === "snare") {
      this._noiseHit(when, vel * 0.7, { type: "bandpass", freq: 2500, q: 0.7, decay: 0.18 });
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.frequency.value = 190;
      g.gain.setValueAtTime(vel * 0.4, when);
      g.gain.exponentialRampToValueAtTime(0.001, when + 0.1);
      o.connect(g).connect(this.out);
      o.start(when); o.stop(when + 0.12);
    } else if (inst === "closed_hat") {
      this._noiseHit(when, vel * 0.35, { type: "highpass", freq: 7000, decay: 0.05 });
    } else if (inst === "open_hat") {
      this._noiseHit(when, vel * 0.3, { type: "highpass", freq: 6500, decay: 0.35 });
    } else if (inst === "crash") {
      this._noiseHit(when, vel * 0.35, { type: "highpass", freq: 4500, decay: 1.4 });
    }
  }
}
