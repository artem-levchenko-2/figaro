// The window's height and place (plugin/code.js, fitWindow) against a stub of
// Figma's canvas: the window is as tall as its islands and stays where the
// user put it while there is room under it. With no room it moves up instead,
// just enough and at most to the canvas's top, and as it gets shorter it comes
// back down, never past where the user put it. What the user left below the
// canvas's bottom edge stays out of sight there. Only a window taller than the
// canvas stops short of its height, and its islands scroll. Minimized, it is
// narrower, and it keeps its bottom right corner, as it does restored, within
// Figma's window.
//
//     node tests/window.test.js
const fs = require("fs");
const path = require("path");
const src = fs.readFileSync(path.join(__dirname, "..", "plugin", "code.js"), "utf8");

const HEADER = 40;  // FIGMA_HEADER in code.js
const GAP = 8;      // CANVAS_GAP

// Figma's screen: the canvas starts `canvasTop` px down the window space and
// is `env.canvasHeight` px tall, at `env.zoom`; the plugin window's corner is
// at `at`. getPosition gives that corner in window space and on the canvas, as
// Figma does, and reposition takes the canvas's coordinates. With `pixels`, as
// Figma does it (checked live), getPosition gives the corner in whole pixels and
// on the canvas in whole canvas units: at 280 % one is 2.8 px.
function makeFigma({ at = { x: 1600, y: 300 }, canvasTop = 0, canvasLeft = 332, canvasHeight = 1000, zoom = 1,
                     origin = { x: -1000, y: -686 }, pixels = false, noPosition = false, noMove = false } = {}) {
  const view = { x: origin.x, y: origin.y };  // the canvas point at the canvas's top left corner
  const win = { x: at.x, y: at.y };
  const sizes = [];
  const widths = [];
  const moves = [];
  const places = [];  // where reposition put it, [x, y]
  const told = [];
  const ui = {
    onmessage: null,
    postMessage(m) { told.push(m); },
    resize(w, h) { sizes.push(h); widths.push(w); },
  };
  if (!noPosition) {
    ui.getPosition = () => {
      const c = { x: view.x + (win.x - canvasLeft) / env.zoom, y: view.y + (win.y - canvasTop) / env.zoom };
      if (!pixels) return { windowSpace: { x: win.x, y: win.y }, canvasSpace: c };
      return { windowSpace: { x: Math.round(win.x), y: Math.round(win.y) }, canvasSpace: { x: Math.round(c.x), y: Math.round(c.y) } };
    };
  }
  if (!noPosition && !noMove) {
    ui.reposition = (cx, cy) => {
      win.x = canvasLeft + (cx - view.x) * env.zoom;
      win.y = canvasTop + (cy - view.y) * env.zoom;
      moves.push(Math.round(win.y));
      places.push([Math.round(win.x), Math.round(win.y)]);
    };
  }
  const env = { win, sizes, widths, moves, places, told, zoom, canvasHeight };
  const figma = {
    root: { name: "Draft", children: [] },
    fileKey: "KEY",
    showUI() {},
    ui,
    get viewport() {
      return { zoom: env.zoom, bounds: { x: view.x, y: view.y, width: 1987 / env.zoom, height: env.canvasHeight / env.zoom } };
    },
    on() {},
  };
  new Function("figma", "__html__", src)(figma, "");
  env.figma = figma;
  env.ask = (height, width) => figma.ui.onmessage({ type: "resize", height, width });
  env.width = () => widths[widths.length - 1];
  env.height = () => sizes[sizes.length - 1];
  env.bottom = () => Math.round(win.y) + HEADER + env.height();       // the window's bottom edge
  env.floor = () => canvasTop + env.canvasHeight - GAP;                // where it may end
  env.room = () => told.filter((m) => m.type === "room").map((m) => m.height).pop();
  return env;
}

let failed = 0;
function check(label, actual, expected) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  const ok = a === e;
  if (!ok) failed++;
  console.log(`${ok ? "  ok  " : " FAIL "} ${label}${ok ? "" : `\n         got ${a}\n         want ${e}`}`);
}

