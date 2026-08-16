// Feed abstraction. Replay mode reads data/sightings.json and re-emits it on a clock.
// Live mode opens a WebSocket that must send the same message shapes (see CONTRACT.md).
// Consumers only ever see: {type:"camera"|"sighting"|"event"|"prediction", ...payload}

export class Feed extends EventTarget {
  constructor(opts = {}) { super(); this.speed = 6; this.playing = false; this.autoplay = !!opts.autoplay; this.armed = false; this.freezeAtSplit = false; this._timer = null;
    this.maxGapS = opts.maxGapS || 0; this.holdUntil = 0; }
  hold(ms) { this.holdUntil = Math.max(this.holdUntil, performance.now() + ms); }   // pause the replay clock (real ms) e.g. while a recording plays   // >0: fast-forward idle stretches so no more than this many replay-seconds pass before the next event

  emit(msg) { this.dispatchEvent(new CustomEvent("msg", { detail: msg })); }

  async loadReplay(url, overlayUrl = null) {
    const d = await (await fetch(url)).json();
    if (overlayUrl) {
      // real-run payload from the pipeline: take its timeline, keep the big camera roster (union, main wins)
      const o = await (await fetch(overlayUrl)).json();
      const have = new Set(d.cameras.map(c => c.id));
      d.cameras = [...d.cameras, ...(o.cameras || []).filter(c => !have.has(c.id))];
      for (const k of ["tracks", "sightings", "events", "predictions", "ground_truth"]) d[k] = o[k] || [];
      d.meta = { ...d.meta, ...(o.meta || {}), overlay: overlayUrl };
    }
    this.data = d;
    d.cameras.forEach(c => this.emit({ type: "camera", ...c }));
    const items = [
      ...d.sightings.map(s => ({ type: "sighting", ...s })),
      ...d.events.map(e => ({ type: "event", ...e })),
      ...d.predictions.map(p => ({ type: "prediction", ...p })),
    ].sort((a, b) => new Date(a.t) - new Date(b.t));
    this.items = items;
    // Do NOT auto-play the demo ride: emit the roster, then wait for start() (R.1/R.2 chips or ?demo=1).
    this.i = 0; this.playing = false; this.armed = false;
    this.emit({ type: "ready", cameras: d.cameras.length, sightings: d.sightings.length });
    if (this.autoplay) this.restart();
  }

  start() { if (!this.armed) this.restart(); else { this.playing = true; } this.armed = true; }

  restart() {
    clearTimeout(this._timer); this.i = 0;
    this.t0 = new Date(this.items[0].t).getTime() - 2000;   // 2 s lead-in
    this.clock = this.t0; this.playing = true;
    this.emit({ type: "reset" });
    this.data.cameras.forEach(c => this.emit({ type: "camera", ...c }));
    this._lastReal = performance.now(); this.armed = true;
    this._tick();
  }

  _tick() {
    const now = performance.now();
    if (this.playing && now >= this.holdUntil) this.clock += (now - this._lastReal) * this.speed;
    this._lastReal = now;
    while (this.playing && this.i < this.items.length && new Date(this.items[this.i].t).getTime() <= this.clock) {
      const m = this.items[this.i++];
      if (m.type === "prediction" && this.freezeAtSplit) { this.playing = false; this.emit({ type: "frozen", prediction: m }); }
      this.emit(m);
    }
    if (this.playing && this.maxGapS && this.i < this.items.length) {
      const nextT = new Date(this.items[this.i].t).getTime();
      if (nextT - this.clock > this.maxGapS * 1000) { this.clock = nextT - this.maxGapS * 1000; this.emit({ type: "skip", to: this.clock }); }
    }
    this.emit({ type: "clock", t: this.clock });
    if (this.i >= this.items.length && this.playing) { this.playing = false; this.emit({ type: "done" }); }
    this._timer = setTimeout(() => this._tick(), 100);
  }

  connectLive(url) {
    const ws = new WebSocket(url);
    ws.onmessage = e => this.emit(JSON.parse(e.data));
    ws.onopen = () => this.emit({ type: "event", t: new Date().toISOString(), kind: "system", text: `live feed connected ${url}` });
    ws.onclose = () => this.emit({ type: "event", t: new Date().toISOString(), kind: "system", text: "live feed closed" });
    this.ws = ws;
  }
}
