// The window's height and place (plugin/code.js, fitWindow) against a stub of
// Figma's canvas: the window is as tall as its islands but ends at the bottom
// of the canvas; parked low, it keeps this file's island. Only a parked window
// moves: an island that opens in it, or a last line that comes with a button,
// lifts it just enough, at most to the top of the canvas, and their going puts
// it back, unless the user moved it meanwhile. Anywhere else the window stays
// where the user put it, and an island that opens scrolls in it.
//
//     node tests/window.test.js
const fs = require("fs");
const path = require("path");
const src = fs.readFileSync(path.join(__dirname, "..", "plugin", "code.js"), "utf8");

const HEADER = 41;  // FIGMA_HEADER in code.js
const GAP = 8;      // CANVAS_GAP

// Figma's screen: the canvas starts `canvasTop` px down the window space and
// is `canvasHeight` px tall, at `zoom`; the plugin window's corner is at `at`.
// getPosition gives that corner in window space and on the canvas, as Figma
// does, and reposition takes the canvas's coordinates. `lag`: how many reads
// after a move still give the old place, as Figma's may for a moment.
function makeFigma({ at = { x: 1600, y: 300 }, canvasTop = 0, canvasLeft = 332, canvasHeight = 1000, zoom = 1,
                     noPosition = false, lag = 0 } = {}) {
  const view = { x: -1000, y: -686 };  // the canvas point at the canvas's top left corner
  const win = { x: at.x, y: at.y };
  let old = null, stale = 0;
  const sizes = [];
  const moves = [];
  const ui = {
    onmessage: null,
    postMessage() {},
    resize(w, h) { sizes.push(h); },
  };
  if (!noPosition) {
    ui.getPosition = () => {
      const w = stale-- > 0 ? old : win;
      return {
        windowSpace: { x: w.x, y: w.y },
        canvasSpace: { x: view.x + (w.x - canvasLeft) / env.zoom, y: view.y + (w.y - canvasTop) / env.zoom },
      };
    };
    ui.reposition = (cx, cy) => {
      old = { x: win.x, y: win.y };
      stale = lag;
      win.x = canvasLeft + (cx - view.x) * env.zoom;
      win.y = canvasTop + (cy - view.y) * env.zoom;
      moves.push(Math.round(win.y));
    };
  }
  const env = { win, sizes, moves, zoom };
  const figma = {
    root: { name: "Draft", children: [] },
    fileKey: "KEY",
    showUI() {},
    ui,
    get viewport() {
      return { zoom: env.zoom, bounds: { x: view.x, y: view.y, width: 1987 / env.zoom, height: canvasHeight / env.zoom } };
    },
    on() {},
  };
  new Function("figma", "__html__", src)(figma, "");
  env.ask = (msg) => figma.ui.onmessage(Object.assign({ type: "resize" }, msg));
  env.height = () => sizes[sizes.length - 1];
  // The room under the window's header down to the canvas's bottom edge, as fitWindow counts it.
  env.below = () => canvasTop + canvasHeight - GAP - win.y - HEADER;
  return env;
}
// Where the window's corner is when the canvas's edge leaves it `room` px under its header.
const parkedAt = (room, canvasHeight = 1000) => canvasHeight - GAP - HEADER - room;

let failed = 0;
function check(label, actual, expected) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  const ok = a === e;
  if (!ok) failed++;
  console.log(`${ok ? "  ok  " : " FAIL "} ${label}${ok ? "" : `\n         got ${a}\n         want ${e}`}`);
}

