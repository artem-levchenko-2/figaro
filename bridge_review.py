"""The Reviewer: a built-in agent that checks what other agents changed.

It is the last row of every file's Agents in the window. When an agent runs
`figaro done`, the bridge checks the layers that agent changed since its last
check with `h.audit` (a read-only script in the file's queue, no agent's work)
and shows the notes to the designer, not to the agent. The designer ticks the
notes the agent should fix and sends them; the agent waits for that answer
with `figaro notes --wait`, up to EXPIRE seconds.

    POST /notes   {target?, agent?, wait?}  -> the designer's answer, once

The file's switch is kept per file key in `reviewer.json` next to the token
({"<fileKey>": true}); off in every file until switched. The global switch
(bridge_settings `reviewer`) off: no row, no check, no `reviewer` on the board.

From the window: `reviewer` {doc, on}, `review-answer` {doc, send: [note ids]}
and the Stop of the row (`stop` with agent "reviewer"; bridge_board.handle).
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import time

from aiohttp import web

import bridge
import bridge_board
import bridge_exec
import bridge_settings
import figaro_token
import figma_links

AGENT = "reviewer"       # the agent's name on the board, and the check's own
EXPIRE = 600             # the agent stops waiting for the designer's answer after ten minutes
HOLD = 55                # seconds one /notes call holds: under the CLI's own socket limit
CHECK_TIMEOUT = 15.0     # the check's script
CHECK_QUEUE = 30.0       # how long it waits in the file's queue
CHECK_MS = 10000         # time h.audit gets for all the layers together
ROOTS_MAX = 25           # top-most layers audited in one check
TRACK_MAX = 500          # changed layers remembered per agent
NOTES_MAX = 30           # notes kept from one check
LAYERS_MAX = 5           # layers named per note

# One per h.audit rule, in its order of importance.
TITLES = {
    "missing-font": "Font is missing",
    "text-clipped": "Text is cut off",
    "zero-size": "Layer has no size",
    "low-contrast": "Text contrast is too low",
    "low-contrast-ui": "Icon or control contrast is too low",
    "placeholder-text": "Placeholder text left in",
    "small-target": "Click target is too small",
    "no-auto-layout": "Children placed by hand",
    "empty-frame": "Empty frame",
    "detached-instance": "Detached instance",
    "unbound-color": "Colour isn't a token",
    "no-text-style": "Text has no style",
    "default-name": "Layer has a default name",
    "uneven-siblings": "Siblings are uneven",
    "heading-order": "Heading levels skip",
    "absolute-in-layout": "Layer ignores auto-layout",
}
RULES = list(TITLES)

ON: dict = {}        # file key (else doc key) -> the file's switch
STATE: dict = {}     # doc key -> the Reviewer's row: see _row
TRACK: dict = {}     # doc key -> {agent: {layer id: None}}: changed since its last check
ASKING: dict = {}    # doc key -> notes waiting for the designer: {agent, notes, at, expired, layers}
ANSWERS: dict = {}   # doc key -> {agent: the designer's answer, until the agent reads it}
LOCKS: dict = {}     # doc key -> a check at a time per file


def reset():
    for registry in (ON, STATE, TRACK, ASKING, ANSWERS, LOCKS):
        registry.clear()


# ─── the file's switch ────────────────────────────────────────────────────

def _path():
    return figaro_token.token_path(bridge.PORT).parent / "reviewer.json"


def load():
    ON.clear()
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if isinstance(data, dict):
        ON.update({k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, bool)})


def _save():
    path = _path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")  # a write cut short leaves the old file whole
        part.write_text(json.dumps({k: v for k, v in ON.items() if v}, indent=2) + "\n",
                        encoding="utf-8")
        os.replace(part, path)
    except OSError as e:
        print(f"[reviewer] can't keep the switches in {path} ({e}): until the bridge restarts")


def _info(doc):
    for cid, info in bridge._live_plugins():
        if bridge._doc_key(cid) == doc:
            return info
    return {}


def _key(doc, info=None):
    """What the file's switch is kept under: its file key, which outlives the plugin run."""
    info = info if info is not None else _info(doc)
    return info.get("fileKey") or doc


