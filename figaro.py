#!/usr/bin/env python3
"""figaro — CLI client for the Figaro bridge.

Commands:
    figaro exec "<js>"             # run arbitrary JS in plugin context
    figaro exec --file f.js
    figaro exec --stdin
    figaro status                   # check server / plugin connection
    figaro doctor                   # diagnose the whole chain, with fixes
    figaro sel                      # what is selected in Figma right now
    figaro tree <id> [--depth N] [--layout]
    figaro find <id> <filter>       # find descendants (name=X, name~X, type=X, text=X)
    figaro text <id> "<new text>"   # set TEXT node characters (autoloads font)
    figaro variant <id> "P=V" ...   # set INSTANCE variant property values
    figaro clone <id> [--right|--left|--up|--down] [--gap N] [--name N]
    figaro rm <id> [<id> ...]       # remove one or more nodes
    figaro import-component <key>   # import library component, instantiate (--focus: show it)
    figaro undo                     # undo the file's last script, when that is safe
    figaro reload                   # load plugin/code.js from disk into the open plugin
    figaro done "<note>"            # you are done in the file: the plugin's window says so, with the note
    figaro inspect <id> [--json | -o f.json]   # layout, styles, text, keys of what it uses
    figaro shot <id> ...            # picture(s) saved to files; tall ones cut in parts
    figaro link <id|sel> ...        # clickable links to layers, for reports
    figaro "<js>"                   # shorthand for `exec`

-R / --read-only: the script must not change the file; if it does, the plugin
rolls the change back and the call fails. sel, tree, find, inspect, shot, link
and doctor run as quick reads: their own code changes nothing, so they skip the
undo step and the ~150 ms wait for Figma's change report. A writing script
gets a checkpoint (a version in File → Version history) before the first one
in a file and then hourly; --checkpoint LABEL forces one, --no-checkpoint
skips it. Every reply says what the script changed.

Anywhere a node id is taken, `page` (current page) and `sel` (first selected
node) work too, and so does a Figma link (Copy link to selection): its file
becomes the target. -T takes a link as well. Bridge address comes from
FIGARO_HOST / FIGARO_PORT; when nothing answers there, the CLI starts the
bridge (start-bridge.sh) and waits for the plugin — FIGARO_AUTOSTART=0 stops it.

exec --lib f.js (repeatable, or FIGARO_LIB): the file's top-level functions
and consts reach the script as lib.<name>; the plugin keeps it compiled, so
later calls send only its hash. exec --shot [id]: a picture after the script.

Helpers available inside exec'd code (as `h.*`):
    h.bF(node, idx, varOrId)    h.bS(node, idx, varOrId)   h.bN(node, prop, varOrId)
    h.findByName(root, name)    h.findAllByName(root, name)
    h.dumpTree(node, {maxDepth, showSize, showText, showLayout})
    h.withFonts(root, asyncFn)  h.setText(node, text)
    h.cloneNext(node, {direction, gap, name})
    h.variant(instance, props)  h.variantsOf(instance)
    h.sel()                     h.resolve(idOrAlias)
    h.hex("#1a2b3c")            h.solid("#1a2b3c", opacity?)
    h.frame(parent, {layout, w, h, spacing, padding, align, fill, radius, name})
    h.node(id)                  h.var_(idOrKey)
    h.importComp(key)           h.importVar(key)
    h.inspect(node, {depth})    h.shot(node, {format, scale, width})   h.link(node)
    h.fonts(node)  h.fa()       h.fill(node, axis)  h.hug(node, axis)  h.fixed(node, w, h)
    h.wrapText(node, w?)        h.find(root, {name, nameHas, type, text, textHas, hidden})
    h.annotations(scope?)       Dev Mode notes: [{frame, node, name, visible, labels}]
"""

import argparse
import json
import locale
import os
import sys
import urllib.error
import urllib.request

# On Windows `figaro` is the figaro.exe from tools/install.ps1, which starts
# Python outside this folder: the modules next to this file are found anyway.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_extras  # noqa: E402  links, autostart, inspect / shot / link, --lib


