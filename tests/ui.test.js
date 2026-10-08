// The plugin window (plugin/ui.html) against stubs of the browser. The
// keep-awake tone starts with a request from the bridge, stops 3 min after the
// last one, and stops at once when the bridge goes away: it never plays while
// the bridge is down.
//
//     node tests/ui.test.js
const fs = require("fs");
const path = require("path");
const html = fs.readFileSync(path.join(__dirname, "..", "plugin", "ui.html"), "utf8");
const script = html.slice(html.indexOf("<script>") + "<script>".length, html.lastIndexOf("</script>"));

const flush = () => new Promise((r) => setImmediate(r));

// Timers on a fake clock, so the 3-minute idle runs in no time.
function makeClock() {
  let now = 0, next = 1;
  const timers = new Map();
  return {
    now: () => now,
    setTimeout: (fn, ms = 0) => { timers.set(next, { fn, at: now + ms }); return next++; },
    clearTimeout: (id) => { timers.delete(id); },
    // Run every timer due within `ms` in time order, letting promises settle between them.
    async advance(ms) {
      const end = now + ms;
      for (;;) {
        await flush();
        let due = null;
        for (const [id, t] of timers) if (t.at <= end && (!due || t.at < due.at)) due = { id, ...t };
        if (!due) break;
        timers.delete(due.id);
        now = due.at;
        due.fn();
      }
      now = end;
      await flush();
    },
  };
}

// Load the window's script with a stub WebSocket (the test opens, feeds and
// drops it) and a stub AudioContext whose state is the tone.
function load() {
  const clock = makeClock();
  const sockets = [];
  const contexts = [];
  const posted = [];
  class WebSocket {
    constructor(url) { this.url = url; this.readyState = 0; sockets.push(this); }
    send() {}
    open() { this.readyState = 1; this.onopen(); }
    receive(m) { this.onmessage({ data: JSON.stringify(m) }); }
    drop() { this.readyState = 3; this.onerror(); this.onclose(); }
  }
  Object.assign(WebSocket, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 });
  class AudioContext {
    constructor() { this.state = "suspended"; this.destination = {}; contexts.push(this); }
    createOscillator() { return { frequency: {}, connect() {}, start() {} }; }
    createGain() { return { gain: {}, connect() {} }; }
    resume() { this.state = "running"; return Promise.resolve(); }
    suspend() { this.state = "suspended"; return Promise.resolve(); }
  }
  const elements = {};
  const document = {
    getElementById: (id) => (elements[id] = elements[id] || { textContent: "", innerHTML: "" }),
    body: { className: "" },
    visibilityState: "hidden",
    addEventListener() {},
  };
  const parent = { postMessage: (m) => posted.push(m.pluginMessage) };
  new Function("document", "window", "parent", "WebSocket", "AudioContext",
               "setTimeout", "clearTimeout", "Date", "console", script)(
    document, {}, parent, WebSocket, AudioContext,
    clock.setTimeout, clock.clearTimeout, { now: clock.now }, { log() {} });
  return {
    clock, sockets, posted,
    ws: () => sockets[sockets.length - 1],
    tone: () => (contexts.length ? contexts[0].state : "none"),
    exec: (id) => sockets[sockets.length - 1].receive({ type: "exec", id, code: "return 1" }),
  };
}

let failed = 0;
function check(label, actual, expected) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  const ok = a === e;
  if (!ok) failed++;
  console.log(`${ok ? "  ok  " : " FAIL "} ${label}${ok ? "" : `\n         got ${a}\n         want ${e}`}`);
}

const MIN = 60 * 1000;

(async () => {
  // ─── scripts keep it up ─────────────────────────────────────────────────
  {
    const env = load();
    env.ws().open();
    check("connected: no tone until a script comes", env.tone(), "none");
    env.exec("e1");
    await env.clock.advance(300);
    check("a script from the bridge starts the tone", env.tone(), "running");
    check("…and reaches the sandbox once the window is awake",
          env.posted.filter((m) => m.type === "exec").map((m) => m.id), ["e1"]);
    await env.clock.advance(2 * MIN);
    env.exec("e2");
    await env.clock.advance(2 * MIN);
    check("each script keeps it up 3 more minutes", env.tone(), "running");
    await env.clock.advance(1 * MIN + 300);
    check("3 min after the last script it stops", env.tone(), "suspended");
  }

  // ─── the bridge goes away ───────────────────────────────────────────────
  {
    const env = load();
    env.ws().open();
    env.exec("e1");
    await env.clock.advance(300);
    env.ws().drop();
    check("the bridge is gone: the tone stops at once", env.tone(), "suspended");
    await env.clock.advance(10 * MIN);
    check("…and stays off while the window knocks", env.tone(), "suspended");
    env.ws().open();
    check("the bridge is back: still off until a script comes", env.tone(), "suspended");
    env.exec("e2");
    await env.clock.advance(300);
    check("…the next script brings it back", env.tone(), "running");
  }

  // ─── no bridge at all ───────────────────────────────────────────────────
  {
    const env = load();
    for (let i = 0; i < 5; i++) {
      env.ws().drop();
      await env.clock.advance(2000);
    }
    check("no bridge: the window keeps knocking every 2 s", env.sockets.length, 6);
    check("…and never plays", env.tone(), "none");
  }

  console.log(failed ? `\n${failed} FAILED` : "\nall window checks passed");
  process.exit(failed ? 1 : 0);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
