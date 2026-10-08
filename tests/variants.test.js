// Instance properties in plugin/code.js: h.variant takes an
// instance property by its short name and "true"/"false" for a BOOLEAN —
// the CLI's `variant` sends every value as a string, and Figma wants the
// full name ("✒️ Label#164:0") and a real boolean. And h.bF/h.bS/h.bN say
// what to do with a key Figma would not import, and a layer inside an instance
// is found even when Figma's async lookup never answers.
//
//     node tests/variants.test.js
const fs = require("fs");
const path = require("path");
const src = fs.readFileSync(path.join(__dirname, "..", "plugin", "code.js"), "utf8");

const KEY = "AbCdEfGhIjKlMnOpQrStUv";

// The Button of the draft file: emoji in front of its property names, and a
// BOOLEAN and an INSTANCE_SWAP that are both "Right Icon".
function makeFigma() {
  const nodes = new Map();
  const props = {
    "✒️ Label#164:0": { type: "TEXT", value: "Label" },
    "➡️ Right Icon#164:41": { type: "BOOLEAN", value: true },
    "🔍 Right Icon#226:0": { type: "INSTANCE_SWAP", value: "1:6279" },
    "🔍 Icon#216:250": { type: "INSTANCE_SWAP", value: "1:6281" },
    "📏 Size": { type: "VARIANT", value: "Normal" },
  };
  const calls = [];
  // Figma's own checks, as measured in real Figma.
  const inst = {
    id: "5:1", name: "Button", type: "INSTANCE",
    get componentProperties() { return props; },
    setProperties(p) {
      for (const [k, v] of Object.entries(p)) {
        if (!(k in props)) throw new Error("in setProperties: Could not find a component property with name: '" + k + "'");
        if ((props[k].type === "BOOLEAN") !== (typeof v === "boolean")) {
          throw new Error("in setProperties: Property value is incompatible with component property type");
        }
      }
      for (const [k, v] of Object.entries(p)) props[k].value = v;
      calls.push(p);
    },
  };
  nodes.set("5:1", inst);
  nodes.set("I5:1;1:2", { id: "I5:1;1:2", name: "Label", type: "TEXT" });
  nodes.set("5:2", { id: "5:2", name: "Card", type: "FRAME", bound: {},
                     setBoundVariable(prop, v) { this.bound[prop] = v.id; } });
  // As in the draft: a library variable the file uses is there by id, but its
  // key does not import.
  const radius = { id: "VariableID:5893/2287:63", name: "Border radius/radius-sm", key: "5893" };
  const figma = {
    mixed: Symbol("mixed"),
    root: { name: "Sandbox 5$", children: [] },
    fileKey: KEY,
    showUI() {},
    ui: { onmessage: null, posted: [], postMessage(m) { this.posted.push(m); } },
    currentPage: { selection: [] },
    on() {}, off() {},
    commitUndo() {}, triggerUndo() {},
    getNodeById(id) { return nodes.get(String(id)) || null; },
    async getNodeByIdAsync(id) {
      if (String(id).includes(";")) return new Promise(() => {});  // as Figma once did
      return nodes.get(String(id)) || null;
    },
    variables: {
      async getVariableByIdAsync(id) {
        if (!/^VariableID:/.test(id)) throw new Error("in getVariableByIdAsync: Invalid id");
        return id === radius.id ? radius : null;
      },
      async importVariableByKeyAsync(key) { throw new Error('could not find variable with key "' + key + '"'); },
    },
  };
  new Function("figma", "__html__", src)(figma, "");
  return { figma, props, calls };
}

let seq = 0;
async function exec(env, code) {
  const id = "e" + (++seq);
  const posted = env.figma.ui.posted;
  await env.figma.ui.onmessage({ type: "exec", id, code, timeout: 10, quick: true });
  const reply = posted.find((m) => m.id === id && (m.type === "result" || m.type === "error"));
  const value = typeof reply.valueJson === "string" ? JSON.parse(reply.valueJson) : reply.value;
  return Object.assign({}, reply, { value });
}

let failed = 0;
function check(label, actual, expected) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  const ok = a === e;
  if (!ok) failed++;
  console.log(`${ok ? "  ok  " : " FAIL "} ${label}${ok ? "" : `\n         got ${a}\n         want ${e}`}`);
}

const variant = (props) => `await h.variant(await h.node("5:1"), ${JSON.stringify(props)}); return 1;`;

