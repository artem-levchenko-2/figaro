// The window: one island per file (ui.html). It grows and shrinks with them —
// the UI measures itself and asks for its height (`resize` below).
const UI_WIDTH = 320;
figma.showUI(__html__, { width: UI_WIDTH, height: 70, title: "Figaro Relay", themeColors: true });

// Build id of this plugin code. The bridge compares it with plugin/code.js on
// disk and asks for a re-Run when they differ, because a running plugin keeps
// the code it started with. Bump it on every change to plugin/ —
// tests/test_plugin_version.py fails until you do.
const PLUGIN_VERSION = "2026-10-08.8";

// What this build can do beyond a plain exec, so the bridge knows which
// requests it may send (an older build gets `figaro reload` first).
const CAPS = ["changes", "readOnly", "undo", "reload", "checkpoint",
                 "quick", "libs", "links", "inspect", "shot", "gate", "board"];

// Tell the UI which file we're in, so it can register this connection with the
// bridge by name (figma.root.name). The bridge routes --target by that name.
function postIdentity() {
  let fileKey = null;
  try { fileKey = figma.fileKey || null; } catch (e) { /* not always available */ }
  figma.ui.postMessage({
    type: "identity",
    fileKey: fileKey,
    name: figma.root.name,
    docSig: docSignature(),
    pluginVersion: PLUGIN_VERSION,
    caps: CAPS,
  });
}

// The bridge needs an id that is the same for one file open in two windows and
// different for two files that share a name, to route a target safely.
// `figma.root.id` is "0:0" in every file, and page ids are not enough either:
// every new file starts with a single page "0:1".
//
// The id is the file key. "enablePrivatePluginApi" in manifest.json gives a
// development plugin `figma.fileKey`, and reading it writes nothing: storing an
// id in the document's plugin data would edit every file the plugin is run in,
// shared libraries included. Without a key the id lives only as long as this
// run, and still nothing is written.
const RUN_ID = "r" + Math.random().toString(36).slice(2, 10) + Date.now().toString(36);

function docSignature() {
  try {
    if (figma.fileKey) return figma.fileKey;
  } catch (e) { /* not always available */ }
  return RUN_ID;
}
postIdentity();

function safeStringify(value) {
  if (value === undefined) return null;
  try { return JSON.parse(JSON.stringify(value)); } catch (e) {
    try { return String(value); } catch (e2) { return null; }
  }
}

// The `result` message for a finished exec. It carries the value once: the
// bridge derives the human-readable `result` text from it. Sending both, as
// before, doubled every payload and pretty-printed objects a third time — a
// 50k-item array took ~0.6 s to come back. `text` is only sent when the value
// can't express it: no return value (logs or "Done"), or NaN / BigInt / etc.
function resultMessage(id, result, logs) {
  const t = typeof result;
  if (result === undefined) {
    return { type: "result", id, value: null, text: asText(result, logs) };
  }
  if (t === "string" || t === "boolean" || (t === "number" && isFinite(result))) {
    return { type: "result", id, value: result };
  }
  if (t === "object") {  // objects, arrays, null
    // Serialized exactly once, here. The UI splices this string into the
    // WebSocket frame as is, so the object is never cloned across postMessage
    // or stringified again (that was 3 passes over a big result).
    try {
      const json = JSON.stringify(result);
      if (json !== undefined) return { type: "result", id, valueJson: json };
    } catch (e) { /* cyclic, BigInt inside… — fall through */ }
    return { type: "result", id, value: safeStringify(result) };
  }
  return { type: "result", id, value: safeStringify(result), text: String(result) };
}

function asText(value, logs) {
  try {
    if (value !== undefined) {
      return typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
    }
  } catch (e) { /* unserializable — fall back to logs */ }
  return logs.length > 0 ? logs.join("\n") : "Done";
}

// ─── helpers exposed as `h.*` to every exec ──────────────────────────────

// Set for the duration of one exec so helpers can surface warnings through the
// same `print()` the user's code gets. No-op outside an exec.
let CURRENT_PRINT = () => {};

async function resolveVar(varOrId) {
  if (varOrId == null) return null;
  if (typeof varOrId !== "string") return varOrId;

  // Local variables are addressed as "VariableID:1:23"; anything else is a
  // library key, which has to be imported rather than looked up.
  if (varOrId.indexOf("VariableID:") === 0) {
    return await figma.variables.getVariableByIdAsync(varOrId);
  }
  // A library key is not a well-formed id, and getVariableByIdAsync rejects
  // those by throwing rather than returning null — so this has to be guarded,
  // otherwise the import below is unreachable for the very case it exists for.
  let local = null;
  try {
    local = await figma.variables.getVariableByIdAsync(varOrId);
  } catch (e) {
    local = null;
  }
  if (local) return local;

  try {
    return await figma.variables.importVariableByKeyAsync(varOrId);
  } catch (e) {
    return null;
  }
}