def enabled(doc, info=None):
    """The Reviewer is on for this file: the global switch and the file's."""
    return bridge_settings.on("reviewer") and ON.get(_key(doc, info), False)


def switch(doc, on):
    if not isinstance(on, bool):
        return False
    ON[_key(doc)] = on
    _save()
    if not on:
        _withdraw(doc)
        STATE.pop(doc, None)
    print(f"[reviewer] turned {'on' if on else 'off'} in {doc}")
    bridge_board.changed()
    return True


# ─── what agents changed ──────────────────────────────────────────────────

def track(doc, agent, changes):
    """A script of `agent` changed layers: remember them for the next check."""
    if not isinstance(changes, dict):
        return
    ids = []
    for t in changes.get("top") or []:
        if isinstance(t, dict) and isinstance(t.get("id"), str):
            ids.append(t["id"])
    for i in changes.get("items") or []:
        if (isinstance(i, dict) and i.get("op") in ("+", "~") and isinstance(i.get("id"), str)
                and i.get("type") not in ("STYLE", "DOCUMENT")):
            ids.append(i["id"])
    if not ids:
        return
    mine = TRACK.setdefault(doc, {}).setdefault(agent, {})
    for i in ids:
        mine.pop(i, None)  # the newest at the end
        mine[i] = None
    while len(mine) > TRACK_MAX:
        mine.pop(next(iter(mine)))


# ─── the board ────────────────────────────────────────────────────────────

def _row(doc):
    return STATE.setdefault(doc, {"state": "waiting", "agent": None, "layers": 0, "since": None,
                                  "at": time.time(), "sent": 0, "left": 0})


def _set(doc, state, **fields):
    row = _row(doc)
    row.update(state=state, at=time.time(), since=None, **fields)
    return row


def board_part(doc, info):
    """The file's `reviewer` for the board; None with the global switch off, so a
    window shows no row."""
    if not bridge_settings.on("reviewer"):
        return None
    on = ON.get(_key(doc, info), False)
    out = {"on": on, "state": "off", "agent": None, "layers": 0, "since": None, "at": None,
           "notes": [], "expired": False, "sent": 0, "left": 0}
    if not on:
        return out
    row = STATE.get(doc)
    if row is None:
        return dict(out, state="waiting")
    out.update(state=row["state"], agent=row["agent"], layers=row["layers"], since=row["since"],
               at=row["at"], sent=row["sent"], left=row["left"])
    asking = ASKING.get(doc)
    if row["state"] == "notes" and asking:
        out["notes"] = asking["notes"]
        out["expired"] = asking["expired"]
    return out


def _recent(doc, event):
    bridge_board._add(bridge_board._file(doc), dict(event, at=time.time(), kind="ok"))


# ─── the check ────────────────────────────────────────────────────────────

def _script(ids):
    return _SCRIPT.replace("__IDS__", json.dumps(ids)).replace("__MS__", str(CHECK_MS)) \
                  .replace("__ROOTS__", str(ROOTS_MAX))


