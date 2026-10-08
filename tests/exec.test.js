// The exec core in plugin/code.js against a stub of Figma that reports
// edits the way Figma does — `documentchange` in a batch a moment after them —
// and keeps an undo stack: what a script changed, one Cmd+Z step per script,
// read-only rollback, `undo`, line numbers in errors, the guarded `figma`.
//
//     node tests/exec.test.js
const fs = require("fs");
const path = require("path");
const src = fs.readFileSync(path.join(__dirname, "..", "plugin", "code.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "..", "plugin", "ui.html"), "utf8");

// "+5:1 Box" creates, "-5:1" deletes, "~1:1 Card fills strokes" changes
// properties, "s9 Brand" creates a style.
function parse(spec, origin) {
  const [head, name, ...props] = spec.split(" ");
  const op = head[0], id = head.slice(1);
  // A deleted node is a RemovedNode: id, type and `removed`, no name.
  const node = op === "-" ? { id, type: "FRAME", removed: true } : { id, name, type: "FRAME", removed: false };
  // "+5:2 Label ^5:1": made inside 5:1
  const up = props.find((p) => p[0] === "^");
  if (up) node.parent = { id: up.slice(1) };
  if (op === "+") return { origin, type: "CREATE", id, node };
  if (op === "-") return { origin, type: "DELETE", id, node };
  if (op === "~") return { origin, type: "PROPERTY_CHANGE", id, node, properties: props };
  if (op === "s") return { origin, type: "STYLE_CREATE", id, style: { name } };
  throw new Error("bad spec " + spec);
}

function makeFigma() {
  const handlers = [];
  const posted = [];
  const shown = [];
  const saved = [];
  const fontLoads = [];
  const undo = { steps: [], pending: [], log: [], reverted: [] };
  const nodes = new Map([["1:1", { id: "1:1", name: "Card", type: "FRAME" }]]);
  const made = new Map();  // nodes created by edit(), to remove some with a parent
  const gone = new Map();  // deleted nodes, for an undo to bring back
  const keepAlive = new Set();  // nodes an undo fails to remove (a broken Figma)
  // The document follows the edits, and an undo puts it back.
  const apply = (changes) => {
    for (const c of changes) {
      if (c.type === "CREATE") nodes.set(c.id, c.node);
      if (c.type === "DELETE") { gone.set(c.id, nodes.get(c.id) || c.node); nodes.delete(c.id); }
    }
  };
  const unapply = (changes) => {
    for (const c of changes.slice().reverse()) {
      if (c.type === "CREATE" && !keepAlive.has(c.id)) nodes.delete(c.id);
      if (c.type === "DELETE") nodes.set(c.id, gone.get(c.id) || { id: c.id, name: "?", type: "FRAME" });
    }
  };
  let buffer = [];
  let timer = null;
  let failSave = false;
  // Figma batches its reports ~100 ms after an edit; 20 ms here.
  const emit = (changes) => {
    buffer.push(...changes);
    if (!timer) {
      timer = setTimeout(() => {
        timer = null;
        const batch = buffer;
        buffer = [];
        for (const fn of handlers.slice()) fn({ documentChanges: batch });
      }, 20);
    }
  };
  const figma = {
    root: { name: "Draft", children: [{ id: "0:1" }] },
    fileKey: "KEY",
    showUI(h) { shown.push(h); },
    ui: { onmessage: null, postMessage(m) { posted.push(m); } },
    currentPage: { selection: [] },
    variables: { setValueForMode() {} },
    // A library call that never settles, as figma.teamLibrary sometimes does.
    teamLibrary: { getAvailableLibraryVariableCollectionsAsync: () => new Promise(() => {}) },
    on(event, fn) { if (event === "documentchange") handlers.push(fn); },
    off(event, fn) { const i = handlers.indexOf(fn); if (i !== -1) handlers.splice(i, 1); },
    commitUndo() {
      undo.log.push("commit");
      if (undo.pending.length) { undo.steps.push(undo.pending); undo.pending = []; }
    },
    triggerUndo() {
      undo.log.push("undo");
      // As in Figma: edits not yet committed go together with the
      // step before them.
      const reverted = undo.pending.concat(undo.steps.pop() || []);
      undo.pending = [];
      undo.reverted.push(reverted.map((c) => c.id));
      unapply(reverted);
      if (reverted.length) {
        emit(reverted.map((c) => ({ origin: "LOCAL", type: "PROPERTY_CHANGE", id: c.id,
          node: c.node, properties: ["undo"] })));
      }
    },
    getNodeById(id) { return nodes.get(String(id)) || null; },
    async getNodeByIdAsync(id) { return nodes.get(String(id)) || null; },
    async saveVersionHistoryAsync(title) {
      if (failSave) throw new Error("no edit access");
      saved.push(title);
      return { id: "v" + saved.length };
    },
    async loadFontAsync(font) { fontLoads.push(font.family + " " + font.style); },
    // A script's edits: they wait in the undo stack until committed.
    edit(...specs) {
      const changes = specs.map((s) => parse(s, "LOCAL"));
      for (const c of changes) if (c.type === "CREATE") made.set(c.id, c.node);
      apply(changes);
      undo.pending.push(...changes);
      emit(changes);
    },
    // What Figma does inside addComponentProperty: it closes an undo step.
    splitUndo() {
      if (undo.pending.length) { undo.steps.push(undo.pending); undo.pending = []; }
    },
    // A node removed with its parent: Figma reports only the parent's removal.
    removeWith(parent, ...children) {
      for (const id of children) made.get(id).removed = true;
      figma.edit("-" + parent);
    },
    remoteEdit(...specs) { emit(specs.map((s) => parse(s, "REMOTE"))); },
  };
  // Someone working in Figma by hand: each action is its own undo step.
  const userEdit = (...specs) => {
    const changes = specs.map((s) => parse(s, "LOCAL"));
    apply(changes);
    undo.steps.push(changes);
    emit(changes);
  };
  return { figma, handlers, posted, shown, saved, fontLoads, undo, userEdit, nodes, keepAlive,
           failSaves() { failSave = true; } };
}

function load() {
  const env = makeFigma();
  env.api = new Function("figma", "__html__", src +
    "\nreturn { HISTORY, OUTSIDE, RUNS, ABORTED, GATES, markAborted, SCRIPT_LINE_OFFSET };")(env.figma, html);
  return env;
}

// The reply as the bridge sees it: the value parsed, the log lines gathered.
let seq = 0;
async function send(env, msg) {
  const id = (msg.type === "undo" ? "u" : "e") + (++seq);
  await env.figma.ui.onmessage(Object.assign({ id }, msg));
  const reply = env.posted.find((m) => m.id === id && (m.type === "result" || m.type === "error"));
  const logs = [].concat(...env.posted.filter((m) => m.id === id && m.type === "log").map((m) => m.lines));
  const value = typeof reply.valueJson === "string" ? JSON.parse(reply.valueJson) : reply.value;
  return Object.assign({}, reply, { value, logs });
}
const exec = (env, code, opts) => send(env, Object.assign({ type: "exec", code, timeout: 10 }, opts));
const undoLast = (env, opts) => send(env, Object.assign({ type: "undo" }, opts));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let failed = 0;
function check(label, actual, expected) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  const ok = a === e;
  if (!ok) failed++;
  console.log(`${ok ? "  ok  " : " FAIL "} ${label}${ok ? "" : `\n         got ${a}\n         want ${e}`}`);
}

(async () => {
  // ─── the script ─────────────────────────────────────────────────────────
  {
    const env = load();
    check("line 1 lands where it should", env.api.SCRIPT_LINE_OFFSET, 3);
    const r = await exec(env, "return 42 // the answer");
    check("a trailing comment does not eat the script's end", [r.type, r.value], ["result", 42]);

    const e = await exec(env, "const a = 1;\nconst b = 2;\nnull.boom;\nreturn a + b;");
    check("error names the script line", [e.type, e.line, e.source], ["error", 3, "null.boom;"]);
    check("stack points into the script", /script:3:\d+/.test(e.stack), true);

    const bare = await exec(env, "throw {};");
    check("an error without text is explained", /^an error with no text/.test(bare.text), true);
  }

  // ─── what a script changed ──────────────────────────────────────────────
  {
    const env = load();
    const r = await exec(env, 'figma.edit("+5:1 Box", "~1:1 Card fills", "~1:1 Card strokes", "-2:2 Old"); return 1;');
    const c = r.changes;
    check("counts", [c.created, c.deleted, c.changed, c.styles], [1, 1, 1, 0]);
    check("properties", c.props, { fills: 1, strokes: 1 });
    check("items by node", c.items.map((i) => i.op + i.id + (i.name ? " " + i.name : "")),
      ["+5:1 Box", "~1:1 Card", "-2:2"]);
    check("item props merge", c.items[1].props, ["fills", "strokes"]);
    check("the script is one undo step", env.undo.steps.length, 1);

    const made = await exec(env, 'figma.edit("+6:1 New"); figma.edit("~6:1 New fills"); return 1;');
    check("a new node's first properties are part of creating it",
      [made.changes.created, made.changes.changed], [1, 0]);

    const gone = await exec(env, 'figma.edit("+7:1 Tmp"); figma.edit("-7:1 Tmp"); return 1;');
    check("made and dropped in one script: nothing to report", gone.changes, undefined);

    const nested = await exec(env, 'figma.edit("+7:2 Box", "+7:3 Label", "~1:1 Card fills");' +
      'figma.removeWith("7:2", "7:3"); return 1;');
    check("…and so is a layer that went with its new parent",
      [nested.changes.created, nested.changes.changed, nested.changes.items.map((i) => i.op + i.id)],
      [0, 1, ["~1:1"]]);

    const quiet = await exec(env, "return figma.root.name;");
    check("a script that changed nothing has no changes", [quiet.value, quiet.changes], ["Draft", undefined]);

    const style = await exec(env, 'figma.edit("s9 Brand"); return 1;');
    check("styles are counted", style.changes.styles, 1);

    const remote = await exec(env, 'figma.remoteEdit("~1:1 Card fills"); return 1;');
    check("other people's edits are counted apart", [remote.changes.remote, remote.changes.changed], [1, 0]);

    const many = Array.from({ length: 40 }, (_, i) => `"~${i + 100}:1 N${i} fills"`).join(", ");
    const big = await exec(env, `figma.edit(${many}); return 1;`);
    check("at most 30 items, the rest counted", [big.changes.items.length, big.changes.more], [30, 10]);
  }

  // ─── one undo step per script ───────────────────────────────────────────
  {
    const env = load();
    env.undo.pending.push(parse("~1:1 Card x", "LOCAL"));  // an edit nobody committed yet
    await exec(env, 'figma.edit("~1:1 Card fills"); return 1;');
    await exec(env, 'figma.edit("~1:1 Card opacity"); return 1;');
    check("what came before and each script are separate steps",
      env.undo.steps.map((s) => s.map((c) => c.properties[0])), [["x"], ["fills"], ["opacity"]]);
  }

  // ─── read-only ──────────────────────────────────────────────────────────
  {
    const env = load();
    const ok = await exec(env, "return figma.root.name;", { readOnly: true });
    check("a read-only script that reads", [ok.type, ok.value], ["result", "Draft"]);

    const r = await exec(env, 'figma.edit("~1:1 Card fills", "+8:1 Box"); return 1;', { readOnly: true });
    check("a read-only script that wrote fails", [r.type, r.text], ["error", "read-only: the script changed 2 — rolled back"]);
    check("…and is rolled back", [r.rolledBack, env.undo.steps.length, env.undo.reverted.length], [true, 0, 1]);
    check("…which is not news", [env.api.OUTSIDE.local, env.api.HISTORY.length], [0, 0]);

    const t = await exec(env, 'figma.edit("~1:1 Card fills"); throw new Error("oops");', { readOnly: true });
    check("a read-only script that wrote and threw", t.text, "oops — read-only: the script changed 1 — rolled back");

    // Two at once: whose edit it was is unknown, so nothing is rolled back.
    const writer = exec(env, 'await new Promise((r) => setTimeout(r, 80)); figma.edit("~1:1 Card fills"); return 1;');
    const reader = exec(env, 'figma.edit("~1:1 Card opacity"); return 1;', { readOnly: true });
    const [w, rd] = await Promise.all([writer, reader]);
    check("overlapping read-only: a notice, no rollback", [rd.type, /nothing rolled back/.test(rd.notice), rd.changes.shared],
      ["result", true, true]);
    check("overlapping writer still reports", w.changes.changed, 1);
  }

  // ─── undo ───────────────────────────────────────────────────────────────
  {
    const env = load();
    await exec(env, 'figma.edit("+5:1 Box", "~1:1 Card fills"); return 1;', { agent: "anna" });
    await exec(env, "return 1;", { agent: "anna" });                       // changed nothing
    await exec(env, 'figma.edit("~1:1 Card x"); return 1;', { readOnly: true });  // rolled back
    const u = await undoLast(env, { agent: "anna" });
    check("undo reverts the last script that changed something",
      [u.type, u.value.undone.agent, u.value.undone.changes, u.value.left], ["result", "anna", "+1 ~1", 0]);
    check("…and only it", [env.undo.steps.length, env.undo.reverted.slice(-1)[0]], [0, ["5:1", "1:1"]]);
    check("…which is not news", env.api.OUTSIDE.local, 0);
    const none = await undoLast(env, {});
    check("nothing left to undo", [none.type, /nothing to undo/.test(none.text)], ["error", true]);
  }
  {
    const env = load();
    await exec(env, 'figma.edit("~1:1 Card fills"); return 1;');
    await exec(env, 'figma.edit("~1:1 Card opacity"); return 1;');
    const a = await undoLast(env, {});
    const b = await undoLast(env, {});
    check("undo walks back script by script", [a.value.left, b.value.left, env.undo.steps.length], [1, 0, 0]);
  }
  {
    const env = load();
    await exec(env, 'figma.edit("~1:1 Card fills"); return 1;');
    env.userEdit("~1:1 Card name");
    const u = await undoLast(env, {});
    check("an edit by hand after the script blocks undo", [u.type, /1 more changed in the file \(Card\)/.test(u.text)], ["error", true]);
    check("…and nothing is reverted", env.undo.log.filter((x) => x === "undo").length, 0);
  }
  {
    const env = load();
    await exec(env, 'figma.edit("~1:1 Card fills"); return 1;', { agent: "anna" });
    const other = await undoLast(env, { agent: "bob" });
    check("another agent's script needs --force", [other.type, /by anna, not bob/.test(other.text)], ["error", true]);
    const forced = await undoLast(env, { agent: "bob", force: true });
    check("…and goes with it", forced.type, "result");
  }
  {
    const env = load();
    await exec(env, 'figma.variables.setValueForMode(); return 1;');
    const u = await undoLast(env, {});
    check("variable edits are invisible, so undo stops there", [u.type, /may have changed variables/.test(u.text)], ["error", true]);
    const ro = await exec(env, "const c = figma.root; if (false) c.remove(); return 1;", { readOnly: true });
    check("a read-only script that may remove what Figma does not report gets a notice",
      [ro.type, /-R cannot roll it back/.test(ro.notice)], ["result", true]);
  }
  {
    // A script that made layers and removed them left nothing, but Figma
    // still keeps its Cmd+Z step: undo must revert that step, not the one before.
    const env = load();
    await exec(env, 'figma.edit("~1:1 Card fills"); return 1;');
    const tmp = await exec(env, 'figma.edit("+7:1 Tmp"); figma.edit("-7:1 Tmp"); return 1;');
    check("a script that only made and dropped layers reports nothing", tmp.changes, undefined);
    const u = await undoLast(env, {});
    check("…yet undo takes its own step",
      [u.type, u.value.undone.changes, u.value.left, env.undo.reverted.slice(-1)[0]],
      ["result", "nothing: layers made and removed", 1, ["7:1", "7:1"]]);
  }

  // ─── the new layers a report links to ───────────────────────────────────
  {
    const env = load();
    const r = await exec(env, 'figma.edit("+5:2 Label ^5:1", "+5:3 Icon ^5:1", "+5:1 Set ^1:2", "+6:1 Note"); return 1;');
    check("the report names the new layers that are not inside other new ones",
      r.changes.top.map((t) => t.id + " " + t.name), ["5:1 Set", "6:1 Note"]);
    const many = await exec(env, "for (let i = 1; i <= 7; i++) figma.edit(\"+9:\" + i + \" Card\"); return 1;");
    check("…at most five of them", [many.changes.top.length, many.changes.topMore], [5, 2]);
  }

  // ─── Figma's own undo steps inside a script ─────────────────────────────
  {
    const env = load();
    env.userEdit("~1:1 Card fills");  // the user's step, before the script
    await sleep(50);  // reported before the script starts
    const r = await exec(env, 'figma.edit("+5:1 Set", "+5:2 Label"); figma.splitUndo();' +
      ' figma.edit("~5:1 Set componentPropertyDefinitions"); figma.splitUndo(); figma.edit("+5:3 Icon"); return 1;');
    check("a script Figma split into steps reports as one", r.changes.created, 3);
    const u = await undoLast(env, {});
    check("undo reverts every step of it",
      [u.type, u.value.steps, ["5:1", "5:2", "5:3"].some((id) => env.nodes.has(id)), u.value.warning],
      ["result", 3, false, undefined]);
    check("…and not the step before it", [env.undo.steps.length, env.undo.steps[0][0].id], [1, "1:1"]);
  }
  {
    const env = load();
    env.userEdit("~1:1 Card fills");
    await sleep(50);
    const r = await exec(env, 'figma.edit("+6:1 Box"); figma.splitUndo(); figma.edit("-1:1 Card"); return 1;',
      { readOnly: true });
    check("a read-only script is rolled back across Figma's steps",
      [r.type, r.rolledBack, env.nodes.has("6:1"), env.nodes.has("1:1"), env.undo.steps.length],
      ["error", true, false, true, 1]);
  }
  {
    const env = load();
    await exec(env, 'figma.edit("~1:1 Card componentPropertyDefinitions"); figma.splitUndo();' +
      ' figma.edit("~1:1 Card componentPropertyDefinitions"); return 1;');
    const u = await undoLast(env, {});
    check("properties of a component that was there get a warning",
      [u.type, u.value.steps, /properties of existing components/.test(u.value.warning)], ["result", undefined, true]);
  }
  {
    // An undo that cannot remove what the script made must not eat the stack.
    const env = load();
    env.userEdit("~1:1 Card fills");
    env.userEdit("~1:1 Card name");
    await sleep(50);
    await exec(env, 'figma.edit("+8:1 Stuck"); return 1;');
    env.keepAlive.add("8:1");
    const u = await undoLast(env, {});
    check("undo stops once a step is not the script's",
      [u.type, u.value.steps, /not everything was undone: 1 layers/.test(u.value.warning), env.undo.steps.length],
      ["result", 2, true, 1]);
  }

  // ─── ids from elsewhere ─────────────────────────────────────────────────
  {
    const env = load();
    const e = await exec(env, 'return await h.node("9:9");');
    check("h.node fails at once for an id this file lacks", /no node 9:9 in "Draft"/.test(e.text), true);
    const n = await exec(env, 'const n = await figma.getNodeByIdAsync("9:9"); return n === null;');
    check("getNodeByIdAsync gives null at once", n.value, true);
    check("…and says why", /has no such node/.test(n.logs.join("\n")), true);
    const ok = await exec(env, 'return (await h.resolve("1:1")).name;');
    check("a real id still resolves", ok.value, "Card");
  }

  // ─── the figma a script gets ────────────────────────────────────────────
  {
    const env = load();
    const close = await exec(env, "figma.closePlugin();");
    check("closePlugin is off", /closePlugin\(\) is turned off/.test(close.text), true);
    const ui = await exec(env, "figma.ui.onmessage = () => {};");
    check("ui.onmessage is guarded", /keeps the plugin connected/.test(ui.text), true);
    const show = await exec(env, 'figma.showUI("<p>hi</p>");');
    check("showUI is off", /showUI\(\) is turned off/.test(show.text), true);
    const read = await exec(env, 'return [figma.root.name, "root" in figma, typeof figma.commitUndo];');
    check("everything else passes through", read.value, ["Draft", true, "function"]);

    const fonts = await exec(env, 'const f = {family: "Inter", style: "Regular"};' +
      "await figma.loadFontAsync(f); await figma.loadFontAsync(f); return 1;");
    check("a loaded font is not loaded again", [fonts.type, env.fontLoads], ["result", ["Inter Regular"]]);

    const late = await exec(env, 'await new Promise((r) => setTimeout(r, 120));' +
      'await figma.loadFontAsync({family: "Inter", style: "Bold"}); return 1;', { timeout: 0.05 });
    check("a Figma call past the deadline aborts", [late.type, /^aborted/.test(late.text)], ["error", true]);

    const hung = await exec(env, "return await figma.teamLibrary.getAvailableLibraryVariableCollectionsAsync();",
      { timeout: 0.05 });
    check("a Figma call that never settles ends at the deadline",
      [hung.type, /^aborted: figma\.teamLibrary\.getAvailableLibraryVariableCollectionsAsync\(\) did not answer before --timeout ran out/.test(hung.text)],
      ["error", true]);
    check("…and names the script's line that awaited it", [hung.line, /^return await figma\.teamLibrary/.test(hung.source)],
      [1, true]);
    const stuck = env.figma.ui.onmessage({ id: "e-stuck", type: "exec", timeout: 10,
      code: "await figma.teamLibrary.getAvailableLibraryVariableCollectionsAsync(); return 1;" });
    await sleep(20);
    const t0 = Date.now();
    await env.figma.ui.onmessage({ type: "abort", id: "e-stuck" });
    await stuck;
    const ended = env.posted.find((m) => m.id === "e-stuck" && (m.type === "result" || m.type === "error"));
    check("…or at once on the bridge's abort", [ended.type, /^aborted/.test(ended.text), Date.now() - t0 < 1000],
      ["error", true, true]);
    check("no gate outlives its script", env.api.GATES.size, 0);
    const caught = await exec(env, "try { await figma.teamLibrary.getAvailableLibraryVariableCollectionsAsync(); }" +
      ' catch (e) { return "caught"; }', { timeout: 0.05 });
    check("a script may catch it and still answer", caught.value, "caught");

    for (let i = 0; i < 150; i++) env.api.markAborted("x" + i);
    check("aborted ids are bounded", [env.api.ABORTED.size, env.api.ABORTED.has("x0"), env.api.ABORTED.has("x149")],
      [100, false, true]);
  }

  // ─── checkpoints ────────────────────────────────────────────────────────
  {
    const env = load();
    const r = await exec(env, 'figma.edit("~1:1 Card fills"); return 1;', { checkpoint: "Figaro · test" });
    check("a checkpoint is saved before the script", [env.saved, r.checkpoint.id, r.checkpoint.title],
      [["Figaro · test"], "v1", "Figaro · test"]);
    env.failSaves();
    const f = await exec(env, "return 2;", { checkpoint: "Figaro · again" });
    check("a failed checkpoint is reported, the script still runs",
      [f.value, f.checkpoint.error], [2, "no edit access"]);
  }

  // ─── hot reload ─────────────────────────────────────────────────────────
  {
    const env = load();
    const before = env.figma.ui.onmessage;
    await env.figma.ui.onmessage({ type: "reload", id: "r1", code: src, html: "<p>new</p>" });
    check("the new code shows its window", env.shown.slice(-1)[0], "<p>new</p>");
    check("…takes the messages", env.figma.ui.onmessage !== before, true);
    check("…and the old listener is gone", env.handlers.length, 1);
    const r = await exec(env, 'figma.edit("~1:1 Card fills"); return 1;');
    check("changes are counted once after a reload", r.changes.changed, 1);

    await env.figma.ui.onmessage({ type: "reload", id: "r2", code: "throw new Error('broken')", html: "" });
    const err = env.posted.find((m) => m.id === "r2");
    check("a broken reload says so", err && err.text, "reload: broken");
  }

  await sleep(50);
  console.log(failed ? `\n${failed} FAILED` : "\nall exec checks passed");
  process.exit(failed ? 1 : 0);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
