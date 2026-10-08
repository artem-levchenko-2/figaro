"""The CLI against live Figma — run by hand, not by pytest.

    FIGARO_TEST_FILE=<key or link> venv/bin/python tests/live_cli.py    # ~1 min

Only in a scratch file of your own (tests/live_file.py). Through the CLI, as
agents call it: links in -T and in node ids, inspect, shot (a tall frame in
parts), link, --lib with an error inside the lib, fonts and Font Awesome, the
auto-layout helpers, find, two-phase rm, clone without moving the view, quick
reads. It makes a frame "figaro-cli" far off the canvas on the current page
and removes it at the end. Needs the bridge (8788) and the plugin of the
current build running in that file.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import live_file

ROOT = Path(__file__).resolve().parent.parent
PORT = live_file.PORT
DRAFT = live_file.key()
AGENT = "live-cli"
FIND = "figma.currentPage.findAll((n) => n.name.startsWith('figaro-cli'))"
ENV = dict(os.environ, FIGARO_PORT=PORT, FIGARO_AGENT=AGENT, FIGARO_AUTOSTART="0")

failed = 0


def cli(*args, stdin=None):
    r = subprocess.run([sys.executable, str(ROOT / "figaro.py"), *args], env=ENV, input=stdin,
                       capture_output=True, text=True, timeout=120)
    return r.returncode, r.stdout, r.stderr


def js(code, *flags):
    """A script in the draft; its value."""
    rc, out, err = cli("exec", "-T", DRAFT, *flags, "--raw", code)
    body = json.loads(out)
    if not body.get("ok"):
        raise RuntimeError(f"{body.get('error')}\n{code}")
    return body.get("value")


def check(label, ok, detail=None):
    global failed
    if not ok:
        failed += 1
    print(f"{'  ok  ' if ok else ' FAIL '} {label}"
          + ("" if ok or detail is None else f"\n         {str(detail)[:900]}"))


def main():
    entry, status = live_file.connected(DRAFT)
    if entry.get("plugin") != status.get("plugin_version"):
        sys.exit(f"the test file runs build {entry.get('plugin')}, the disk has "
                 f"{status.get('plugin_version')} — figaro reload -T {DRAFT}")
    DRAFT_URL = live_file.url(entry)
    if js(f"return {FIND}.length;", "-R"):
        sys.exit("figaro-cli nodes are left from an earlier run — remove them first")
    tmp = Path(tempfile.mkdtemp(prefix="figaro-cli-"))
    shots = tmp / "shots"
    ENV["FIGARO_SHOTS"] = str(shots)

    # ── links as the target ───────────────────────────────────────────────
    rc, out, err = cli("sel", "-T", DRAFT_URL + "?node-id=0-1")
    check("-T takes a link", rc == 0, err)
    other = "QqQqQqQqQqQqQqQqQqQqQq"
    rc, out, err = cli("sel", "-T", f"https://www.figma.com/design/{other}/Landing?node-id=2-3")
    check("a file that is not open: how to open it",
          rc != 0 and f"open https://www.figma.com/design/{other}/Landing" in err
          and "Figaro" in err, err)

    # ── a frame to work on, with a picture of it ──────────────────────────
    make = """
const f = figma.createFrame();
f.name = "figaro-cli"; f.x = -30000; f.y = -30000;
f.layoutMode = "VERTICAL"; f.itemSpacing = 16;
f.paddingTop = f.paddingBottom = 24; f.paddingLeft = f.paddingRight = 20;
f.fills = h.solid("#f4f6fb");
await figma.loadFontAsync({family: "Inter", style: "Regular"});
await figma.loadFontAsync({family: "Inter", style: "Bold"});
const t = figma.createText();
t.name = "figaro-cli title"; t.characters = "Hello bold world, a long line that will wrap later";
t.setRangeFontName(6, 10, {family: "Inter", style: "Bold"});
t.fontSize = 18;
f.appendChild(t);
const r = figma.createRectangle();
r.name = "figaro-cli rect"; r.resize(120, 40);
f.appendChild(r);
const tall = figma.createFrame();
tall.name = "figaro-cli tall"; tall.resize(400, 4000);
tall.fills = [{type: "GRADIENT_LINEAR", gradientTransform: [[0, 1, 0], [-1, 0, 1]],
  gradientStops: [{position: 0, color: {r: 1, g: 0.3, b: 0.2, a: 1}}, {position: 1, color: {r: 0.2, g: 0.3, b: 1, a: 1}}]}];
