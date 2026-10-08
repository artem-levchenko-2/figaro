"""More of the CLI: links, starting the bridge, inspect, shot, link, --lib.

figaro.py calls in here for:
- Figma links in place of `-T` and of node ids (figma_links);
- starting the bridge when nothing answers on its port;
- `inspect`, `shot`, `link`, `exec --shot`, `exec --lib`.

It reaches figaro.py's own functions (`_exec`, `_emit`, `node_expr`)
through `CLI`, which figaro.py sets with attach() — importing it from here
would load a second copy with its own settings.
"""

from __future__ import annotations

import base64
import io
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import inspect_text
import figma_links

HERE = Path(__file__).resolve().parent
START = HERE / "start-bridge.sh"
START_PS1 = HERE / "start-bridge.ps1"  # Windows has no bash
# /tmp/figaro/shots; Windows has no /tmp, so there it is %TEMP%\figaro\shots.
SHOTS = Path(os.environ.get("FIGARO_SHOTS")
             or (Path(tempfile.gettempdir(), "figaro", "shots") if os.name == "nt" else "/tmp/figaro/shots"))
TILE = 1600          # px: taller pictures are cut into parts about this high
# After starting the bridge: how long to wait for the open plugin windows (each
# retries every 2 s), and how long no new one has to come before we go on.
PLUGIN_WAIT = float(os.environ.get("FIGARO_PLUGIN_WAIT") or 6)
PLUGIN_SETTLE = 2.2
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
EXT = {"PNG": "png", "JPG": "jpg", "SVG": "svg", "PDF": "pdf"}

CLI = None           # the figaro module, set by attach()
_started = False


def attach(cli):
    global CLI
    CLI = cli


def say(text):
    print(text, file=sys.stderr)


# ─── links in arguments ──────────────────────────────────────────────────

def target_and_ids(args):
    """Links in -T and in node ids. A layer link's node-id becomes the id, and
    the link itself the target unless -T names one — the bridge takes a link
    as a target, and says to open that very link when its file is not
    connected. '1-11' becomes '1:11'. Returns the target to use."""
    target = getattr(args, "target", None)
    t_link = figma_links.parse(target) if target else None
    t_key = t_link.key if t_link else target if target and figma_links.FILE_KEY.match(target) else None
    links = {}

    def fix(value):
        link = figma_links.parse(value)
        if link:
            if not link.node:
                sys.exit(f"figaro: the link has no node-id — use a link to a layer "
                         f"(Copy link to selection): {value}")
            links.setdefault(link.key, link.url)
            return link.node
        return figma_links.node_id(value) or value

    if getattr(args, "node_id", None):
        args.node_id = fix(args.node_id)
    if getattr(args, "node_ids", None):
        args.node_ids = [fix(v) for v in args.node_ids]
    if getattr(args, "shot", None) not in (None, "", "auto"):
        args.shot = fix(args.shot)
    if len(links) > 1:
        sys.exit("figaro: the links point to different files — one file per call")
    if links:
        key, url = links.popitem()
        if target is None:
            target = url
        elif t_key and t_key != key:
            sys.exit(f"figaro: the layer is from file {key}, but -T is {t_key}")
    return target


# ─── starting the bridge ─────────────────────────────────────────────────

def autostart(host, port):
    """Start the bridge when nothing listens on its port — once per call, and
    only on this machine. Then wait a little for the plugins to reconnect: they
    retry every 2 s. FIGARO_AUTOSTART=0 turns it off."""
    global _started
    if _started or os.environ.get("FIGARO_AUTOSTART", "1") == "0":
        return False
    _started = True
    script, cmd = start_command(port)
    if host not in LOCAL_HOSTS or not script.exists():
        return False
    say(f"  no bridge answered on {port} — starting it: {' '.join(cmd)}")
    try:
        # A session of its own: the bridge must outlive this call and whatever
        # process group the agent's shell kills when it is done. Its output goes
        # to a file, not a pipe: on Windows the bridge inherits the script's
        # handles, and a pipe would stay open, and this call wait, until the
        # bridge exits.
        with tempfile.TemporaryFile() as out:
            r = subprocess.run(cmd, env=dict(os.environ, FIGARO_PORT=str(port)),
                               stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                               timeout=40, start_new_session=True)
            out.seek(0)
            printed = out.read().decode("utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError) as e:
        say(f"   ⚠ it did not start: {e}")
        return False
    if r.returncode != 0:
        tail = printed.strip().splitlines()[-5:]
        say("   ⚠ it did not start:\n     " + "\n     ".join(tail))
        return False
    _wait_for_plugins(host, port)
    return True


def start_command(port, windows=os.name == "nt"):
    """The script that starts the bridge and the command that runs it:
    start-bridge.sh, or start-bridge.ps1 on Windows, which has no bash."""
    if windows:
        return START_PS1, ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                           str(START_PS1), "-Port", str(port)]
    return START, ["bash", str(START)]


