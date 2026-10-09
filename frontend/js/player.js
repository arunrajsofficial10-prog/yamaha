// Web Audio playback with a sample-accurate metronome on the detected beat grid.
// The metronome is the fastest way to verify beat / downbeat detection by ear.

const LOOKAHEAD = 0.12;   // seconds of clicks scheduled ahead
const TICK_MS = 25;

export class Player {
  constructor() {
    this.ctx = null;
    this.buffer = null;
    this.source = null;
    this.startedAt = 0;   // ctx time corresponding to song position 0
    this.offset = 0;      // paused position
    this.playing = false;
    this.beats = [];      // [{time, beat}]
    this.metronome = true;
    this.songVol = 0.8;
    this.clickVol = 0.6;
    this._timer = null;
    this._nextBeat = 0;
    this.onEnded = null;
  }

  _ensureCtx() {
    if (!this.ctx) {
      this.ctx = new AudioContext();
      this.songGain = this.ctx.createGain();
      this.clickGain = this.ctx.createGain();
      this.songGain.connect(this.ctx.destination);
      this.clickGain.connect(this.ctx.destination);
      this.songGain.gain.value = this.songVol;
      this.clickGain.gain.value = this.clickVol;
    }
    return this.ctx;
  }

  async load(url) {
    this.stop();
    this.buffer = null;
    const res = await fetch(url);
    if (!res.ok) throw new Error("Couldn't load the audio for playback.");
    const data = await res.arrayBuffer();
    // Decode offline: works before any user gesture and without an audio output device, and the
    // resulting AudioBuffer plays in the live context later.
    this.buffer = await new OfflineAudioContext(1, 1, 44100).decodeAudioData(data);
    return this.buffer;
  }

  get duration() { return this.buffer?.duration || 0; }

  get position() {
    if (!this.playing) return this.offset;
    return Math.min(this.ctx.currentTime - this.startedAt, this.duration);
  }

  setBeats(beats) {
    this.beats = beats || [];
    this._resetBeatCursor(this.position);
  }

  setSongVolume(v) { this.songVol = v; if (this.songGain) this.songGain.gain.value = v; }
  setClickVolume(v) { this.clickVol = v; if (this.clickGain) this.clickGain.gain.value = v; }

  async play() {
    if (!this.buffer || this.playing) return;
    const ctx = this._ensureCtx();
    if (ctx.state === "suspended") await ctx.resume();
    if (this.offset >= this.duration - 0.05) this.offset = 0;
    const src = ctx.createBufferSource();
    src.buffer = this.buffer;
    src.connect(this.songGain);
    this.startedAt = ctx.currentTime + 0.03 - this.offset;
    src.start(ctx.currentTime + 0.03, this.offset);
    src.onended = () => {
      if (this.source !== src) return;
      this.playing = false;
      this.offset = this.duration;
      clearInterval(this._timer);
      this.onEnded?.();
    };
    this.source = src;
    this.playing = true;
    this._resetBeatCursor(this.offset);
    this._timer = setInterval(() => this._schedule(), TICK_MS);
    this._schedule();
  }

  pause() {
    if (!this.playing) return;
    this.offset = this.position;
    this._halt();
  }

  stop() {
    this._halt();
    this.offset = 0;
  }

  seek(t) {
    const was = this.playing;
    this._halt();
    this.offset = Math.max(0, Math.min(t, this.duration));
    if (was) this.play();
  }

  _halt() {
    clearInterval(this._timer);
    if (this.source) {
      const s = this.source;
      this.source = null;
      try { s.stop(); } catch { /* already stopped */ }
    }
    this.playing = false;
  }

  _resetBeatCursor(pos) {
    let i = 0;
    while (i < this.beats.length && this.beats[i].time < pos) i++;
    this._nextBeat = i;
  }

  _schedule() {
    if (!this.playing) return;
    const horizon = this.ctx.currentTime + LOOKAHEAD;
    while (this._nextBeat < this.beats.length) {
      const b = this.beats[this._nextBeat];
      const when = this.startedAt + b.time;
      if (when > horizon) break;
      if (this.metronome && when >= this.ctx.currentTime - 0.01) this._click(when, b.beat === 1);
      this._nextBeat++;
    }
  }

  _click(when, accent) {
    const osc = this.ctx.createOscillator();
    const g = this.ctx.createGain();
    osc.frequency.value = accent ? 1760 : 1175;
    g.gain.setValueAtTime(0, when);
    g.gain.linearRampToValueAtTime(accent ? 0.9 : 0.5, when + 0.002);
    g.gain.exponentialRampToValueAtTime(0.001, when + 0.05);
    osc.connect(g).connect(this.clickGain);
    osc.start(when);
    osc.stop(when + 0.06);
  }
}