(async () => {
  // ─── the bottom of the canvas ───────────────────────────────────────────
  {
    const env = makeFigma({ at: { x: 1600, y: 100 } });
    await env.ask({ height: 300, least: 70 });
    check("room under it: as tall as its islands", env.height(), 300);
    await env.ask({ height: 5000, least: 70 });
    check("…and never past the bottom of the canvas", env.height(), 1000 - GAP - 100 - HEADER);
  }
  {
    const env = makeFigma({ at: { x: 1600, y: 700 } });
    await env.ask({ height: 500, least: 70 });
    check("low on the canvas: down to its bottom, the islands scroll", env.height(), env.below());
    const n = env.sizes.length;
    await env.ask({ height: 500, least: 70 });
    check("asked again with nothing new: no resize", env.sizes.length, n);
    env.win.y = 600;
    await env.ask({ height: 500, least: 70 });
    check("the user moved it up: the next ask grows it", env.height(), env.below());
  }
  {
    const env = makeFigma({ at: { x: 1600, y: 940 } });
    await env.ask({ height: 500, least: 70 });
    check("parked at the bottom: never shorter than this file's island and the last line", env.height(), 70);
    check("…and it stays where the user put it", env.moves, []);
  }
  {
    const near = makeFigma({ at: { x: 1600, y: 700 }, zoom: 1 });
    const far = makeFigma({ at: { x: 1600, y: 700 }, zoom: 0.25 });
    await near.ask({ height: 500, least: 70 });
    await far.ask({ height: 500, least: 70 });
    check("the canvas's zoom doesn't change the room", far.height(), near.height());
  }
  {
    const env = makeFigma({ at: { x: 1600, y: 700 }, canvasTop: 48 });
    await env.ask({ height: 500, least: 70 });
    check("a canvas that starts lower (a banner above it): the room is counted from it", env.height(), env.below());
  }
  {
    const env = makeFigma({ noPosition: true });
    await env.ask({ height: 300, least: 70 });
    check("a Figma that can't say where the window is: as tall as the islands", env.height(), 300);
    await env.ask({ height: 2600, least: 70 });
    check("…up to 2000 px", env.height(), 2000);
  }

  // ─── an island that opens in a parked window ────────────────────────────
  {
    const env = makeFigma({ at: { x: 1600, y: parkedAt(70) } });
    await env.ask({ height: 142, least: 70 });
    const parked = env.win.y;
    check("parked: the canvas's edge holds it to its least height", env.height(), 70);
    await env.ask({ height: 400, least: 70, lift: 400, island: "here" });
    check("an island opens: the window goes up just enough to show it whole",
          [env.win.y, env.height()], [parked - (400 - 70), 400]);
    await env.ask({ height: 400, least: 70 });
    check("…and stays there while it is open", env.moves.length, 1);
    await env.ask({ height: 142, least: 70, drop: ["here"] });
    check("it closes: the window goes back where the user parked it", env.win.y, parked);
    check("…and down to its least height again", env.height(), 70);
    await env.ask({ height: 400, least: 70, lift: 400, island: "here" });
    check("…and opens again the same way", env.win.y, parked - (400 - 70));
  }
  {
    const env = makeFigma({ at: { x: 1600, y: parkedAt(70, 600) }, canvasHeight: 600 });
    await env.ask({ height: 142, least: 70 });
    await env.ask({ height: 900, least: 70, lift: 900, island: "here" });
    check("a taller island goes up to the top of the canvas, no further", env.win.y, GAP);
    check("…and fills the canvas, the rest scrolls", env.height(), 600 - GAP - GAP - HEADER);
  }
  {
    const env = makeFigma({ at: { x: 1600, y: 900 } });
    await env.ask({ height: 106, least: 70 });
    await env.ask({ height: 400, least: 70, lift: 400, island: "here" });
    env.win.y = 500;  // the user drags it
    await env.ask({ height: 400, least: 70 });
    await env.ask({ height: 106, least: 70, drop: ["here"] });
    check("the user moved the window meanwhile: closing leaves it where they put it", env.win.y, 500);
  }
  {
    const env = makeFigma({ at: { x: 1600, y: 900 } });
    await env.ask({ height: 400, least: 70, lift: 400, island: "here" });
    const lifted = env.win.y;
    await env.ask({ height: 300, least: 70, drop: ["doc:OTHER"] });
    check("another island closes: the window stays up", env.win.y, lifted);
  }
  {
    const env = makeFigma({ at: { x: 1600, y: parkedAt(70) }, lag: 2 });
    await env.ask({ height: 142, least: 70 });
    const parked = env.win.y;
    await env.ask({ height: 400, least: 70, lift: 400, island: "here" });
    await env.ask({ height: 400, least: 70 });  // Figma still reads the old place
    check("a moved window that still reads its old place is not taken for the user's move",
          [env.win.y, env.height()], [parked - 330, 400]);
    await env.ask({ height: 142, least: 70, drop: ["here"] });
    check("…and closing still takes it back", env.win.y, parked);
  }

  // ─── anywhere else it stays put ─────────────────────────────────────────
  {
    const env = makeFigma({ at: { x: 1600, y: 100 } });
    await env.ask({ height: 400, least: 70, lift: 400, island: "here" });
    check("room enough under it: an island opens and nothing moves", env.moves, []);
  }
  {
    const env = makeFigma({ at: { x: 1600, y: 24 }, canvasHeight: 420 });
    await env.ask({ height: 142, least: 42 });
    await env.ask({ height: 602, least: 42, lift: 538, island: "here" });
    check("high on the canvas: a long island opens, the window stays, the island scrolls in it",
          [env.moves, env.height()], [[], 420 - GAP - 24 - HEADER]);
  }
  {
    const env = makeFigma({ at: { x: 1600, y: parkedAt(101) } });
    await env.ask({ height: 142, least: 70 });
    check("low, but with room for more than its least", env.height(), 101);
    await env.ask({ height: 400, least: 70, lift: 400, island: "here" });
    check("…is not parked: an island opens and the window stays", [env.moves, env.height()], [[], 101]);
  }

  // ─── a last line with a button ──────────────────────────────────────────
  {
    const env = makeFigma({ at: { x: 1600, y: parkedAt(42) } });
    await env.ask({ height: 136, least: 42 });
    const parked = env.win.y;
    await env.ask({ height: 164, least: 70 });
    check("parked, the last line comes with a button: the window goes up by its height",
          [env.win.y, env.height()], [parked - 28, 70]);
    await env.ask({ height: 136, least: 42 });
    check("…and the line goes: back where the user parked it", [env.win.y, env.height()], [parked, 42]);
  }
  {
    const env = makeFigma({ at: { x: 1600, y: parkedAt(42) } });
    await env.ask({ height: 136, least: 42 });
    await env.ask({ height: 164, least: 70 });
    env.win.y = 400;  // the user drags it
    await env.ask({ height: 164, least: 70 });
    await env.ask({ height: 136, least: 42 });
    check("the user moved it meanwhile: the line goes and the window stays", env.win.y, 400);
  }
  {
    const env = makeFigma({ at: { x: 1600, y: parkedAt(42) } });
    await env.ask({ height: 136, least: 42 });
    const parked = env.win.y;
    await env.ask({ height: 300, least: 42, lift: 300, island: "here" });
    check("an island opens in a parked window", env.win.y, parked - (300 - 42));
    await env.ask({ height: 328, least: 70 });
    check("…the last line comes: up by as much again", env.win.y, parked - (300 - 42) - 28);
    await env.ask({ height: 164, least: 70, drop: ["here"] });
    check("…the island closes: down to what the line needs", env.win.y, parked - 28);
    await env.ask({ height: 136, least: 42 });
    check("…the line goes: back where the user parked it", env.win.y, parked);
  }
  {
    const env = makeFigma({ at: { x: 1600, y: 100 } });
    await env.ask({ height: 136, least: 42 });
    await env.ask({ height: 164, least: 70 });
    check("room enough under it: the line comes and nothing moves", env.moves, []);
  }
  {
    const env = makeFigma({ at: { x: 1600, y: parkedAt(60) } });
    await env.ask({ height: 136, least: 42 });
    const low = env.win.y;
    await env.ask({ height: 164, least: 70 });
    check("room for its least but not for the line with a button: up by what is missing",
          [env.win.y, env.height()], [low - 10, 70]);
    await env.ask({ height: 136, least: 42 });
    check("…and back when the line goes", [env.win.y, env.height()], [low, 60]);
  }

  console.log(failed ? `\n${failed} FAILED` : "\nall window place checks passed");
  process.exit(failed ? 1 : 0);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