# The top-most changed layers that still exist, audited; each note carries the
# first layers it names, with their names and types.
_SCRIPT = """
const ids = __IDS__;
const want = new Set(ids);
const nodes = [];
for (const id of ids) {
  h.ck();
  let n = null;
  try { n = await h.node(id); } catch (e) { n = null; }
  let gone = !n;
  try { if (n && (n.removed || n.type === "DOCUMENT" || n.type === "PAGE")) gone = true; } catch (e) { gone = true; }
  if (!gone) nodes.push(n);
}
const top = nodes.filter((n) => {
  let p = null;
  try { p = n.parent; } catch (e) { p = null; }
  while (p) {
    if (want.has(p.id)) return false;
    try { p = p.parent; } catch (e) { p = null; }
  }
  return true;
});
const roots = top.slice(0, __ROOTS__);
const per = Math.max(1500, Math.floor(__MS__ / Math.max(1, roots.length)));
const notes = [], info = {};
let partial = null, errors = 0;
for (const r of roots) {
  h.ck();
  let res = null;
  try { res = await h.audit(r, { maxMs: per, links: false }); } catch (e) { errors++; continue; }
  if (res.partial) partial = res.partial;
  errors += res.errors || 0;
  for (const n of res.notes) {
    const some = (n.ids || [n.id]).slice(0, 5);
    notes.push({ rule: n.rule, text: n.text, count: n.count || 1, ids: some });
    for (const id of some) {
      if (info[id]) continue;
      try { const x = await h.node(id); info[id] = { name: x.name, type: x.type }; } catch (e) { /* gone */ }
    }
  }
}
return { layers: nodes.length, roots: roots.length, skipped: top.length - roots.length,
         notes, info, partial, errors };
"""


def _merge(found):
    """One note per rule and text, in the order of the rules; ids n1, n2…"""
    groups = {}
    for n in found.get("notes") or []:
        if not (isinstance(n, dict) and n.get("rule") in TITLES and isinstance(n.get("text"), str)):
            continue
        g = groups.setdefault((n["rule"], n["text"]), {"count": 0, "ids": []})
        g["count"] += int(n.get("count") or 1)
        g["ids"] += [i for i in n.get("ids") or [] if isinstance(i, str) and i not in g["ids"]]
    info = found.get("info") if isinstance(found.get("info"), dict) else {}
    order = sorted(groups, key=lambda k: RULES.index(k[0]))
    notes = []
    for rule, text in order[:NOTES_MAX]:
        g = groups[(rule, text)]
        layers = []
        for i in g["ids"][:LAYERS_MAX]:
            meta = info.get(i) if isinstance(info.get(i), dict) else {}
            layers.append({"id": i, "name": str(meta.get("name") or "Untitled")[:60],
                           "type": str(meta.get("type") or "FRAME")})
        notes.append({"id": f"n{len(notes) + 1}", "rule": rule, "title": TITLES[rule],
                      "text": text[:bridge_board.TEXT_MAX], "layers": layers,
                      "more": max(0, g["count"] - len(layers))})
    return notes


async def _check(request, conn_id, doc, ids):
    """Run the check in the file's queue. (found, why): the script's value, or None and the
    reason it gave no answer."""
    row = STATE.get(doc)

    async def dispatch(meta):
        if row is None or row.get("cancel"):
            return web.json_response({"ok": False, "error": "stopped", "stopped": True})
        return await bridge._dispatch(
            bridge.PLUGINS[conn_id]["ws"], conn_id, _script(ids), CHECK_TIMEOUT, False, meta=meta,
            board=False, fields={"readOnly": True, "quick": True, "agent": AGENT})

    resp = await bridge._queued(request, conn_id, AGENT, CHECK_QUEUE, dispatch, script=False,
                                read=True)
    try:
        body = json.loads(resp.text)
    except (TypeError, ValueError):
        return None, "no answer"
    if resp.status == 200 and body.get("ok") and isinstance(body.get("value"), dict):
        return body["value"], None
    return None, "stopped" if body.get("stopped") else str(body.get("error") or "no answer")[:120]


def _n(n, word):
    return f"{n} {word}" + ("" if n == 1 else "s")