(async () => {
  // ─── room under it ──────────────────────────────────────────────────────
  {
    const env = makeFigma({ at: { x: 1600, y: 100 } });
    await env.ask(300);
    check("room under it: as tall as its islands", env.height(), 300);
    await env.ask(800);
    check("…and it grows down while there is room", [env.height(), env.moves], [800, []]);
    const n = env.sizes.length;
    await env.ask(800);
    check("asked again with nothing new: no resize, no move", [env.sizes.length, env.moves], [n, []]);
  }

  // ─── no room under it ───────────────────────────────────────────────────
  {
    const env = makeFigma({ at: { x: 1600, y: 700 } });
    await env.ask(142);
    check("low on the canvas, short: it stays where the user put it", [env.height(), env.moves], [142, []]);
    await env.ask(400);
    check("an island opens with no room under it: the window moves up instead, just enough",
          [env.height(), env.bottom()], [400, env.floor()]);
    await env.ask(142);
    check("…and as it gets shorter it comes back down where the user put it", [env.win.y, env.height()], [700, 142]);
    await env.ask(100);
    check("…never past it", env.win.y, 700);
  }
  {
    const env = makeFigma({ at: { x: 1600, y: 600 } });
    await env.ask(200);
    const tops = [], bottoms = [];
    for (let h = 220; h <= 500; h += 20) {
      await env.ask(h);
      tops.push(Math.round(env.win.y));
      bottoms.push(env.bottom());
    }
    check("a frame at a time: the window grows down to the canvas's bottom first…",
          bottoms.slice(0, 8), [220, 240, 260, 280, 300, 320, 340].map((h) => 600 + HEADER + h).concat(env.floor()));
    check("…then its bottom stays there and its top goes up",
          [bottoms.slice(7).every((b) => b === env.floor()), tops[tops.length - 1]], [true, env.floor() - HEADER - 500]);
  }
  {
    const env = makeFigma({ at: { x: 1600, y: 300 }, canvasHeight: 600 });
    await env.ask(142);
    await env.ask(900);
    check("taller than the canvas: up to its top, no further", Math.round(env.win.y), GAP);
    check("…and it fills the canvas, the rest scrolls", env.height(), 600 - GAP - GAP - HEADER);
    check("…which ui.html is told: how tall the window may get", env.room(), 600 - GAP - GAP - HEADER);
    await env.ask(142);
    check("…and it goes back down when the islands get short again", Math.round(env.win.y), 300);
  }

  // ─── the user moves it ──────────────────────────────────────────────────
  {
    const env = makeFigma({ at: { x: 1600, y: 700 } });
    await env.ask(142);
    await env.ask(400);
    env.win.y = 300;  // the user drags it up
    await env.ask(400);
    await env.ask(142);
    check("the user moves the window: where they put it is its new place", [env.win.y, env.height()], [300, 142]);
    env.win.y = 750;  // and down again
    await env.ask(142);
    await env.ask(400);
    check("…and from there it moves up again when it has to", env.bottom(), env.floor());
  }
  {
    const env = makeFigma({ at: { x: 1600, y: 700 } });
    await env.ask(142);
    env.win.x = 900;  // sideways only
    await env.ask(142);
    await env.ask(400);
    check("a window moved sideways keeps its new x", Math.round(env.win.x), 900);
  }

  // ─── a window dragged partly off the canvas ─────────────────────────────
  {
    const parked = 1000 - GAP - HEADER - 42;  // only its first island shows
    const env = makeFigma({ at: { x: 1600, y: parked } });
    await env.ask(142);
    check("dragged partly below the canvas's bottom: it stays there, the rest out of sight",
          [env.win.y, env.height(), env.moves], [parked, 142, []]);
    await env.ask(400);
    check("an island opens: the window rises by as much as it grew, the rest still out of sight",
          Math.round(env.win.y), parked - (400 - 142));
    await env.ask(142);
    check("…and goes back down when it closes", env.win.y, parked);
    await env.ask(184);
    check("anything new rises it by its height, so no more of the window goes out of sight",
          Math.round(env.win.y), parked - 42);
  }

  // ─── minimized ──────────────────────────────────────────────────────────
  {
    const env = makeFigma({ at: { x: 1600, y: 300 } });
    await env.ask(300, 320);
    await env.ask(42, 240);
    check("minimized: 240 px wide, its bottom right corner where it was, so Minimize's spot is restore's",
          [env.width(), env.height(), Math.round(env.win.x), Math.round(env.win.y)], [240, 42, 1600 + 80, 300 + 300 - 42]);
    await env.ask(42, 70);
    check("…nano: 70 px, the same corner", [env.width(), Math.round(env.win.x), Math.round(env.win.y)], [70, 1600 + 250, 300 + 300 - 42]);
    await env.ask(300, 320);
    check("…and restored, it comes back where it was", [env.width(), env.height(), Math.round(env.win.x), Math.round(env.win.y)], [320, 300, 1600, 300]);
    await env.ask(500, 320);
    check("…and grows down from there as before", [Math.round(env.win.y), env.height()], [300, 500]);
    await env.ask(42, 10);
    check("never narrower than Figma allows", env.width(), 70);
  }
  {
    const env = makeFigma({ at: { x: 1600, y: 300 } });
    await env.ask(300, 320);
    await env.ask(42, 70);
    env.win.x = 332 + 20;  // the user puts the nano window by the canvas's left edge
    await env.ask(42, 70);
    await env.ask(300, 320);
    check("restored by the canvas's left edge: its corner stays, so restore stays under the pointer, over the layers",
          [env.width(), Math.round(env.win.x)], [320, 332 + 20 - 250]);
    await env.ask(42, 70);
    check("…and minimized again, it is back where it was", Math.round(env.win.x), 332 + 20);
    env.win.x = 100;  // over the layers
    await env.ask(42, 70);
    await env.ask(300, 320);
    check("…but Figma's window stops it: restored over the layers, it goes no further left than its edge", Math.round(env.win.x), 0);
  }
  {
    // the window glides from one size to the other a frame at a time: every step keeps the corner,
    // a step that changes only the height too
    const env = makeFigma({ at: { x: 1600, y: 300 } });
    await env.ask(300, 320);
    const corner = () => [Math.round(env.win.x) + env.width(), Math.round(env.win.y) + env.height()];
    const at = corner(), seen = [];
    for (const [h, w] of [[250, 300], [200, 280], [150, 260], [100, 245], [60, 240], [42, 240]]) {
      await env.figma.ui.onmessage({ type: "resize", height: h, width: w, corner: true });
      seen.push(corner());
    }
    check("gliding to mini: every step keeps the bottom right corner, a step of height alone too",
          seen.every((c) => c[0] === at[0] && c[1] === at[1]), true);
    await env.ask(42, 240);
    check("…and the ask after the glide changes nothing", corner(), at);
  }
  {
    const parked = 1000 - GAP - HEADER - 42;
    const env = makeFigma({ at: { x: 1600, y: parked } });
    await env.ask(142);
    await env.ask(42, 240);
    check("minimized while partly below the canvas: it ends at the canvas's bottom, in sight", env.bottom(), env.floor());
  }
  {
    const env = makeFigma({ at: { x: 1600, y: 300 } });
    await env.ask(300);
    check("a window that doesn't say its width is 320 px wide", env.width(), 320);
  }

  // ─── the canvas ─────────────────────────────────────────────────────────
  {
    const near = makeFigma({ at: { x: 1600, y: 700 }, zoom: 1 });
    const far = makeFigma({ at: { x: 1600, y: 700 }, zoom: 0.25 });
    await near.ask(142); await far.ask(142);
    await near.ask(500); await far.ask(500);
    check("the canvas's zoom doesn't change the place", [Math.round(far.win.y), far.height()], [Math.round(near.win.y), near.height()]);
  }
  {
    const env = makeFigma({ at: { x: 1600, y: 748 }, canvasTop: 48 });
    await env.ask(142);
    await env.ask(500);
    check("a canvas that starts lower (a banner above it): its edges are counted from it", env.bottom(), env.floor());
  }
  {
    const env = makeFigma({ at: { x: 1600, y: 500 } });
    await env.ask(400);
    env.canvasHeight = 800;  // Figma's window got shorter
    await env.ask(400);
    check("the canvas gets shorter: the window moves up to stay on it", env.bottom(), env.floor());
    env.canvasHeight = 1000;
    await env.ask(400);
    check("…and back down where the user put it when it grows again", env.win.y, 500);
  }
  {
    // Figma's numbers for the canvas come out a hair off at most zooms (991.9999999999998 for 992), and
    // the window's corner on the canvas comes in whole canvas units (at 280 %, 2.8 px): how far off the
    // canvas's edges come out depends on where the window is. A window held at the canvas's bottom by
    // them moved a pixel and back every second as ui.html asked again, and at 280 % sideways too
    const moved = [];
    for (const [zoom, originY, y, asks] of [[0.13, 13828.25, 500, [142, 600]], [0.09, 2207.33, 700, [142, 545]],
                                             [0.13, 13828.25, 600, [545]],  // partly below the canvas
                                             [2.8164684772491455, -213.3877978810035, 700, [142, 545]],  // live, full screen
                                             [2.8164684772491455, -213.3877978810035, 400, [142, 700]],
                                             [10, 51.37, 600, [142, 545]]]) {
      const env = makeFigma({ at: { x: 1600, y }, zoom, origin: { x: -664.662159123525, y: originY }, pixels: true });
      for (const h of asks) await env.ask(h);
      const n = env.places.length;
      for (let i = 0; i < 6; i++) await env.ask(asks[asks.length - 1]);
      const drift = Math.abs(Math.round(env.win.x) - 1600);
      if (env.places.length > n || drift > 2) moved.push(`zoom ${zoom}, from y ${y}: ${env.places.slice(n).join(" ")}, x off by ${drift}`);
    }
    check("held at the canvas's bottom, the window stays put while it asks again, at any zoom", moved, []);
  }
  {
    // where Figma puts it is a little off at a large zoom, but it ends at the canvas's bottom all the same
    const env = makeFigma({ at: { x: 1600, y: 700 }, zoom: 2.8164684772491455, origin: { x: -664.66, y: -213.39 }, pixels: true });
    await env.ask(142);
    await env.ask(545);
    check("at 280 %: the window ends within a pixel of the canvas's bottom", Math.abs(env.bottom() - env.floor()) <= 1, true);
  }
  {
    const env = makeFigma({ noPosition: true });
    await env.ask(300);
    check("a Figma that can't say where the window is: as tall as the islands", env.height(), 300);
    await env.ask(2600);
    check("…up to 2000 px", env.height(), 2000);
  }
  {
    const env = makeFigma({ at: { x: 1600, y: 700 }, noMove: true });
    await env.ask(500);
    check("a Figma that can't move the window: it ends at the canvas's bottom", env.bottom(), env.floor());
  }

  console.log(failed ? `\n${failed} FAILED` : "\nall window place checks passed");
  process.exit(failed ? 1 : 0);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