f.appendChild(tall);
const loose = figma.createFrame();
loose.name = "figaro-cli loose"; loose.resize(100, 100);
f.appendChild(loose);
loose.layoutPositioning = "ABSOLUTE";
const lr = figma.createRectangle(); lr.name = "figaro-cli loose child"; loose.appendChild(lr);
return {frame: f.id, text: t.id, rect: r.id, tall: tall.id, loose: loose.id, looseChild: lr.id};"""
    rc, out, err = cli("exec", "-T", DRAFT, "--shot", make)
    check("exec --shot: made the frame", rc == 0, err)
    ids, end = json.JSONDecoder().raw_decode(out)  # the value, then the picture's path
    frame = ids["frame"]
    check("…a link to what it created", re.search(
        '  created: "figaro-cli" ' + re.escape(f"{DRAFT_URL}?node-id={frame.replace(':', '-')}"), err), err)
    pic = list((shots / DRAFT).glob(frame.replace(":", "-") + "*.png"))
    check(f"…and a picture of it ({len(pic)} file(s))", len(pic) >= 1 and str(shots) in out[end:], out)

    # ── inspect ───────────────────────────────────────────────────────────
    frame_url = f"{DRAFT_URL}?node-id={frame.replace(':', '-')}"
    rc, out, err = cli("inspect", frame_url)
    check("inspect <link>: header with the link", rc == 0 and out.startswith('"figaro-cli" FRAME ' + frame)
          and frame_url in out, out[:400] + err)
    check("…auto-layout line", re.search(r"↓ gap 16 .*pad 24 20", out), out[:600])
    check("…the bold word as its own segment", re.search(r'┆ "bold" Inter Bold 18', out), out)
    rc, out, err = cli("inspect", frame, "-T", DRAFT, "--json")
    data = json.loads(out)
    check("inspect --json", [c["name"] for c in data["node"]["children"]][:3]
          == ["figaro-cli title", "figaro-cli rect", "figaro-cli tall"], data["node"].get("children"))
    dest = tmp / "inspect.json"
    rc, out, err = cli("inspect", frame, "-T", DRAFT, "-o", str(dest))
    check("inspect -o", rc == 0 and json.loads(dest.read_text())["node"]["id"] == frame, out + err)

    inst, comp_set = js("""
let inst = null, set = null;
for (const p of figma.root.children) {
  inst = inst || p.findAllWithCriteria({types: ['INSTANCE']})[0];
  set = set || p.findAllWithCriteria({types: ['COMPONENT_SET']})[0];
}
return [inst && inst.id, set && set.id];""", "-R")
    if inst:
        rc, out, err = cli("inspect", inst, "-T", DRAFT, "--depth", "1")
        check(f"inspect an instance ({inst}): its component id and key",
              rc == 0 and "⟐ " in out and "\ncomponents:\n" in out
              and re.search(r" — id [\w:;,-]+ · key [0-9a-f]{40}", out), out[:800] + err)
    if comp_set:
        rc, out, err = cli("inspect", comp_set, "-T", DRAFT, "--depth", "0")
        check(f"inspect a component set ({comp_set}): key and properties",
              rc == 0 and re.search(r"◆ key [0-9a-f]{40}", out) and "┆ prop " in out, out[:800] + err)
    if not inst or not comp_set:
        print("  --   no instance or component set in the test file — keys not checked live")

    # ── shot ──────────────────────────────────────────────────────────────
    rc, out, err = cli("shot", ids["tall"], "-T", DRAFT)
    parts = sorted((shots / DRAFT).glob(ids["tall"].replace(":", "-") + "-p*.png"))
    check(f"shot of a 400×4000 frame at 2×: {len(parts)} parts", rc == 0 and len(parts) == 5, out + err)
    sizes = [tuple(map(int, m)) for m in re.findall(r"  (\d+)×(\d+) · part", out)]
    check("…each at most 1600 px tall, 8000 px together",
          len(sizes) == 5 and all(h <= 1600 for w, h in sizes) and sum(h for w, h in sizes) == 8000, out)
    # "figaro-cli" is 100 px wide with 20 px padding and clips its content: Figma
    # exports what it shows, so 80 px of the 400 px frame, at 2×.
    check("…clipped by its parent as in Figma: 160 px wide", {w for w, h in sizes} == {160}, sizes)
    rc, out, err = cli("shot", ids["rect"], "-T", DRAFT, "--format", "svg")
    check("shot --format svg", rc == 0 and (shots / DRAFT / (ids["rect"].replace(":", "-") + ".svg")).exists(), out + err)

    # ── link ──────────────────────────────────────────────────────────────
    rc, out, err = cli("link", ids["text"], "-T", DRAFT)
    check("link", out.startswith(f'{DRAFT_URL}?node-id={ids["text"].replace(":", "-")}  "figaro-cli title"'), out + err)

    # ── --lib ─────────────────────────────────────────────────────────────
    lib = tmp / "grid.js"
    lib.write_text("// helpers of one agent\n"
                   "export function names(root) {\n"
                   "  return root.children.map((c) => c.name);\n"
                   "}\n"
                   "const GAP = 24;\n"
                   "function boom(node) {\n"
                   "  return node.nothing.here;\n"
                   "}\n")
    rc, out, err = cli("exec", "-T", DRAFT, "-R", "--lib", str(lib),
                       f"return [lib.GAP, ...lib.names(await h.node('{frame}'))];")
    check("--lib: functions and consts as lib.*", rc == 0 and json.loads(out)[:3] == [24, "figaro-cli title", "figaro-cli rect"],
          out + err)
    rc, out, err = cli("exec", "-T", DRAFT, "-R", "--lib", str(lib), f"return lib.names(await h.node('{frame}')).length;")
    check("…again, by hash", rc == 0 and out.strip() == "4", out + err)
    rc, out, err = cli("exec", "-T", DRAFT, "-R", "--lib", str(lib), "const n = 1;\nreturn lib.boom({});")
    check("an error inside the lib names its line (QuickJS)",
          rc == 1 and "grid.js, line 7: return node.nothing.here;" in err, err)

    # ── fonts ─────────────────────────────────────────────────────────────
    got = js(f"return await h.fonts(await h.node('{ids['text']}'));", "-R")
    check("h.fonts on a two-font text", got == {"loaded": 2, "missing": []}, got)
    fa = js("return await h.fa();", "-R")
    if fa.get("pro"):
        got = js("return await h.fonts({family: 'Font Awesome 1 Pro', style: 'Solid'});", "-R")
        check(f"a missing Font Awesome: names what is here ({fa['pro']})",
              got.get("missing") == ["Font Awesome 1 Pro Solid"] and fa["pro"] in got.get("note", ""), got)
    else:
        print("  --   no Font Awesome Pro installed here — h.fa not checked live")

    # ── auto-layout helpers ───────────────────────────────────────────────
    got = js(f"""
