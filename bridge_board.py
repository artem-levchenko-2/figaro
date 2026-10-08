"""What every plugin window shows, and what its buttons do.

A Figaro window draws one island per file: its own file first, then each file
where agents are at work. This module keeps what the islands show and sends
it to every window as a `board` message whenever something changes:

- per file: the running script's agent and the queue (bridge.QUEUE), the note
  an agent left for the user (`figaro wait`), the last failed script, recent
  changes with their layers, and when each agent last ran a script;
- the update in progress, or why it failed (bridge_update).

    POST /wait  {target?, agent?, text}  -> the file's island asks the user

A window's buttons come back over its WebSocket: `stop` (the script running in
a file ends, and its agent gets a 409), `dismiss` (a note or an error),
`update-now` and `reload-all` (bridge_update).
"""

from __future__ import annotations

import asyncio
import json
import time

from aiohttp import web

import bridge
import bridge_exec
import bridge_update

RECENT_MAX = 5     # changes listed per file
LAYERS_MAX = 3     # layers named per change
TEXT_MAX = 300     # characters of a note or an error
KEEP = 24 * 3600   # a closed file's island data is forgotten after a day
SEND_DELAY = 0.05  # one board for a burst of changes

FILES: dict = {}   # doc key -> {"waiting", "error", "recent", "seen"}
_send_task = None
_stale = {"at": 0.0, "value": False}

STOPPED = ("stopped: the user pressed Stop in the Figaro window in Figma. What the script changed "
           "before that stays in the file (see changed:). Don't run it again: ask the user what to do.")


def _who(agent):
    return agent or "agent"


def _file(key):
    f = FILES.get(key)
    if f is None:
        _forget_old()
        f = FILES[key] = {"waiting": None, "error": None, "recent": [], "seen": {}}
    return f


def _last(f):
    times = [e["at"] for e in f["recent"]] + list(f["seen"].values())
    for k, at in (("waiting", "since"), ("error", "at")):
        if f.get(k):
            times.append(f[k][at])
    return max(times, default=0.0)


def _forget_old():
    live = {bridge._doc_key(cid) for cid, _ in bridge._live_plugins()}
    now = time.time()
    for key in [k for k, f in FILES.items() if k not in live and now - _last(f) > KEEP]:
        FILES.pop(key, None)


def _add(f, event):
    f["recent"].insert(0, {k: v for k, v in event.items() if v is not None})
    del f["recent"][RECENT_MAX:]


# ─── the board ────────────────────────────────────────────────────────────

def changed():
    """Send the board to every window soon: one message for a burst of changes."""
    global _send_task
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if not any(info.get("hello") for _, info in bridge._live_plugins()):
        return  # nobody to tell; a window that connects gets a board at hello
    if _send_task is not None and not _send_task.done() and _send_task.get_loop() is loop:
        return
    _send_task = loop.create_task(_send_soon())


async def _send_soon():
    global _send_task
    await asyncio.sleep(SEND_DELAY)
    _send_task = None  # a change from here on sends another board
    await send()


async def send(ws=None):
    text = json.dumps(payload(), ensure_ascii=False)
    sockets = [ws] if ws is not None else [
        info["ws"] for _, info in bridge._live_plugins() if info.get("hello")]
    for s in sockets:
        try:
            await s.send_str(text)
        except Exception:
            pass


def payload():
    """The board: every connected file, in the order the files connected."""
    files, done = [], set()
    for cid, info in bridge._live_plugins():
        key = bridge._doc_key(cid)
        if not info.get("hello") or key in done:
            continue
        done.add(key)
        f = FILES.get(key) or {}
        q = bridge.QUEUE.get(key) or {}
        running = q.get("running")
        files.append({
            "doc": key, "sig": info.get("docSig"), "name": info.get("name") or "Untitled",
            "running": ({"agent": _who(running.get("agent")), "since": running["since"]}
                        if running else None),
            "queue": [_who(w.get("agent")) for w in q.get("waiting", [])],
            "waiting": f.get("waiting"),
            "error": f.get("error"),
            "recent": f.get("recent", []),
            "seen": f.get("seen", {}),
        })
    board = {"type": "board", "now": time.time(), "version": bridge.VERSION, "files": files,
             "update": bridge.UPDATE, "stale": _bridge_stale()}
    board.update(bridge_update.board())
    return board


def _bridge_stale():
    """bridge.bridge_outdated(), read at most every few seconds: it hashes files."""
    now = time.time()
    if now - _stale["at"] > 5:
        _stale.update(at=now, value=bridge.bridge_outdated())
    return _stale["value"]


# ─── what scripts do ──────────────────────────────────────────────────────

def started(key, agent):
    """A script got the file. Its agent is back at work, so the note it left
    for the user has been answered."""
    f = FILES.get(key)
    if f and f.get("waiting") and f["waiting"]["agent"] == _who(agent):
        f["waiting"] = None
    changed()


def finished(entry, mtype, fields, result):
    """A script ended: what it changed, its error, or the user's Stop."""
    key = entry.get("doc")
    if not key:
        return
    fields = fields or {}
    agent = _who(fields.get("agent"))
    now = time.time()
    f = _file(key)
    f["seen"][agent] = now
    if entry.get("stopped"):
        _add(f, {"agent": agent, "at": now, "kind": "stop", "summary": "Stopped by you"})
    elif result.get("type") == "error":
        # Not the script's fault: the plugin restarted, or it asks for a --lib again.
        if (mtype == "exec" and not result.get("libMissing")
                and result.get("text") != bridge.DISCONNECTED):
            line = result.get("line") if isinstance(result.get("line"), int) else None
            text = str(result.get("text") or "unknown error")[:TEXT_MAX]
            f["error"] = {"agent": agent, "at": now, "text": text, "line": line}
            _add(f, {"agent": agent, "at": now, "kind": "err", "summary": "Script failed",
                     "note": f"line {line}" if line else None})
    else:
        if f.get("error") and f["error"]["agent"] == agent:
            f["error"] = None  # its agent got past it
        if mtype == "undo":
            _add(f, {"agent": agent, "at": now, "kind": "ok", "summary": "Undid a script"})
        elif not fields.get("readOnly"):
            made = summary(result.get("changes"))
            if made:
                _add(f, dict(made, agent=agent, at=now, kind="ok"))
    changed()


