// Loops that never end (plugin/code.js, guardLoops and loopGuard): every
// `while`, `do … while` and `for (;;)` of a script or a --lib file ends at the
// script's deadline, or once the bridge gives up on it, with an error that
// names the loop's line. for … of, for … in and for await stay as they are,
// and so do strings, comments, regular expressions and the text of templates;
// a loop inside a template's ${…} is guarded. No line moves, the columns in
// errors are the code's own, and a script the guards would break runs as it is.
//
//     node tests/loops.test.js
const fs = require("fs");
const path = require("path");
const src = fs.readFileSync(path.join(__dirname, "..", "plugin", "code.js"), "utf8");

function load() {
  const posted = [];
  const figma = {
    root: { name: "Draft", children: [] },
    fileKey: "KEY",
    showUI() {},
    ui: { onmessage: null, postMessage(m) { posted.push(m); } },
    currentPage: { selection: [] },
    on() {},
    off() {},
    commitUndo() {},
  };
  const api = new Function("figma", "__html__", src +
    "\nreturn { guardLoops, codeMask, unshift, SCRIPT_LINE_OFFSET };")(figma, "");
  return { figma, posted, api };
}

let seq = 0;
function start(env, code, opts) {
  const id = "e" + (++seq);
  const done = env.figma.ui.onmessage(Object.assign({ type: "exec", id, code, timeout: 10, quick: true }, opts))
    .then(() => {
      const reply = env.posted.find((m) => m.id === id && (m.type === "result" || m.type === "error"));
      const value = typeof reply.valueJson === "string" ? JSON.parse(reply.valueJson) : reply.value;
      return Object.assign({}, reply, { value });
    });
  return { id, done };
}
const exec = (env, code, opts) => start(env, code, opts).done;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let failed = 0;
function check(label, actual, expected) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  const ok = a === e;
  if (!ok) failed++;
  console.log(`${ok ? "  ok  " : " FAIL "} ${label}${ok ? "" : `\n         got ${a}\n         want ${e}`}`);
}

// The frames of a stack ("at …" lines), the error's text left out.
const frames = (stack) => String(stack || "").split("\n").filter((l) => /^\s*at\s/.test(l));

const DEADLINE = /^aborted: the loop on line (\d+)( of [^ ]+)? was still running when --timeout ran out — the script was stopped$/;