const f = await h.node('{frame}'), r = await h.node('{ids['rect']}'), t = await h.node('{ids['text']}');
await h.fixed(f, 480);
await h.hug(f, 'y');
await h.fill(r);
await h.wrapText(t);
return [f.width, f.layoutSizingHorizontal, f.layoutSizingVertical, r.layoutSizingHorizontal, r.width,
        t.textAutoResize, t.layoutSizingHorizontal, t.width];""")
    check("h.fixed / h.hug / h.fill / h.wrapText", got == [480, "FIXED", "HUG", "FILL", 440, "HEIGHT", "FILL", 440], got)
    rc, out, err = cli("exec", "-T", DRAFT, f"await h.fill(await h.node('{ids['looseChild']}')); return 1;")
    check("h.fill outside auto-layout says why", rc == 1 and "only a child of an auto-layout frame can FILL" in err, err)
    rc, out, err = cli("exec", "-T", DRAFT, f"(await h.node('{frame}')).counterAxisAlignItems = 'STRETCH'; return 1;")
    check("STRETCH: the hint names h.fill", rc == 1 and "h.fill" in err, err)

    # ── find ──────────────────────────────────────────────────────────────
    rc, out, err = cli("find", frame, "name~figaro-cli", "-T", DRAFT)
    check("find name~", rc == 0 and len(json.loads(out)) == 5, out + err)
    rc, out, err = cli("find", frame_url, "type=text")
    check("find type= by link", rc == 0 and [x["name"] for x in json.loads(out)] == ["figaro-cli title"], out + err)

    # ── rm finds every id first ───────────────────────────────────────────
    rc, out, err = cli("rm", ids["rect"], "999999:1", "-T", DRAFT)
    check("rm with a wrong id removes nothing", rc == 1 and "nothing removed" in err
          and js(f"return !!(await figma.getNodeByIdAsync('{ids['rect']}'));", "-R"), err)

    # ── clone keeps the view ──────────────────────────────────────────────
    view = "return [figma.viewport.center.x, figma.viewport.center.y, figma.viewport.zoom];"
    before = js(view, "-R")
    rc, out, err = cli("clone", ids["rect"], "-T", DRAFT, "--down", "--name", "figaro-cli clone")
    check("clone without --focus leaves the view", rc == 0 and js(view, "-R") == before, [before, out, err])

    # ── quick reads ───────────────────────────────────────────────────────
    def elapsed(*args):
        rc, out, err = cli(*args)
        m = re.search(r"\((\d+)ms\)", err)
        return int(m.group(1)) if m else None
    quick = [elapsed("sel", "-T", DRAFT) for _ in range(3)]
    full = [elapsed("exec", "-T", DRAFT, "-R", "return h.sel();") for _ in range(3)]
    check(f"a quick read is faster: sel {quick} ms, exec -R {full} ms",
          None not in quick + full and max(quick) < 100 and min(full) >= 150, [quick, full])

    # ── clean up ──────────────────────────────────────────────────────────
    rc, out, err = cli("rm", frame, ids["looseChild"], "-T", DRAFT, "--no-checkpoint")
    check("rm a frame and its child: the child is skipped", rc == 0
          and f"already removed with a parent or listed twice: {ids['looseChild']}" in err, out + err)
    left = js(f"for (const n of {FIND}) n.remove(); return 1;", "--no-checkpoint")
    check("clean: no figaro-cli nodes left", js(f"return {FIND}.length;", "-R") == 0, left)
    print(f"\n  pictures: {shots}")
    print(f"\n{failed} FAILED" if failed else "\nall live CLI checks passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