def timed_out(entry, mtype, fields, timeout):
    key = entry.get("doc")
    if not key or mtype != "exec":
        return
    agent = _who((fields or {}).get("agent"))
    now = time.time()
    f = _file(key)
    f["seen"][agent] = now
    f["error"] = {"agent": agent, "at": now, "line": None,
                  "text": f"timed out after {timeout:g} s: it may still be running"}
    _add(f, {"agent": agent, "at": now, "kind": "err", "summary": "Script timed out"})
    changed()


def summary(changes):
    """What a script did, in a few words, and the layers to show:
    {summary, note?, layers, more?} — or None when it changed nothing."""
    if not isinstance(changes, dict):
        return None
    count = {k: int(changes.get(k) or 0) for k in ("created", "deleted", "changed", "styles")}
    items = [i for i in changes.get("items") or [] if isinstance(i, dict)]
    top = [t for t in changes.get("top") or [] if isinstance(t, dict)]
    note = None
    if count["created"]:
        shown = top or [i for i in items if i.get("op") == "+"][:1]
        total = len(top) + int(changes.get("topMore") or 0) if top else count["created"]
        text = (f"Created {shown[0].get('name') or 'a layer'}" if total == 1 and shown
                else f"Created {_n(total, 'layer')}")
    elif count["changed"]:
        props = list(changes.get("props") or {})
        if props == ["name"]:
            text = f"Renamed {_n(count['changed'], 'layer')}"
        else:
            text = f"Changed {_n(count['changed'], 'layer')}"
            note = ", ".join(props[:2]) or None
        shown = [i for i in items if i.get("op") == "~" and i.get("type") != "STYLE"]
        total = count["changed"]
    elif count["deleted"]:
        return {"summary": f"Deleted {_n(count['deleted'], 'layer')}", "layers": []}
    elif count["styles"]:
        return {"summary": f"Changed {_n(count['styles'], 'style')}", "layers": []}
    else:
        return None
    layers = [{"id": i.get("id"), "name": i.get("name") or "Untitled", "type": i.get("type") or "FRAME"}
              for i in shown[:LAYERS_MAX] if i.get("id")]
    out = {"summary": text, "layers": layers, "note": note}
    if total > len(layers) and layers:
        out["more"] = total - len(layers)
    return {k: v for k, v in out.items() if v is not None}


def _n(n, word):
    return f"{n} {word}" + ("" if n == 1 else "s")


# ─── the window's buttons ─────────────────────────────────────────────────

def stop(doc):
    """End the scripts running in a file now. h.ck() and every Figma call
    fail in them; the bridge answers their agent with a 409 (bridge._reply)."""
    hit = False
    for rid, entry in list(bridge.PENDING.items()):
        if (entry.get("doc") == doc and entry.get("mtype") == "exec"
                and not entry.get("finished") and not entry.get("stopped")):
            entry["stopped"] = True
            info = bridge.PLUGINS.get(entry.get("conn")) or {}
            if info.get("ws") is not None:
                asyncio.get_running_loop().create_task(bridge._send_abort(info["ws"], rid))
            hit = True
    if hit:
        print(f"[board] the user pressed Stop in {doc}")
    return hit


def dismiss(doc, what):
    f = FILES.get(doc)
    if f and what in ("waiting", "error"):
        f[what] = None
        changed()


async def handle(conn_id, m):
    """A button in a window. True if `m` was one."""
    mtype = m.get("type")
    doc = m.get("doc") if isinstance(m.get("doc"), str) else None
    if mtype == "stop":
        if doc:
            stop(doc)
    elif mtype == "dismiss":
        if doc:
            dismiss(doc, m.get("what"))
    elif mtype == "update-now":
        bridge_update.start(pull=True)
    elif mtype == "reload-all":
        bridge_update.start(pull=False)
    else:
        return False
    return True


# ─── POST /wait ───────────────────────────────────────────────────────────

async def wait_handler(request: web.Request) -> web.Response:
    """An agent asks the user for something: the file's island turns amber
    with its note until the agent's next script, or until the user dismisses it."""
    blocked = bridge._guard(request)
    if blocked is not None:
        return blocked
    body, error = await bridge_exec._body(request)
    if error:
        return error
    text = body.get("text")
    if not isinstance(text, str) or not text.strip():
        return web.json_response({"ok": False, "error": "missing 'text': what the user should do"},
                                 status=400)
    agent = body.get("agent")
    agent = agent.strip()[:40] or None if isinstance(agent, str) else None
    conn_id, info = bridge_exec._target(body)
    if conn_id is None:
        return info
    if "board" not in (info.get("caps") or []):
        return web.json_response({
            "ok": False,
            "error": (f"the plugin in \"{info.get('name')}\" runs an older build that can't show it — "
                      f"update it first: figaro reload"),
        }, status=409)
    note = " ".join(text.split())[:TEXT_MAX]
    f = _file(bridge._doc_key(conn_id))
    f["waiting"] = {"agent": _who(agent), "text": note, "since": time.time()}
    changed()
    return web.json_response({"ok": True, "file": info.get("name"), "text": note})


def install(app: web.Application):
    app.router.add_post("/wait", wait_handler)
