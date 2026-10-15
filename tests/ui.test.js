// The plugin window (plugin/ui.html) against stubs of the browser.
//
// The keep-awake tone starts with a request from the bridge, plays while it
// runs, stops 3 min after the last one ends, and stops at once when the bridge
// goes away: it never plays while the bridge is down. The islands show what the bridge's `board` says —
// each agent at work, done, stopped or failed, and the error of its last script
// until one succeeds — the line under them the Figaro version and a newer one
// (the list's last item, or pinned at the bottom while it has a button),
// and their buttons send the bridge and the sandbox what they should. An
// agent's Stop shows while the pointer is on its row; an island's panel folds
// open and shut, at once for those who ask for less motion. The window asks the
// sandbox for the height of all it shows, again every second. An island that
// opens or closes drives the window a frame at a time, and its fold takes the
// height Figma has given the window, so the islands fill it on every frame;
// one that opens low in a window that can't grow scrolls into view. A file that stopped
// answering (a script running on past its time, or a window that closed while
// one ran) has a red island, first after this file's, that says what to do.
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
// system asks for less motion. `layout`: boxes as a browser would lay the
// window out — every island a 30 px row and, open, its `panel` under it, 6 px
// apart, the list 6 px from the top and 6 px over its end, and the last line,
// when there is one, 28 px at the bottom — in a window `window` px tall. `figma`: frames
// (requestAnimationFrame every 16 ms) and a Figma that gives the window the
// height it asks for `lag` frames later, half a frame before one is drawn, at
// most `room` px (the sandbox's cap); `deaf`: it never does.
function load({ calm = false, layout = null, figma = null } = {}) {
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
  // A sound's layers, as `heard()` lists them: "sine 660", "noise", …
  const voices = [];
  const param = () => ({ setValueAtTime() {}, linearRampToValueAtTime() {}, setTargetAtTime() {}, exponentialRampToValueAtTime() {} });
  class AudioContext {
    constructor() { this.state = "suspended"; this.destination = {}; this.currentTime = 0; this.sampleRate = 48000; this.oscs = []; contexts.push(this); }
    createOscillator() {
      const osc = { type: "sine", frequency: Object.assign(param(), { setValueAtTime(v) { this.value = v; } }), connect() {},
                    start: () => voices.push(`${osc.type} ${osc.frequency.value}`), stop() {} };
      this.oscs.push(osc);
      return osc;
    }
    createBufferSource() { return { connect() {}, start: () => voices.push("noise"), stop() {} }; }
    createBuffer(channels, length) { const data = new Float32Array(length); return { getChannelData: () => data }; }
    createBiquadFilter() { return { frequency: param(), Q: {}, connect() {} }; }
    createGain() { return { gain: param(), connect() {} }; }
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
  const window = { matchMedia: (q) => ({ matches: calm && /reduce/.test(q) }), innerHeight: layout ? layout.window : 0 };
  const parent = { postMessage: (m) => {
    posted.push(m.pluginMessage);
    if (figma && !figma.deaf && m.pluginMessage.type === "resize") {
      const h = Math.min(m.pluginMessage.height, figma.room || Infinity);
      clock.setTimeout(() => { window.innerHeight = h; if (layout) layout.window = h; }, 16 * (figma.lag || 0) + 8);
    }
  } };
  const frame = figma ? (fn) => clock.setTimeout(() => fn(clock.now()), 16) : undefined;
  new Function("document", "window", "parent", "WebSocket", "AudioContext",
               "setTimeout", "clearTimeout", "setInterval", "Date", "console", "requestAnimationFrame", "performance", script)(
    document, window, parent, WebSocket, AudioContext,
    clock.setTimeout, clock.clearTimeout, clock.setInterval, { now: clock.now }, { log() {} }, frame, { now: clock.now });
  if (layout) lay(elements, layout);
  const env = {
    clock, sockets, posted, scrolled: layout ? layout.scrolled : [],
    inner: () => window.innerHeight,
    // what the window's islands and last line take, as ui.html measures it; an island's fold height
    content: () => elements.scroll.getBoundingClientRect().top + elements.app.getBoundingClientRect().height
      + elements.end.getBoundingClientRect().height + elements.foot.getBoundingClientRect().height,
    fold: (fid) => (layout && layout.folds.has(fid) ? layout.folds.get(fid).style.height : null),
    room: (height) => window.onmessage({ data: { pluginMessage: { type: "room", height } } }),
    asks: () => posted.filter((m) => m.type === "resize").map((m) => m.height),
    ws: () => sockets[sockets.length - 1],
    tone: () => { const c = contexts.find((x) => x.oscs.some((o) => o.frequency.value === 20)); return c ? c.state : "none"; },
    heard: () => voices.splice(0),  // the sounds' layers since the last look
    cues: () => { const c = contexts.find((x) => !x.oscs.some((o) => o.frequency.value === 20)); return c ? c.state : "none"; },
    exec: (id, timeout) => env.ws().receive({ type: "exec", id, code: "return 1", timeout }),
    // The sandbox's answer to a request, on its way to the bridge.
    answer: (id) => window.onmessage({ data: { pluginMessage: { type: "result", id, value: 1 } } }),
    html: () => elements.app.innerHTML + elements.end.innerHTML + elements.foot.innerHTML,  // the islands, then the last line
    pinned: () => elements.foot.innerHTML !== "",  // the last line, at the bottom
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

function lay(elements, layout) {
  const TOP = 6, ROW = 30, GAP = 6, END = 6, LINE = 28;
  const pinned = () => elements.foot.innerHTML !== "";
  const box = (top, height) => ({ top, height, bottom: top + height });
  const row = { getBoundingClientRect: () => box(0, ROW) };
  // An open or closing island's fold, while the window draws it: a height of its own
  // (style.height, from a drive) or the CSS's, the panel whole when open.
  let drawn = null;
  layout.folds = new Map();
  const foldOf = (fid) => {
    if (drawn !== elements.app.innerHTML) { drawn = elements.app.innerHTML; layout.folds = new Map(); }
    if (!layout.folds.has(fid)) layout.folds.set(fid, { style: { height: "" }, firstElementChild: { scrollHeight: layout.panel } });
    return layout.folds.get(fid);
  };
  // The islands the window drew, top to bottom.
  const slots = () => {
    let top = TOP;
    return [...elements.app.innerHTML.matchAll(/data-fid="([^"]+)"><div class="island([^"]*)"/g)].map((m) => {
      const fold = / open| closing/.test(m[2]) ? foldOf(m[1]) : null;
      const own = fold && fold.style.height !== "" ? parseFloat(fold.style.height) : null;
      const height = ROW + (own !== null ? own : / open/.test(m[2]) ? layout.panel : 0);
      const at = top;
      const island = { classList: { add() {} }, querySelector: (sel) => (sel === ".fold" ? fold : null) };
      const slot = { dataset: { fid: m[1] }, getBoundingClientRect: () => box(at, height),
        querySelector: (sel) => (sel === ".row" ? row : sel === ".island" ? island : null) };
      top += height + GAP;
      return slot;
    });
  };
  const listHeight = () => slots().reduce((h, el) => h + el.getBoundingClientRect().height + GAP, -GAP);
  layout.scrolled = [];
  Object.assign(elements.app, {
    getBoundingClientRect: () => box(TOP, listHeight()),
    querySelector: (sel) => (sel === ".row" ? row : null),
    querySelectorAll: (sel) => (sel === ".slot" ? slots() : []),
  });
  elements.end.getBoundingClientRect = () => box(TOP + listHeight(), END);
  elements.foot.getBoundingClientRect = () => (pinned() ? box(layout.window - LINE, LINE) : box(0, 0));
  Object.assign(elements.scroll, {
    getBoundingClientRect: () => box(TOP, layout.window - (pinned() ? LINE : 0) - TOP),
    scrollBy: (o) => layout.scrolled.push(o.top),
  });
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
    env.answer("e1");
    await env.clock.advance(2 * MIN);
    env.exec("e2");
    await env.clock.advance(300);
    env.answer("e2");
    await env.clock.advance(2 * MIN);
    check("each script keeps it up 3 more minutes", env.tone(), "running");
    await env.clock.advance(1 * MIN);
    check("3 min after the last script it stops", env.tone(), "suspended");
  }
  {
    const env = load();
    env.ws().open();
    env.exec("long", 600);
    await env.clock.advance(5 * MIN);
    check("a script that runs longer than 3 min keeps it up", env.tone(), "running");
    env.answer("long");
    await env.clock.advance(3 * MIN - 1000);
    check("…until 3 min after it ends", env.tone(), "running");
    await env.clock.advance(2000);
    check("…and then it stops", env.tone(), "suspended");

    env.exec("lost", 60);
    await env.clock.advance(4 * MIN - 1000);
    check("a script whose answer never comes keeps it up 3 min past its time", env.tone(), "running");
    await env.clock.advance(2000);
    check("…not longer", env.tone(), "suspended");
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
    check("…and has no last line: there is nothing to do about the version", env.pinned(), false);
  }

  // ─── this file's island ─────────────────────────────────────────────────
  // (The stubs have no layout: what keeps an island as narrow as the window,
  // whatever its names, is in the CSS.)
  check("an island is never wider than the window, however long the names in its row",
        /\n\.island \{[^}]*\bmin-width: 0;[^}]*overflow: clip;/.test(html), true);
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
    check("nothing to do about the version: no last line under the islands", [/class="foot"/.test(closed), env.pinned()], [false, false]);
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
    check("open: the recent changes", /Created KPI card/.test(env.html()), true);
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

  // ─── the window's height ────────────────────────────────────────────────
  {
    const env = load({ layout: { panel: 400, window: 200 } });
    env.identity("Dashboard", "KEY1");
    env.ws().open();
    env.board([file("KEY1", "Dashboard"), file("KEY2", "Sandbox 5$")]);
    await env.clock.advance(400);
    const asks = () => env.posted.filter((m) => m.type === "resize");
    const last = () => asks()[asks().length - 1];
    check("the window asks the sandbox for the height of all it shows", last(), { type: "resize", height: 6 + 30 + 6 + 30 + 6 });
    const n = asks().length;
    await env.clock.advance(1000);
    check("…and asks again every second, so the sandbox sees a window that moved", asks().length > n, true);
    env.click("toggle", "here");
    check("an island opens with no frames to drive it: the window asks for all it shows at once", last().height, 78 + 400);
    await env.clock.advance(400);
    check("…and nothing is under the window's edge to bring into view: the list stays", env.scrolled, []);
    env.click("toggle", "here");
    await env.clock.advance(400);
    check("it closes: the window asks for its height without the panel", last().height, 78);
  }
  {
    const env = load({ layout: { panel: 300, window: 200 } });
    env.identity("Dashboard", "KEY1");
    env.ws().open();
    env.board([file("KEY1", "Dashboard"), file("KEY2", "Sandbox 5$"), file("KEY3", "Kit")]);
    await env.clock.advance(400);
    env.click("toggle", "doc:KEY3");
    await env.clock.advance(500);
    check("an island opens low in a short window: the list scrolls it up to the top, no further",
          env.scrolled, [6 + 2 * 36 - 6]);
  }
  {
    const env = load({ calm: true, layout: { panel: 400, window: 200 } });
    env.identity("Dashboard", "KEY1");
    env.ws().open();
    env.board([file("KEY1", "Dashboard")]);
    await env.clock.advance(400);
    env.click("toggle", "here");
    env.click("toggle", "here");
    check("less motion: the window asks for its height without the panel at once", env.asks().pop(), 6 + 30 + 6);
  }
  {
    const env = load({ layout: { panel: 400, window: 200 } });
    env.identity("Dashboard", "KEY1");
    env.ws().open();
    env.board([file("KEY1", "Dashboard"), file("KEY2", "Sandbox 5$")], { update: { latest: "1.2.0", url: RELEASE } });
    await env.clock.advance(400);
    check("a line with a button is pinned at the bottom, and the window asks for it too",
          [env.pinned(), env.asks().pop()], [true, 6 + 30 + 6 + 30 + 6 + 28]);
    env.board([file("KEY1", "Dashboard"), file("KEY2", "Sandbox 5$")]);
    await env.clock.advance(400);
    check("…and with nothing left to do, it goes, and so does its height", [env.pinned(), env.asks().pop()], [false, 6 + 30 + 6 + 30 + 6]);
  }

  // ─── an island that opens or closes drives the window ───────────────────
  // Frames every 16 ms from the click; Figma resizes the window two frames
  // after it asks, half a frame before one is drawn; each frame is looked at
  // just after the window drew it.
  const watch = async (env, ms) => {
    const frames = [];
    await env.clock.advance(1);
    for (let t = 0; t < ms; t += 16) {
      await env.clock.advance(16);
      frames.push({ inner: env.inner(), content: env.content(), fold: env.fold("here"), ask: env.asks().pop() });
    }
    return frames;
  };
  const rises = (list) => list.every((h, i) => i === 0 || h >= list[i - 1]);
  {
    const env = load({ layout: { panel: 300, window: 106 }, figma: { lag: 2 } });
    env.identity("Dashboard", "KEY1");
    env.ws().open();
    env.board([file("KEY1", "Dashboard"), file("KEY2", "Sandbox 5$")]);
    await env.clock.advance(400);
    const h0 = env.inner(), before = env.asks().length;
    env.click("toggle", "here");
    let frames = await watch(env, 640);
    let asks = env.asks().slice(before);
    check("an island opens: every frame the window asks for its next height, up to all it shows",
          [asks.length > 15, rises(asks), asks[asks.length - 1]], [true, true, h0 + 300]);
    check("…the islands fill the window on every frame: the fold takes the height Figma has given it",
          frames.filter((f) => Math.abs(f.inner - f.content) > 1).length, 0);
    check("…it goes the island's curve, slow at first", frames.slice(0, 4).every((f) => f.inner - h0 < 40), true);
    check("…until the island is whole", [frames.some((f) => f.fold === "300px" && f.inner === h0 + 300)], [true]);
    await env.clock.advance(400);
    check("…and once drawn anew, the open island is as tall as when the fold was driven", env.content(), h0 + 300);
    const mid = env.asks().length;
    env.click("toggle", "here");
    frames = await watch(env, 560);
    asks = env.asks().slice(mid);
    check("it closes: the window asks for less every frame, down to its height without the panel",
          [asks.length > 12, rises(asks.slice().reverse()), asks[asks.length - 1]], [true, true, h0]);
    check("…the islands fill the window on every frame", frames.filter((f) => Math.abs(f.inner - f.content) > 1).length, 0);
    await env.clock.advance(400);
    check("…and the island is drawn shut", [env.content(), env.inner()], [h0, h0]);
  }
  {
    const env = load({ layout: { panel: 300, window: 106 }, figma: { lag: 2, room: 106 + 120 } });
    env.identity("Dashboard", "KEY1");
    env.ws().open();
    env.board([file("KEY1", "Dashboard"), file("KEY2", "Sandbox 5$")]);
    await env.clock.advance(400);
    env.room(106 + 120);  // the sandbox: no taller than this
    const before = env.asks().length;
    env.click("toggle", "here");
    const frames = await watch(env, 640);
    const driven = frames.filter((f) => f.fold !== "");
    check("the canvas lets the window grow only so much: it asks for no more while it drives",
          Math.max(...driven.filter((f) => f.fold !== "300px").map((f) => f.ask)), 106 + 120);
    check("…and the fold opens whole, past the window's bottom: the islands scroll",
          [frames[frames.length - 1].inner, driven[driven.length - 1].fold, env.content()], [106 + 120, "300px", 78 + 300]);
    check("…never short of the window on the way", frames.filter((f) => f.content < f.inner - 1).length, 0);
  }
  {
    const env = load({ layout: { panel: 300, window: 106 }, figma: { lag: 2, room: 106 } });
    env.identity("Dashboard", "KEY1");
    env.ws().open();
    env.board([file("KEY1", "Dashboard"), file("KEY2", "Sandbox 5$")]);
    await env.clock.advance(400);
    env.room(106);  // a window that can't grow at all
    env.click("toggle", "here");
    const frames = await watch(env, 640);
    const half = parseFloat(frames[10].fold);
    check("a window that can't grow: the fold follows the clock",
          [half > 50 && half < 250, frames.some((f) => f.fold === "300px"), env.inner()], [true, true, 106]);
  }
  {
    const env = load({ layout: { panel: 300, window: 106 }, figma: { deaf: true } });
    env.identity("Dashboard", "KEY1");
    env.ws().open();
    env.board([file("KEY1", "Dashboard"), file("KEY2", "Sandbox 5$")]);
    await env.clock.advance(400);
    env.click("toggle", "here");
    const frames = await watch(env, 400);
    await watch(env, 400);
    check("Figma never gives the window that height: the fold waits for it…", frames[frames.length - 1].fold, "0px");
    check("…and the island opens whole anyway soon after", env.content(), 78 + 300);
  }
  {
    const env = load({ calm: true, layout: { panel: 300, window: 106 }, figma: { lag: 2 } });
    env.identity("Dashboard", "KEY1");
    env.ws().open();
    env.board([file("KEY1", "Dashboard"), file("KEY2", "Sandbox 5$")]);
    await env.clock.advance(400);
    const before = env.asks().length;
    env.click("toggle", "here");
    check("less motion: no drive, the window asks for all it shows at once", env.asks().slice(before), [78 + 300]);
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

  // ─── a file that stopped answering ──────────────────────────────────────
  {
    const env = load();
    const ago = (sec) => 1000 + env.clock.now() / 1000 - sec;
    env.identity("Dashboard", "KEY1");
    env.ws().open();
    env.board([file("KEY1", "Dashboard"), file("KEY2", "Sandbox 5$"),
               file("KEY3", "Kit", { stuck: { agent: "icons", since: ago(130) } })]);
    await env.clock.advance(400);
    const at = ["Dashboard", "Kit", "Sandbox 5$"].map((n) => env.html().indexOf(`title="${n}"`));
    check("a file where a script runs on past its time is red, first after this file",
          [at[0] < at[1] && at[1] < at[2], (env.html().match(/class="sub alarm">Figma isn't answering/g) || []).length],
          [true, 1]);
    env.click("toggle", "doc:KEY3");
    await env.clock.advance(400);
    check("…open, it says whose script it is, since when, and what to do",
          [/A script by icons is still running: its time ran out <span[^>]*>2m ago/.test(env.html()),
           /Reload the file in Figma: close its tab and open it again, or quit Figma if the tab won't close\. Then run Figaro in it\./.test(env.html())],
          [true, true]);
    env.board([file("KEY1", "Dashboard"), file("KEY2", "Sandbox 5$")],
              { lost: [file("KEY3", "Kit", { lost: { agent: "icons", at: ago(20) } })] });
    await env.clock.advance(400);
    check("its window closed while a script ran there: its island stays, red, and says so",
          [/title="Kit"/.test(env.html()), /Figaro's window there closed while a script by icons was running/.test(env.html())],
          [true, true]);
    env.board([file("KEY1", "Dashboard"), file("KEY2", "Sandbox 5$"), file("KEY3", "Kit")]);
    await env.clock.advance(400);
    check("…and back, it is an island like the others", [/title="Kit"/.test(env.html()), /isn't answering/.test(env.html())],
          [true, false]);
    env.board([file("KEY1", "Dashboard", { stuck: { agent: "designer", since: ago(40) } })]);
    await env.clock.advance(400);
    env.click("toggle", "here");
    await env.clock.advance(400);
    check("this file's own script runs on past its time: its island says to reload this file",
          [/sub alarm|Figma isn't answering/.test(env.html()), /Reload this file in Figma/.test(env.html())], [true, true]);
    env.board([file("KEY1", "Dashboard")], { lost: [file("KEY1", "Dashboard", { lost: { agent: "x", at: ago(5) } })] });
    await env.clock.advance(400);
    check("…and a board that still calls this very file lost draws it once", (env.html().match(/title="Dashboard"/g) || []).length, 1);
  }

  // ─── updates and new builds ─────────────────────────────────────────────
  {
    const env = load();
    env.identity("Dashboard", "KEY1");
    env.ws().open();
    const files = [file("KEY1", "Dashboard")];
    env.board(files, { update: { latest: "1.2.0", current: "1.1.0", url: RELEASE } });
    await env.clock.advance(400);
    check("a new release: a last line, the version and Update with a download arrow, pinned at the bottom",
          [/<span class="ver">Figaro 1\.1\.0<\/span><span class="what">.*<button[^>]*data-act="update"[^>]*><svg[^>]*><path d="M5 1\.2v5\.5[^"]*h6\.4"[^>]*><\/svg>Update to 1\.2\.0</.test(env.html()), env.pinned()],
          [true, true]);
    check("…and Skip before it, with its hint, until the next release",
          /<button[^>]*class="skip" data-act="skip" data-version="1\.2\.0"[^>]*>Skip<\/button><span class="tip"[^>]*>Hide until the next release<\/span><\/span><button[^>]*data-act="update"/.test(env.html()), true);
    env.click("update");
    check("Update asks the bridge", env.sent("update-now").length, 1);
    env.board(files, { update: { latest: "1.2.0" }, updating: { step: "wait", what: "update", to: "1.2.0" } });
    await env.clock.advance(400);
    check("…which waits for the scripts first", /Updates once the scripts finish/.test(env.html()), true);
    env.board(files, { update: { latest: "1.2.0" }, failed: { what: "update", error: "local changes", url: RELEASE } });
    await env.clock.advance(400);
    check("a failed update: the release page on the last line, and why under it, as a field's error",
          [/<div class="line">.*data-act="release"[^>]*>Open release<\/button><\/span><\/div><div class="why">.*Couldn't update to 1\.2\.0: local changes<\/span><\/div><\/div>$/.test(env.html()),
           /data-act="skip"/.test(env.html())], [true, true]);
    env.click("release", null, { url: RELEASE });
    check("…in the browser", env.posted.filter((m) => m.type === "open-url").map((m) => m.url), [RELEASE]);

    env.ws().receive({ type: "outdated", running: "2026-10-08.6", expected: "2026-10-08.7" });
    env.board(files);
    await env.clock.advance(400);
    check("newer plugin code on disk: Reload with a circling arrow, on the last line, pinned, and no Skip",
          [/<div class="line">.*New build<\/span><button[^>]*data-act="reload"[^>]*><svg[^>]*><path d="M8\.6 5\.9A/.test(env.html()), env.pinned(), /data-act="skip"/.test(env.html())],
          [true, true, false]);
    env.click("reload", "here");
    check("Reload asks the bridge", env.sent("reload-all").length, 1);
    env.board(files, { failed: { what: "reload", error: "files stayed busy" } });
    await env.clock.advance(400);
    check("a failed reload: why under the line, and Reload again",
          /<div class="line">.*data-act="reload".*<\/div><div class="why">.*Couldn't reload: files stayed busy/.test(env.html()), true);
  }

  // ─── Skip: a release goes away until the next one ───────────────────────
  {
    const env = load();
    env.identity("Dashboard", "KEY1");
    env.ws().open();
    const files = [file("KEY1", "Dashboard")];
    env.board(files, { update: { latest: "1.2.0", current: "1.1.0", url: RELEASE } });
    await env.clock.advance(400);
    env.click("skip", null, { version: "1.2.0" });
    check("Skip tells the bridge which release", env.sent("skip"), [{ type: "skip", version: "1.2.0" }]);
    check("…and the line goes at once", env.pinned(), false);
    env.board(files, { update: { latest: "1.2.0", current: "1.1.0", url: RELEASE } });
    await env.clock.advance(400);
    check("…and stays away with a bridge that still offers it", env.pinned(), false);
    env.board(files, { release: { latest: "1.2.0", url: RELEASE } });
    await env.clock.advance(400);
    check("…as a link too", env.pinned(), false);
    env.ws().receive({ type: "outdated", running: "2026-10-08.6", expected: "2026-10-08.7" });
    env.board(files, { update: { latest: "1.2.0", url: RELEASE } });
    await env.clock.advance(400);
    check("newer plugin code on disk still shows, with Reload", [/New build/.test(env.html()), /data-act="update"/.test(env.html())], [true, false]);
  }
  {
    const env = load();
    env.identity("Dashboard", "KEY1");
    env.ws().open();
    const files = [file("KEY1", "Dashboard")];
    env.board(files, { update: { latest: "1.2.0", url: RELEASE } });
    await env.clock.advance(400);
    env.click("skip", null, { version: "1.2.0" });
    env.board(files, { update: { latest: "1.2.1", url: RELEASE } });
    await env.clock.advance(400);
    check("a newer release than the skipped one brings the line back", /Update to 1\.2\.1/.test(env.html()), true);
  }
  {
    const env = load();
    env.identity("Dashboard", "KEY1");
    env.ws().open();
    env.ws().receive({ type: "update", latest: "1.2.0", current: "1.1.0", url: RELEASE });
    env.board([file("KEY1", "Dashboard")], { skipped: "1.2.0" });
    await env.clock.advance(400);
    check("a release skipped in another window has no line here, though the bridge said update at hello", env.pinned(), false);
  }

  // ─── a bridge with the Update button off ────────────────────────────────
  {
    const env = load();
    env.identity("Dashboard", "KEY1");
    env.ws().open();
    const files = [file("KEY1", "Dashboard")];
    env.board(files, { release: { latest: "1.2.0", current: "1.1.0", url: RELEASE } });
    await env.clock.advance(400);
    check("a new release: the last line links to it, with no Update, and Skip before it",
          [/<span class="what"><span class="skip-w">.*<\/span><button[^>]*class="out"[^>]*data-act="release"[^>]*><span>1\.2\.0 is out<\/span>/.test(env.html()),
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
  // ─── at work: a pixel grid, and the word shimmers ───────────────────────
  {
    const env = load();
    const ago = (sec) => 1000 + env.clock.now() / 1000 - sec;
    env.identity("Dashboard", "KEY1");
    env.ws().open();
    env.board([file("KEY1", "Dashboard", { agents: [agent("designer", { since: ago(12), last: ago(1), busy: true })] })]);
    await env.clock.advance(400);
    const grids = () => (env.html().match(/<span class="px" aria-hidden="true">(<i><\/i>){9}<\/span>/g) || []).length;
    check("an agent at work: a 3x3 pixel grid by the file's name", [grids(), /class="spin"/.test(env.html())], [1, false]);
    env.click("toggle", "here");
    await env.clock.advance(400);
    check("…and open, one by the agent too, with the word shimmering and the clock steady",
          [grids(), /<span><span class="shim">working<\/span> <span[^>]*data-clock/.test(env.html())], [2, true]);
  }

  // ─── sounds ─────────────────────────────────────────────────────────────
  {
    const env = load();
    const ago = (sec) => 1000 + env.clock.now() / 1000 - sec;
    env.identity("Dashboard", "KEY1");
    env.ws().open();
    const designer = (extra) => agent("designer", Object.assign({ since: ago(60), last: ago(1), busy: true }, extra));
    const icons = agent("icons", { since: ago(30), last: ago(0), busy: true });
    const kit = (agents) => file("KEY2", "Kit", { agents });
    env.board([file("KEY1", "Dashboard", { agents: [designer()] }), kit([])]);
    await env.clock.advance(400);
    check("no sound for what the first board finds", env.heard(), []);
    env.click("toggle", "here");
    await env.clock.advance(50);
    check("opening an island: a tick", env.heard(), ["square 2100"]);
    env.click("toggle", "here");
    await env.clock.advance(50);
    check("…closing it: a press", env.heard(), ["noise", "sine 680"]);
    const failing = (at) => agent("icons", { since: ago(30), last: ago(0), busy: true, error: { text: "boom", line: 3, at } });
    env.board([file("KEY1", "Dashboard", { agents: [designer(), icons] }), kit([])]);
    env.board([file("KEY1", "Dashboard", { agents: [designer({ busy: false, done: { text: "Have a look", at: ago(0) } }), failing(ago(0))] }), kit([])]);
    await env.clock.advance(50);
    check("an agent begins, is done, a script fails: quiet", env.heard(), []);
    env.board([file("KEY1", "Dashboard", { agents: [failing(ago(20))], stuck: { agent: "icons", since: ago(40) } }), kit([])]);
    await env.clock.advance(50);
    check("Figma stops answering in this file: two low knocks", env.heard(), ["square 220", "square 220"]);
    env.board([file("KEY1", "Dashboard", { agents: [failing(ago(20))], stuck: { agent: "icons", since: ago(40) } }),
               kit([]), file("KEY3", "Sandbox", { stuck: { agent: "tokens", since: ago(40) } })]);
    await env.clock.advance(50);
    check("…once, and another file's is that window's to play", env.heard(), []);
    env.click("stop", "here", { agent: "icons" });
    env.click("update");
    env.click("layer", null, { id: "1:2" });
    env.click("skip", null, { version: "1.2.0" });
    await env.clock.advance(50);
    check("Stop thuds down, Update pulses, a layer blips, Skip flies off", env.heard(),
          ["noise", "sine 340", "sine 330", "sine 430", "noise", "sine 600"]);
    await env.clock.advance(4000);
    check("the sounds' AudioContext sleeps a moment after the last", env.tone() === "none" && env.cues(), "suspended");
  }

  process.exit(failed ? 1 : 0);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