(async () => {
  const env = load();
  const { guardLoops, codeMask, unshift } = env.api;

  // ─── loops that never end ───────────────────────────────────────────────
  {
    const t0 = Date.now();
    const r = await exec(env, "let n = 0;\nwhile (true) { n++; }", { timeout: 0.2 });
    const ms = Date.now() - t0;
    check("while (true) ends at the deadline, naming its line",
      [r.type, DEADLINE.exec(r.text) && DEADLINE.exec(r.text)[1], r.line, r.source],
      ["error", "2", 2, "while (true) { n++; }"]);
    check("…soon after the deadline", ms >= 200 && ms < 1200, true);
    check("…and its stack is the script's line alone, not the guard's or the plugin's",
      frames(r.stack).map((l) => (/script:(\d+):\d+/.exec(l) || [])[1] || l), ["2"]);
  }
  for (const [label, code, line] of [
    ["for (;;)", "for (;;) {}", 1],
    ["for (a; b; c) whose b is always true", "for (let i = 0; i >= 0; i++) {}", 1],
    ["do … while", "let i = 0;\ndo {\n  i++;\n} while (i > 0);", 4],
    ["a loop in a loop", "for (let i = 0; i < 3; i++) {\n  while (i < 3) {}\n}", 2],
    ["a loop in a template's ${…}", "const t = `n = ${(() => {\n  while (1) {}\n})()}`;\nreturn t;", 2],
    ["a loop that awaits only promises", "while (true) {\n  await null;\n}", 1],
    ["a loop in a function the script calls", "function spin() {\n  for (;;) {}\n}\nspin();", 2],
    ["a loop with a head on two lines", "let i = 0;\nwhile (\n  i >= 0\n) i++;", 2],
  ]) {
    const r = await exec(env, code, { timeout: 0.15 });
    const m = DEADLINE.exec(r.text || "");
    check(label + ": ends at the deadline, on its line", [r.type, m && Number(m[1]), r.line], ["error", line, line]);
  }
  {
    // the try catches the inner loop's stop and the outer loop goes round again; 3 s is the
    // test's own way out, should no guard ask the clock again
    const t0 = Date.now();
    const code = "const t0 = Date.now();\nfor (let r = 0; r < 2; r++) {\n  try { while (Date.now() - t0 < 3000) {} } catch (e) {}\n}\nreturn 'went on';";
    const r = await exec(env, code, { timeout: 0.15 });
    const m = DEADLINE.exec(r.text || "");
    check("a try that catches its inner loop's stop: the loop around it stops next, on its line",
      [r.type, m && Number(m[1]), r.line, Date.now() - t0 < 1500], ["error", 2, 2, true]);
  }
  {
    const r = await exec(env, "const t = [1, 2].map((x) => {\n  while (x) {}\n  return x;\n});", { timeout: 0.15, quick: false });
    check("…also with the exec's change tracking", [r.type, DEADLINE.test(r.text), r.line], ["error", true, 2]);
  }

  // ─── the bridge gives up ────────────────────────────────────────────────
  {
    const run = start(env, "let i = 0;\nwhile (true) {\n  await new Promise((r) => setTimeout(r, 5));\n  i++;\n}");
    await sleep(60);
    await env.figma.ui.onmessage({ type: "abort", id: run.id });
    const r = await run.done;
    check("abort from the bridge (Stop) ends the loop at once",
      [r.type, r.text, r.line],
      ["error", "aborted: the loop on line 2 was still running when the bridge stopped the script", 2]);
  }

  // ─── --lib files ────────────────────────────────────────────────────────
  {
    const spin = { hash: "s1", name: "spin.js", code: "function spin() {\n  let i = 0;\n  while (i >= 0) i++;\n  return i;\n}" };
    const r = await exec(env, "return lib.spin();", { timeout: 0.15, libs: [spin] });
    const m = DEADLINE.exec(r.text || "");
    check("a loop in a --lib file ends at the deadline, naming the file and its line",
      [r.type, m && m[1], m && m[2], r.lib && r.lib.name, r.lib && r.lib.line, r.lib && r.lib.source],
      ["error", "3", " of spin.js", "spin.js", 3, "while (i >= 0) i++;"]);
    check("…its stack: the lib's line, then the script's",
      frames(r.stack).map((l) => (/(spin\.js|script):(\d+):\d+/.exec(l) || []).slice(1).join(":") || l),
      ["spin.js:3", "script:1"]);
    const again = await exec(env, "return lib.spin();", { timeout: 0.15, libs: [{ hash: "s1", name: "spin.js" }] });
    check("…in the next script too: the guard is that script's", [again.type, DEADLINE.test(again.text)], ["error", true]);
    const sum = { hash: "s2", name: "sum.js", code: "function sum(n) {\n  let s = 0;\n  for (let i = 1; i <= n; i++) s += i;\n  return s;\n}" };
    const ok = await exec(env, "return lib.sum(100);", { libs: [sum] });
    check("…and one that ends works as before", [ok.type, ok.value], ["result", 5050]);
    const odd = { hash: "s3", name: "odd.js", code: "const o = { while(x) { return x * 3; } };\nmodule.exports = { o };" };
    const r3 = await exec(env, "return lib.o.while(2);", { libs: [odd] });
    check("…and one the guards would break runs as it is", [r3.type, r3.value], ["result", 6]);
  }

  // ─── what the guards leave as it is ─────────────────────────────────────
  {
    const code = 'const a = "while (x) {}";\n' +
      "const b = 'for (;;) {}';\n" +
      "// while (true) {}\n" +
      "/* for (;;) {} */\n" +
      "const r = /while (x)/g;\n" +
      "const t = `for (;;) ${a} while (y)`;\n" +
      "return [a, b, r.source, t];";
    check("strings, comments, regular expressions and template text are not loops",
      guardLoops(code).shifts, null);
    const r = await exec(env, code);
    check("…and the script gives what it did", r.value,
      ["while (x) {}", "for (;;) {}", "while (x)", "for (;;) while (x) {} while (y)"]);
  }
  for (const [label, code] of [
    ["for … of", "for (const x of [1, 2]) {}"],
    ["for … in", "for (const k in { a: 1 }) {}"],
    ["for await", "for await (const x of [Promise.resolve(1)]) {}"],
    ["a call named while or for", "const o = { ['while']: (x) => x, ['for']: (x) => x };\no.while(1); o?.for(2); o . while(3);"],
    ["a method named while or for", "class A { static while(x) { return x; } }\nconst o = { *for(x) { yield x; }, async while(x) { return x; }, get for() { return 1; } };"],
    ["a property named while", "const o = { while: 1, for: 2 };\nreturn o.while + o.for;"],
    ["a name that has while in it", "const awhile = (x) => x, for_ = (x) => x, $for = (x) => x, \u00e9while = (a, b) => b;\n" +
      "awhile(1); for_(2); $for(3); \u00e9while(4, 5);"],
  ]) {
    const g = guardLoops(code);
    check(label + ": left as it is", [g.shifts, g.code === code], [null, true]);
  }
  {
    const code = "let i = 0;\nwhile (i < 10 && 10 / 2 > 1) i++;\nconst r = /a/g, d = 6 / 2 / 1;\nfor (let j = 0; j < d; j++) i += 10;\n" +
      "do { i--; } while (i > 35);\nouter: for (let a = 0; a < 3; a++) {\n  for (let b = 0; ; b++) {\n    if (b > 1) continue outer;\n  }\n}\n" +
      "for (let f = () => { let x = 1; return x; }; f() < 0;) {}\nfor (let p = 0, q = 9; p < q; p++, q--) i += 100;\n" +
      "const t = `${(() => { let k = 0; while (k < 4) k++; return k; })()}`;\nreturn [i, t];";
    const g = guardLoops(code);
    check("every while, do … while and for (;;) is guarded", Object.values(g.shifts).reduce((n, s) => n + s.length, 0) >= 9, true);
    check("…and the guard adds no line", g.code.split("\n").length, code.split("\n").length);
    const r = await exec(env, code);
    check("…and changes nothing a loop does", [r.type, r.value], ["result", [535, "4"]]);
  }
  {
    const r = await exec(env, "const G = 1, N = 2, $figaroN = 3;\nlet k = 0;\nwhile (k < 3) k++;\nreturn [G, N, $figaroN, k];");
    check("the guard's names don't meet the script's", r.value, [1, 2, 3, 3]);
  }
  {
    const r = await exec(env, "const o = { while(x) { return x * 2; } };\nlet i = 0;\nwhile (i < 2) i++;\nreturn o.while(21) + i;");
    check("a script the guards would break (an object's method named while) runs as it is", [r.type, r.value], ["result", 44]);
    const bad = await exec(env, "let i = 0;\nwhile (i < 3 {\n  i++;\n}");
    check("…and a script with a syntax error says so, as before",
      [bad.type, /SyntaxError|expected|Unexpected/.test(bad.text)], ["error", true]);
  }
  {
    const code = "codeOnly finds the same code: while (a / b) {} x = /while (b)/;";
    const mask = codeMask(code);
    check("codeMask keeps every character in its place", mask.length, code.length);
    check("…and blanks what codeOnly drops", mask.indexOf("while (b)"), -1);
  }

  // ─── line and column in an error ────────────────────────────────────────
  {
    const r = await exec(env, "let i = 0;\nwhile (i < 3) {\n  i++;\n}\nfor (let j = 0; j < 2; j++) i++;\nnull.boom;");
    check("an error after guarded loops keeps its line", [r.type, r.line, r.source], ["error", 6, "null.boom;"]);
    const code = "let i = 0;\nfor (let j = 0; j < 1; j++) while (i < 1) { i++; null.boom; }";
    const guarded = await exec(env, code);
    let plain = null;
    try {
      await new Function('"use strict"; return (async () => {\n' + code + "\n})();")();
    } catch (e) {
      const m = /<anonymous>:(\d+):(\d+)/.exec(e.stack);
      plain = m && Number(m[2]);
    }
    check("…and its column on a line with guards is the code's own", [guarded.line, guarded.col], [2, plain]);
    const g = guardLoops("while (a) b;");
    const at = g.code.indexOf(" b;") + 2;  // where `b` is in the guarded code, 1-based
    check("unshift: a column after a guard", unshift(g.shifts, 1, at), 11);
    check("…before it", unshift(g.shifts, 1, 3), 3);
    check("…inside it: the loop's condition", unshift(g.shifts, 1, 12), 8);
  }

  // ─── what it costs ──────────────────────────────────────────────────────
  {
    const body = "let n = 0;\nfor (let i = 0; i < 1e6; i++) n += i;\nlet j = 0;\nwhile (j < 1e6) j++;\nreturn n + j;";
    const time = (code) => {
      const best = [];
      for (let k = 0; k < 3; k++) {
        const fn = new Function("$g", "let $n = 1;\n" + code);
        const t0 = process.hrtime.bigint();
        fn(() => 1024);
        best.push(Number(process.hrtime.bigint() - t0) / 1e6);
      }
      return Math.min(...best);
    };
    const guardedCode = guardLoops(body).code.replace(/\$figaroN\w+/g, "$$n").replace(/\$figaroG\w+/g, "$$g");
    const plain = time(body), guarded = time(guardedCode);
    console.log(`       2 × 1e6 rounds: ${plain.toFixed(1)} ms as it is, ${guarded.toFixed(1)} ms guarded (Node)`);
    check("2 × 1e6 guarded rounds cost little", guarded < plain + 200, true);
    const r = await exec(env, body);
    check("…and a script with them gives the same answer", r.value, 499999500000 + 1e6);
  }

  console.log(failed ? `\n${failed} FAILED` : "\nall loop guard checks passed");
  process.exit(failed ? 1 : 0);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