(async () => {
  {
    const env = makeFigma();
    await exec(env, variant({ Label: "Pay now" }));
    check("a short name finds the property behind its emoji and #id", env.props["✒️ Label#164:0"].value, "Pay now");

    await exec(env, variant({ size: "Large" }));
    check("a variant axis too, in any case", env.props["📏 Size"].value, "Large");

    await exec(env, variant({ "Right Icon": "false" }));
    check("\"false\" picks the BOOLEAN of two namesakes and becomes false",
      [env.props["➡️ Right Icon#164:41"].value, env.props["🔍 Right Icon#226:0"].value], [false, "1:6279"]);

    await exec(env, variant({ "Right Icon": "12-34" }));
    check("a component id picks the INSTANCE_SWAP; a link-form id is fine",
      env.props["🔍 Right Icon#226:0"].value, "12:34");

    await exec(env, variant({ Icon: "7:7" }));
    check("\"Icon\" is the one called Icon, not every … Icon",
      [env.props["🔍 Icon#216:250"].value, env.props["🔍 Right Icon#226:0"].value], ["7:7", "12:34"]);

    await exec(env, variant({ "✒️ Label#164:0": "Full", "➡️ Right Icon#164:41": true }));
    check("full names and real booleans pass as they are",
      env.calls[env.calls.length - 1], { "✒️ Label#164:0": "Full", "➡️ Right Icon#164:41": true });

    // a smoke test: the agent wrote the names it read, without the emoji
    await exec(env, variant({ "Label#164:0": "Choose", "Right Icon#164:41": false }));
    check("a name with its #id but no emoji: the #id finds it",
      env.calls[env.calls.length - 1], { "✒️ Label#164:0": "Choose", "➡️ Right Icon#164:41": false });

    await exec(env, variant({ "Label#1:0": "Other set" }));
    check("an #id from another set: the name still finds it", env.props["✒️ Label#164:0"].value, "Other set");
  }
  {
    const env = makeFigma();
    let r = await exec(env, variant({ Color: "Red" }));
    check("an unknown name lists what there is",
      [r.type, /no property "Color"; there are: ✒️ Label#164:0 \(TEXT\), .*📏 Size \(VARIANT\)/.test(r.text)], ["error", true]);

    r = await exec(env, variant({ "Right Icon": "Hello" }));
    check("namesakes the value cannot tell apart: asks for the full name",
      [r.type, /matches several properties: ➡️ Right Icon#164:41 \(BOOLEAN\), 🔍 Right Icon#226:0 \(INSTANCE_SWAP\)/.test(r.text)],
      ["error", true]);

    r = await exec(env, variant({ "➡️ Right Icon#164:41": "yes" }));
    check("a BOOLEAN takes only true or false", [r.type, /is a BOOLEAN, so true or false/.test(r.text)], ["error", true]);

    r = await exec(env, 'await h.variant(await h.node("5:2"), {Label: "x"}); return 1;');
    check("not an instance", [r.type, /needs an INSTANCE, not FRAME "Card"/.test(r.text)], ["error", true]);
    check("nothing was set by the failed calls", env.calls.length, 0);
  }
  {
    const env = makeFigma();
    const bind = (v) => `await h.bN(await h.node("5:2"), "itemSpacing", ${JSON.stringify(v)}); return 1;`;
    let r = await exec(env, bind("VariableID:5893/2287:63"));
    check("a library variable the file uses binds by its id",
      [r.type, env.figma.getNodeById("5:2").bound], ["result", { itemSpacing: "VariableID:5893/2287:63" }]);

    r = await exec(env, bind("5893"));
    check("a key Figma would not import: says to take the id from inspect",
      [r.type, /h\.bN: no variable 5893: it is not an id, and Figma did not import it by key.*figaro inspect.*VariableID/.test(r.text)],
      ["error", true]);

    r = await exec(env, bind("VariableID:1:1"));
    check("an id that is not in the file", [r.type, /no variable VariableID:1:1 in this file/.test(r.text)], ["error", true]);
  }
  {
    const env = makeFigma();
    let r = await exec(env, 'return (await h.node("I5:1;1:2")).name');
    check("a layer inside an instance: h.node does not wait for Figma's async lookup", [r.type, r.value], ["result", "Label"]);
    r = await exec(env, 'return (await figma.getNodeByIdAsync("I5:1;1:2")).name');
    check("…nor does the script's figma.getNodeByIdAsync", [r.type, r.value], ["result", "Label"]);
    r = await exec(env, 'return await h.node("9:9")');
    check("a missing id still fails at once", [r.type, /no node 9:9 in "Sandbox 5\$"/.test(r.text)], ["error", true]);
  }

  console.log(failed ? `\n${failed} FAILED` : "\nall variant checks passed");
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