# 127.0.0.1, not "localhost": on Windows "localhost" tries ::1 first, and against a
# bridge that only listens on IPv4 every call stalls ~2 s before falling back.
DEFAULT_HOST = os.environ.get("FIGARO_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("FIGARO_PORT", "8788"))

HOST = DEFAULT_HOST
PORT = DEFAULT_PORT
TARGET = None  # which connected Figma file to route to (file name / fileKey / substring)
PARALLEL = False  # skip the bridge's per-file lock — READ-ONLY scripts only
AGENT = os.environ.get("FIGARO_AGENT") or None  # who is calling, shown in the file's queue
QUEUE_TIMEOUT = None  # max seconds to wait for a busy file; bridge default = --timeout
READ_ONLY = False  # the plugin rolls back whatever the script changed
CHECKPOINT = None  # a label forces a checkpoint, False skips it, None = hourly rule

# An agent's sandbox that keeps commands off the network (Codex, Cursor) blocks
# 127.0.0.1 too: the call fails with "Operation not permitted", not "refused".
SANDBOXED = ("a sandbox keeps this command off the network, 127.0.0.1 included. Run figaro "
             "outside the sandbox: ask the user to approve that, or to allow figaro for good")

KNOWN_CMDS = {
    "exec", "status", "targets", "clear", "doctor", "sel", "tree", "find", "text", "variant",
    "clone", "rm", "import-component", "icomp", "undo", "reload", "done",
    "inspect", "shot", "link",
}


def force_utf8_output():
    """Windows consoles default to cp1252 and raise on anything outside it.

    Layer names are routinely Cyrillic, CJK or emoji, so without this a plain
    `figaro tree` blows up with UnicodeEncodeError instead of printing.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def read_stdin():
    """The script on stdin, read as UTF-8: on Windows Python reads a pipe in the
    ANSI code page, which garbles every character outside ASCII in a script's
    texts, and Figma would get them garbled."""
    data = sys.stdin.buffer.read()
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode(locale.getpreferredencoding(False), errors="replace")


def node_expr(id_or_alias):
    """JS that resolves a node id, or the aliases `page` / `sel`."""
    return f"await h.resolve({json.dumps(id_or_alias)})"


def _request(method, path, payload=None, timeout=65):
    url = f"http://{HOST}:{PORT}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        body = e.read() or b"{}"
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, {"ok": False, "error": body.decode("utf-8", "replace")}
    except urllib.error.URLError as e:
        # Nothing listens — start our bridge once and ask again.
        if isinstance(e.reason, ConnectionRefusedError) and cli_extras.autostart(HOST, PORT):
            return _request(method, path, payload, timeout)
        resp = {"ok": False, "error": f"connection: {e.reason}"}
        if isinstance(e.reason, PermissionError):
            resp["hint"] = SANDBOXED
        return 0, resp
    except TimeoutError:
        return 0, {"ok": False, "error": "request timed out"}


def _exec(code, timeout=60, read_only=False, quick=False, libs=None, probe=False):
    payload = {"code": code, "timeout": timeout}
    if TARGET:
        payload["target"] = TARGET
    if PARALLEL:
        payload["parallel"] = True
    if AGENT:
        payload["agent"] = AGENT
    if READ_ONLY or read_only:
        payload["read_only"] = True
    if CHECKPOINT is not None:
        payload["checkpoint"] = CHECKPOINT
    if quick:  # a built-in reader — no undo step, change list or wait for Figma's events
        payload["quick"] = True
    if probe:  # doctor's check of the connection: the plugin's window doesn't show it
        payload["probe"] = True
    if libs:
        payload["libs"] = libs
    queue = timeout if QUEUE_TIMEOUT is None else QUEUE_TIMEOUT
    payload["queue_timeout"] = queue
    # The socket deadline has to outlast the bridge's own: time in the file's
    # queue plus the run itself. Hanging up earlier loses the bridge's error
    # payload, and used to leave a queued script to run with nobody waiting.
    status, resp = _request("POST", "/exec", payload, timeout=queue + timeout + 5)
    if resp.get("queued_ms", 0) >= 1000:
        print(f"  (waited {resp['queued_ms'] / 1000:.1f}s in the file's queue)", file=sys.stderr)
    return status, resp


def _emit(resp, raw=False):
    if raw:
        print(json.dumps(resp, indent=2, ensure_ascii=False))
        return 0 if resp.get("ok") else 1

    for line in resp.get("logs") or []:
        print(f"  log: {line}", file=sys.stderr)
    if resp.get("notice"):
        print(f"   ⚠ {resp['notice']}", file=sys.stderr)
    for line in _change_lines(resp):
        print(line, file=sys.stderr)

    if resp.get("ok") is False:
        print(f"figaro: {resp.get('error', 'unknown')}", file=sys.stderr)
        if resp.get("line"):
            print(f"   line {resp['line']}: {resp.get('source', '')}", file=sys.stderr)
        lib = resp.get("lib")  # the error is inside a --lib file
        if isinstance(lib, dict):
            print(f"   {lib.get('name')}, line {lib.get('line')}: {lib.get('source', '')}",
                  file=sys.stderr)
        if resp.get("warning"):
            print(f"   ⚠ {resp['warning']}", file=sys.stderr)
        if resp.get("hint"):
            print(f"   hint: {resp['hint']}", file=sys.stderr)
        if resp.get("stack"):
            print(resp["stack"], file=sys.stderr)
        return 1

    if resp.get("result"):
        print(resp["result"])
    print(f"  ({resp.get('elapsed_ms', '?')}ms)", file=sys.stderr)
    return 0


def _change_lines(resp):
    """What the script changed, and the checkpoint made before it."""
    out = []
    c = resp.get("changes")
    if isinstance(c, dict):
        parts = [f"{sign}{c[key]}" for sign, key in (("+", "created"), ("−", "deleted"),
                                                     ("~", "changed")) if c.get(key)]
        if c.get("styles"):
            parts.append(f"styles {c['styles']}")
        line = "  changed: " + (" ".join(parts) or "nothing")
        if c.get("props"):
            line += f" ({', '.join(c['props'])})"
        if c.get("remote"):
            line += f"; {c['remote']} more by other people in the file"
        if c.get("shared"):
            line += "; other scripts ran at the same time — their changes are here too"
        if c.get("late"):
            line += "; Figma reported late — the list may be incomplete"
        out.append(line)
        made = None if resp.get("rolled_back") else cli_extras.created_line(c)
        if made:
            out.append(made)
    cp = resp.get("checkpoint")
    if isinstance(cp, dict):
        if cp.get("error"):
            out.append(f"   ⚠ checkpoint not saved: {cp['error']}")
        else:
            out.append(f"  checkpoint: \"{cp.get('title')}\" — File → Version history")
    return out


# ─── command builders ─────────────────────────────────────────────────────

def cmd_status(args):
    status, resp = _request("GET", "/status")
    print(json.dumps(resp, indent=2, ensure_ascii=False))
    return 0 if status == 200 else 2


def cmd_targets(args):
    status, resp = _request("GET", "/targets")
    if status == 0:  # no bridge to ask
        return _emit(resp)
    files = resp.get("files") or []
    if not files:
        print("no files connected — Run the Figaro plugin in each Figma file",
              file=sys.stderr)
        return 1
    for f in files:
        name = f.get("name") or "(unidentified)"
        key = f.get("fileKey") or "-"
        q = f.get("queue") or {}
        run = q.get("running")
        busy = f"busy: {run.get('agent') or '?'} {run.get('for_s')}s" if run else "idle"
        if q.get("waiting"):
            busy += f", waiting: {', '.join(q['waiting'])}"
        same = f"\tsame document as {f['sameDocAs']}" if f.get("sameDocAs") else ""
        # doc is stable (stored in the file) — that's the id to use with -T.
        print(f"{name}\t{f.get('doc') or '-'}\t{key}\t{f.get('conn')}\t{busy}{same}")
    return 0


def cmd_clear(args):
    """Drop a file's abandoned-script interlock after a 504."""
    payload = {"target": TARGET} if TARGET else {}
    status, resp = _request("POST", "/clear", payload)
    if status != 200:
        print(resp.get("error", "clear failed"), file=sys.stderr)
        if resp.get("hint"):
            print(f"   hint: {resp['hint']}", file=sys.stderr)
        return 2
    cleared = resp.get("cleared") or []
    print(f"cleared {len(cleared)} abandoned script(s) on {resp.get('file')!r}"
          + (f": {', '.join(cleared)}" if cleared else ""))
    return 0


def cmd_sel(args):
    return _emit(_exec("return h.sel();", args.timeout, quick=True)[1], raw=args.raw)


def cmd_undo(args):
    """Revert the file's last script — the plugin refuses when that is unsafe."""
    payload = {"force": args.force}
    if TARGET:
        payload["target"] = TARGET
    if AGENT:
        payload["agent"] = AGENT
    status, resp = _request("POST", "/undo", payload)
    if args.raw:
        return _emit(resp, raw=True)
    if not resp.get("ok"):
        print(f"figaro: {resp.get('error', 'undo failed')}", file=sys.stderr)
        if resp.get("hint"):
            print(f"   hint: {resp['hint']}", file=sys.stderr)
        return 1
    v = resp.get("value") or {}
    done = v.get("undone") or {}
    # Figma may have kept one script as several Cmd+Z steps (addComponentProperty)
    steps = f" ({v['steps']} Cmd+Z steps)" if v.get("steps") else ""
    print(f"undone: {done.get('changes')}{steps} — {done.get('agent') or 'a script without -A'}, "
          f"{done.get('ago')}; can still undo: {v.get('left', 0)}")
    if v.get("warning"):
        print(f"   ⚠ {v['warning']}", file=sys.stderr)
    return 0


def cmd_reload(args):
    """Run plugin/code.js and ui.html from disk in the open plugin."""
    payload = {"target": TARGET} if TARGET else {}
    status, resp = _request("POST", "/reload", payload)
    if args.raw:
        return _emit(resp, raw=True)
    if not resp.get("ok"):
        print(f"figaro: {resp.get('error', 'reload failed')}", file=sys.stderr)
        if resp.get("hint"):
            print(f"   hint: {resp['hint']}", file=sys.stderr)
        return 1
    print(f"plugin {resp.get('plugin')} runs in \"{resp.get('file')}\" "
          f"({resp.get('elapsed_ms')} ms)")
    return 0


def cmd_done(args):
    """Say this agent is done in the file, for now: the plugin's window shows
    it done, with the note for the user, until its next script there that can
    change the file."""
    payload = {"text": args.text} if args.text else {}
    if TARGET:
        payload["target"] = TARGET
    if AGENT:
        payload["agent"] = AGENT
    status, resp = _request("POST", "/done", payload)
    if status == 404:
        resp = {"ok": False, "error": "the bridge runs code older than `figaro done`",
                "hint": ("restart it when no other agent is using it:  "
                         + cli_extras.start_hint(restart=True))}
    if args.raw:
        return _emit(resp, raw=True)
    if not resp.get("ok"):
        print(f"figaro: {resp.get('error', 'done failed')}", file=sys.stderr)
        if resp.get("hint"):
            print(f"   hint: {resp['hint']}", file=sys.stderr)
        return 1
    note = f", with your note: {resp['text']}" if resp.get("text") else ""
    print(f"the Figaro window in \"{resp.get('file')}\" shows you done{note}")
    print("  until your next script in this file that can change it", file=sys.stderr)
    return 0


def cmd_doctor(args):
    """Walk the chain bridge -> plugin -> Figma, naming the fix at each break."""
    def ok(text):
        print(f"  ✓  {text}")

    def fail(text, fix):
        print(f"  ✗  {text}")
        print(f"     → {fix}")

    status, resp = _request("GET", "/status")
    if status == 0:
        start = f"start it:  {cli_extras.start_hint()}"
        fail(f"bridge not answering on {HOST}:{PORT} — {resp.get('error')}", resp.get("hint") or start)
        return 1
    if status == 403:
        fail(f"bridge refused the request: {resp.get('error')}",
             "a proxy is rewriting Host/Origin — talk to the bridge directly")
        return 1
    ok(f"bridge answering on {HOST}:{PORT}"
       + (f" — Figaro {resp['version']}" if resp.get("version") else ""))
    stale = False
    if resp.get("bridge_outdated"):
        stale = True
        fail("bridge.py changed since the bridge started — it runs old code",
             "restart it when no other agent is using it:  "
             + cli_extras.start_hint(restart=True))

    if not resp.get("plugin_connected"):
        fail("plugin not connected",
             "in Figma Desktop: Plugins → Development → Figaro")
        return 1
    ok("plugin connected")

    current = resp.get("plugin_version")
    for f in resp.get("files") or []:
        if f.get("outdated"):
            stale = True
            fail(f"\"{f.get('name')}\" runs an older plugin build "
                 f"({f.get('plugin') or 'unversioned'}, current {current})",
                 f"figaro reload -T {f.get('fileKey') or f.get('doc')}   "
             "(or re-run it in that file: Plugins → Development → Figaro)")
    if current and not stale:
        ok(f"plugin build {current} everywhere")
    update = resp.get("update")
    if update:
        # Not a failure — everything works, there is just something newer.
        print(f"  !  Figaro {update.get('latest')} is released (this is {update.get('current')})")
        print(f"     → git pull, restart the bridge, re-run the plugin   ({update.get('url')})")

    status, r = _exec("return 1 + 1;", 10, quick=True, probe=True)
    if status == 409 and r.get("abandoned"):
        fail(f"file is interlocked: {r.get('error')}",
             "wait for that script to finish, or:  figaro clear -T <file>")
        return 1
    if status == 409:
        # Several files connected and no -T, or an ambiguous one — the plugin is
        # fine, the request just doesn't say which file it means.
        fail(r.get("error", "target required"),
             "pick one with -T: a file name, or the doc id from `figaro targets` "
             "when two files share a name")
        return 1
    if not r.get("ok") or r.get("value") != 2:
        fail(f"round trip failed: {r.get('error', r)}",
             "close the plugin window in Figma and run it again")
        return 1
    ok(f"round trip works ({r.get('elapsed_ms', '?')}ms)")

    status, r = _exec(
        "return {file: figma.root.name, page: figma.currentPage.name, "
        "pages: figma.root.children.length};", 10, quick=True, probe=True)
    if r.get("ok"):
        v = r.get("value") or {}
        ok(f"editing \"{v.get('file')}\" — page \"{v.get('page')}\" "
           f"of {v.get('pages')}")
        print("\n  The plugin is bound to whichever file was open when you ran it.")
        print("  Switched files? Run the plugin again in the new one.")
    return 1 if stale else 0


def cmd_exec(args):
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            code = f.read()
    elif args.stdin:
        code = read_stdin()
    elif args.code:
        code = args.code
    else:
        print("figaro: provide code (positional, --file, or --stdin)", file=sys.stderr)
        return 2
    libs = cli_extras.read_libs(args.lib)
    resp = _exec(code, args.timeout, libs=libs)[1]
    if resp.get("lib_missing"):  # the plugin lost a --lib before the script ran; now the code goes
        resp = _exec(code, args.timeout, libs=libs)[1]
    rc = _emit(resp, raw=args.raw)
    if rc == 0 and args.shot is not None:
        rc = cli_extras.shot_after(args, resp)
    return rc


def cmd_tree(args):
    opts = json.dumps({
        "maxDepth": args.depth,
        "showSize": not args.no_size,
        "showText": not args.no_text,
        "showLayout": args.layout,
    })
    code = (
        f"const n = {node_expr(args.node_id)};"
        f"if (!n) throw new Error('node not found: ' + {json.dumps(args.node_id)});"
        f"return h.dumpTree(n, {opts});"
    )
    return _emit(_exec(code, args.timeout, quick=True)[1], raw=args.raw)


def cmd_find(args):
    if "=" not in args.filter and "~" not in args.filter:
        print("figaro: filter must be key=value or key~value", file=sys.stderr)
        print("  forms: name=X (exact), name~X (substring), type=X, text=X (substring)", file=sys.stderr)
        return 2

    # Whichever separator comes first decides the mode, so a value may contain
    # the other character (name~a=b is a substring search for "a=b").
    tilde = args.filter.find("~")
    equals = args.filter.find("=")
    substring_mode = tilde != -1 and (equals == -1 or tilde < equals)

    # H.find searches by type with findAllWithCriteria and skips layers
    # hidden inside instances unless --hidden.
    if substring_mode:
        key, value = args.filter.split("~", 1)
        if key not in ("name", "text"):
            print(f"figaro: substring filter only supports name~ and text~ (got {key}~)", file=sys.stderr)
            return 2
        query = {key + "Has": value}
    else:
        key, value = args.filter.split("=", 1)
        if key not in ("name", "type", "text"):
            print(f"figaro: unknown filter key '{key}'. Use name, type, text", file=sys.stderr)
            return 2
        query = {key: value.upper() if key == "type" else value}
    if args.hidden:
        query["hidden"] = True

    code = (
        f"const root = {node_expr(args.node_id)};"
        f"if (!root) throw new Error('node not found: ' + {json.dumps(args.node_id)});"
        f"return h.find(root, {json.dumps(query, ensure_ascii=False)});"
    )
    return _emit(_exec(code, args.timeout, quick=True)[1], raw=args.raw)


def cmd_text(args):
    code = (
        f"const n = {node_expr(args.node_id)};"
        f"if (!n) throw new Error('node not found');"
        f"if (n.type !== 'TEXT') throw new Error('not a TEXT node (got ' + n.type + ')');"
        f"const before = n.characters;"
        f"await h.setText(n, {json.dumps(args.text)});"
        f"return {{id: n.id, before, after: n.characters}};"
    )
    return _emit(_exec(code, args.timeout)[1], raw=args.raw)


def cmd_variant(args):
    props = {}
    for kv in args.props:
        if "=" not in kv:
            print(f"figaro: variant prop must be 'Property=Value', got {kv!r}", file=sys.stderr)
            return 2
        k, v = kv.split("=", 1)
        props[k.strip()] = v.strip()

    code = (
        f"const n = {node_expr(args.node_id)};"
        f"if (!n) throw new Error('node not found');"
        f"if (n.type !== 'INSTANCE') throw new Error('not an INSTANCE (got ' + n.type + ')');"
        # H.variant takes short names ("Label") and "true"/"false" for a BOOLEAN
        f"await h.variant(n, {json.dumps(props, ensure_ascii=False)});"
        f"const out = {{}};"
        f"for (const k in n.componentProperties) out[k] = n.componentProperties[k].value;"
        f"return {{id: n.id, applied: {json.dumps(props)}, current: out}};"
    )
    return _emit(_exec(code, args.timeout)[1], raw=args.raw)


def cmd_clone(args):
    direction = "right"
    for d in ("left", "right", "up", "down"):
        if getattr(args, d, False):
            direction = d
    opts = {"direction": direction, "gap": args.gap}
    if args.name:
        opts["name"] = args.name

    code = (
        f"const n = {node_expr(args.node_id)};"
        f"if (!n) throw new Error('node not found');"
        f"const c = h.cloneNext(n, {json.dumps(opts)});"
        # The view moves only when asked — it is the user's screen too.
        + ("figma.viewport.scrollAndZoomIntoView([n, c]);" if args.focus else "")
        + f"return {{clone_id: c.id, x: c.x, y: c.y, name: c.name}};"
    )
    return _emit(_exec(code, args.timeout)[1], raw=args.raw)


def cmd_rm(args):
    # Cleaning up after an experiment is almost always plural.
    # Every id is found first, so a typo removes nothing; a layer already
    # gone with its parent earlier in the list is skipped.
    code = (
        f"const ids = {json.dumps(args.node_ids)};"
        f"const nodes = [], missing = [];"
        f"for (const id of ids) {{"
        f"  let n = null;"
        f"  try {{ n = await h.resolve(id); }} catch (e) {{}}"
        f"  if (n) nodes.push(n); else missing.push(id);"
        f"}}"
        f"if (missing.length) throw new Error('node not found: ' + missing.join(', ') + ' — nothing removed');"
        f"const removed = [], skipped = [];"
        f"for (const n of nodes) {{"
        f"  if (n.removed) {{ skipped.push(n.id); continue; }}"
        f"  removed.push({{id: n.id, name: n.name, type: n.type}});"
        f"  n.remove();"
        f"}}"
        f"if (skipped.length) print('already removed with a parent or listed twice: ' + skipped.join(', '));"
        f"return removed;"
    )
    return _emit(_exec(code, args.timeout)[1], raw=args.raw)


def cmd_import_component(args):
    code = (
        f"const comp = await figma.importComponentByKeyAsync({json.dumps(args.key)});"
        f"const inst = comp.createInstance();"
        f"figma.currentPage.appendChild(inst);"
        + ("figma.viewport.scrollAndZoomIntoView([inst]);" if args.focus else "")
        + f"return {{component: comp.name, instance_id: inst.id, w: inst.width, h: inst.height}};"
    )
    return _emit(_exec(code, args.timeout)[1], raw=args.raw)


# ─── argparse / dispatch ───────────────────────────────────────────────────

def _add_common_flags(p):
    p.add_argument("--timeout", "-t", type=int, default=60)
    p.add_argument("--raw", action="store_true", help="print full JSON response")
    p.add_argument("--target", "-T", default=None,
                   help="which connected file to route to (name, fileKey, or substring)")
    p.add_argument("--parallel", action="store_true",
                   help="bypass the per-file lock so reads can fan out. "
                        "READ-ONLY scripts only — a parallel writer interleaves.")
    p.add_argument("--agent", "-A", default=None,
                   help="your name in the plugin's window and the file's queue (env FIGARO_AGENT)")
    p.add_argument("--queue-timeout", type=float, default=None,
                   help="seconds to wait while another caller holds the file "
                        "(default: same as --timeout); gives up with 'file busy' "
                        "without running anything")
    p.add_argument("--read-only", "-R", action="store_true",
                   help="the script must not change the file: whatever it changes "
                        "is rolled back and the call fails")
    cp = p.add_mutually_exclusive_group()
    cp.add_argument("--checkpoint", metavar="LABEL", default=None,
                    help="save a version in File → Version history before the script")
    cp.add_argument("--no-checkpoint", action="store_true",
                    help="skip the hourly checkpoint for this script")


def build_parser():
    ap = argparse.ArgumentParser(prog="figaro", description=__doc__.splitlines()[0],
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=DEFAULT_HOST,
                    help="bridge host (env FIGARO_HOST)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT,
                    help="bridge port (env FIGARO_PORT)")

    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("status", help="the bridge's raw state, as JSON")
    sub.add_parser("targets", help="list connected Figma files (name / fileKey / conn)")

    p_clear = sub.add_parser("clear",
                             help="drop a file's abandoned-script interlock after a 504")
    _add_common_flags(p_clear)

    p_doctor = sub.add_parser("doctor", help="diagnose the bridge -> plugin -> Figma chain")
    _add_common_flags(p_doctor)

    p_sel = sub.add_parser("sel", help="what is selected in Figma right now")
    _add_common_flags(p_sel)

    p_exec = sub.add_parser("exec", help="run a JS script in the file: the Plugin API plus h.* helpers")
    _add_common_flags(p_exec)
    g = p_exec.add_mutually_exclusive_group()
    g.add_argument("code", nargs="?", help="the script, inline")
    g.add_argument("--file", "-f", help="read the script from a file")
    g.add_argument("--stdin", action="store_true", help="read the script from stdin")
    cli_extras.add_exec_flags(p_exec)

    p_tree = sub.add_parser("tree", help="the layer tree under a node: name, type, id, size")
    _add_common_flags(p_tree)
    p_tree.add_argument("node_id", help="node id, or `page` / `sel`")
    p_tree.add_argument("--depth", type=int, default=99)
    p_tree.add_argument("--no-size", action="store_true")
    p_tree.add_argument("--no-text", action="store_true")
    p_tree.add_argument("--layout", action="store_true",
                        help="show layoutMode, gap, padding and sizing modes")

    p_find = sub.add_parser("find", help="find layers under a node by name, type or text")
    _add_common_flags(p_find)
    p_find.add_argument("node_id")
    p_find.add_argument("filter", help="name=X | name~X | type=X | text=X | text~X")
    p_find.add_argument("--hidden", action="store_true",
                        help="search layers hidden inside instances too (slower)")

    p_text = sub.add_parser("text", help="set a text layer's characters (loads its fonts)")
    _add_common_flags(p_text)
    p_text.add_argument("node_id")
    p_text.add_argument("text")

    p_variant = sub.add_parser("variant", help="set an instance's variant and properties: Size=L Label=\"Pay now\"")
    _add_common_flags(p_variant)
    p_variant.add_argument("node_id")
    p_variant.add_argument("props", nargs="+")

    p_clone = sub.add_parser("clone", help="copy a layer next to itself")
    _add_common_flags(p_clone)
    p_clone.add_argument("node_id")
    p_clone.add_argument("--right", action="store_true")
    p_clone.add_argument("--left", action="store_true")
    p_clone.add_argument("--up", action="store_true")
    p_clone.add_argument("--down", action="store_true")
    p_clone.add_argument("--gap", type=int, default=100)
    p_clone.add_argument("--name")
    p_clone.add_argument("--focus", action="store_true", help="scroll Figma's view to the clone")

    p_rm = sub.add_parser("rm", help="delete layers; checks every id first")
    _add_common_flags(p_rm)
    p_rm.add_argument("node_ids", nargs="+", help="one or more node ids, or `sel`")

    for name in ("import-component", "icomp"):
        p = sub.add_parser(name, help="an instance of a published library component, by key"
                           if name == "import-component" else "short for import-component")
        _add_common_flags(p)
        p.add_argument("key")
        p.add_argument("--focus", action="store_true", help="scroll Figma's view to the instance")

    p_undo = sub.add_parser("undo", help="undo the file's last script, when that is safe")
    _add_common_flags(p_undo)
    p_undo.add_argument("--force", action="store_true",
                        help="revert it even though another agent made it")

    p_reload = sub.add_parser("reload",
                              help="load plugin/code.js and ui.html from disk into the open plugin")
    _add_common_flags(p_reload)

    p_done = sub.add_parser("done", help="you are done in the file, for now: the plugin's window "
                                         "says so, with a note for the user, until your next script "
                                         "that can change the file")
    _add_common_flags(p_done)
    p_done.add_argument("text", nargs="?", default="",
                        help="what the user should look at or answer, in their language")

    cli_extras.add_parsers(sub, _add_common_flags)

    return ap


def main():
    force_utf8_output()

    if len(sys.argv) >= 2 and sys.argv[1] == "wait":
        sys.argv[1] = "done"  # its name before 1.1.0 came out, still in some agents' context
    # Backward-compat shorthand: `figaro "<js>"` → `figaro exec "<js>"`
    if (
        len(sys.argv) >= 2
        and sys.argv[1] not in KNOWN_CMDS
        and not sys.argv[1].startswith("-")
    ):
        sys.argv.insert(1, "exec")

    ap = build_parser()
    args = ap.parse_args()

    if args.cmd is None:
        ap.print_help()
        sys.exit(2)

    global HOST, PORT, TARGET, PARALLEL, AGENT, QUEUE_TIMEOUT, READ_ONLY, CHECKPOINT
    HOST = args.host
    PORT = args.port
    if args.cmd == "exec":
        cli_extras.fix_exec_args(args)
    TARGET = cli_extras.target_and_ids(args)  # Figma links in -T and in node ids
    PARALLEL = getattr(args, "parallel", False)
    AGENT = getattr(args, "agent", None) or AGENT
    QUEUE_TIMEOUT = getattr(args, "queue_timeout", None)
    READ_ONLY = getattr(args, "read_only", False)
    CHECKPOINT = (False if getattr(args, "no_checkpoint", False)
                  else getattr(args, "checkpoint", None))

    dispatch = {
        "status": cmd_status,
        "targets": cmd_targets,
        "clear": cmd_clear,
        "doctor": cmd_doctor,
        "sel": cmd_sel,
        "exec": cmd_exec,
        "tree": cmd_tree,
        "find": cmd_find,
        "text": cmd_text,
        "variant": cmd_variant,
        "clone": cmd_clone,
        "rm": cmd_rm,
        "import-component": cmd_import_component,
        "icomp": cmd_import_component,
        "undo": cmd_undo,
        "reload": cmd_reload,
        "done": cmd_done,
        "inspect": cli_extras.cmd_inspect,
        "shot": cli_extras.cmd_shot,
        "link": cli_extras.cmd_link,
    }
    sys.exit(dispatch[args.cmd](args))


cli_extras.attach(sys.modules[__name__])

if __name__ == "__main__":
    main()
