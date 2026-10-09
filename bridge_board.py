"""What every plugin window shows, and what its buttons do.

A Figaro window draws one island per file where the plugin runs: its own file
first, then the others in the order they connected. This module keeps what
the islands show and sends it to every window as a `board` message whenever
something changes:

- per file: its agents and the recent changes with their layers. An agent is
  at work from its first script until ACTIVE_FOR seconds pass without one,
  or until it says it is done, with a note for the user; the window also
  shows that the user stopped it, and a last script that failed;
- the update in progress, or why it failed (bridge_update).

    POST /done  {target?, agent?, text?}  -> the agent is done in the file, for now

A window's buttons come back over its WebSocket: `stop` (one agent in a file:
its running script ends and its agent gets a 409; pressed between its
scripts, its next script there is refused with a 409 and runs nothing),
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
KEEP = 24 * 3600   # a closed file's island data, and an agent quiet that long, is forgotten
SEND_DELAY = 0.05  # one board for a burst of changes
ACTIVE_FOR = 300   # an agent is at work until five minutes after its last script: 97% of the
                   # pauses between an agent's scripts in one turn are shorter
HOLD = 600         # a Stop between scripts refuses the agent's next one within ten minutes

FILES: dict = {}   # doc key -> {"agents": {name: agent}, "recent": [change]}
_send_task = None
_stale = {"at": 0.0, "value": False}

STOPPED = ("stopped: the user pressed Stop in the Figaro window in Figma. What the script changed "
           "before that stays in the file (see changed:). Don't run it again: ask the user what to do.")
HELD = ("stopped: the user pressed Stop for you in the Figaro window in Figma, so this script did not "
        "run. Don't run more scripts in this file: ask the user what to do.")


def _who(agent):
    return agent or "agent"


def _file(key):
    f = FILES.get(key)
    if f is None:
        _forget_old()
        f = FILES[key] = {"agents": {}, "recent": []}
    return f


def _agent(f, name):
    """since: when its run of scripts began; last: its last script; done: its
    note, {text, at}; stopped: when the user stopped it; error: its last
    script failed, {text, line, at}; hold: a Stop waiting for its next script."""
    a = f["agents"].get(name)
    if a is None:
        a = f["agents"][name] = {"since": None, "last": 0.0, "done": None, "stopped": None,
                                 "error": None, "hold": None}
    return a


def _touch(a, now, read=False):
    """A script of the agent began or ended: it is at work, in a new run of
    scripts if it was done, stopped or quiet. A script that can't change the
    file (a reader, -R) leaves its done or the user's Stop on show: agents
    take one more look after they say done, or at what a stopped script left."""
    if read and (a["done"] or a["stopped"]):
        a["last"] = now
        return
    if a["since"] is None or a["done"] or a["stopped"] or now - a["last"] > ACTIVE_FOR:
        a["since"] = now
    a.update(last=now, done=None, stopped=None)


def _seen(a):
    return max(a["last"], a["stopped"] or 0.0, (a["done"] or {}).get("at", 0.0))


def _last(f):
    times = [e["at"] for e in f["recent"]] + [_seen(a) for a in f["agents"].values()]
    return max(times, default=0.0)


def _forget_old():
    live = {bridge._doc_key(cid) for cid, _ in bridge._live_plugins()}
    now = time.time()
    for key, f in list(FILES.items()):
        for name in [n for n, a in f["agents"].items() if now - _seen(a) > KEEP]:
            del f["agents"][name]
        if key not in live and now - _last(f) > KEEP:
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
    """The board: every connected file, in the order the files connected.
    An agent is `busy` while a script of its runs in the file or waits for it;
    the window works out the rest from the times and ACTIVE_FOR."""
    files, done = [], set()
    for cid, info in bridge._live_plugins():
        key = bridge._doc_key(cid)
        if not info.get("hello") or key in done:
            continue
        done.add(key)
        f = FILES.get(key) or {"agents": {}, "recent": []}
        busy = _busy(key)
        files.append({
            "doc": key, "sig": info.get("docSig"), "name": info.get("name") or "Untitled",
            "agents": [{"name": n, "since": a["since"], "last": a["last"], "busy": n in busy,
                        "done": a["done"], "stopped": a["stopped"], "error": a["error"]}
                       for n, a in f["agents"].items()],
            "recent": f["recent"],
        })
    # A newer release goes as `update` (the window's Update button) or, with the
    # button off, as `release` (a link to it): a window too old to know `release`
    # shows nothing.
    button = bridge.update_allowed()
    board = {"type": "board", "now": time.time(), "active_for": ACTIVE_FOR,
             "version": bridge.VERSION, "files": files,
             "update": bridge.UPDATE if button else None,
             "release": None if button else bridge.UPDATE,
             "stale": _bridge_stale()}
    board.update(bridge_update.board())
    return board


def _busy(key):
    """The agents with a script running in the file or waiting in its queue."""
    q = bridge.QUEUE.get(key) or {}
    names = {_who(w.get("agent")) for w in q.get("waiting", []) if w.get("script", True)}
    names.update(_who(e.get("agent")) for e in bridge.PENDING.values()
                 if e.get("doc") == key and not e.get("finished") and e.get("board", True))
    return names


def _bridge_stale():
    """bridge.bridge_outdated(), read at most every few seconds: it hashes files."""
    now = time.time()
    if now - _stale["at"] > 5:
        _stale.update(at=now, value=bridge.bridge_outdated())
    return _stale["value"]


# ─── what scripts do ──────────────────────────────────────────────────────

def refusal(key, agent):
    """The 409 for the next script of an agent the user stopped between its
    scripts: it runs nothing. Only that one: once the user has answered the
    agent, its scripts go on."""
    a = (FILES.get(key) or {}).get("agents", {}).get(_who(agent))
    held = a["hold"] if a else None
    if not held:
        return None
    a["hold"] = None
    if time.time() - held > HOLD:
        return None
    return web.json_response({"ok": False, "error": HELD, "stopped": True}, status=409)


def started(key, agent, read=False):
    """A script came for the file: its agent is at work."""
    _touch(_agent(_file(key), _who(agent)), time.time(), read)
    changed()


def finished(entry, mtype, fields, result):
    """A script ended: what it changed, or its error. A script the user
    stopped was noted by stop(); figaro doctor's checks are no agent's work."""
    key = entry.get("doc")
    if not key or not entry.get("board", True):
        return
    fields = fields or {}
    name = _who(fields.get("agent"))
    now = time.time()
    f = _file(key)
    a = _agent(f, name)
    if entry.get("stopped"):
        changed()
        return
    _touch(a, now, bool(fields.get("readOnly")))
    if result.get("type") == "error":
        # Not the script's fault: the plugin restarted, or it asks for a --lib again.
        if (mtype == "exec" and not result.get("libMissing")
                and result.get("text") != bridge.DISCONNECTED):
            line = result.get("line") if isinstance(result.get("line"), int) else None
            text = str(result.get("text") or "unknown error")[:TEXT_MAX]
            a["error"] = {"text": text, "line": line, "at": now}
            _add(f, {"agent": name, "at": now, "kind": "err", "summary": "Script failed",
                     "note": f"line {line}" if line else None})
    else:
        a["error"] = None  # the agent got past it
        if mtype == "undo":
            _add(f, {"agent": name, "at": now, "kind": "ok", "summary": "Undid a script"})
        elif not fields.get("readOnly"):
            made = summary(result.get("changes"))
            if made:
                _add(f, dict(made, agent=name, at=now, kind="ok"))
    changed()


def timed_out(entry, mtype, fields, timeout):
    key = entry.get("doc")
    if not key or mtype != "exec" or not entry.get("board", True):
        return
    name = _who((fields or {}).get("agent"))
    now = time.time()
    f = _file(key)
    a = _agent(f, name)
    _touch(a, now, bool((fields or {}).get("readOnly")))
    a["error"] = {"text": f"timed out after {timeout:g} s: it may still be running", "line": None,
                  "at": now}
    _add(f, {"agent": name, "at": now, "kind": "err", "summary": "Script timed out"})
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

def stop(doc, agent=None):
    """The user's Stop for an agent in a file; from an older window, without
    `agent`, for every script running there.

    A running script ends now: h.ck() and every Figma call fail in it, and the
    bridge answers its agent with a 409 (bridge._reply). An agent between its
    scripts, or with one waiting in the queue, gets the 409 for its next
    script there instead (refusal)."""
    now = time.time()
    hit = set()
    for rid, entry in list(bridge.PENDING.items()):
        name = _who(entry.get("agent"))
        if (entry.get("doc") == doc and entry.get("mtype") == "exec" and entry.get("board", True)
                and not entry.get("finished") and not entry.get("stopped")
                and agent in (None, name)):
            entry["stopped"] = True
            info = bridge.PLUGINS.get(entry.get("conn")) or {}
            if info.get("ws") is not None:
                asyncio.get_running_loop().create_task(bridge._send_abort(info["ws"], rid))
            hit.add(name)
    names = hit | ({agent} if agent else set())
    if not names:
        return False
    queued = {_who(w.get("agent")) for w in (bridge.QUEUE.get(doc) or {}).get("waiting", [])
              if w.get("script", True)}
    f = _file(doc)
    for name in sorted(names):
        a = _agent(f, name)
        a.update(stopped=now, done=None, error=None,
                 hold=now if name not in hit or name in queued else None)
        _add(f, {"agent": name, "at": now, "kind": "stop", "summary": "Stopped by you"})
    print(f"[board] the user pressed Stop for {', '.join(sorted(names))} in {doc}")
    changed()
    return True


async def handle(conn_id, m):
    """A button in a window. True if `m` was one."""
    mtype = m.get("type")
    doc = m.get("doc") if isinstance(m.get("doc"), str) else None
    if mtype == "stop":
        agent = m.get("agent")
        if doc:
            stop(doc, agent.strip()[:40] or None if isinstance(agent, str) else None)
    elif mtype == "update-now":
        bridge_update.start(pull=True)
    elif mtype == "reload-all":
        bridge_update.start(pull=False)
    else:
        return False
    return True


# ─── POST /done ───────────────────────────────────────────────────────────

async def done_handler(request: web.Request) -> web.Response:
    """An agent is done in a file, for now: its row in the window says so, with
    the note it leaves the user, until its next script there that can change
    the file."""
    blocked = bridge._guard(request)
    if blocked is not None:
        return blocked
    body, error = await bridge_exec._body(request)
    if error:
        return error
    text = body.get("text")
    if text is not None and not isinstance(text, str):
        return web.json_response({"ok": False, "error": "'text' must be a string: what the user "
                                                         "should look at"}, status=400)
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
    note = " ".join((text or "").split())[:TEXT_MAX] or None
    a = _agent(_file(bridge._doc_key(conn_id)), _who(agent))
    a.update(done={"text": note, "at": time.time()}, stopped=None, error=None, hold=None)
    changed()
    return web.json_response({"ok": True, "file": info.get("name"), "text": note})


def install(app: web.Application):
    app.router.add_post("/done", done_handler)