// "#1a2b3c" / "1a2b3c" / "#f00" -> {r,g,b} in Figma's 0..1 range.
function hexToRgb(value) {
  let s = String(value).trim().replace(/^#/, "");
  if (s.length === 3) s = s[0] + s[0] + s[1] + s[1] + s[2] + s[2];
  if (!/^[0-9a-fA-F]{6}$/.test(s)) {
    throw new Error("h.hex: expected #RGB or #RRGGBB, got " + JSON.stringify(value));
  }
  const n = parseInt(s, 16);
  return {
    r: ((n >> 16) & 255) / 255,
    g: ((n >> 8) & 255) / 255,
    b: (n & 255) / 255,
  };
}

// Normalise padding given as a number, [v, h], or {top,right,bottom,left}.
function paddingOf(p) {
  if (p == null) return null;
  if (typeof p === "number") return { top: p, right: p, bottom: p, left: p };
  if (Array.isArray(p)) {
    const v = p[0], hz = p.length > 1 ? p[1] : p[0];
    return { top: v, right: hz, bottom: v, left: hz };
  }
  return {
    top: p.top || 0, right: p.right || 0,
    bottom: p.bottom || 0, left: p.left || 0,
  };
}

// Copy a paint array before mutating it — node.fills/strokes are frozen.
function copyPaints(node, prop, who) {
  const paints = node[prop];
  if (typeof paints === "symbol") {
    throw new Error(
      who + ": '" + node.name + "' has mixed " + prop +
      "; set the paint per-range, or unify " + prop + " on the node first"
    );
  }
  if (!Array.isArray(paints)) {
    throw new Error(who + ": '" + node.name + "' has no " + prop);
  }
  return JSON.parse(JSON.stringify(paints));
}

// A key Figma would not import is the usual dead end — library variables
// the file already uses can still be bound by id.
function noVar(who, varOrId) {
  const id = String(varOrId);
  return new Error(who + ": no variable " + id + (id.indexOf("VariableID:") === 0 ? " in this file"
    : ": it is not an id, and Figma did not import it by key (the library is not enabled for this file, " +
      "or the variable is not published). Take a variable a layer in this file already uses by id: " +
      "figaro inspect of that layer prints \"id VariableID:…\""));
}

const HELPERS = {
  // Bind fill paint at index to a variable (id or instance)
  async bF(node, idx, varOrId) {
    const v = await resolveVar(varOrId);
    if (!v) throw noVar("h.bF", varOrId);
    const f = copyPaints(node, "fills", "h.bF");
    if (!f[idx]) throw new Error("h.bF: '" + node.name + "' has no fill at index " + idx);
    f[idx] = figma.variables.setBoundVariableForPaint(f[idx], "color", v);
    node.fills = f;
    return v;
  },

  // Bind stroke paint at index
  async bS(node, idx, varOrId) {
    const v = await resolveVar(varOrId);
    if (!v) throw noVar("h.bS", varOrId);
    const s = copyPaints(node, "strokes", "h.bS");
    if (!s[idx]) throw new Error("h.bS: '" + node.name + "' has no stroke at index " + idx);
    s[idx] = figma.variables.setBoundVariableForPaint(s[idx], "color", v);
    node.strokes = s;
    return v;
  },

  // Bind numeric property (radii, padding, sizes, itemSpacing, etc.)
  async bN(node, prop, varOrId) {
    const v = await resolveVar(varOrId);
    if (!v) throw noVar("h.bN", varOrId);
    node.setBoundVariable(prop, v);
    return v;
  },

  // First descendant by exact name
  findByName(root, name) {
    return root.findOne((n) => n.name === name);
  },

  // All descendants by exact name
  findAllByName(root, name) {
    return root.findAll((n) => n.name === name);
  },

  // Dump subtree as indented text
  dumpTree(node, opts) {
    opts = opts || {};
    const maxDepth = opts.maxDepth == null ? 99 : opts.maxDepth;
    const showSize = opts.showSize !== false;
    const showText = opts.showText !== false;
    const showLayout = opts.showLayout === true;
    const lines = [];
    const walk = (n, d) => {
      if (d > maxDepth) return;
      const pad = "  ".repeat(d);
      let line = pad + n.name + " [" + n.type + "] " + n.id;
      if (showSize && n.width !== undefined) {
        line += " " + Math.round(n.width) + "×" + Math.round(n.height);
      }
      if (showLayout && n.layoutMode && n.layoutMode !== "NONE") {
        line += " {" + n.layoutMode[0] +
          " gap:" + n.itemSpacing +
          " pad:" + n.paddingTop + "," + n.paddingRight + "," + n.paddingBottom + "," + n.paddingLeft +
          " " + n.primaryAxisSizingMode + "/" + n.counterAxisSizingMode + "}";
      }
      if (showText && n.type === "TEXT") line += ' "' + n.characters + '"';
      lines.push(line);
      if (n.children) for (const c of n.children) walk(c, d + 1);
    };
    walk(node, 0);
    return lines.join("\n");
  },

  // Load every unique font in subtree, then run async fn
  async withFonts(rootNode, asyncFn) {
    const texts = rootNode.findAll
      ? rootNode.findAll((n) => n.type === "TEXT")
      : (rootNode.type === "TEXT" ? [rootNode] : []);
    const seen = new Set();
    const fonts = [];
    const skipped = [];
    for (const t of texts) {
      // Mixed-font nodes can't be loaded wholesale; editing one later throws a
      // confusing "font not loaded" far from here, so say it out loud now.
      if (typeof t.fontName === "symbol") { skipped.push(t.name); continue; }
      const fn = t.fontName;
      const key = fn.family + "|" + fn.style;
      if (!seen.has(key)) { seen.add(key); fonts.push(fn); }
    }
    if (skipped.length) {
      CURRENT_PRINT(
        "h.withFonts: skipped " + skipped.length + " mixed-font text node(s): " +
        skipped.slice(0, 5).join(", ") + (skipped.length > 5 ? ", …" : "") +
        " — editing them will fail unless you load each range manually"
      );
    }
    await Promise.all(fonts.map((f) => loadFont(f)));  // cached, see loadFont
    return await asyncFn();
  },

  // Set a text node's characters with auto font load (single-font texts only)
  async setText(node, text) {
    if (typeof node.fontName === "symbol") {
      throw new Error("h.setText: text '" + node.name + "' has mixed fonts; load each range manually");
    }
    await loadFont(node.fontName);  // cached, see loadFont
    node.characters = text;
  },

  // Clone node and place it next to the original
  cloneNext(node, opts) {
    opts = opts || {};
    const direction = opts.direction || "right";
    const gap = opts.gap == null ? 100 : opts.gap;
    const c = node.clone();
    node.parent.appendChild(c);
    if (direction === "right") { c.x = node.x + node.width + gap; c.y = node.y; }
    else if (direction === "left")  { c.x = node.x - node.width - gap; c.y = node.y; }
    else if (direction === "down")  { c.x = node.x; c.y = node.y + node.height + gap; }
    else if (direction === "up")    { c.x = node.x; c.y = node.y - node.height - gap; }
    if (opts.name) c.name = opts.name;
    return c;
  },

  // Set instance variant properties
  async variant(instance, props) {
    await instance.setProperties(props);
    return instance;
  },

  // Available variants for an instance's component
  async variantsOf(instance) {
    const main = await instance.getMainComponentAsync();
    if (!main) return null;
    const set = main.parent && main.parent.type === "COMPONENT_SET" ? main.parent : null;
    return set
      ? { current: main.name, groups: set.variantGroupProperties, all: set.children.map(c => c.name) }
      : { current: main.name, groups: null, all: null };
  },

  // What the user has selected right now — the bridge between "this one here"
  // and a node id you can act on.
  sel() {
    return figma.currentPage.selection.map((n) => ({
      id: n.id, name: n.name, type: n.type,
      w: n.width, h: n.height,
      chars: n.type === "TEXT" ? n.characters : undefined,
    }));
  },

  // Hex string -> {r,g,b}. Hand-rolling this is where the missing /255 lives.
  hex(value) { return hexToRgb(value); },

  // Ready-to-assign paint array: node.fills = h.solid("#1a2b3c")
  solid(value, opacity) {
    const paint = { type: "SOLID", color: hexToRgb(value) };
    if (opacity != null) paint.opacity = opacity;
    return [paint];
  },

  // Create a frame with auto-layout applied in the order Figma demands:
  // into the tree -> layoutMode -> size -> sizing mode -> spacing/padding.
  // Getting that order wrong silently drops the settings.
  frame(parent, opts) {
    opts = opts || {};
    const f = figma.createFrame();
    if (parent) parent.appendChild(f);

    if (opts.name) f.name = opts.name;

    if (opts.layout) {
      const l = String(opts.layout).toUpperCase();
      f.layoutMode = l === "V" ? "VERTICAL" : l === "H" ? "HORIZONTAL" : l;
    }

    if (opts.w != null || opts.h != null) {
      f.resize(opts.w == null ? f.width : opts.w, opts.h == null ? f.height : opts.h);
    }

    if (f.layoutMode && f.layoutMode !== "NONE") {
      // Hug by default on axes the caller didn't pin to a number.
      if (opts.hug !== false) {
        const horizontalIsPrimary = f.layoutMode === "HORIZONTAL";
        const primaryFixed = horizontalIsPrimary ? opts.w != null : opts.h != null;
        const counterFixed = horizontalIsPrimary ? opts.h != null : opts.w != null;
        if (!primaryFixed) f.primaryAxisSizingMode = "AUTO";
        if (!counterFixed) f.counterAxisSizingMode = "AUTO";
      }
      if (opts.spacing != null) f.itemSpacing = opts.spacing;
      if (opts.align) {
        if (opts.align.primary) f.primaryAxisAlignItems = opts.align.primary;
        if (opts.align.counter) f.counterAxisAlignItems = opts.align.counter;
      }
      const pad = paddingOf(opts.padding);
      if (pad) {
        f.paddingTop = pad.top; f.paddingRight = pad.right;
        f.paddingBottom = pad.bottom; f.paddingLeft = pad.left;
      }
    }

    if (opts.fill != null) f.fills = opts.fill === false ? [] : HELPERS.solid(opts.fill);
    if (opts.radius != null) f.cornerRadius = opts.radius;
    return f;
  },

  // Accept "page" / "sel" alongside a real node id, so callers can say
  // "the thing I'm looking at" without first hunting for its id.
  async resolve(idOrAlias) {
    if (idOrAlias === "page") return figma.currentPage;
    if (idOrAlias === "sel") {
      const s = figma.currentPage.selection;
      if (!s.length) throw new Error("nothing selected in Figma");
      return s[0];
    }
    return await HELPERS.node(idOrAlias);  // fails at once for a missing id
  },

  // Quick async accessors
  // An id this file does not have fails at once. Figma itself waits ~10 s
  // on another file's id or a deleted node, then throws an error with no text.
  // A Figma link or a link-form id ("1-11") works too.
  // The sync lookup answers first: getNodeByIdAsync has hung for a layer
  // inside an instance (I2:1068;2:1041) that the sync lookup found.
  async node(id) {
    id = linkNodeId(id);
    const found = nodeHere(id);
    if (found === null) {
      throw new Error("no node " + id + " in \"" + figma.root.name +
        "\": it is from another file or was deleted — check -T and the id");
    }
    return found || await figma.getNodeByIdAsync(id);
  },
  async var_(idOrKey) { return await resolveVar(idOrKey); },
  async importComp(key) { return await figma.importComponentByKeyAsync(key); },
  async importVar(key)  { return await figma.variables.importVariableByKeyAsync(key); },
};

// ──────────────────────────────────────────────────────────────────────────

// Ids the bridge gave up waiting for. A running script cannot be killed, so
// cancellation is cooperative: long loops call h.ck() and bail out.
const ABORTED = new Set();
// Bounded — a long session used to keep every aborted id forever.
const ABORTED_MAX = 100;
const ABORTED_TEXT = "aborted: past this exec's timeout — the bridge stopped waiting";

function markAborted(id) {
  ABORTED.add(id);
  if (ABORTED.size > ABORTED_MAX) ABORTED.delete(ABORTED.values().next().value);
}

// What ends a Figma call that never settles. The call a script awaits
// through its `figma` also races this gate, which fails at the deadline or on
// the bridge's `abort`. figma.teamLibrary has gone minutes without an answer,
// and the file stayed blocked (409) until the plugin restarted.
const GATES = new Map();  // exec id -> fail()

function makeGate(id, deadline) {
  let fail;
  const lost = new Promise((_, reject) => { fail = reject; });
  lost.catch(() => {});  // a script that awaited nothing never looks at it
  const timer = deadline === Infinity ? null : setTimeout(fail, Math.max(0, deadline - Date.now()));
  GATES.set(id, fail);
  return {
    race(p, name) {
      // made here, so its stack has the script's line that awaited the call
      const hung = new Error("aborted: figma." + name + "() did not answer before --timeout ran out — " +
        "the script was stopped");
      return Promise.race([p, lost.then(null, () => { throw hung; })]);
    },
    close() { if (timer !== null) clearTimeout(timer); GATES.delete(id); },
  };
}

figma.ui.onmessage = async (msg) => {
  if (msg.type === "need-identity") { postIdentity(); return; }
  if (msg.type === "open-url") {
    // The release page, when an update can't be done here. Only ever the project's GitHub pages.
    if (typeof msg.url === "string" && msg.url.startsWith("https://github.com/artem-levchenko-2/figaro/")) {
      figma.openExternal(msg.url);
    }
    return;
  }
  if (msg.type === "resize") {
    const height = Math.round(Number(msg.height));
    if (height > 0) figma.ui.resize(UI_WIDTH, Math.min(height, 2000));
    return;
  }
  if (msg.type === "select") { await selectLayer(msg.id); return; }
  if (msg.type === "abort") {
    if (msg.id) markAborted(msg.id);
    const fail = GATES.get(msg.id);  // a script stuck in a Figma call ends now
    if (fail) fail();
    return;
  }
  // Undo the last script and hot reload — see below.
  if (msg.type === "undo") { await answerUndo(msg); return; }
  if (msg.type === "reload") { reloadPlugin(msg); return; }
  if (msg.type !== "exec") return;
  const { id, code } = msg;
  // When the bridge gives up on this run. Checked by h.ck() against the
  // sandbox's own clock, because the bridge's `abort` message can't get in
  // while a loop only awaits Figma APIs: those promises settle as microtasks,
  // so the plugin never returns to its message queue until the loop ends.
  const deadline = typeof msg.timeout === "number" ? Date.now() + msg.timeout * 1000 : Infinity;

  const logs = [];
  // print() lines go to the bridge in batches, not one message per line: each
  // message crosses two hops (sandbox -> UI -> WebSocket), and 20k separate
  // lines took ~0.5 s. They are still streamed (flushed every 200 lines or
  // 100 ms), so a script that times out still returns the logs it got to.
  let unsent = [];
  let flushTimer = null;
  const flushLogs = () => {
    if (flushTimer !== null) { clearTimeout(flushTimer); flushTimer = null; }
    if (unsent.length) {
      figma.ui.postMessage({ type: "log", id, lines: unsent });
      unsent = [];
    }
  };
  const print = (...args) => {
    const text = args.map((a) =>
      typeof a === "object" ? JSON.stringify(a, null, 2) : String(a)
    ).join(" ");
    logs.push(text);
    unsent.push(text);
    if (unsent.length >= 200) flushLogs();
    else if (flushTimer === null) flushTimer = setTimeout(flushLogs, 100);
  };

  // Per-exec helper view: h.ck() throws once the bridge has given up on this
  // run (its timeout passed, or it sent `abort`), so chunked sweeps stop
  // instead of mutating under the next caller.
  const h = Object.create(HELPERS);
  h.aborted = () => ABORTED.has(id) || Date.now() > deadline;
  h.ck = () => {
    if (h.aborted()) throw new Error(ABORTED_TEXT);
    return true;
  };

  // Figma's reports about this run's edits are collected into `run`. A
  // writing script becomes one Cmd+Z step; a read-only one is rolled back if
  // it changed anything. A checkpoint is a version in the file's history. A
  // quick run — the CLI's own readers, which change nothing — skips all that
  // and the ~150 ms wait for Figma's report.
  const run = msg.quick === true ? null
    : startRun(id, { agent: msg.agent, readOnly: msg.readOnly === true, code });
  if (run) figma.commitUndo();  // whatever came before is not this run's step
  const checkpoint = run && msg.checkpoint ? await saveCheckpoint(msg.checkpoint) : null;

  CURRENT_PRINT = print;
  let ok = false;
  let result, error;
  const libErrors = { missing: [] };
  const gate = makeGate(id, deadline);
  try {
    const fig = scriptFigma(h.aborted, print, gate);
    const lib = loadLibs(msg.libs, fig, print, h, libErrors);  // --lib files
    const fn = compileScript(code);
    result = await fn(fig, print, h, lib);
    ok = true;
  } catch (e) {
    error = e;
  } finally {
    CURRENT_PRINT = () => {};
    gate.close();
  }
  ABORTED.delete(id);
  const outcome = run ? await finishRun(run) : { fields: {}, error: null };
  if (libErrors.missing.length) outcome.fields.libMissing = libErrors.missing;
  if (checkpoint) outcome.fields.checkpoint = checkpoint;
  flushLogs();
  if (ok && !outcome.error) {
    figma.ui.postMessage(Object.assign(resultMessage(id, result, logs), outcome.fields));
  } else {
    const reply = ok ? { type: "error", id, text: outcome.error, stack: null }
                     : errorMessage(id, error, code);
    if (!ok && outcome.error) reply.text += " — " + outcome.error;
    figma.ui.postMessage(Object.assign(reply, outcome.fields));
  }
};

// ─── exec core ───────────────────────────────────────────────────────────
// What Figma reports about the document while each script runs: so a reply can
// say what the script changed, a read-only script can be rolled back and
// `undo` can tell whether anything else changed since. Figma sends
// `documentchange` in a batch ~100 ms after an edit — but in a background
// window Chromium slows that to about once a minute, which is why ui.html keeps
// the window awake while agents work.

const FLUSH_MS = 150;    // a batch comes ~100 ms after an edit while Figma is awake
const ITEMS_MAX = 30;    // nodes listed by name in a reply
const HISTORY_MAX = 50;
const OUTSIDE_MAX = 200;

const RUNS = new Map();  // id -> run: scripts and undos in flight, until Figma has reported them
const HISTORY = [];      // runs that changed the file, newest last — what `undo` walks back
const OUTSIDE = { local: 0, remote: 0, recent: [] };  // changes made while no run was open

// Variable edits never reach documentchange, and neither does removing a
// component or a component set. A script that may make them
// is kept in HISTORY even when Figma reported nothing, so `undo` stops there
// instead of reverting the wrong step.
const VARIABLE_WRITES = /\b(setValueForMode|createVariable|createVariableCollection|addMode|removeMode|renameMode|setVariableCodeSyntax|removeVariableCodeSyntax)\b|\.remove\(\)/;

function startRun(id, opts) {
  const run = {
    id, agent: opts.agent || null, readOnly: !!opts.readOnly, quiet: false,
    mayWriteVariables: VARIABLE_WRITES.test(opts.code || ""),
    created: new Set(), createdNodes: new Map(), deleted: new Set(), changed: new Set(),
    styles: 0, props: {}, items: new Map(), itemsTotal: 0, remote: 0,
    overlap: RUNS.size > 0, settled: null, late: false,
  };
  for (const other of RUNS.values()) other.overlap = true;
  RUNS.set(id, run);
  return run;
}

function onDocumentChange(event) {
  if (RUNS.size === 0) {
    const now = Date.now();
    for (const c of event.documentChanges) {
      if (c.origin === "REMOTE") OUTSIDE.remote++; else OUTSIDE.local++;
      OUTSIDE.recent.push({ at: now, origin: c.origin, type: c.type, id: c.id, name: changeName(c) });
    }
    if (OUTSIDE.recent.length > OUTSIDE_MAX) OUTSIDE.recent.splice(0, OUTSIDE.recent.length - OUTSIDE_MAX);
    return;
  }
  // Runs that overlap (parallel reads) each get every change: nobody can tell
  // whose it was, and such a run is marked `overlap`.
  for (const run of RUNS.values()) {
    if (!run.quiet) for (const c of event.documentChanges) record(run, c);
    else if (run.seen) for (const c of event.documentChanges) run.seen.add(c.id);  // revertSteps
    if (run.settled) run.settled();
  }
}

function changeName(c) {
  try {
    if (c.node && !c.node.removed) return c.node.name;
    if (c.style) return c.style.name;
  } catch (e) { /* the node went away meanwhile */ }
  return undefined;
}

function record(run, c) {
  if (c.origin === "REMOTE") { run.remote++; return; }
  run.touched = true;  // Figma saw an edit, so the run has its own Cmd+Z step
  const t = c.type;
  if (t === "CREATE") {
    run.created.add(c.id);
    if (c.node) run.createdNodes.set(c.id, c.node);  // to drop it if it is gone at the end
    note(run, c, "+");
  } else if (t === "DELETE") {
    if (dropCreated(run, c.id)) return;  // made and dropped here
    run.deleted.add(c.id);
    note(run, c, "-");
  } else if (t === "PROPERTY_CHANGE") {
    if (run.created.has(c.id)) return;  // a new node's first properties are part of creating it
    run.changed.add(c.id);
    for (const p of c.properties || []) run.props[p] = (run.props[p] || 0) + 1;
    note(run, c, "~", c.properties);
  } else if (t.indexOf("STYLE_") === 0) {
    run.styles++;
    note(run, c, t === "STYLE_CREATE" ? "+" : t === "STYLE_DELETE" ? "-" : "~", c.properties, true);
  }
}

function note(run, c, op, props, style) {
  const item = run.items.get(c.id);
  if (item) {
    if (props) item.props = Array.from(new Set(item.props.concat(props)));
    return;
  }
  run.itemsTotal++;
  if (run.items.size >= ITEMS_MAX) return;
  run.items.set(c.id, {
    id: c.id, op, name: changeName(c),
    type: style ? "STYLE" : (c.node && c.node.type) || undefined,
    props: props ? props.slice() : [],
  });
}

// A layer the script made and then removed is no change. Figma reports the
// removal of the topmost one only, so a layer that went with its new parent is
// found by its `removed` flag at the end.
function dropCreated(run, id) {
  if (!run.created.delete(id)) return false;
  run.createdNodes.delete(id);
  run.items.delete(id);
  run.itemsTotal--;
  return true;
}

function dropRemovedCreated(run) {
  for (const [id, node] of Array.from(run.createdNodes)) {
    let gone = false;
    try { gone = node.removed === true; } catch (e) { gone = true; }
    if (gone) dropCreated(run, id);
  }
}

function ownCount(run) {
  return run.created.size + run.deleted.size + run.changed.size + run.styles;
}

function summarize(run) {
  const own = ownCount(run);
  if (!own && !run.remote && !run.late) return null;
  const props = Object.keys(run.props).sort((a, b) => run.props[b] - run.props[a]);
  const out = {
    created: run.created.size, deleted: run.deleted.size, changed: run.changed.size,
    styles: run.styles,
    props: props.slice(0, 12).reduce((o, p) => { o[p] = run.props[p]; return o; }, {}),
    items: Array.from(run.items.values()),
  };
  if (run.itemsTotal > out.items.length) out.more = run.itemsTotal - out.items.length;
  const top = topCreated(run);
  if (top.list.length) out.top = top.list;
  if (top.more) out.topMore = top.more;
  if (run.remote) out.remote = run.remote;
  if (run.late) out.late = true;
  if (run.overlap) out.shared = true;
  return out;
}

// The new layers that are not inside other new ones — what a report links
// to. A component set comes last of all it holds, so the first new layer is
// usually a text deep inside it.
const TOP_MAX = 5;

function topCreated(run) {
  const list = [];
  let more = 0;
  for (const id of run.created) {
    const node = run.createdNodes.get(id);
    let parent = null;
    try { parent = node ? node.parent : null; } catch (e) { parent = null; }
    if (parent && run.created.has(parent.id)) continue;
    if (list.length >= TOP_MAX) { more++; continue; }
    let name, type;
    try { name = node ? node.name : undefined; type = node ? node.type : undefined; } catch (e) { /* gone */ }
    list.push({ id, name, type });
  }
  return { list, more };
}

// Wait for Figma's report on this run: the first batch after the script ended
// holds every edit it made, and no batch within FLUSH_MS means there were none.
// A timer that fires far too late means Figma was throttled after all, and the
// report may still be on its way.
function settle(run) {
  return new Promise((resolve) => {
    const t0 = Date.now();
    let timer = null;
    const done = () => { clearTimeout(timer); run.settled = null; resolve(); };
    run.settled = done;
    timer = setTimeout(() => {
      if (Date.now() - t0 > FLUSH_MS * 4) run.late = true;
      done();
    }, FLUSH_MS);
  });
}

async function finishRun(run) {
  if (!run.readOnly) figma.commitUndo();  // the whole script is one Cmd+Z step
  await settle(run);
  dropRemovedCreated(run);
  const own = ownCount(run);
  const out = { fields: {}, error: null };
  const changes = summarize(run);
  if (changes) out.fields.changes = changes;
  if (run.readOnly && own === 0 && !run.touched && run.mayWriteVariables) {
    out.fields.notice = "read-only: the code may have changed variables or removed components (remove()) — " +
      "Figma does not report that, so -R cannot roll it back; check the file";
  }
  if (run.readOnly && own > 0) {
    if (run.overlap) {
      out.fields.notice = "read-only: " + own + " changed in the file during the script — other " +
        "scripts ran at the same time, so whose is unknown; nothing rolled back";
    } else {
      // Commit first: an undo with edits still pending also reverts the step
      // before them.
      figma.commitUndo();
      const trace = traceOf(run);
      run.quiet = true;  // the reversal is not news
      const back = await revertSteps(run, trace);
      out.fields.rolledBack = true;
      out.error = "read-only: the script changed " + own + " — rolled back" + leftNote(back, trace);
    }
  }
  RUNS.delete(run.id);
  if (!run.readOnly && (own > 0 || run.touched || run.mayWriteVariables)) {
    HISTORY.push({
      id: run.id, agent: run.agent, at: Date.now(), own, touched: !!run.touched,
      brief: changes ? briefChanges(changes) : run.touched ? "nothing: layers made and removed" : "no visible changes",
      outsideAtEnd: OUTSIDE.local, trace: traceOf(run),
    });
    if (HISTORY.length > HISTORY_MAX) HISTORY.shift();
  }
  return out;
}

function briefChanges(c) {
  const parts = [];
  if (c.created) parts.push("+" + c.created);
  if (c.deleted) parts.push("−" + c.deleted);
  if (c.changed) parts.push("~" + c.changed);
  if (c.styles) parts.push("styles " + c.styles);
  return parts.join(" ") || "nothing";
}

function agoText(at) {
  const s = Math.round((Date.now() - at) / 1000);
  return s < 60 ? s + " s ago" : Math.round(s / 60) + " min ago";
}

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// ─── the script ──────────────────────────────────────────────────────────

// The script's line 1 starts on its own line, so a trailing `// comment` no
// longer swallows the closing `})();`. `lib` is what the --lib files define.
const SCRIPT_PARAMS = ["figma", "print", "h", "lib"];

function compileScript(code) {
  return new Function(...SCRIPT_PARAMS, "return (async () => {\n" + code + "\n})();");
}

// A script line in a stack trace: "<input>:L:C" in Figma's sandbox (QuickJS),
// "<anonymous>:L:C" under Node. Where line 1 lands is measured once, for
// whichever engine runs us.
const STACK_POS = /<(?:input|anonymous)>:(\d+):(\d+)/g;
const SCRIPT_LINE_OFFSET = (() => {
  try {
    new Function(...SCRIPT_PARAMS, "return (() => {\nthrow new Error('probe')\n})();")();
  } catch (e) {
    STACK_POS.lastIndex = 0;
    const m = STACK_POS.exec(String(e && e.stack));
    STACK_POS.lastIndex = 0;
    if (m) return Number(m[1]) - 1;
  }
  return null;
})();

// Each script position in the stack, as the script's own line number.
function scriptPositions(stack, lineCount) {
  const out = [];
  if (SCRIPT_LINE_OFFSET === null || !stack) return out;
  STACK_POS.lastIndex = 0;
  let m;
  while ((m = STACK_POS.exec(String(stack)))) {
    const line = Number(m[1]) - SCRIPT_LINE_OFFSET;
    if (line >= 1 && line <= lineCount) out.push({ at: m[0], line, col: Number(m[2]) });
  }
  STACK_POS.lastIndex = 0;
  return out;
}

function errorText(e) {
  if (e && e.message) return String(e.message);
  if (e && typeof e === "object") {
    return "an error with no text — that is how Figma ends getNodeByIdAsync for an id from another " +
      "file or a deleted node (after ~10 s)";
  }
  return String(e);
}

function errorMessage(id, e, code) {
  const lines = String(code).split("\n");
  let stack = (e && e.stack) || null;
  const where = scriptPositions(stack, lines.length);
  const msg = { type: "error", id, text: errorText(e), stack };
  if (where.length) {
    msg.line = where[0].line;
    msg.col = where[0].col;
    msg.source = lines[where[0].line - 1].trim().slice(0, 200);
    for (const w of where) stack = stack.replace(w.at, "script:" + w.line + ":" + w.col);
    msg.stack = stack;
  }
  // Where in a --lib file it failed, if it did there.
  const inLib = libPositions(stack);
  if (inLib.length) {
    const first = inLib[0];
    msg.lib = { name: first.name, line: first.line, source: first.source };
    for (const w of inLib) stack = stack.replace(w.at, w.name + ":" + w.line + ":" + w.col);
    msg.stack = stack;
  }
  return msg;
}

// Fonts loaded in this run of the plugin. Figma keeps a loaded font loaded, and
// in a background window every real loadFontAsync in a loop can stall up to a
// minute (the 7th call once took 49 s), so a known font returns at once.
const FONTS = new Map();  // "family|style" -> promise of its load

function loadFont(font) {
  if (!font || typeof font !== "object") return figma.loadFontAsync(font);
  const key = font.family + "|" + font.style;
  let p = FONTS.get(key);
  if (!p) {
    p = figma.loadFontAsync(font);
    FONTS.set(key, p);
    p.catch(() => FONTS.delete(key));
  }
  return p;
}

// The node, null when this file has no such id, or undefined when there is no
// sync lookup (dynamic-page) and only Figma's async one can answer.
function nodeHere(id) {
  try {
    return figma.getNodeById(String(id));
  } catch (e) {
    return undefined;
  }
}

// A view of `target` for scripts. `figma` itself is frozen, so a Proxy over it
// cannot change a thing (QuickJS: "proxy: inconsistent get"); this one forwards
// to it from an empty object. Every *Async call goes through `guard`: it checks
// the deadline first, so a loop over Figma calls ends once the bridge gave up,
// even without h.ck(), and a call that never settles ends at the deadline.
function forward(target, overrides, guard, path, guarded) {
  const bound = new Map();
  return new Proxy({}, {
    get(_, key) {
      if (Object.prototype.hasOwnProperty.call(overrides, key)) return overrides[key];
      const value = target[key];
      if (typeof value !== "function") return value;
      let fn = bound.get(key);
      if (!fn) {
        fn = typeof key === "string" && /Async$/.test(key)
          ? function () { const args = arguments; return guard(() => value.apply(target, args), path + key); }
          : value.bind(target);
        bound.set(key, fn);
      }
      return fn;
    },
    set(_, key, value) {
      if (guarded && guarded.indexOf(key) !== -1) {
        throw new Error("figma." + path + String(key) + " keeps the plugin connected to figaro — leave it alone");
      }
      target[key] = value;
      return true;
    },
    has(_, key) { return key in target; },
  });
}

// What a script gets as `figma`.
function scriptFigma(isAborted, print, gate) {
  const guard = (start, name) => {
    if (isAborted()) throw new Error(ABORTED_TEXT);
    return gate ? gate.race(start(), name) : start();
  };
  const cut = (name) => () => {
    throw new Error("figma." + name + "() is turned off: the plugin keeps the connection to figaro");
  };
  let library = null;
  return forward(figma, {
    getNodeByIdAsync(id) {
      return guard(() => {
        const found = nodeHere(id);  // as in h.node
        if (found === null) {
          print("figma.getNodeByIdAsync(" + JSON.stringify(id) + "): \"" + figma.root.name +
            "\" has no such node — it is from another file or was deleted");
          return Promise.resolve(null);
        }
        return found ? Promise.resolve(found) : figma.getNodeByIdAsync(id);
      }, "getNodeByIdAsync");
    },
    loadFontAsync(font) { return guard(() => loadFont(font), "loadFontAsync"); },
    closePlugin: cut("closePlugin"),
    showUI: cut("showUI"),
    ui: forward(figma.ui, { close: cut("ui.close") }, guard, "ui.", ["onmessage"]),
    variables: forward(figma.variables, {}, guard, "variables."),
    // Lazy — reading figma.teamLibrary needs the manifest's permission
    get teamLibrary() { return library || (library = forward(figma.teamLibrary, {}, guard, "teamLibrary.")); },
  }, guard, "");
}

async function saveCheckpoint(title) {
  const t0 = Date.now();
  try {
    const v = await figma.saveVersionHistoryAsync(String(title).slice(0, 140));
    return { id: v && v.id, title: String(title), ms: Date.now() - t0 };
  } catch (e) {
    return { error: errorText(e), title: String(title) };
  }
}

// ─── undo ────────────────────────────────────────────────────────────────
// Each writing script is one step in Figma's undo stack (commitUndo above), so
// triggerUndo reverts the newest one — whoever made it. It is safe only when
// that step is the script's: it changed something Figma reported, and nothing
// else changed after it. An undo with nothing to revert would revert the step
// before instead.

async function answerUndo(msg) {
  try {
    const value = await undoLast(msg);
    figma.ui.postMessage({ type: "result", id: msg.id, value });
  } catch (e) {
    figma.ui.postMessage({ type: "error", id: msg.id, text: errorText(e), stack: null });
  }
}

async function undoLast(msg) {
  await wait(FLUSH_MS);  // edits made just before this request are still on their way
  const top = HISTORY[HISTORY.length - 1];
  if (!top) {
    throw new Error("undo: nothing to undo — scripts in this run of the plugin changed nothing, " +
      "or they are undone already");
  }
  const who = top.agent || "a script without -A";
  const when = agoText(top.at);
  if (top.own === 0 && !top.touched) {
    throw new Error("undo: Figma showed no changes for the last script (" + who + ", " + when + "), " +
      "but it may have changed variables or removed components — Figma does not report that. " +
      "Undo it by hand: Cmd+Z in Figma");
  }
  const after = OUTSIDE.local - top.outsideAtEnd;
  if (after > 0) {
    const names = OUTSIDE.recent.slice(-after).map((c) => c.name).filter(Boolean);
    const list = Array.from(new Set(names)).slice(0, 5).join(", ");
    throw new Error("undo: after the script of " + who + " (" + when + ") " + after + " more changed in " +
      "the file" + (list ? " (" + list + ")" : "") + " — in Figma itself, not by a script. Undo would " +
      "revert that instead, so nothing is done. Undo by hand: Cmd+Z in Figma, or a version in " +
      "File → Version history");
  }
  if (msg.agent && top.agent && msg.agent !== top.agent && !msg.force) {
    throw new Error("undo: the last changes are by " + top.agent + ", not " + msg.agent +
      ". To undo them anyway, run it again with --force");
  }
  const run = startRun(msg.id, { agent: msg.agent });
  run.quiet = true;  // the reversal is not news
  const trace = top.trace || traceOf(run);
  const back = await revertSteps(run, trace);
  RUNS.delete(run.id);
  HISTORY.pop();
  const value = { undone: { agent: top.agent, ago: when, changes: top.brief }, left: HISTORY.length };
  if (back.steps > 1) value.steps = back.steps;
  const note = leftNote(back, trace);
  if (note) value.warning = note.replace(/^; /, "");
  return value;
}

// Figma closes an undo step of its own inside some calls — each
// addComponentProperty does — so one script can be several
// Cmd+Z steps. Reverting goes back step by step while the script's traces are
// still there: a layer it made exists, or one it removed is missing. Only its
// own steps can hold those, and each step reverted must touch its layers, so
// it does not go past the step the script started from.
const UNDO_STEPS_MAX = 20;
const TRACE_MAX = 500;

function traceOf(run) {
  return {
    created: Array.from(run.created).slice(0, TRACE_MAX),
    deleted: Array.from(run.deleted).slice(0, TRACE_MAX),
    changed: Array.from(run.changed).slice(0, TRACE_MAX),
    // properties of components that were there before: no layer to check them by
    props: (run.props.componentPropertyDefinitions || 0) > 0,
  };
}

function exists(id) {
  try {
    const n = figma.getNodeById(id);
    return n !== null && n.removed !== true;
  } catch (e) {
    return null;  // no sync lookup (dynamic-page): no evidence either way
  }
}

function traceLeft(trace) {
  let left = 0;
  for (const id of trace.created) if (exists(id) === true) left++;
  for (const id of trace.deleted) if (exists(id) === false) left++;
  return left;
}

async function revertSteps(run, trace) {
  const mine = new Set(trace.created.concat(trace.deleted, trace.changed));
  let steps = 0;
  for (;;) {
    run.seen = new Set();
    figma.triggerUndo();
    steps++;
    await settle(run);
    const left = traceLeft(trace);
    if (left === 0 || steps >= UNDO_STEPS_MAX) return { steps, left };
    if (!Array.from(run.seen).some((id) => mine.has(id))) return { steps, left };
  }
}

function leftNote(back, trace) {
  if (back.left > 0) {
    return "; not everything was undone: " + back.left + " layers of this script are left — look at " +
      "the file and remove the rest by hand (Cmd+Z in Figma, or a version in File → Version history)";
  }
  if (trace.props) {
    return "; the script changed properties of existing components, and Figma splits such changes " +
      "into several Cmd+Z steps — check with inspect that all of it is undone";
  }
  return "";
}

// ─── links ───────────────────────────────────────────────────────────────
// A Figma link names a file by its key and a layer by node-id ("1-11" there,
// "1:11" here). h.node and h.resolve take a link — or a bare "1-11" — and
// refuse one into another file; h.link makes one for a report. figma_links.py
// does the same on the bridge's side.

const LINK = /^(?:https?:\/\/)?(?:[\w-]+\.)?figma\.com\/(?:design|file|proto|board|slides|deck|make|site)\/([A-Za-z0-9]{10,})(?:\/branch\/([A-Za-z0-9]{10,}))?[^?#]*(?:\?([^#]*))?/i;
const LINK_NODE = /^I?\d+[:-]\d+(?:;\d+[:-]\d+)*$/;

function decoded(s) {
  try { return decodeURIComponent(s); } catch (e) { return s; }
}

// "1-11", "I1-2;3-4", "1%3A11" → "1:11", "I1:2;3:4"; null if not a node id.
function urlNodeId(value) {
  const s = decoded(String(value).trim());
  return LINK_NODE.test(s) ? s.replace(/(\d+)-(\d+)/g, "$1:$2") : null;
}

function parseLink(text) {
  const m = LINK.exec(String(text).trim());
  if (!m) return null;
  const q = /(?:^|&)node-id=([^&]*)/.exec(m[3] || "");
  return { key: m[2] || m[1], node: q ? urlNodeId(q[1]) : null };
}

function fileKey() {
  try { return figma.fileKey || null; } catch (e) { return null; }
}

// A node id from an id, a link-form id or a link into this file.
function linkNodeId(value) {
  if (typeof value !== "string") return value;
  const link = parseLink(value);
  if (!link) return urlNodeId(value) || value;
  const here = fileKey();
  if (here && link.key !== here) {
    throw new Error("the link points to another file (" + link.key + "), but the script runs in \"" +
      figma.root.name + "\" — run it with -T <link>");
  }
  if (!link.node) throw new Error("the link has no node-id: " + value);
  return link.node;
}

function nodeLink(id) {
  const key = fileKey();
  if (!key || !id) return undefined;
  const slug = String(figma.root.name || "").replace(/[^A-Za-z0-9]+/g, "-") || "Untitled";
  return "https://www.figma.com/design/" + key + "/" + slug + "?node-id=" +
    encodeURIComponent(String(id).replace(/:/g, "-"));
}

// ─── inspect ─────────────────────────────────────────────────────────────
// What a design-to-code worker needs from a subtree, as data: layout, sizes,
// paddings, radii, paints, effects, text with its styled segments, and the
// names AND keys of the components, styles and variables it uses — a key is
// what import*ByKeyAsync takes. Hidden layers are counted, not listed. Scripts
// read these keys from `figaro inspect --json`, so keep them stable;
// inspect_text.py prints the same data as text.

const SEGMENTS_MAX = 60;

async function inspectTree(root, opts, ctx) {
  opts = opts || {};
  const maxDepth = opts.depth == null ? 8 : Number(opts.depth);
  const maxNodes = opts.maxNodes == null ? 2000 : Number(opts.maxNodes);
  const withHidden = opts.hidden === true;
  const out = {
    file: figma.root.name, fileKey: fileKey() || undefined, url: nodeLink(root.id),
    node: null, nodes: 0, components: {}, styles: {}, variables: {}, collections: {},
  };
  const varNames = new Map();
  const styleNames = new Map();
  const collections = new Map();
  const r = (v) => Math.round(v * 100) / 100;
  const hex = (c) => "#" + [c.r, c.g, c.b].map((x) => Math.round(x * 255).toString(16).padStart(2, "0")).join("");
  const alpha = (c) => (c.a !== undefined && c.a !== 1 ? "/" + r(c.a) : "");
  const safe = (fn) => { try { return fn(); } catch (e) { return undefined; } };
  const mixed = (v) => v === figma.mixed;

  const collection = async (id) => {
    if (!collections.has(id)) {
      let c = null;
      try { c = await figma.variables.getVariableCollectionByIdAsync(id); } catch (e) { /* gone */ }
      if (c && !out.collections[c.name]) {
        out.collections[c.name] = { key: c.key, remote: c.remote, modes: c.modes.map((m) => m.name) };
      }
      collections.set(id, c);
    }
    return collections.get(id);
  };
  const variable = async (alias) => {
    const id = alias && alias.id;
    if (!id) return undefined;
    if (!varNames.has(id)) {
      let label;
      try {
        const v = await figma.variables.getVariableByIdAsync(id);
        if (v) {
          const c = await collection(v.variableCollectionId);
          label = v.name;
          const same = out.variables[label];
          if (same && same.key !== v.key) label = v.name + " (" + (c ? c.name : "?") + ")";
          out.variables[label] = { id: v.id, key: v.key, type: v.resolvedType,
                                   collection: c ? c.name : undefined, remote: v.remote };
        }
      } catch (e) { /* a library that is gone */ }
      varNames.set(id, label);
    }
    return varNames.get(id);
  };
  const style = async (id) => {
    if (typeof id !== "string" || !id) return undefined;
    if (!styleNames.has(id)) {
      let label;
      try {
        const st = await figma.getStyleByIdAsync(id);
        if (st) {
          label = st.name;
          out.styles[label] = { id: st.id, key: st.key, type: st.type, remote: st.remote };
        }
      } catch (e) { /* gone */ }
      styleNames.set(id, label);
    }
    return styleNames.get(id);
  };
  const paint = async (p) => {
    if (!p || p.visible === false) return null;
    const o = { type: p.type };
    if (p.type === "SOLID") o.color = hex(p.color);
    if (p.opacity !== undefined && p.opacity !== 1) o.opacity = r(p.opacity);
    if (p.type.indexOf("GRADIENT") === 0) {
      o.stops = p.gradientStops.map((s) => hex(s.color) + "@" + r(s.position) + alpha(s.color));
    }
    if (p.type === "IMAGE") o.scaleMode = p.scaleMode;
    if (p.boundVariables && p.boundVariables.color) o.var = await variable(p.boundVariables.color);
    return o;
  };
  const paints = async (list) => {
    if (!Array.isArray(list)) return mixed(list) ? "mixed" : undefined;
    const got = [];
    for (const p of list) { const x = await paint(p); if (x) got.push(x); }
    return got.length ? got : undefined;
  };
  const NOT_VARS = { fills: 1, strokes: 1, effects: 1, layoutGrids: 1, componentProperties: 1, textRangeFills: 1 };
  const boundVars = async (n) => {
    const bv = safe(() => n.boundVariables) || {};
    const o = {};
    for (const k of Object.keys(bv)) {
      if (NOT_VARS[k]) continue;
      const v = bv[k];
      const name = await variable(Array.isArray(v) ? v[0] : v);
      if (name) o[k] = name;
    }
    return Object.keys(o).length ? o : undefined;
  };
  const modes = async (n) => {
    const em = safe(() => n.explicitVariableModes);
    if (!em) return undefined;
    const o = {};
    for (const cid of Object.keys(em)) {
      const c = await collection(cid);
      if (!c) continue;
      const m = c.modes.filter((x) => x.modeId === em[cid])[0];
      o[c.name] = m ? m.name : em[cid];
    }
    return Object.keys(o).length ? o : undefined;
  };
  const swapName = (id) => {
    const c = safe(() => figma.getNodeById(id));
    return c ? c.name : id;
  };
  const component = async (n) => {
    let mc = null;
    try { mc = await n.getMainComponentAsync(); } catch (e) { /* its library is gone */ }
    if (!mc) return undefined;
    const set = mc.parent && mc.parent.type === "COMPONENT_SET" ? mc.parent : null;
    const label = set ? set.name + " / " + mc.name : mc.name;
    if (!out.components[label]) {
      out.components[label] = { id: mc.id, key: mc.key, remote: mc.remote };
      if (set) Object.assign(out.components[label], { set: set.name, setKey: set.key });
    }
    return label;
  };
  const effects = async (list) => {
    if (!Array.isArray(list) || !list.length) return undefined;
    const got = [];
    for (const e of list) {
      if (e.visible === false) continue;
      const x = { type: e.type };
      if (e.radius) x.radius = e.radius;
      if (e.offset) x.offset = [e.offset.x, e.offset.y];
      if (e.spread) x.spread = e.spread;
      if (e.color) x.color = hex(e.color) + alpha(e.color);
      if (e.boundVariables) {
        const v = {};
        for (const k of Object.keys(e.boundVariables)) {
          const name = await variable(e.boundVariables[k]);
          if (name) v[k] = name;
        }
        if (Object.keys(v).length) x.vars = v;
      }
      got.push(x);
    }
    return got.length ? got : undefined;
  };
  const text = async (n, o) => {
    const chars = n.characters;
    o.text = chars.length > 400 ? chars.slice(0, 400) + "…" : chars;
    const st = await style(n.textStyleId);
    if (st) o.textStyle = st;
    const lh = n.lineHeight, ls = n.letterSpacing;
    o.font = {
      family: mixed(n.fontName) ? "mixed" : n.fontName.family + " " + n.fontName.style,
      size: mixed(n.fontSize) ? "mixed" : n.fontSize,
      lineHeight: mixed(lh) ? "mixed" : lh.unit === "AUTO" ? "auto" : r(lh.value) + (lh.unit === "PERCENT" ? "%" : "px"),
      letterSpacing: mixed(ls) ? "mixed" : r(ls.value) + (ls.unit === "PERCENT" ? "%" : "px"),
      align: n.textAlignHorizontal,
    };
    if (n.textCase && n.textCase !== "ORIGINAL" && !mixed(n.textCase)) o.font.case = n.textCase;
    if (n.textAutoResize) o.autoResize = n.textAutoResize;
    if (n.hasMissingFont) o.missingFont = true;
    const link = safe(() => n.hyperlink);
    if (link && !mixed(link)) o.link = link.type === "URL" ? link.value : "node:" + link.value;
    const segs = safe(() => n.getStyledTextSegments(["fontName", "fontSize", "textDecoration",
      "textCase", "fills", "textStyleId", "hyperlink", "listOptions"]));
    if (!segs || segs.length < 2) return;
    o.segments = [];
    for (const s of segs.slice(0, SEGMENTS_MAX)) {
      const x = { text: s.characters.length > 120 ? s.characters.slice(0, 120) + "…" : s.characters,
                  font: s.fontName.family + " " + s.fontName.style, size: s.fontSize };
      const sst = await style(s.textStyleId);
      if (sst) x.style = sst;
      const f = await paints(s.fills);
      if (f) x.fills = f;
      if (s.textDecoration && s.textDecoration !== "NONE") x.decoration = s.textDecoration;
      if (s.textCase && s.textCase !== "ORIGINAL") x.case = s.textCase;
      if (s.hyperlink) x.link = s.hyperlink.type === "URL" ? s.hyperlink.value : "node:" + s.hyperlink.value;
      if (s.listOptions && s.listOptions.type && s.listOptions.type !== "NONE") x.list = s.listOptions.type;
      o.segments.push(x);
    }
    if (segs.length > SEGMENTS_MAX) o.segmentsCut = segs.length - SEGMENTS_MAX;
  };

  const walk = async (n, level) => {
    if (ctx && typeof ctx.aborted === "function" && ctx.aborted()) throw new Error(ABORTED_TEXT);
    out.nodes++;
    const o = { id: n.id, name: n.name, type: n.type };
    if ("width" in n) { o.w = r(n.width); o.h = r(n.height); }
    if (level > 0 && "x" in n) { o.x = r(n.x); o.y = r(n.y); }
    if (n.visible === false) o.visible = false;
    if (n.layoutMode && n.layoutMode !== "NONE") {
      const l = { mode: n.layoutMode };
      if (n.layoutMode === "GRID") {
        Object.assign(l, { rows: n.gridRowCount, cols: n.gridColumnCount,
                           rowGap: n.gridRowGap, colGap: n.gridColumnGap });
      } else {
        Object.assign(l, { gap: n.itemSpacing, main: n.primaryAxisAlignItems,
                           cross: n.counterAxisAlignItems });
        if (n.layoutWrap === "WRAP") { l.wrap = true; l.crossGap = n.counterAxisSpacing; }
      }
      l.pad = [n.paddingTop, n.paddingRight, n.paddingBottom, n.paddingLeft];
      o.layout = l;
    }
    const sh = safe(() => n.layoutSizingHorizontal), sv = safe(() => n.layoutSizingVertical);
    if (sh && sv) o.sizing = sh + "/" + sv;
    if (n.layoutPositioning === "ABSOLUTE") o.absolute = true;
    if ("minWidth" in n) {
      const mm = [n.minWidth, n.maxWidth, n.minHeight, n.maxHeight];
      if (mm.some((x) => x != null)) o.minmax = mm;
    }
    if ("cornerRadius" in n) {
      if (mixed(n.cornerRadius)) {
        o.radius = [n.topLeftRadius, n.topRightRadius, n.bottomRightRadius, n.bottomLeftRadius];
      } else if (n.cornerRadius) o.radius = n.cornerRadius;
    }
    if (n.clipsContent) o.clip = true;
    if ("opacity" in n && n.opacity !== 1) o.opacity = r(n.opacity);
    const fills = await paints(safe(() => n.fills));
    if (fills) o.fills = fills;
    const strokes = await paints(safe(() => n.strokes));
    if (strokes) {
      o.strokes = strokes;
      o.strokeWeight = mixed(n.strokeWeight)
        ? [n.strokeTopWeight, n.strokeRightWeight, n.strokeBottomWeight, n.strokeLeftWeight] : n.strokeWeight;
      o.strokeAlign = n.strokeAlign;
    }
    const fx = await effects(safe(() => n.effects));
    if (fx) o.effects = fx;
    const styles = {};
    for (const pair of [["fill", "fillStyleId"], ["stroke", "strokeStyleId"],
                        ["effect", "effectStyleId"], ["grid", "gridStyleId"]]) {
      const name = await style(safe(() => n[pair[1]]));
      if (name) styles[pair[0]] = name;
    }
    if (Object.keys(styles).length) o.styles = styles;
    const bound = await boundVars(n);
    if (bound) o.vars = bound;
    const md = await modes(n);
    if (md) o.modes = md;
    if (n.type === "TEXT") await text(n, o);
    // Which component property drives this layer — "characters" ← "Label#12:0"
    const refs = safe(() => n.componentPropertyReferences);
    if (refs && Object.keys(refs).length) o.refs = Object.assign({}, refs);
    if (n.type === "INSTANCE") {
      const c = await component(n);
      if (c) o.component = c;
      const props = safe(() => n.componentProperties) || {};
      const p = {};
      for (const k of Object.keys(props)) {
        p[k] = props[k].type === "INSTANCE_SWAP" ? swapName(props[k].value) : props[k].value;
      }
      if (Object.keys(p).length) o.props = p;
    }
    if (n.type === "COMPONENT" || n.type === "COMPONENT_SET") {
      o.key = n.key;
      if (n.remote) o.remote = true;
      const defs = safe(() => n.componentPropertyDefinitions);  // a variant has none: its set does
      if (defs && Object.keys(defs).length) {
        o.propDefs = {};
        for (const k of Object.keys(defs)) {
          const d = defs[k];
          const x = { type: d.type,
                      default: d.type === "INSTANCE_SWAP" ? swapName(d.defaultValue) : d.defaultValue };
          if (d.variantOptions) x.options = d.variantOptions;
          if (d.preferredValues && d.preferredValues.length) x.preferred = d.preferredValues.map((v) => v.key);
          o.propDefs[k] = x;
        }
      }
      if (n.description) o.description = n.description.slice(0, 300);
    }
    if ("children" in n && n.children.length) {
      if (level >= maxDepth) o.childrenCut = n.children.length;
      else {
        const kids = [];
        let hidden = 0, cut = 0;
        for (const c of n.children) {
          if (c.visible === false && !withHidden) { hidden++; continue; }
          if (out.nodes >= maxNodes) { cut++; continue; }
          kids.push(await walk(c, level + 1));
        }
        if (kids.length) o.children = kids;
        if (hidden) o.hiddenKids = hidden;
        if (cut) { o.childrenCut = cut; out.cut = true; }
      }
    }
    return o;
  };
  out.node = await walk(root, 0);
  return out;
}

// ─── shot ────────────────────────────────────────────────────────────────
// A picture of a layer for the agent to look at: the CLI saves it into a file
// and cuts a tall one into parts (cli_extras.py), because a model sees an image
// shrunk to about 1.15 megapixels. Small layers come at 2×, wide ones at most
// SHOT_MAX_W pixels wide. Exporting changes nothing in the file.

const SHOT_MAX_W = 1600;

function shotScale(node) {
  const w = Math.max(1, node.width || 1);
  if (w * 2 <= SHOT_MAX_W) return 2;
  if (w <= SHOT_MAX_W) return 1;
  return Math.floor((SHOT_MAX_W / w) * 100) / 100;
}

async function shotOf(node, opts) {
  if (!node || typeof node.exportAsync !== "function") {
    throw new Error("h.shot: " + (node ? node.type + " \"" + node.name + "\"" : "no node") +
      " — it cannot be exported");
  }
  opts = opts || {};
  const format = String(opts.format || "PNG").toUpperCase().replace("JPEG", "JPG");
  let settings;
  let scale;
  if (format === "SVG") {
    settings = { format: "SVG", svgOutlineText: opts.outlineText !== false, svgIdAttribute: opts.ids === true };
  } else if (format === "PDF") {
    settings = { format: "PDF" };
  } else if (format === "PNG" || format === "JPG") {
    if (opts.width) {
      settings = { format, constraint: { type: "WIDTH", value: Number(opts.width) } };
    } else {
      scale = opts.scale ? Number(opts.scale) : shotScale(node);
      settings = { format, constraint: { type: "SCALE", value: scale } };
    }
  } else {
    throw new Error("h.shot: the format is png, jpg, svg or pdf, not " + JSON.stringify(opts.format));
  }
  const bytes = await node.exportAsync(settings);
  return {
    kind: "shot", id: node.id, name: node.name, type: node.type,
    file: figma.root.name, fileKey: fileKey() || undefined, url: nodeLink(node.id),
    format, scale, width: opts.width ? Number(opts.width) : undefined,
    w: Math.round(node.width), h: Math.round(node.height),
    size: bytes.length, data: figma.base64Encode(bytes),
  };
}

// ─── fonts ───────────────────────────────────────────────────────────────
// A text with several fonts has `figma.mixed` for fontName, so its fonts are
// read per range. loadFonts loads them all at once and reports the ones this
// computer has not got instead of failing on the first; for Font Awesome icon
// fonts it names the families that are installed, since the fix for a missing
// one is to change the font and keep the glyph name.

function fontLabel(f) {
  return f.family + " " + f.style;
}

function fontsIn(node) {
  const texts = node.type === "TEXT" ? [node]
    : typeof node.findAllWithCriteria === "function" ? node.findAllWithCriteria({ types: ["TEXT"] })
    : typeof node.findAll === "function" ? node.findAll((n) => n.type === "TEXT") : [];
  const seen = new Map();
  for (const t of texts) {
    const list = t.fontName === figma.mixed ? t.getRangeAllFontNames(0, t.characters.length) : [t.fontName];
    for (const f of list) seen.set(fontLabel(f), f);
  }
  return Array.from(seen.values());
}

async function loadFonts(target) {
  const list = Array.isArray(target) ? target
    : target && typeof target === "object" && "family" in target ? [target]
    : target && typeof target === "object" ? fontsIn(target) : [];
  const settled = await Promise.all(list.map((f) => loadFont(f).then(() => null, () => f)));
  const missing = settled.filter(Boolean);
  const out = { loaded: list.length - missing.length, missing: missing.map(fontLabel) };
  if (missing.length) {
    const fa = missing.some((f) => /font awesome/i.test(f.family)) ? await faAdvice() : "";
    out.note = "fonts not installed on this computer: " + out.missing.join(", ") + fa;
    CURRENT_PRINT(out.note);
  }
  return out;
}

let FA_FONTS = null;  // promise of {family: [styles]} for the installed Font Awesome fonts

function faFamilies() {
  if (!FA_FONTS) {
    FA_FONTS = figma.listAvailableFontsAsync().then((all) => {
      const fam = {};
      for (const f of all) {
        const n = f.fontName;
        if (/font awesome/i.test(n.family)) (fam[n.family] = fam[n.family] || []).push(n.style);
      }
      return fam;
    });
    FA_FONTS.catch(() => { FA_FONTS = null; });
  }
  return FA_FONTS;
}

async function faInfo() {
  const families = await faFamilies();
  const names = Object.keys(families);
  const version = (n) => Number((/\d+/.exec(n) || [0])[0]);
  const pro = names.filter((n) => /^Font Awesome \d+ Pro$/i.test(n)).sort((a, b) => version(b) - version(a));
  const kit = names.filter((n) => /^Font Awesome Kit\b/i.test(n) && !/duotone/i.test(n));
  return { pro: pro[0] || null, kit: kit[0] || null, families };
}

async function faAdvice() {
  try {
    const fa = await faInfo();
    const parts = [];
    if (fa.pro) parts.push(fa.pro + " (" + fa.families[fa.pro].join(", ") + ")");
    if (fa.kit) parts.push(fa.kit + " — custom icons");
    return parts.length ? "; Font Awesome installed here: " + parts.join("; ") +
      " — change the font, keep the glyph name (h.fa())" : "; no Font Awesome installed here";
  } catch (e) {
    return "";
  }
}

// ─── auto-layout ─────────────────────────────────────────────────────────
// Sizing in the order Figma needs, so nothing is silently undone: FILL only on
// a child of an auto-layout frame, HUG only on an auto-layout frame or a text,
// and a new size keeps the other axis as it was — resize() makes both FIXED.
// A text's sizing needs its fonts, so these load them.

const SIZING_AXES = { x: ["layoutSizingHorizontal"], y: ["layoutSizingVertical"],
                      xy: ["layoutSizingHorizontal", "layoutSizingVertical"] };

function sizingProps(axis, fallback, who) {
  const a = String(axis == null ? fallback : axis).toLowerCase();
  const alias = { both: "xy", h: "x", horizontal: "x", width: "x", v: "y", vertical: "y", height: "y" };
  const props = SIZING_AXES[alias[a] || a];
  if (!props) throw new Error(who + ": the axis is 'x', 'y' or 'xy', not " + JSON.stringify(axis));
  return props;
}

function isAutoLayout(n) {
  return !!n && typeof n.layoutMode === "string" && n.layoutMode !== "NONE";
}

function nodeLabel(n) {
  return n ? n.type + " \"" + n.name + "\"" : "nothing";
}

async function textReady(node, who) {
  if (!node || node.type !== "TEXT") return;
  const got = await loadFonts(node);
  if (got.missing.length) throw new Error(who + ": \"" + node.name + "\" — " + got.note);
}

// ─── agent libraries (--lib) ─────────────────────────────────────────────
// `figaro exec --lib my.js` sends a file's code with its hash the first time
// and only the hash after that (bridge_exec.py). The code is compiled once and run
// again for every script, so its functions get that script's `figma`, `print`
// and `h`. Top-level functions, classes and consts become `lib.<name>`, and so
// does whatever `module.exports` holds; a later lib sees the earlier ones as
// `lib`. A lib's lines sit at slot × LIB_STRIDE in a stack trace — that is how
// an error inside one names its file and line.

const LIBS = new Map();  // hash -> {name, base, lines, factory | error}, oldest first
const LIBS_MAX = 50;      // as LIBS_MAX in bridge_exec.py: both forget the same one
const LIB_STRIDE = 10000;
const LIB_DECL = /^(?:async\s+)?(?:function\s*\*?\s*|class\s+|(?:const|let|var)\s+)([A-Za-z_$][\w$]*)/gm;

function compileLib(item) {
  const src = String(item.code)
    .replace(/^export\s+(?:default\s+)?(?=(?:async\s+)?function|class\s|const\s|let\s|var\s)/gm, "");
  const names = [];
  let m;
  LIB_DECL.lastIndex = 0;
  while ((m = LIB_DECL.exec(src))) if (names.indexOf(m[1]) === -1) names.push(m[1]);
  LIB_DECL.lastIndex = 0;
  // The lowest slot no kept lib uses, so two libs never share their lines.
  const used = new Set(Array.from(LIBS.values(), (e) => e.base));
  let base = LIB_STRIDE;
  while (used.has(base)) base += LIB_STRIDE;
  const tail = "\n;return Object.assign({}, module.exports, {" + names.map((n) =>
    JSON.stringify(n) + ": typeof " + n + " === 'undefined' ? undefined : " + n).join(", ") + "});";
  const entry = { name: String(item.name || "lib"), base, lines: src.split("\n") };
  try {
    entry.factory = new Function("figma", "print", "h", "lib", "module", "exports",
      "\n".repeat(base) + src + tail);
  } catch (e) {
    entry.error = e;
  }
  return entry;
}

function loadLibs(items, fig, print, h, errors) {
  const lib = {};
  if (!Array.isArray(items)) return lib;
  for (const item of items) {
    let entry = LIBS.get(item.hash);
    if (entry) {
      LIBS.delete(item.hash);  // newest last, as the bridge counts
      LIBS.set(item.hash, entry);
    } else if (typeof item.code === "string") {
      entry = compileLib(item);
      LIBS.set(item.hash, entry);
      while (LIBS.size > LIBS_MAX) LIBS.delete(LIBS.keys().next().value);
    } else {
      errors.missing.push(item.hash);
      throw new Error("lib " + item.name + " is not loaded in the plugin (was it restarted?) — " +
        "run the call again, the bridge sends its code");
    }
    if (entry.error) throw entry.error;
    const module = { exports: {} };
    Object.assign(lib, entry.factory(fig, print, h, lib, module, module.exports));
  }
  return lib;
}

// A --lib file's own line for each of its positions in a stack trace.
function libPositions(stack) {
  const out = [];
  if (SCRIPT_LINE_OFFSET === null || !stack) return out;
  STACK_POS.lastIndex = 0;
  let m;
  while ((m = STACK_POS.exec(String(stack)))) {
    const bodyLine = Number(m[1]) - SCRIPT_LINE_OFFSET + 1;
    for (const e of LIBS.values()) {
      const line = bodyLine - e.base;
      if (line >= 1 && line <= e.lines.length) {
        out.push({ at: m[0], name: e.name, line, col: Number(m[2]),
                   source: e.lines[line - 1].trim().slice(0, 200) });
        break;
      }
    }
  }
  STACK_POS.lastIndex = 0;
  return out;
}

// ─── more helpers ────────────────────────────────────────────────────────
// h.withFonts, h.setText and h.sel: mixed fonts and a missing one do not stop
// them, and sel gives a link to each layer.

Object.assign(HELPERS, {
  // A link to a layer, for reports to people: h.link(node) or h.link("1:11").
  link(nodeOrId) {
    const id = typeof nodeOrId === "string" ? linkNodeId(nodeOrId) : nodeOrId && nodeOrId.id;
    return nodeLink(id) || null;
  },

  sel() {
    return figma.currentPage.selection.map((n) => ({
      id: n.id, name: n.name, type: n.type,
      w: n.width, h: n.height,
      chars: n.type === "TEXT" ? n.characters : undefined,
      url: nodeLink(n.id),
    }));
  },

  // Everything about a subtree, with the keys of what it uses: {depth, hidden, maxNodes}.
  async inspect(node, opts) {
    const n = typeof node === "string" ? await HELPERS.resolve(node) : node;
    if (!n) throw new Error("h.inspect: no node");
    return await inspectTree(n, opts, this);
  },

  // A picture: {format: png|jpg|svg|pdf, scale, width}; base64 in `data`.
  async shot(node, opts) {
    const n = typeof node === "string" ? await HELPERS.resolve(node) : node;
    return await shotOf(n, opts);
  },

  // Load every font of a subtree (mixed ranges too), a FontName or a list of
  // them; never throws for a missing one — {loaded, missing, note?}.
  async fonts(target) {
    return await loadFonts(target);
  },

  // The installed Font Awesome: {pro, kit, families}.
  async fa() {
    return await faInfo();
  },

  async withFonts(rootNode, asyncFn) {
    await loadFonts(rootNode);  // a missing font is reported, the rest still load
    return await asyncFn();
  },

  // Mixed-font texts too: the new text takes the first character's style.
  async setText(node, text) {
    if (!node || node.type !== "TEXT") throw new Error("h.setText: needs a TEXT, not " + nodeLabel(node));
    await textReady(node, "h.setText");
    node.characters = String(text);
  },

  // FILL along an axis ("x" by default, "y", "xy") — inside an auto-layout parent.
  async fill(node, axis) {
    const props = sizingProps(axis, "x", "h.fill");
    if (!isAutoLayout(node.parent)) {
      throw new Error("h.fill: only a child of an auto-layout frame can FILL, and the parent of \"" +
        node.name + "\" is " + nodeLabel(node.parent) + ": first parent.appendChild(node), and give the " +
        "parent a layoutMode");
    }
    if (node.layoutPositioning === "ABSOLUTE") {
      throw new Error("h.fill: \"" + node.name + "\" is absolute — it cannot FILL");
    }
    await textReady(node, "h.fill");
    for (const p of props) {
      node[p] = "FILL";
      if (node.parent[p] === "HUG") {
        CURRENT_PRINT("h.fill: the parent \"" + node.parent.name + "\" HUGs on this axis — a FILL child " +
          "in it collapses; make the parent FIXED or FILL");
      }
    }
    return node;
  },

  // HUG ("xy" by default) — an auto-layout frame or a text.
  async hug(node, axis) {
    const props = sizingProps(axis, "xy", "h.hug");
    if (!isAutoLayout(node) && node.type !== "TEXT") {
      throw new Error("h.hug: only an auto-layout frame or a text can HUG, and this is " + nodeLabel(node) +
        (node.type === "FRAME" ? " without a layoutMode" : ""));
    }
    await textReady(node, "h.hug");
    for (const p of props) node[p] = "HUG";
    return node;
  },

  // A fixed size on the axes given; null keeps that axis's sizing (HUG, FILL).
  async fixed(node, width, height) {
    if (!node || typeof node.resize !== "function") throw new Error("h.fixed: " + nodeLabel(node) + " has no size");
    await textReady(node, "h.fixed");
    const read = (p) => { try { return node[p]; } catch (e) { return undefined; } };
    const keep = {};
    if (width == null) keep.layoutSizingHorizontal = read("layoutSizingHorizontal");
    if (height == null) keep.layoutSizingVertical = read("layoutSizingVertical");
    node.resize(width == null ? node.width : Number(width), height == null ? node.height : Number(height));
    for (const p of Object.keys(keep)) if (keep[p] && keep[p] !== "FIXED") node[p] = keep[p];
    return node;
  },

  // Wrapping text: the parent's width in auto-layout, or `width` px.
  async wrapText(node, width) {
    if (!node || node.type !== "TEXT") throw new Error("h.wrapText: needs a TEXT, not " + nodeLabel(node));
    await textReady(node, "h.wrapText");
    if (width == null) {
      if (!isAutoLayout(node.parent)) {
        throw new Error("h.wrapText: \"" + node.name + "\" is not in auto-layout — give a width: " +
          "h.wrapText(node, 320)");
      }
      node.textAutoResize = "HEIGHT";
      node.layoutSizingHorizontal = "FILL";
    } else {
      node.resize(Number(width), Math.max(1, node.height));
      node.textAutoResize = "HEIGHT";
    }
    if (node.width < 2) {
      CURRENT_PRINT("h.wrapText: \"" + node.name + "\" is " + node.width + " px wide — the text became a " +
        "column; check the parent's width");
    }
    return node;
  },

  // The CLI's `find`: {name, nameHas, type, text, textHas, hidden}. Layers
  // hidden inside instances are skipped while it searches (several times
  // faster) unless `hidden`; with a type it uses findAllWithCriteria.
  find(root, q) {
    q = q || {};
    if (!root || typeof root.findAll !== "function") throw new Error("h.find: " + nodeLabel(root) + " has no children");
    const type = q.type ? String(q.type).toUpperCase() : q.text != null || q.textHas != null ? "TEXT" : null;
    const match = (n) => (q.name == null || n.name === q.name)
      && (q.nameHas == null || n.name.indexOf(q.nameHas) !== -1)
      && (q.text == null || n.characters === q.text)
      && (q.textHas == null || n.characters.indexOf(q.textHas) !== -1);
    const was = figma.skipInvisibleInstanceChildren;
    if (!q.hidden) figma.skipInvisibleInstanceChildren = true;
    try {
      const found = type && typeof root.findAllWithCriteria === "function"
        ? root.findAllWithCriteria({ types: [type] }).filter(match)
        : root.findAll((n) => (!type || n.type === type) && match(n));
      return found.map((n) => ({
        id: n.id, name: n.name, type: n.type, w: n.width, h: n.height,
        chars: n.type === "TEXT" ? n.characters : undefined,
      }));
    } finally {
      figma.skipInvisibleInstanceChildren = was;
    }
  },
});

// ─── instance properties by their short names ───────────────────────────
// Figma wants a TEXT, BOOLEAN or INSTANCE_SWAP property by its full name
// ("✒️ Label#164:0") and a BOOLEAN as a boolean; the CLI's `variant` sends
// strings. h.variant takes "Label", "label" or "Label#164:0" for it, and
// "true"/"false".

const PROP_ID = /#\d+:\d+$/;
const PROP_DECOR = /^[^A-Za-z0-9\u0400-\u04FF]+/;  // an emoji and spaces in front: "✒️ Label"

function propBase(name) {
  return name.replace(PROP_ID, "").trim().toLowerCase();
}

function propKey(defs, name, value) {
  if (Object.prototype.hasOwnProperty.call(defs, name)) return name;
  const all = Object.keys(defs);
  // "Label#164:0" without the emoji in front: the #id alone is unique.
  const id = String(name).trim().match(PROP_ID);
  const byId = id ? all.filter((k) => k.endsWith(id[0])) : [];
  if (byId.length === 1) return byId[0];
  const want = propBase(String(name).trim());
  // The same name without its #id, then without what decorates it.
  let hits = all.filter((k) => propBase(k) === want);
  if (!hits.length) hits = all.filter((k) => propBase(k).replace(PROP_DECOR, "") === want.replace(PROP_DECOR, ""));
  if (hits.length > 1) {
    const flag = typeof value === "boolean" || /^(true|false)$/i.test(String(value));
    const swap = !flag && LINK_NODE.test(String(value).trim());
    const kind = (k) => defs[k].type;
    const narrowed = hits.filter((k) => flag ? kind(k) === "BOOLEAN"
      : swap ? kind(k) === "INSTANCE_SWAP" : kind(k) !== "BOOLEAN" && kind(k) !== "INSTANCE_SWAP");
    if (narrowed.length === 1) hits = narrowed;
  }
  const list = (keys) => keys.map((k) => k + " (" + defs[k].type + ")").join(", ");
  if (hits.length === 1) return hits[0];
  if (hits.length > 1) {
    throw new Error("h.variant: \"" + name + "\" matches several properties: " + list(hits) + "; give the full name");
  }
  throw new Error("h.variant: no property \"" + name + "\"; there are: " + list(all));
}

function propValue(def, key, value) {
  if (def.type === "BOOLEAN") {
    if (typeof value === "boolean") return value;
    if (/^(true|false)$/i.test(String(value).trim())) return /^true$/i.test(String(value).trim());
    throw new Error("h.variant: \"" + key + "\" is a BOOLEAN, so true or false, not " + JSON.stringify(value));
  }
  if (def.type === "INSTANCE_SWAP") return linkNodeId(String(value).trim());  // a component id or its link
  return value;
}

Object.assign(HELPERS, {
  async variant(instance, props) {
    if (!instance || instance.type !== "INSTANCE") throw new Error("h.variant: needs an INSTANCE, not " + nodeLabel(instance));
    const defs = instance.componentProperties;
    const out = {};
    for (const name of Object.keys(props || {})) {
      const key = propKey(defs, name, props[name]);
      out[key] = propValue(defs[key], key, props[name]);
    }
    instance.setProperties(out);
    return instance;
  },
});

// ─── the window's Recent list ────────────────────────────────────────────
// A click on a layer there selects it and brings it into view, on its own
// page. The user asked for it, so their selection and camera are ours to move.

async function selectLayer(id) {
  let node = null;
  try {
    node = nodeHere(id);
    if (node === undefined) node = await figma.getNodeByIdAsync(String(id));
  } catch (e) { node = null; }
  let page = node;
  try {
    while (page && page.type !== "PAGE") page = page.parent;
  } catch (e) { page = null; }
  const ok = !!(node && page && node !== page);
  if (ok) {
    try {
      if (figma.currentPage !== page) await figma.setCurrentPageAsync(page);
      figma.currentPage.selection = [node];
      figma.viewport.scrollAndZoomIntoView([node]);
    } catch (e) { /* removed meanwhile, or locked away in an instance */ }
  }
  figma.ui.postMessage({ type: "selected", id, ok });
}

// ─── hot reload ──────────────────────────────────────────────────────────
// `figaro reload` sends plugin/code.js and ui.html from disk; running them
// here replaces this code inside the running plugin, as a re-Run in Figma
// would. The new UI reconnects to the bridge on its own.

function reloadPlugin(msg) {
  try {
    new Function("figma", "__html__", String(msg.code))(figma, String(msg.html));
  } catch (e) {
    figma.ui.postMessage({ type: "error", id: msg.id, text: "reload: " + errorText(e), stack: null });
  }
}

// A previous copy of this code (before a hot reload) is still listening; it
// must stop, or every change would be counted twice.
if (typeof globalThis.__figaroDispose === "function") {
  try { globalThis.__figaroDispose(); } catch (e) { /* it was half-started anyway */ }
}
figma.on("documentchange", onDocumentChange);
globalThis.__figaroDispose = () => figma.off("documentchange", onDocumentChange);