async def on_done(request, conn_id, info, doc, agent):
    """`figaro done`: check what the agent changed, if the Reviewer is on in this file.
    The answer's `review`, or None when there is nothing to say."""
    mine = TRACK.get(doc, {}).pop(agent, None)
    if not enabled(doc, info) or not mine:
        return None
    ids = list(mine)
    lock = LOCKS.setdefault(doc, asyncio.Lock())
    async with lock:
        now = time.time()
        _withdraw(doc, agent)
        ANSWERS.get(doc, {}).pop(agent, None)
        row = _set(doc, "checking", agent=agent, layers=len(ids))
        row.update(since=now, cancel=False)
        bridge_board.changed()
        found, why = await _check(request, conn_id, doc, ids)
        row = STATE.get(doc)
        if row is None or row.get("cancel") or row.get("state") != "checking":
            return {"state": "none", "layers": len(ids), "notes": 0,
                    "text": "The designer stopped the reviewer's check, so nothing was reviewed."}
        if found is None:
            _set(doc, "waiting", agent=agent, layers=len(ids))
            for i in ids:
                TRACK.setdefault(doc, {}).setdefault(agent, {}).setdefault(i, None)
            bridge_board.changed()
            return {"state": "none", "layers": len(ids), "notes": 0,
                    "text": f"The reviewer couldn't check your layers this time ({why}). "
                            f"Carry on; run figaro done again to have them checked."}
        layers = int(found.get("layers") or 0)
        notes = _merge(found)
        if not layers:
            _set(doc, "waiting", agent=agent, layers=0)
            bridge_board.changed()
            return None
        if not notes:
            _set(doc, "good", agent=agent, layers=layers)
            _recent(doc, {"agent": AGENT, "summary": f"Checked {_n(layers, 'layer')}",
                          "note": "all good"})
            bridge_board.changed()
            return {"state": "good", "layers": layers, "notes": 0,
                    "text": f"Done. The reviewer checked the {_n(layers, 'layer')} you changed: "
                            f"all good."}
        at = time.time()
        ASKING[doc] = {"agent": agent, "notes": notes, "at": at, "expired": False}
        _set(doc, "notes", agent=agent, layers=layers)
        _recent(doc, {"agent": AGENT, "summary": f"Checked {_n(layers, 'layer')}",
                      "note": _n(len(notes), "note")})
        _expire_later(doc, at)
        bridge_board.changed()
        return {"state": "notes", "layers": layers, "notes": len(notes),
                "text": (f"Done. The reviewer is on in this file: it checked the "
                         f"{_n(layers, 'layer')} you changed and shows its notes to the "
                         f"designer, not to you.\nWait for their answer: figaro notes -A {shlex.quote(agent)} "
                         f"--wait   (up to {EXPIRE // 60} min)")}


def _withdraw(doc, rechecking=None):
    """The notes waiting for the designer go: the file's switch went off, or a newer check
    replaced them. Their agent is told when it asks, unless it is the one being checked again."""
    asking = ASKING.pop(doc, None)
    if asking and asking["agent"] != rechecking:
        ANSWERS.setdefault(doc, {})[asking["agent"]] = {"kind": "withdrawn"}


def _expire_later(doc, at):
    try:
        asyncio.get_running_loop().call_later(EXPIRE + 0.1, _expire, doc, at)
    except RuntimeError:
        pass


def _expire(doc, at):
    asking = ASKING.get(doc)
    if asking and asking["at"] == at and not asking["expired"]:
        asking["expired"] = True
        bridge_board.changed()


def stop(doc):
    """Stop in the Reviewer's row while it checks: the check ends, the row waits again."""
    row = STATE.get(doc)
    if not row or row.get("state") != "checking":
        return False
    row["cancel"] = True
    for rid, entry in list(bridge.PENDING.items()):
        if (entry.get("doc") == doc and entry.get("agent") == AGENT and entry.get("mtype") == "exec"
                and not entry.get("finished") and not entry.get("stopped")):
            entry["stopped"] = True
            info = bridge.PLUGINS.get(entry.get("conn")) or {}
            if info.get("ws") is not None:
                asyncio.get_running_loop().create_task(bridge._send_abort(info["ws"], rid))
    _set(doc, "waiting")
    row = STATE[doc]
    row["cancel"] = True
    print(f"[reviewer] the user stopped the check in {doc}")
    bridge_board.changed()
    return True