def start_hint(restart=False, windows=os.name == "nt"):
    """The command that starts the bridge by hand on this computer.
    start-bridge.sh restarts a running bridge by itself."""
    if windows:
        return (f'powershell -ExecutionPolicy Bypass -File "{START_PS1}"'
                + (" -Restart" if restart else ""))
    return f"bash {START}"


def _wait_for_plugins(host, port):
    deadline = time.time() + PLUGIN_WAIT
    names, since = [], time.time()
    while time.time() < deadline:
        now = _connected(host, port)
        if now != names:
            names, since = now, time.time()
        elif names and time.time() - since >= PLUGIN_SETTLE:
            break
        time.sleep(0.25)
    if names:
        say("  plugin connected: " + ", ".join(f'"{n}"' for n in names))
    else:
        say("  ⚠ the plugin has not connected yet. If Figma has it open in the background, it "
            "connects by itself within a minute — run the command again; if not, open the file "
            "in Figma and run Plugins → Development → Figaro")


def _connected(host, port):
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/status", timeout=2) as r:
            return sorted(f.get("name") or "?" for f in json.load(r).get("files") or [])
    except (OSError, ValueError):
        return []


# ─── reading what came back ──────────────────────────────────────────────

def report(resp):
    """Logs, notices and timing of a reply whose value we print ourselves."""
    CLI._emit(dict(resp, result=None, value=None))


def created_line(changes):
    """Links to the new layers that are not inside other new ones (a build
    without `top`: to the first new layer), for the agent's report."""
    changes = changes or {}
    made = [t for t in changes.get("top") or [] if t.get("url")]
    if not made:
        made = [i for i in changes.get("items") or [] if i.get("op") == "+" and i.get("url")][:1]
    lines = []
    for t in made[:3]:
        name = f'"{t["name"]}" ' if t.get("name") else ""
        lines.append(f"  created: {name}{t['url']}")
    more = len(made) - 3 + (changes.get("topMore") or 0)
    if lines and more > 0:
        lines.append(f"  … and {more} more")
    return "\n".join(lines) or None


# ─── inspect ─────────────────────────────────────────────────────────────

def cmd_inspect(args):
    opts = {"depth": args.depth, "hidden": args.hidden}
    code = f"return await h.inspect({CLI.node_expr(args.node_id)}, {json.dumps(opts)});"
    status, resp = CLI._exec(code, args.timeout, quick=True)
    if args.raw or not resp.get("ok"):
        return CLI._emit(resp, raw=args.raw)
    report(resp)
    value = resp.get("value") or {}
    if args.out:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        path = Path(args.out).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        node = value.get("node") or {}
        print(json.dumps({"file": value.get("file"), "node": node.get("id"), "name": node.get("name"),
                          "out": str(path.resolve()), "nodes": value.get("nodes"), "chars": len(text)},
                         ensure_ascii=False))
    elif args.json:
        print(json.dumps(value, ensure_ascii=False))
    else:
        print(inspect_text.render(value))
    return 0


# ─── shot ────────────────────────────────────────────────────────────────

def shot_code(ids, opts):
    return ("const out = [];"
            f"for (const id of {json.dumps(ids)}) {{"
            "  const nodes = id === 'sel' ? figma.currentPage.selection : [await h.resolve(id)];"
            "  if (!nodes.length) throw new Error('nothing is selected in Figma');"
            f"  for (const n of nodes) out.push(await h.shot(n, {json.dumps(opts)}));"
            "}"
            "return out;")


def shot_opts(args):
    opts = {k: v for k, v in (("format", args.format), ("scale", args.scale),
                              ("width", args.width)) if v}
    if getattr(args, "svg_text", False):
        opts["outlineText"] = False  # an SVG for code keeps its text as <text>
    return opts


