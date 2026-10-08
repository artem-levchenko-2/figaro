// The plugin window (plugin/ui.html) against stubs of the browser.
//
// The keep-awake tone starts with a request from the bridge, stops 3 min after
// the last one, and stops at once when the bridge goes away: it never plays
// while the bridge is down. The islands show what the bridge's `board` says,
// and their buttons send the bridge and the sandbox what they should.
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
    setInterval: (fn, ms) => {
      const id = next++;
      const tick = () => { timers.set(id, { fn: tick, at: now + ms }); fn(); };
      timers.set(id, { fn: tick, at: now + ms });
      return id;
    },
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
    constructor(url) { this.url = url; this.readyState = 0; this.sent = []; sockets.push(this); }
    send(data) { this.sent.push(JSON.parse(data)); }
    open() { this.readyState = 1; this.onopen(); }
    receive(m) { this.onmessage({ data: JSON.stringify(m) }); }
    drop() { this.readyState = 3; if (this.onerror) this.onerror(); this.onclose(); }
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
  const listeners = {};
  const document = {
    getElementById: (id) => (elements[id] = elements[id] || {
      textContent: "", innerHTML: "", querySelectorAll: () => [],
    }),
    visibilityState: "hidden",
    addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
  };
  const window = {};
  const parent = { postMessage: (m) => posted.push(m.pluginMessage) };
  new Function("document", "window", "parent", "WebSocket", "AudioContext",
               "setTimeout", "clearTimeout", "setInterval", "Date", "console", script)(
    document, window, parent, WebSocket, AudioContext,
    clock.setTimeout, clock.clearTimeout, clock.setInterval, { now: clock.now }, { log() {} });
  const env = {
    clock, sockets, posted,
    ws: () => sockets[sockets.length - 1],
    tone: () => (contexts.length ? contexts[0].state : "none"),
    exec: (id) => env.ws().receive({ type: "exec", id, code: "return 1" }),
    html: () => elements.app.innerHTML,
    sent: (type) => env.ws().sent.filter((m) => m.type === type),
    identity: (name, key) => window.onmessage({ data: { pluginMessage: {
      type: "identity", name, fileKey: key, docSig: key, pluginVersion: "test", caps: [] } } }),
    board: (files, extra) => env.ws().receive(Object.assign({ type: "board", now: 1000, version: "1.1.0", files }, extra)),
    // A click on a button: what the page's click listener gets from it.
    click(act, fid, data) {
      const slot = fid ? { dataset: { fid } } : null;
      const el = { dataset: Object.assign({ act }, data), disabled: false,
                   closest: (sel) => (sel === "[data-fid]" ? slot : null) };
      for (const fn of listeners.click || []) fn({ target: { closest: (sel) => (sel === "[data-act]" ? el : null) } });
    },
  };
  return env;
}

// A file in the bridge's board; its times are the bridge's clock (now = 1000 s).
function file(key, name, extra) {
  return Object.assign({ doc: "doc:" + key, sig: key, name, running: null, queue: [], waiting: null,
                         error: null, recent: [], seen: {} }, extra);
}

let failed = 0;
function check(label, actual, expected) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  const ok = a === e;
  if (!ok) failed++;
  console.log(`${ok ? "  ok  " : " FAIL "} ${label}${ok ? "" : `\n         got ${a}\n         want ${e}`}`);
}