# ─── the window's messages ────────────────────────────────────────────────

def answer(doc, send):
    """The designer's answer: the ids of the notes the agent should fix, [] for all fine."""
    asking = ASKING.pop(doc, None)
    if not asking or not isinstance(send, list):
        return False
    chosen = {s for s in send if isinstance(s, str)}
    notes = asking["notes"]
    sent = [n for n in notes if n["id"] in chosen]
    left = len(notes) - len(sent)
    agent = asking["agent"]
    if sent:
        ANSWERS.setdefault(doc, {})[agent] = {"kind": "sent", "notes": sent, "total": len(notes),
                                              "left": left}
        _recent(doc, {"agent": "you", "summary": f"Sent {_n(len(sent), 'note')} to designer",
                      "note": f"{left} left as is" if left else None})
    else:
        ANSWERS.setdefault(doc, {})[agent] = {"kind": "fine", "total": len(notes)}
        _recent(doc, {"agent": "you", "summary": "All fine, nothing sent to designer"})
    row = _row(doc)
    row.update(state="sent", at=time.time(), since=None, sent=len(sent), left=left)
    bridge_board.changed()
    return True


def window_message(m):
    """`reviewer` and `review-answer` from a window. True if `m` was one."""
    mtype = m.get("type")
    doc = m.get("doc") if isinstance(m.get("doc"), str) else None
    if mtype not in ("reviewer", "review-answer"):
        return False
    if doc:
        if mtype == "reviewer":
            switch(doc, m.get("on"))
        else:
            answer(doc, m.get("send"))
    return True


# ─── POST /notes ──────────────────────────────────────────────────────────

def _result(doc, agent, info):
    """What the agent asks for, now: (state, extra fields). The answer is consumed."""
    got = ANSWERS.get(doc, {}).pop(agent, None)
    if got:
        if got["kind"] == "sent":
            notes = [dict(n, layers=[dict(l, url=figma_links.node_url(info.get("fileKey"),
                                                                      info.get("name"), l["id"]))
                                     for l in n["layers"]]) for n in got["notes"]]
            return "sent", {"notes": notes, "total": got["total"], "left": got["left"]}
        return got["kind"], {}
    asking = ASKING.get(doc)
    if asking and asking["agent"] == agent:
        if asking["expired"] or time.time() - asking["at"] >= EXPIRE:
            if not asking["expired"]:
                asking["expired"] = True
                bridge_board.changed()
            return "expired", {}
        return "pending", {}
    return "none", {}


async def notes_handler(request: web.Request) -> web.Response:
    """The designer's answer to the reviewer's notes for this agent, once. With `wait`, one
    call holds up to HOLD seconds while the designer has not answered; the CLI asks again."""
    body, error = await bridge_exec._body(request)
    if error:
        return error
    agent = body.get("agent")
    agent = agent.strip()[:40] or None if isinstance(agent, str) else None
    conn_id, info = bridge_exec._target(body)
    if conn_id is None:
        return info
    doc = bridge._doc_key(conn_id)
    agent = bridge_board._who(agent)
    end = time.monotonic() + (HOLD if body.get("wait") is True else 0)
    while True:
        asking = ASKING.get(doc)
        waiting = asking is not None and asking["agent"] == agent and not asking["expired"] \
            and time.time() - asking["at"] < EXPIRE
        if not waiting or time.monotonic() >= end:
            break
        await asyncio.sleep(0.2)
    state, extra = _result(doc, agent, info)
    return web.json_response(dict({"ok": True, "state": state, "agent": agent,
                                   "file": info.get("name")}, **extra))


def install(app: web.Application):
    app.router.add_post("/notes", notes_handler)