def cmd_shot(args):
    status, resp = CLI._exec(shot_code(args.node_ids, shot_opts(args)), args.timeout, quick=True)
    if args.raw or not resp.get("ok"):
        return CLI._emit(resp, raw=args.raw)
    report(resp)
    for line in save_shots(resp.get("value") or [], args.out, args.tile):
        print(line)
    return 0


def shot_after(args, resp):
    """`exec --shot [id]`: a picture of what the script made — the id given,
    else the node the script returned, else the first layer it created."""
    target = args.shot if args.shot not in ("", "auto") else made_by(resp)
    if not target:
        say("  ⚠ --shot: nothing to shoot — the script returned no id and created nothing; "
            "give one: --shot <id>")
        return 1
    status, shot = CLI._exec(shot_code([target], {"format": "png"}), args.timeout, quick=True)
    if not shot.get("ok"):
        say(f"  ⚠ --shot {target}: {shot.get('error')}")
        return 1
    for line in save_shots(shot.get("value") or [], None, TILE):
        print(line)
    return 0


_ID = re.compile(r"^I?\d+:\d+(?:;\d+:\d+)*$")


def made_by(resp):
    value = resp.get("value")
    if isinstance(value, list) and value:
        value = value[0]
    if isinstance(value, str) and _ID.match(value):
        return value
    if isinstance(value, dict):
        for key in ("id", "node", "frame", "instance_id", "clone_id"):
            if isinstance(value.get(key), str) and _ID.match(value[key]):
                return value[key]
    changes = resp.get("changes") or {}
    for item in (changes.get("top") or []) + (changes.get("items") or []):
        if item.get("op", "+") == "+" and item.get("type") != "STYLE":
            return item.get("id")
    return None


def save_shots(shots, out_dir=None, tile=TILE):
    """Write each shot to a file, a tall one in parts; lines to print."""
    lines = []
    for s in shots:
        data = base64.b64decode(s["data"])
        folder = Path(out_dir).expanduser() if out_dir else SHOTS / _safe(s.get("fileKey") or s.get("file") or "file")
        folder.mkdir(parents=True, exist_ok=True)
        stem, ext = _safe(s["id"]), EXT.get(s.get("format"), "png")
        for old in list(folder.glob(f"{stem}.*")) + list(folder.glob(f"{stem}-p*.*")):
            old.unlink()
        parts, why = split_image(data, ext, tile)
        how = f"{s['scale']}×" if s.get("scale") else f"{s['width']} px" if s.get("width") else ""
        for i, (blob, size, rows) in enumerate(parts, 1):
            path = folder / (f"{stem}.{ext}" if len(parts) == 1 else f"{stem}-p{i}.{ext}")
            path.write_bytes(blob)
            line = str(path)
            if size:
                line += f"  {size[0]}×{size[1]}"
            if rows:
                line += f" · part {i}/{len(parts)}, y {rows[0]}–{rows[1]}"
            if i == 1:
                name = s.get("name")
                line += f' · "{name}"' + (f" {how}" if how else "")
            lines.append(line)
        if why:
            lines.append(f"  ⚠ {why}")
    return lines


def split_image(data, ext, tile):
    """[(bytes, (w, h), (y0, y1) | None)], reason-not-split | None."""
    size = image_size(data, ext)
    if not size or ext not in ("png", "jpg") or size[1] <= tile * 1.15:
        return [(data, size, None)], None
    try:
        from PIL import Image
    except ImportError:
        return [(data, size, None)], ("the picture is tall, and cutting it needs Pillow: "
                                      "venv/bin/pip install -r requirements.txt")
    Image.MAX_IMAGE_PIXELS = None  # Figma's own export, not a stranger's file
    im = Image.open(io.BytesIO(data))
    im.load()
    w, h = im.size
    n = math.ceil(h / tile)
    step = math.ceil(h / n)
    extra = {"icc_profile": im.info["icc_profile"]} if im.info.get("icc_profile") else {}
    parts = []
    for i in range(n):
        y0, y1 = i * step, min(h, (i + 1) * step)
        part = im.crop((0, y0, w, y1))
        buf = io.BytesIO()
        if ext == "jpg":
            part.convert("RGB").save(buf, "JPEG", quality=90, **extra)
        else:
            part.save(buf, "PNG", **extra)
        parts.append((buf.getvalue(), (w, y1 - y0), (y0, y1)))
    return parts, None