const MIN = 60 * 1000;
const RELEASE = "https://github.com/artem-levchenko-2/figaro/releases/tag/v1.2.0";

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
    check("before the bridge answers: this file's island, connecting", /connecting…/.test(env.html()), true);
    for (let i = 0; i < 5; i++) {
      env.ws().drop();
      await env.clock.advance(2000);
    }
    check("no bridge: the window keeps knocking every 2 s", env.sockets.length, 6);
    check("…and never plays", env.tone(), "none");
    check("…and says there is no bridge yet", /no bridge yet/.test(env.html()), true);
  }

  // ─── this file's island ─────────────────────────────────────────────────
  {
    const env = load();
    env.identity("Dashboard", "KEY1");
    env.ws().open();
    check("hello carries the file", env.sent("hello").map((m) => m.name).pop(), "Dashboard");
    env.board([file("KEY1", "Dashboard", { running: { agent: "designer", since: 988 }, queue: ["icons"] })]);
    await env.clock.advance(400);
    const now = env.html();
    check("a script: the file, the agent, its clock and the queue",
          [/Dashboard/.test(now), /designer/.test(now), /data-clock/.test(now), /\+1/.test(now)], [true, true, true, true]);
    env.click("stop", "here");
    check("Stop asks the bridge to end the file's script", env.sent("stop"), [{ type: "stop", doc: "doc:KEY1" }]);
    check("…and the button waits for it", /disabled/.test(env.html()), true);

    env.board([file("KEY1", "Dashboard", {
      recent: [{ agent: "designer", at: 999, kind: "ok", summary: "Created KPI card",
                 layers: [{ id: "5:6", name: "KPI card", type: "COMPONENT" }] }],
      seen: { designer: 999 } })]);
    await env.clock.advance(400);
    check("done: who and when", /designer · /.test(env.html()), true);
    env.click("toggle", "here");
    const panel = env.html();
    check("open: the agents, the recent changes, the version",
          [/Agents/.test(panel), /Created KPI card/.test(panel), /Figaro 1\.1\.0/.test(panel)], [true, true, true]);
    env.click("layer", "here", { id: "5:6" });
    check("a layer of this file: select it in Figma", env.posted.filter((m) => m.type === "select"), [{ type: "select", id: "5:6" }]);

    env.board([file("KEY1", "Dashboard", { waiting: { agent: "designer", text: "Check the card", since: 999 } })]);
    await env.clock.advance(400);
    check("an agent waits for the user: its note", [/is waiting/.test(env.html()), /Check the card/.test(env.html())], [true, true]);
    env.click("dismiss", "here", { what: "waiting" });
    check("Dismiss tells the bridge", env.sent("dismiss"), [{ type: "dismiss", doc: "doc:KEY1", what: "waiting" }]);

    env.board([file("KEY1", "Dashboard", { error: { agent: "designer", at: 999, text: "Cannot read properties of null", line: 14 } })]);
    await env.clock.advance(400);
    check("a failed script stays red with its line", [/designer failed/.test(env.html()), /Line 14/.test(env.html())], [true, true]);
  }

  // ─── other files ────────────────────────────────────────────────────────
  {
    const env = load();
    env.identity("Dashboard", "KEY1");
    env.ws().open();
    env.board([file("KEY1", "Dashboard"), file("KEY2", "Sandbox 5$")]);
    await env.clock.advance(400);
    check("a quiet file has no island", /Sandbox/.test(env.html()), false);
    env.board([file("KEY1", "Dashboard"), file("KEY2", "Sandbox 5$", { running: { agent: "icons", since: 998 } })]);
    await env.clock.advance(400);
    check("an agent at work in another file: its island", /Sandbox 5\$/.test(env.html()), true);
    env.click("stop", "doc:KEY2");
    check("…with its own Stop", env.sent("stop"), [{ type: "stop", doc: "doc:KEY2" }]);
    env.board([file("KEY1", "Dashboard"), file("KEY2", "Sandbox 5$", {
      recent: [{ agent: "icons", at: 1000, kind: "ok", summary: "Created Icons",
                 layers: [{ id: "1:2", name: "Icons", type: "FRAME" }] }], seen: { icons: 1000 } })]);
    await env.clock.advance(400);
    env.click("toggle", "doc:KEY2");
    check("its layers are names, not buttons: Figma can't switch the tab",
          [/Icons/.test(env.html()), /data-act="layer"/.test(env.html())], [true, false]);
    await env.clock.advance(9 * MIN);
    check("it stays for ten quiet minutes", /Sandbox/.test(env.html()), true);
    await env.clock.advance(2 * MIN);
    check("…then goes", /Sandbox/.test(env.html()), false);
  }

  // ─── updates and new builds ─────────────────────────────────────────────
  {
    const env = load();
    env.identity("Dashboard", "KEY1");
    env.ws().open();
    const files = [file("KEY1", "Dashboard")];
    env.board(files, { update: { latest: "1.2.0", current: "1.1.0", url: RELEASE } });
    await env.clock.advance(400);
    check("a new release: a row under the header", [/1\.2\.0 is out/.test(env.html()), /Update and Reload/.test(env.html())], [true, true]);
    env.click("update");
    check("Update and Reload asks the bridge", env.sent("update-now").length, 1);
    env.board(files, { update: { latest: "1.2.0" }, updating: { step: "wait", what: "update", to: "1.2.0" } });
    await env.clock.advance(400);
    check("…which waits for the scripts first", /Updates once the scripts finish/.test(env.html()), true);
    env.board(files, { update: { latest: "1.2.0" }, failed: { what: "update", error: "local changes", url: RELEASE } });
    await env.clock.advance(400);
    check("a failed update says why and offers the release page",
          [/local changes/.test(env.html()), /Open release/.test(env.html())], [true, true]);
    env.click("release", null, { url: RELEASE });
    check("…in the browser", env.posted.filter((m) => m.type === "open-url").map((m) => m.url), [RELEASE]);

    env.ws().receive({ type: "outdated", running: "2026-10-08.5", expected: "2026-10-08.6" });
    env.board(files);
    await env.clock.advance(400);
    check("newer plugin code on disk: Reload", [/new build/.test(env.html()), /data-act="reload"/.test(env.html())], [true, true]);
    env.click("reload", "here");
    check("Reload asks the bridge", env.sent("reload-all").length, 1);
  }

  // ─── an older bridge, without boards ────────────────────────────────────
  {
    const env = load();
    env.identity("Dashboard", "KEY1");
    env.ws().open();
    env.ws().receive({ type: "exec", id: "e1", code: "return 1", agent: "designer" });
    await env.clock.advance(400);
    check("its scripts still show in this file's island", /designer/.test(env.html()), true);
  }

  console.log(failed ? `\n${failed} FAILED` : "\nall window checks passed");
  process.exit(failed ? 1 : 0);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
