// The plugin window (plugin/ui.html) against stubs of the browser.
//
// The keep-awake tone starts with a request from the bridge, stops 3 min after
// the last one, and stops at once when the bridge goes away: it never plays
// while the bridge is down. The islands show what the bridge's `board` says —
// each agent at work, done, stopped or failed, and the error of its last script
// until one succeeds — the line under them the Figaro version and a newer one,
// and their buttons send the bridge and the sandbox what they should. An
// agent's Stop shows while the pointer is on its row; an island's panel folds
// open and shut, at once for those who ask for less motion.
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
// drops it) and a stub AudioContext whose state is the tone. `calm`: the
// system asks for less motion.
function load({ calm = false } = {}) {
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
  const window = { matchMedia: (q) => ({ matches: calm && /reduce/.test(q) }) };
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
    // The pointer on an agent's row, or with no agent anywhere else in the window.
    point(fid, name) {
      const slot = { dataset: { fid } };
      const row = name ? { dataset: { agent: name }, closest: (sel) => (sel === "[data-fid]" ? slot : null) } : null;
      for (const fn of listeners.pointerover || []) fn({ target: { closest: (sel) => (sel === ".agent[data-agent]" ? row : null) } });
    },
  };
  return env;
}

// A file in the bridge's board and an agent in it; times are the bridge's
// clock, which reads 1000 s when the window gets its first board.
function file(key, name, extra) {
  return Object.assign({ doc: "doc:" + key, sig: key, name, agents: [], recent: [] }, extra);
}
function agent(name, extra) {
  return Object.assign({ name, since: null, last: 0, busy: false, done: null, stopped: null, error: null }, extra);
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
    check("…and its last line is Figaro, with no version yet", /<span class="ver">Figaro<\/span>/.test(env.html()), true);
  }

  // ─── this file's island ─────────────────────────────────────────────────
  {
    const env = load();
    const ago = (sec) => 1000 + env.clock.now() / 1000 - sec;  // the bridge's time, `sec` seconds ago
    env.identity("Dashboard", "KEY1");
    env.ws().open();
    check("hello carries the file", env.sent("hello").map((m) => m.name).pop(), "Dashboard");
    env.board([file("KEY1", "Dashboard", { agents: [agent("designer", { since: ago(12), last: ago(1), busy: true })] })]);
    await env.clock.advance(400);
    const closed = env.html();
    check("an agent at work: the file, the agent and its clock",
          [/Dashboard/.test(closed), /designer/.test(closed), /data-clock/.test(closed)], [true, true, true]);
    check("under the islands: the Figaro version",
          [/<span class="ver">Figaro 1\.1\.0<\/span>/.test(closed), closed.indexOf('class="foot"') > closed.lastIndexOf('class="slot')], [true, true]);
    env.click("toggle", "here");
    const open = env.html();
    check("open: its panel grows from nothing", /class="island open opening"/.test(open), true);
    check("…the agent's own row says it works, its Stop there but hidden",
          [/working/.test(open), /data-act="stop" data-agent="designer"/.test(open), /class="agent hot"/.test(open)], [true, true, false]);
    check("…and the island's row keeps only the file's name", /class="sub"/.test(open), false);
    env.point("here", "designer");
    await env.clock.advance(400);
    check("the pointer on the row shows its Stop, through the redraw after the opening",
          [/class="agent hot" data-agent="designer"/.test(env.html()), /opening/.test(env.html())], [true, false]);
    env.click("stop", "here", { agent: "designer" });
    check("Stop asks the bridge to stop that agent", env.sent("stop"), [{ type: "stop", doc: "doc:KEY1", agent: "designer" }]);
    check("…and the button waits for it", /disabled/.test(env.html()), true);

    env.point("here", null);
    env.board([file("KEY1", "Dashboard", {
      agents: [agent("designer", { since: ago(12), last: ago(1) })],
      recent: [{ agent: "designer", at: ago(1), kind: "ok", summary: "Created KPI card",
                 layers: [{ id: "5:6", name: "KPI card", type: "COMPONENT" }] }] })]);
    check("…shown while it waits, the pointer gone", /class="agent held" data-agent="designer"/.test(env.html()), true);
    await env.clock.advance(MIN);
    check("between its scripts the agent is still at work", /working/.test(env.html()), true);
    check("open: the recent changes, and the version still under them",
          [/Created KPI card/.test(env.html()), /<span class="ver">Figaro 1\.1\.0<\/span>/.test(env.html())], [true, true]);
    env.click("layer", "here", { id: "5:6" });
    check("a layer of this file: select it in Figma", env.posted.filter((m) => m.type === "select"), [{ type: "select", id: "5:6" }]);
    await env.clock.advance(4 * MIN);
    check("five quiet minutes after its last script it is idle, with no room kept for a Stop",
          [/working/.test(env.html()), /idle · <span[^>]*>5m</.test(env.html()), /class="act"/.test(env.html())], [false, true, false]);

    env.board([file("KEY1", "Dashboard", { agents: [
      agent("designer", { since: ago(300), last: ago(200), done: { text: "Check the card", at: ago(1) } })] })]);
    await env.clock.advance(400);
    check("an agent says it is done: its row, with the note, and no buttons",
          [/done · /.test(env.html()), /Check the card/.test(env.html()), /<button[^>]*data-act="(stop|dismiss)"/.test(env.html())],
          [true, true, false]);
    env.click("toggle", "here");
    check("closed: who is done", /designer is done/.test(env.html()), true);

    env.board([file("KEY1", "Dashboard", { agents: [agent("designer", { since: ago(30), last: ago(5), stopped: ago(1) })] })]);
    await env.clock.advance(400);
    check("stopped by the user", /designer stopped/.test(env.html()), true);

    env.board([file("KEY1", "Dashboard", { agents: [agent("designer", { since: ago(250), last: ago(1),
      error: { text: "Cannot read properties of null", line: 14, at: ago(1) } })] })]);
    await env.clock.advance(400);
    check("a failed script while the agent works: it is still at work", /designer <span data-clock[^>]*>4:1/.test(env.html()), true);
    env.click("toggle", "here");
    check("…its row says so, with the error under its name",
          /data-agent="designer">.*?working.*?<p class="msg err">Line 14: Cannot read properties of null<\/p>/.test(env.html()), true);
    env.click("toggle", "here");
    check("closing: the panel folds away first", [/class="island closing"/.test(env.html()), /class="fold"/.test(env.html())], [true, true]);
    await env.clock.advance(400);
    check("…then the island is its row alone", [/closing/.test(env.html()), /class="fold"/.test(env.html())], [false, false]);
    await env.clock.advance(5 * MIN);
    check("…and when it goes quiet after it: failed", /designer failed/.test(env.html()), true);
    env.click("toggle", "here");
    check("…with the error under its name", /Line 14: Cannot read properties of null/.test(env.html()), true);
  }

  // ─── less motion ────────────────────────────────────────────────────────
  {
    const env = load({ calm: true });
    env.identity("Dashboard", "KEY1");
    env.ws().open();
    env.board([file("KEY1", "Dashboard")]);
    await env.clock.advance(400);
    env.click("toggle", "here");
    env.click("toggle", "here");
    check("the system asks for less motion: an island closes at once", /closing|class="fold"/.test(env.html()), false);
  }

  // ─── other files ────────────────────────────────────────────────────────
  {
    const env = load();
    const ago = (sec) => 1000 + env.clock.now() / 1000 - sec;
    env.identity("Dashboard", "KEY1");
    env.ws().open();
    env.board([file("KEY2", "Sandbox 5$"), file("KEY1", "Dashboard"), file("KEY3", "Kit")]);
    await env.clock.advance(400);
    const at = ["Dashboard", "Sandbox 5$", "Kit"].map((n) => env.html().indexOf(`title="${n}"`));
    check("every file where Figaro runs has an island, quiet or not: this file first, then the others as they connected",
          [at.every((i) => i >= 0), at[0] < at[1] && at[1] < at[2]], [true, true]);
    env.board([file("KEY1", "Dashboard"), file("KEY2", "Sandbox 5$", { agents: [
      agent("icons", { since: ago(10), last: ago(0), busy: true }), agent("tokens", { since: ago(5), last: ago(1) })] }),
      file("KEY3", "Kit")]);
    await env.clock.advance(400);
    check("agents at work in another file: its island names them",
          [/Sandbox 5\$/.test(env.html()), /icons, tokens/.test(env.html())], [true, true]);
    env.click("toggle", "doc:KEY2");
    env.click("stop", "doc:KEY2", { agent: "tokens" });
    check("…and each has its own Stop", env.sent("stop"), [{ type: "stop", doc: "doc:KEY2", agent: "tokens" }]);
    env.board([file("KEY1", "Dashboard"), file("KEY2", "Sandbox 5$", {
      agents: [agent("icons", { since: ago(10), last: ago(0) })],
      recent: [{ agent: "icons", at: ago(0), kind: "ok", summary: "Created Icons",
                 layers: [{ id: "1:2", name: "Icons", type: "FRAME" }] }] }), file("KEY3", "Kit")]);
    await env.clock.advance(400);
    check("its layers are names, not buttons: Figma can't switch the tab",
          [/Icons/.test(env.html()), /data-act="layer"/.test(env.html())], [true, false]);
    await env.clock.advance(11 * MIN);
    check("it stays while its plugin runs, quiet or not", /Sandbox/.test(env.html()), true);
    env.board([file("KEY1", "Dashboard"), file("KEY3", "Kit")]);
    await env.clock.advance(400);
    check("its plugin closes: its island goes", [/Sandbox/.test(env.html()), /Kit/.test(env.html())], [false, true]);
  }

  // ─── updates and new builds ─────────────────────────────────────────────
  {
    const env = load();
    env.identity("Dashboard", "KEY1");
    env.ws().open();
    const files = [file("KEY1", "Dashboard")];
    env.board(files, { update: { latest: "1.2.0", current: "1.1.0", url: RELEASE } });
    await env.clock.advance(400);
    check("a new release: Update on the last line, next to the version",
          /<span class="ver">Figaro 1\.1\.0<\/span><span class="what"><button[^>]*data-act="update"[^>]*>.*Update to 1\.2\.0</.test(env.html()), true);
    env.click("update");
    check("Update asks the bridge", env.sent("update-now").length, 1);
    env.board(files, { update: { latest: "1.2.0" }, updating: { step: "wait", what: "update", to: "1.2.0" } });
    await env.clock.advance(400);
    check("…which waits for the scripts first", /Updates once the scripts finish/.test(env.html()), true);
    env.board(files, { update: { latest: "1.2.0" }, failed: { what: "update", error: "local changes", url: RELEASE } });
    await env.clock.advance(400);
    check("a failed update says why on a line of its own, the release page on the last line",
          [/<div class="why">.*Couldn't update to 1\.2\.0: local changes<\/span><\/div><div class="line">/.test(env.html()),
           /<div class="line">.*data-act="release"[^>]*>Open release</.test(env.html())], [true, true]);
    env.click("release", null, { url: RELEASE });
    check("…in the browser", env.posted.filter((m) => m.type === "open-url").map((m) => m.url), [RELEASE]);

    env.ws().receive({ type: "outdated", running: "2026-10-08.6", expected: "2026-10-08.7" });
    env.board(files);
    await env.clock.advance(400);
    check("newer plugin code on disk: Reload, on the last line", /<div class="line">.*New build<\/span><button[^>]*data-act="reload"/.test(env.html()), true);
    env.click("reload", "here");
    check("Reload asks the bridge", env.sent("reload-all").length, 1);
  }

  // ─── a bridge with the Update button off ────────────────────────────────
  {
    const env = load();
    env.identity("Dashboard", "KEY1");
    env.ws().open();
    const files = [file("KEY1", "Dashboard")];
    env.board(files, { release: { latest: "1.2.0", current: "1.1.0", url: RELEASE } });
    await env.clock.advance(400);
    check("a new release: the last line links to it, with no Update",
          [/<span class="what"><button[^>]*class="out"[^>]*data-act="release"[^>]*><span>1\.2\.0 is out<\/span>/.test(env.html()),
           /data-act="update"/.test(env.html())], [true, false]);
    env.click("release", null, { url: RELEASE });
    check("…which opens in the browser and asks nothing of the bridge",
          [env.posted.filter((m) => m.type === "open-url").map((m) => m.url), env.sent("update-now").length], [[RELEASE], 0]);
    env.ws().receive({ type: "outdated", running: "2026-10-08.6", expected: "2026-10-08.7" });
    env.board(files, { release: { latest: "1.2.0", url: RELEASE } });
    await env.clock.advance(400);
    check("newer plugin code on disk comes first: Reload", [/data-act="reload"/.test(env.html()), /is out/.test(env.html())], [true, false]);
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
