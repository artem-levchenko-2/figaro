"""The CLI's own helpers (cli_extras.py, inspect_text.py, bridge_idle.py):
links in arguments, starting the bridge, inspect, shot,
link, --lib, exec --shot, and the bridge leaving: when idle, and with its
plugin sockets closed at once.

A fake bridge answers the CLI here; the plugin's side is in
tests/commands.test.js. Three tests start a real bridge on a free port: with
start-bridge.sh, with start-bridge.ps1 on Windows, and directly, to stop it
with SIGTERM.

    pytest tests/test_cli.py
"""
import asyncio
import base64
import http.server
import io
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import aiohttp
import pytest
from aiohttp.test_utils import TestClient, TestServer

import bridge
import figaro
import cli_extras
import bridge_idle
import inspect_text

ROOT = Path(__file__).resolve().parent.parent
KEY = "AbCdEfGhIjKlMnOpQrStUv"
URL = f"https://www.figma.com/design/{KEY}/Sandbox-5-?node-id=1-11&t=x"


def png(w, h, color=(200, 30, 30)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


def shot(node="1:2", data=b"", fmt="PNG", **extra):
    return {"kind": "shot", "id": node, "name": "Card", "type": "FRAME", "file": "Sandbox 5$",
            "fileKey": KEY, "format": fmt, "scale": 2, "w": 10, "h": 10, "size": len(data),
            "data": base64.b64encode(data).decode(), **extra}


class FakeBridge:
    """Stands in for figaro._request: records each call, answers with
    `reply(payload)`."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, method, path, payload=None, timeout=65):
        self.calls.append(payload)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        body = reply(payload) if callable(reply) else reply
        return (200 if body.get("ok", True) else 500), body


def cli(monkeypatch, capsys, *argv, bridge_reply=None):
    fake = bridge_reply if isinstance(bridge_reply, FakeBridge) else FakeBridge(
        bridge_reply or {"ok": True, "value": 1})
    monkeypatch.setattr(figaro, "_request", fake)
    monkeypatch.setattr(sys, "argv", ["figaro", *argv])
    with pytest.raises(SystemExit) as done:
        figaro.main()
    out = capsys.readouterr()
    return done.value.code, out.out, out.err, fake.calls


@pytest.fixture(autouse=True)
def no_autostart(monkeypatch, tmp_path):
    monkeypatch.setenv("FIGARO_AUTOSTART", "0")
    monkeypatch.setattr(cli_extras, "_started", False)
    monkeypatch.setattr(cli_extras, "SHOTS", tmp_path / "shots")


# ─── links in arguments ──────────────────────────────────────────────────

def args_of(*argv):
    args = figaro.build_parser().parse_args(list(argv))
    if args.cmd == "exec":
        cli_extras.fix_exec_args(args)
    return args, cli_extras.target_and_ids(args)


def test_a_layer_link_gives_the_id_and_the_file():
    # The link itself goes as the target: the bridge resolves it, and if the
    # file is not open, tells to open this very link.
    args, target = args_of("tree", URL)
    assert (args.node_id, target) == ("1:11", URL)
    args, target = args_of("tree", "1-11")
    assert (args.node_id, target) == ("1:11", None)
    args, target = args_of("rm", URL, "I5-6;7-8", "sel")
    assert (args.node_ids, target) == (["1:11", "I5:6;7:8", "sel"], URL)


def test_t_takes_a_link_and_a_name_stays_a_name():
    assert args_of("sel", "-T", URL)[1] == URL
    assert args_of("tree", URL, "-T", "Sandbox")[1] == "Sandbox"
    assert args_of("tree", URL, "-T", KEY)[1] == KEY
    assert args_of("tree", URL, "-T", f"https://www.figma.com/design/{KEY}/X")[1] == \
        f"https://www.figma.com/design/{KEY}/X"


def test_links_into_two_files_are_refused():
    other = f"https://www.figma.com/design/{'Q' * 22}/X?node-id=2-3"
    with pytest.raises(SystemExit, match="different files"):
        args_of("rm", URL, other)
    with pytest.raises(SystemExit, match="the layer is from file"):
        args_of("tree", URL, "-T", "Q" * 22)
    with pytest.raises(SystemExit, match="the layer is from file"):
        args_of("tree", URL, "-T", f"https://www.figma.com/design/{'Q' * 22}/X")
    with pytest.raises(SystemExit, match="has no node-id"):
        args_of("tree", f"https://www.figma.com/design/{KEY}/UI")


def test_exec_shot_flag_and_code():
    args, target = args_of("exec", "--shot", "return 1")
    assert (args.code, args.shot) == ("return 1", "auto")
    args, target = args_of("exec", "return 1", "--shot")
    assert (args.code, args.shot) == ("return 1", "auto")
    args, target = args_of("exec", "--shot", URL, "return 1")
    assert (args.code, args.shot, target) == ("return 1", "1:11", URL)
    args, _ = args_of("exec", "--shot", "sel", "return 1")
    assert (args.code, args.shot) == ("return 1", "sel")
    args, _ = args_of("exec", "return 1")
    assert args.shot is None


# ─── the commands ────────────────────────────────────────────────────────

INSPECT = {
    "file": "Sandbox 5$", "fileKey": KEY, "url": URL, "nodes": 3,
    "node": {
        "id": "1:11", "name": "Card", "type": "FRAME", "w": 320, "h": 200,
        "layout": {"mode": "VERTICAL", "gap": 12, "main": "MIN", "cross": "CENTER",
                   "pad": [24, 16, 24, 16]},
        "sizing": "FIXED/HUG", "radius": 12, "styles": {"fill": "Surface/Card"},
        "fills": [{"type": "SOLID", "color": "#ffffff", "var": "surface/card"}],
        "children": [
            {"id": "1:12", "name": "Title", "type": "TEXT", "w": 288, "h": 24, "x": 16, "y": 24,
             "sizing": "FILL/HUG", "text": "Hello world", "textStyle": "Heading/H3",
             "font": {"family": "Inter Semi Bold", "size": 20, "lineHeight": "24px",
                      "letterSpacing": "0px", "align": "LEFT"}, "autoResize": "HEIGHT",
             "segments": [{"text": "Hello ", "font": "Inter Semi Bold", "size": 20},
                          {"text": "world", "font": "Inter Bold", "size": 20,
                           "fills": [{"type": "SOLID", "color": "#ff0000"}]}]},
            {"id": "1:13", "name": "Button", "type": "INSTANCE", "w": 120, "h": 40, "x": 16, "y": 60,
             "component": "Button / Size=M", "props": {"Label#12:0": "Go", "Size": "M"},
             "refs": {"visible": "Show#3:0"}},
        ],
        "hiddenKids": 1,
    },
    "components": {"Button / Size=M": {"id": "1:7177", "key": "c" * 40, "remote": True, "set": "Button",
                                       "setKey": "s" * 40}},
    "styles": {"Surface/Card": {"id": "S:" + "f" * 40 + ",2739:8", "key": "f" * 40, "type": "PAINT",
                                "remote": True}},
    "variables": {"surface/card": {"id": "VariableID:3:4", "key": "v" * 40, "type": "COLOR",
                                   "collection": "Tokens", "remote": False}},
    "collections": {"Tokens": {"key": "k" * 40, "remote": False, "modes": ["Light", "Dark"]}},
}


def test_inspect_prints_compact_text_from_a_quick_read(monkeypatch, capsys):
    rc, out, err, calls = cli(monkeypatch, capsys, "inspect", URL,
                              bridge_reply={"ok": True, "value": INSPECT, "elapsed_ms": 9})
    assert rc == 0
    (payload,) = calls
    assert payload["quick"] is True and payload["target"] == URL
    assert 'h.inspect(await h.resolve("1:11"), {"depth": 8, "hidden": false})' in payload["code"]
    lines = out.splitlines()
    assert lines[0] == '"Card" FRAME 1:11 in "Sandbox 5$" · 3 layers'
    assert lines[1] == URL
    assert any(line.startswith("Card [FRAME] 1:11 320×200 · ↓ gap 12 align min/center pad 24 16")
               and "fixed×hug" in line and "'Surface/Card' #ffffff (surface/card)" in line
               and "+1 hidden" in line for line in lines)
    title = next(line for line in lines if "Title [TEXT]" in line)
    assert "@" not in title  # in an auto-layout flow the position says nothing
    assert '"Hello world"' in title and "Inter Semi Bold 20/24" in title and "style 'Heading/H3'" in title
    assert any("┆" in line and '"world" Inter Bold 20' in line and "#ff0000" in line for line in lines)
    assert any("⟐ Button / Size=M" in line and 'Label="Go"' in line and "← visible Show#3:0" in line
               for line in lines)
    assert any(line.startswith("in this file use the id:") for line in lines)
    assert f"  Button / Size=M — id 1:7177 · key {'c' * 40} · set 'Button' {'s' * 40} · library" in lines
    assert f"  Surface/Card PAINT — id S:{'f' * 40},2739:8 · key {'f' * 40} · library" in lines
    assert f"  surface/card COLOR · Tokens — id VariableID:3:4 · key {'v' * 40} · local" in lines
    assert f"  Tokens: Light, Dark — key {'k' * 40} · local" in lines


def test_inspect_json_and_file(monkeypatch, capsys, tmp_path):
    reply = {"ok": True, "value": INSPECT}
    rc, out, err, calls = cli(monkeypatch, capsys, "inspect", "1:11", "--json", "--depth", "2",
                              "--hidden", bridge_reply=reply)
    assert json.loads(out) == INSPECT
    assert '{"depth": 2, "hidden": true}' in calls[0]["code"]
    dest = tmp_path / "out" / "card.json"
    rc, out, err, calls = cli(monkeypatch, capsys, "inspect", "1:11", "-o", str(dest), bridge_reply=reply)
    assert json.loads(dest.read_text()) == INSPECT
    summary = json.loads(out)
    assert summary["out"] == str(dest.resolve()) and summary["node"] == "1:11" and summary["nodes"] == 3


def test_inspect_cuts_a_long_listing():
    many = dict(INSPECT, node=dict(INSPECT["node"], children=[
        {"id": f"2:{i}", "name": f"Row {i}", "type": "FRAME", "w": 1, "h": 1} for i in range(50)]))
    text = inspect_text.render(many, max_lines=10)
    assert "… 41 more lines" in text
    assert [inspect_text._count(n, "layer") for n in (0, 1, 2, 21)] == [
        "0 layers", "1 layer", "2 layers", "21 layers"]
    assert [inspect_text._count(n, "more child", "more children") for n in (1, 3)] == [
        "1 more child", "3 more children"]


def test_shot_saves_a_tall_picture_in_parts(monkeypatch, capsys, tmp_path):
    data = png(40, 4000)
    rc, out, err, calls = cli(monkeypatch, capsys, "shot", "1:2", "-o", str(tmp_path),
                              bridge_reply={"ok": True, "value": [shot(data=data)]})
    assert rc == 0 and calls[0]["quick"] is True
    parts = sorted(tmp_path.glob("1-2-p*.png"))
    assert [p.name for p in parts] == ["1-2-p1.png", "1-2-p2.png", "1-2-p3.png"]
    from PIL import Image
    heights = [Image.open(p).size for p in parts]
    assert heights == [(40, 1334), (40, 1334), (40, 1332)]
    assert out.count(str(tmp_path)) == 3 and "part 1/3, y 0–1334" in out


def test_shot_default_folder_and_old_parts_go(monkeypatch, capsys):
    cli_extras.save_shots([shot(node="I1:2;3:4", data=png(10, 4000))])
    folder = cli_extras.SHOTS / KEY
    assert len(list(folder.glob("I1-2_3-4-p*.png"))) == 3
    lines = cli_extras.save_shots([shot(node="I1:2;3:4", data=png(10, 100))])
    assert [p.name for p in folder.iterdir()] == ["I1-2_3-4.png"]
    assert lines[0].startswith(str(folder / "I1-2_3-4.png") + "  10×100")


def test_shot_without_pillow_keeps_one_file(monkeypatch, tmp_path):
    data = png(10, 4000)
    monkeypatch.setitem(sys.modules, "PIL", None)
    lines = cli_extras.save_shots([shot(data=data)], tmp_path)
    assert [p.name for p in tmp_path.iterdir()] == ["1-2.png"]
    assert "Pillow" in lines[-1]


def test_svg_and_jpg_shots(tmp_path):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (30, 20)).save(buf, "JPEG")
    assert cli_extras.image_size(buf.getvalue(), "jpg") == (30, 20)
    assert cli_extras.image_size(png(7, 9), "png") == (7, 9)
    lines = cli_extras.save_shots([shot(data=b"<svg/>", fmt="SVG")], tmp_path)
    assert (tmp_path / "1-2.svg").read_bytes() == b"<svg/>" and lines


def test_link_prints_links(monkeypatch, capsys):
    value = [{"id": "1:2", "name": "Card", "url": "https://www.figma.com/design/K/F?node-id=1-2"},
             {"id": "1:3", "name": "Local", "url": None}]
    rc, out, err, calls = cli(monkeypatch, capsys, "link", "sel", bridge_reply={"ok": True, "value": value})
    assert out.splitlines() == ['https://www.figma.com/design/K/F?node-id=1-2  "Card"',
                                '(the file has no key, so no link)  "Local"']
    assert calls[0]["quick"] is True


def test_undo_says_how_many_steps_and_what_is_left(monkeypatch, capsys):
    value = {"undone": {"agent": None, "ago": "5 s ago", "changes": "+3"}, "left": 0, "steps": 3,
             "warning": "not everything was undone: 1 layers of this script are left — …"}
    rc, out, err, _ = cli(monkeypatch, capsys, "undo", "-T", KEY, bridge_reply={"ok": True, "value": value})
    assert rc == 0
    assert out.strip() == "undone: +3 (3 Cmd+Z steps) — a script without -A, 5 s ago; can still undo: 0"
    assert "⚠ not everything was undone" in err


def test_created_names_the_outermost_new_layers():
    url = "https://www.figma.com/design/K/F?node-id="
    items = [{"op": "+", "id": "5:2", "name": "Icon", "url": url + "5-2"}]
    one = {"items": items, "top": [{"op": "+", "id": "5:1", "name": "Button", "url": url + "5-1"}]}
    assert cli_extras.created_line(one) == f'  created: "Button" {url}5-1'
    assert cli_extras.made_by({"value": {"props": []}, "changes": one}) == "5:1"
    assert cli_extras.created_line({"items": items}) == f'  created: "Icon" {url}5-2'  # an older build
    tops = [{"op": "+", "id": f"6:{i}", "name": f"Card {i}", "url": f"{url}6-{i}"} for i in range(1, 6)]
    lines = cli_extras.created_line({"items": [], "top": tops, "topMore": 2}).splitlines()
    assert lines[0] == f'  created: "Card 1" {url}6-1' and len(lines) == 4 and lines[3] == "  … and 4 more"


def test_exec_sends_libs_and_shoots_what_it_made(monkeypatch, capsys, tmp_path):
    lib = tmp_path / "grid.js"
    lib.write_text("export function col(n) { return n * 72; }\n")
    made = {"ok": True, "value": {"frame": "5:6", "w": 10},
            "changes": {"created": 1, "items": [{"op": "+", "id": "5:6", "name": "Grid", "type": "FRAME",
                                                 "url": "https://www.figma.com/design/K/F?node-id=5-6"}]}}
    pictures = {"ok": True, "value": [shot(node="5:6", data=png(10, 10))]}
    fake = FakeBridge(made, pictures)
    rc, out, err, calls = cli(monkeypatch, capsys, "exec", "--lib", str(lib), "--shot",
                              "return lib.col(2)", bridge_reply=fake)
    assert rc == 0
    first, second = calls
    assert first["libs"] == [{"name": "grid.js", "code": lib.read_text()}]
    assert "quick" not in first
    assert second["quick"] is True and '["5:6"]' in second["code"] and "libs" not in second
    assert 'created: "Grid" https://www.figma.com/design/K/F?node-id=5-6' in err
    figaro._emit(dict(made, ok=False, error="read-only: the script changed 1 — rolled back", rolled_back=True))
    assert "created" not in capsys.readouterr().err  # rolled back: the link leads nowhere
    assert (cli_extras.SHOTS / KEY / "5-6.png").exists()


def test_a_lost_lib_is_sent_again_once(monkeypatch, capsys, tmp_path):
    lib = tmp_path / "grid.js"
    lib.write_text("const A = 1")
    lost = {"ok": False, "error": "lib grid.js is not loaded in the plugin", "lib_missing": True}
    fake = FakeBridge(lost, {"ok": True, "value": 1})
    rc, out, err, calls = cli(monkeypatch, capsys, "exec", "--lib", str(lib), "return lib.A", bridge_reply=fake)
    assert rc == 0 and len(calls) == 2 and calls[0] == calls[1]


def test_svg_text_keeps_text_as_text(monkeypatch, capsys):
    reply = {"ok": True, "value": []}
    code = cli(monkeypatch, capsys, "shot", "1:2", "--format", "svg", "--svg-text", bridge_reply=reply)[3][0]["code"]
    assert '{"format": "svg", "outlineText": false}' in code
    code = cli(monkeypatch, capsys, "shot", "1:2", "--format", "svg", bridge_reply=reply)[3][0]["code"]
    assert "outlineText" not in code


def test_shot_sel_takes_every_selected_layer(monkeypatch, capsys):
    calls = cli(monkeypatch, capsys, "shot", "sel", bridge_reply={"ok": True, "value": []})[3]
    assert "id === 'sel' ? figma.currentPage.selection" in calls[0]["code"]


def test_exec_shot_without_a_target_says_so(monkeypatch, capsys):
    rc, out, err, calls = cli(monkeypatch, capsys, "exec", "--shot", "return 1",
                              bridge_reply={"ok": True, "value": 1})
    assert rc == 1 and "nothing to shoot" in err and len(calls) == 1


def test_lib_from_the_environment_and_a_missing_file(monkeypatch, tmp_path):
    a, b = tmp_path / "a.js", tmp_path / "b.js"
    a.write_text("const A = 1")
    b.write_text("const B = 2")
    monkeypatch.setenv("FIGARO_LIB", os.pathsep.join([str(a), str(b)]))
    assert [x["name"] for x in cli_extras.read_libs(None)] == ["a.js", "b.js"]
    assert [x["name"] for x in cli_extras.read_libs([str(b)])] == ["b.js"]  # --lib wins
    with pytest.raises(SystemExit, match="--lib"):
        cli_extras.read_libs([str(tmp_path / "nope.js")])


def test_lib_error_line_is_printed(monkeypatch, capsys):
    error = {"ok": False, "error": "TypeError: cannot read 'width'", "line": 2, "source": "lib.col(x)",
             "lib": {"name": "grid.js", "line": 7, "source": "return n.width * k;"}}
    rc, out, err, calls = cli(monkeypatch, capsys, "exec", "return lib.col(x)", bridge_reply=error)
    assert rc == 1
    assert "   grid.js, line 7: return n.width * k;" in err


@pytest.mark.parametrize("argv", [["sel"], ["tree", "page", "--depth", "1"],
                                  ["find", "page", "name=Card"]])
def test_builtin_readers_are_quick(monkeypatch, capsys, argv):
    rc, out, err, calls = cli(monkeypatch, capsys, *argv, bridge_reply={"ok": True, "value": []})
    assert calls[0]["quick"] is True and rc == 0


@pytest.mark.parametrize("flt, query", [
    ("name=Card", {"name": "Card"}), ("name~Ca", {"nameHas": "Ca"}), ("type=frame", {"type": "FRAME"}),
    ("text=Hi", {"text": "Hi"}), ("text~a=b", {"textHas": "a=b"}),
])
def test_find_asks_h_find(monkeypatch, capsys, flt, query):
    rc, out, err, calls = cli(monkeypatch, capsys, "find", "page", flt, bridge_reply={"ok": True, "value": []})
    assert f"h.find(root, {json.dumps(query)})" in calls[0]["code"]
    rc, out, err, calls = cli(monkeypatch, capsys, "find", "page", flt, "--hidden",
                              bridge_reply={"ok": True, "value": []})
    assert f"h.find(root, {json.dumps(dict(query, hidden=True))})" in calls[0]["code"]


def test_clone_and_icomp_move_the_view_only_with_focus(monkeypatch, capsys):
    for argv in (["clone", "1:2"], ["icomp", "abc"]):
        calls = cli(monkeypatch, capsys, *argv)[3]
        assert "scrollAndZoomIntoView" not in calls[0]["code"]
        calls = cli(monkeypatch, capsys, *argv, "--focus")[3]
        assert "scrollAndZoomIntoView" in calls[0]["code"]


RM_STUB = """
const nodes = {
  "1:1": {id: "1:1", name: "Group", type: "FRAME", removed: false, kids: ["1:2"]},
  "1:2": {id: "1:2", name: "Child", type: "RECTANGLE", removed: false, kids: []},
  "1:3": {id: "1:3", name: "Other", type: "TEXT", removed: false, kids: []},
};
for (const n of Object.values(nodes)) n.remove = function () {
  this.removed = true; for (const k of this.kids) nodes[k].removed = true; };
const h = {async resolve(id) { if (!nodes[id]) throw new Error("no " + id); return nodes[id]; }};
const logs = [];
const print = (s) => logs.push(s);
const body = %s;
new Function("h", "print", "return (async () => {" + body + "})();")(h, print).then(
  (v) => console.log(JSON.stringify({ok: true, v, logs, removed: Object.values(nodes).filter((n) => n.removed).map((n) => n.id)})),
  (e) => console.log(JSON.stringify({ok: false, e: e.message, removed: Object.values(nodes).filter((n) => n.removed).map((n) => n.id)})));
"""


def run_rm(monkeypatch, capsys, *ids):
    code = cli(monkeypatch, capsys, "rm", *ids)[3][0]["code"]
    r = subprocess.run(["node", "-e", RM_STUB % json.dumps(code)], capture_output=True, encoding="utf-8",
                       timeout=30)
    return json.loads(r.stdout)


def test_rm_finds_every_id_before_removing_any(monkeypatch, capsys):
    got = run_rm(monkeypatch, capsys, "1:3", "9:9")
    assert got == {"ok": False, "e": "node not found: 9:9 — nothing removed", "removed": []}


def test_rm_skips_what_went_with_its_parent(monkeypatch, capsys):
    got = run_rm(monkeypatch, capsys, "1:1", "1:2", "1:3")
    assert got["ok"] and [x["id"] for x in got["v"]] == ["1:1", "1:3"]
    assert got["logs"] == ["already removed with a parent or listed twice: 1:2"]
    assert got["removed"] == ["1:1", "1:2", "1:3"]


# ─── starting the bridge ─────────────────────────────────────────────────

def fake_start(tmp_path, monkeypatch, body="open(RUNS, 'a').write('started\\n')"):
    """A start script in Python, so that the tests run on Windows as well."""
    runs = tmp_path / "runs"
    script = tmp_path / "start.py"
    script.write_text(f"import sys\nRUNS = {str(runs)!r}\n{body}\n")
    monkeypatch.setattr(cli_extras, "start_command",
                        lambda port: (script, [sys.executable, str(script)]))
    monkeypatch.setattr(cli_extras, "PLUGIN_WAIT", 0.2)
    monkeypatch.setenv("FIGARO_AUTOSTART", "1")
    return runs


def test_autostart_runs_the_script_once(tmp_path, monkeypatch, capsys):
    runs = fake_start(tmp_path, monkeypatch)
    assert cli_extras.autostart("127.0.0.1", free_port()) is True
    assert cli_extras.autostart("127.0.0.1", free_port()) is False
    assert runs.read_text() == "started\n"
    err = capsys.readouterr().err
    assert "starting it" in err and "the plugin has not connected yet" in err


def test_autostart_can_be_turned_off_and_is_local_only(tmp_path, monkeypatch):
    runs = fake_start(tmp_path, monkeypatch)
    monkeypatch.setenv("FIGARO_AUTOSTART", "0")
    assert cli_extras.autostart("127.0.0.1", 1) is False
    monkeypatch.setenv("FIGARO_AUTOSTART", "1")
    assert cli_extras.autostart("10.0.0.5", 1) is False
    assert not runs.exists()


def test_autostart_does_not_wait_for_the_bridge_it_started(tmp_path, monkeypatch):
    """The bridge outlives the start script and inherits its output (on
    Windows, Start-Process passes it every handle): with a pipe, this call would
    wait until the bridge exits."""
    fake_start(tmp_path, monkeypatch, body=(
        "import subprocess\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(12)'],\n"
        "                 stdout=sys.stdout, stderr=sys.stderr)"))
    t = time.time()
    assert cli_extras.autostart("127.0.0.1", free_port()) is True
    assert time.time() - t < 8


def test_a_failed_start_says_why(tmp_path, monkeypatch, capsys):
    fake_start(tmp_path, monkeypatch, body="print('port is taken by another program'); sys.exit(1)")
    assert cli_extras.autostart("127.0.0.1", 1) is False
    assert "port is taken by another program" in capsys.readouterr().err


def test_windows_starts_the_bridge_with_powershell(monkeypatch):
    script, cmd = cli_extras.start_command(8790, windows=True)
    assert script.name == "start-bridge.ps1" and script.exists()
    assert cmd[0] == "powershell" and cmd[cmd.index("-File") + 1] == str(script)
    assert cmd[-2:] == ["-Port", "8790"]
    script, cmd = cli_extras.start_command(8790, windows=False)
    assert cmd == ["bash", str(script)] and script.name == "start-bridge.sh"


def test_a_sandbox_that_blocks_localhost_is_named(monkeypatch, capsys):
    """Codex's and Cursor's sandboxes refuse the connect itself: no autostart, a hint."""
    def blocked(*a, **k):
        raise urllib.error.URLError(PermissionError(1, "Operation not permitted"))

    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    monkeypatch.setattr(cli_extras, "autostart", lambda *a: pytest.fail("no autostart"))
    status, resp = figaro._request("GET", "/status")
    assert status == 0 and resp["hint"] == figaro.SANDBOXED
    assert figaro.cmd_doctor(None) == 1
    assert figaro.SANDBOXED in capsys.readouterr().out
    assert figaro.cmd_targets(None) == 1
    assert "hint: a sandbox" in capsys.readouterr().err


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Status(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"plugin_connected": True, "files": [{"name": "Draft"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def test_a_refused_request_starts_the_bridge_and_asks_again(monkeypatch):
    port = free_port()
    servers = []

    def start(host, p):
        server = http.server.HTTPServer(("127.0.0.1", p), Status)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return True

    monkeypatch.setattr(cli_extras, "autostart", start)
    monkeypatch.setattr(figaro, "HOST", "127.0.0.1")
    monkeypatch.setattr(figaro, "PORT", port)
    try:
        status, body = figaro._request("GET", "/status")
        assert status == 200 and body["files"] == [{"name": "Draft"}]
    finally:
        for s in servers:
            s.shutdown()
            s.server_close()


@pytest.mark.skipif(sys.platform == "win32", reason="start-bridge.sh is for macOS / Linux")
def test_status_starts_a_real_bridge_that_outlives_the_call():
    port = free_port()
    env = dict(os.environ, FIGARO_PORT=str(port), FIGARO_AUTOSTART="1",
               FIGARO_PLUGIN_WAIT="0.3", FIGARO_IDLE_EXIT="120")
    try:
        r = subprocess.run([sys.executable, str(ROOT / "figaro.py"), "status"], env=env,
                           capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr
        assert "starting it" in r.stderr
        assert json.loads(r.stdout)["plugin_connected"] is False
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/status", timeout=3) as resp:
            assert resp.status == 200
        # Under tmux (Linux runners have it) there is no pid file: look for the process.
        ps = subprocess.run(["ps", "-e", "-o", "command="], capture_output=True, text=True).stdout
        assert f"bridge.py --port {port} --idle-exit 120" in ps
    finally:
        subprocess.run(["bash", str(ROOT / "start-bridge.sh"), "--stop"], env=env,
                       capture_output=True, timeout=30)


@pytest.mark.skipif(sys.platform != "win32", reason="start-bridge.ps1 is for Windows")
def test_status_starts_a_real_bridge_on_windows():
    port = free_port()
    env = dict(os.environ, FIGARO_PORT=str(port), FIGARO_AUTOSTART="1",
               FIGARO_PLUGIN_WAIT="0.3", FIGARO_IDLE_EXIT="120")
    stop = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
            str(ROOT / "start-bridge.ps1"), "-Port", str(port), "-Stop"]
    try:
        t = time.time()
        r = subprocess.run([sys.executable, str(ROOT / "figaro.py"), "status"], env=env,
                           capture_output=True, text=True, timeout=90)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "starting it" in r.stderr
        assert json.loads(r.stdout)["plugin_connected"] is False
        assert time.time() - t < 30, "the call waited for the bridge it started"
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/status", timeout=3) as resp:
            assert resp.status == 200
    finally:
        r = subprocess.run(stop, capture_output=True, text=True, timeout=60)
    assert "stopped pid" in r.stdout, r.stdout + r.stderr


def test_exec_reads_stdin_as_utf8(monkeypatch):
    """On Windows Python reads a pipe in the ANSI code page."""
    class Stdin:
        def __init__(self, data):
            self.buffer = io.BytesIO(data)

    script = "return 'Total: €5 — paid ✓ 🎨'"
    for data in (script.encode("utf-8"), b"\xef\xbb\xbf" + script.encode("utf-8")):
        monkeypatch.setattr(sys, "stdin", Stdin(data))
        assert figaro.read_stdin() == script
    monkeypatch.setattr(sys, "stdin", Stdin("return '© 2026 — €5'".encode("cp1252")))
    assert figaro.read_stdin().startswith("return '")  # not UTF-8: no crash


def test_start_hint_names_this_computers_script():
    assert cli_extras.start_hint(windows=False) == f"bash {cli_extras.START}"
    hint = cli_extras.start_hint(restart=True, windows=True)
    assert hint == f'powershell -ExecutionPolicy Bypass -File "{cli_extras.START_PS1}" -Restart'


# ─── the bridge leaves when idle ─────────────────────────────────────────

def test_duration():
    assert [bridge_idle.duration(x) for x in ("90", "90s", "30m", "3h", " 1.5h ")] == \
        [90, 90, 1800, 10800, 5400]
    for bad in ("", "3d", "h", "-1"):
        with pytest.raises(ValueError):
            bridge_idle.duration(bad)


def test_idle_bridge_leaves_but_not_while_a_plugin_is_connected(monkeypatch):
    left = []
    monkeypatch.setattr(bridge_idle, "LAST", [time.time() - 100])
    monkeypatch.setattr(bridge, "_live_plugins", lambda: [("conn", {})])
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(asyncio.wait_for(bridge_idle.watch(50, every=0.01, exit_fn=left.append), 0.1))
    assert left == []  # a connected plugin keeps it — and counts as being needed
    monkeypatch.setattr(bridge, "_live_plugins", lambda: [])
    monkeypatch.setattr(bridge_idle, "LAST", [time.time() - 100])
    asyncio.run(asyncio.wait_for(bridge_idle.watch(50, every=0.01, exit_fn=left.append), 1))
    assert left == [50]


def test_a_request_counts_as_being_needed(monkeypatch):
    monkeypatch.setattr(bridge_idle, "LAST", [0.0])

    async def go():
        app = bridge.build_app()
        bridge_idle.install(app, 0)
        client = TestClient(TestServer(app))
        await client.start_server()
        assert (await client.get("/status")).status == 200
        await client.close()
        return app

    app = asyncio.run(go())
    assert time.time() - bridge_idle.LAST[0] < 5
    assert "bridge_idle" not in app  # 0: no watcher, the bridge stays for good


@pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM is how start-bridge.sh stops it")
def test_a_stopping_bridge_closes_plugin_sockets_at_once():
    """start-bridge.sh --stop sends SIGTERM. The plugin window has to hear it
    at once, not up to a minute later: only then does it
    stop the keep-awake tone (tests/ui.test.js) and reconnect to a new bridge."""
    port = free_port()
    proc = subprocess.Popen([sys.executable, str(ROOT / "bridge.py"), "--port", str(port)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    async def go():
        async with aiohttp.ClientSession() as s:
            for _ in range(100):
                try:
                    async with s.get(f"http://127.0.0.1:{port}/status") as r:
                        if r.status == 200:
                            break
                except aiohttp.ClientError:
                    await asyncio.sleep(0.1)
            ws = await s.ws_connect(f"ws://127.0.0.1:{port}/plugin", headers={"Origin": "null"})
            await ws.send_json({"type": "hello", "version": "2.1", "fileKey": KEY, "name": "Draft"})
            await asyncio.sleep(0.3)
            t0 = time.monotonic()
            proc.terminate()
            try:
                while (await asyncio.wait_for(ws.receive(), 10)).type == aiohttp.WSMsgType.TEXT:
                    pass  # peers and the like
            except asyncio.TimeoutError:
                return None, None
            return time.monotonic() - t0, ws.close_code

    try:
        took, code = asyncio.run(go())
        assert took is not None, "the plugin socket was still open 10 s after SIGTERM"
        assert took < 3 and code == 1001, (took, code)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(10)