def image_size(data, ext):
    """(width, height) from a PNG or JPEG header; None otherwise."""
    if ext == "png" and data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if ext == "jpg" and data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data) and data[i] == 0xFF:
            marker, length = data[i + 1], int.from_bytes(data[i + 2:i + 4], "big")
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                return int.from_bytes(data[i + 7:i + 9], "big"), int.from_bytes(data[i + 5:i + 7], "big")
            i += 2 + length
    return None


def _safe(text):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(text).replace(":", "-").replace(";", "_"))


# ─── link ────────────────────────────────────────────────────────────────

def cmd_link(args):
    code = ("const out = [];"
            f"for (const id of {json.dumps(args.node_ids)}) {{"
            "  const nodes = id === 'sel' ? figma.currentPage.selection : [await h.resolve(id)];"
            "  if (!nodes.length) throw new Error('nothing is selected in Figma');"
            "  for (const n of nodes) out.push({id: n.id, name: n.name, url: h.link(n)});"
            "}"
            "return out;")
    status, resp = CLI._exec(code, args.timeout, quick=True)
    if args.raw or not resp.get("ok"):
        return CLI._emit(resp, raw=args.raw)
    for item in resp.get("value") or []:
        print(f"{item.get('url') or '(the file has no key, so no link)'}  \"{item.get('name')}\"")
    return 0


# ─── --lib ───────────────────────────────────────────────────────────────

def read_libs(paths):
    """--lib files (or FIGARO_LIB, separated by ':') as [{name, code}]."""
    if not paths:
        paths = [p for p in os.environ.get("FIGARO_LIB", "").split(os.pathsep) if p]
    libs = []
    for p in paths:
        path = Path(p).expanduser()
        try:
            libs.append({"name": path.name, "code": path.read_text(encoding="utf-8")})
        except OSError as e:
            sys.exit(f"figaro: --lib {p}: {e.strerror}")
    return libs


# ─── argparse ────────────────────────────────────────────────────────────

def add_exec_flags(p):
    p.add_argument("--lib", action="append", metavar="FILE",
                   help="a JS file whose top-level functions and consts the script gets as "
                        "lib.<name>; sent to the plugin once, then by its hash (env FIGARO_LIB)")
    p.add_argument("--shot", nargs="?", const="auto", metavar="ID",
                   help="a picture after the script: of ID, else of what it returned or created")


def add_parsers(sub, common):
    p = sub.add_parser("inspect", help="everything about a layer, with the keys of its "
                                       "components, styles and variables")
    common(p)
    p.add_argument("node_id", help="node id, Figma link, `sel` or `page`")
    p.add_argument("--depth", type=int, default=8)
    p.add_argument("--hidden", action="store_true", help="list hidden layers too")
    p.add_argument("--json", action="store_true", help="print the data as JSON")
    p.add_argument("-o", "--out", metavar="FILE", help="write the JSON to FILE")

    p = sub.add_parser("shot", help="pictures of layers, saved as files an agent can open")
    common(p)
    p.add_argument("node_ids", nargs="+", help="node ids, Figma links, `sel` for every selected layer")
    p.add_argument("--scale", type=float, help="default: 2 up to 800 px wide, else 1, at most 1600 px")
    p.add_argument("--width", type=int, help="picture width in px")
    p.add_argument("--format", choices=["png", "jpg", "svg", "pdf"], default="png")
    p.add_argument("--svg-text", action="store_true", help="SVG: keep text as <text>, not outlines")
    p.add_argument("--tile", type=int, default=TILE, help=f"cut taller pictures into parts (default {TILE} px)")
    p.add_argument("-o", "--out", metavar="DIR", help=f"folder (default {SHOTS}/<file key>)")

    p = sub.add_parser("link", help="clickable links to layers, for reports")
    common(p)
    p.add_argument("node_ids", nargs="+", help="node ids, `sel` for every selected layer")


def fix_exec_args(args):
    """`exec --shot "<js>"`: argparse gives the code to --shot — hand it back."""
    shot = getattr(args, "shot", None)
    if (shot not in (None, "auto") and not args.code and not args.file and not args.stdin
            and not (shot in ("page", "sel") or figma_links.node_id(shot) or figma_links.parse(shot))):
        args.code, args.shot = shot, "auto"
