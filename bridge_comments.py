"""Figma comments, and the token that reads them.

The Plugin API can't see comments: only Figma's REST API does, with the user's
personal access token. The user gives it in the plugin window's settings; it
stays on this computer, in a file of its own next to the bridge's API token
(mode 0600, figaro_token.figma_token_path), and nowhere else: not in
settings.json, not on the board (which has only whether there is one and its
last four characters), not in a log, not in a reply.

    POST /comments  {target?, agent?}
        -> {ok, file, fileKey, connected, comments: [thread], skipped}
        A thread is {id, author, at, message, replies: [{id, author, at, message}],
        pin: {node, x, y}}: an open (unresolved) comment, with where it is pinned.
        Without a token: 403 `needs_token`, and the file's island in the window
        asks the designer for one (the agent is named there) until they add it
        or press Not now (`declined`: this file isn't asked again, and the next
        call answers at once, until the bridge restarts).

A window's buttons come back over its socket: `figma-token` (the token to
check and keep), `figma-token-remove`, `ask-hide` (Not now). The check reads
the version history of the file the window is in, which needs the scopes the
later features need too: file_comments:read and file_versions:read.
"""
from __future__ import annotations

import asyncio
import os
import time

import aiohttp
from aiohttp import web

import bridge
import bridge_board
import bridge_exec
import figaro_token
import figma_links

API = "https://api.figma.com"
TIMEOUT = 15          # seconds for one answer of the REST API
TOKEN_MAX = 512
ASK_FOR = 600         # an island's ask goes after ten minutes: its agent probably gave up
ERROR_KEEP = 120      # the window shows a rejected token this long
THREADS_MAX = 200     # open comments one call lists
TEXT_MAX = 4000       # characters of one comment

STATE: dict = {"token": None, "check": None, "why": None, "at": 0.0, "gen": 0, "rejected": False}
ASKS: dict = {}       # doc key -> {"agent", "at"}: an agent that wanted comments with no token
DECLINED: set = set()  # doc keys whose designer pressed Not now, until the bridge restarts
_tasks: set = set()


def reset():
    STATE.update(token=None, check=None, why=None, at=0.0, gen=0, rejected=False)
    ASKS.clear()
    DECLINED.clear()


# ─── the token ────────────────────────────────────────────────────────────

def _path():
    return figaro_token.figma_token_path(bridge.PORT)


def load():
    """The token the file keeps, once the bridge starts."""
    STATE.update(token=None, rejected=False)
    try:
        text = _path().read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return
    STATE["token"] = text if _valid(text) else None


def _valid(text):
    return (isinstance(text, str) and 0 < len(text) <= TOKEN_MAX
            and text.isascii() and text.isprintable() and not any(c.isspace() for c in text))


def has_token():
    return bool(STATE["token"])


def mask(token):
    """figd_…a1c4: what a window shows of a token."""
    if not token:
        return None
    return ("figd_" if token.startswith("figd_") else "") + "…" + token[-4:]


def _keep(token):
    """The token in its file, readable only by the user; False when it can't be written."""
    path = _path()
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")
        fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            if os.name != "nt":
                os.fchmod(fd, 0o600)
            f.write(token)
        os.replace(part, path)
        return True
    except OSError as e:
        try:
            path.with_name(path.name + ".part").unlink()
        except OSError:
            pass
        print(f"[figma] can't keep the token in {path} ({e.strerror or e}): until the bridge restarts")
        return False


def _drop():
    try:
        _path().unlink()
    except FileNotFoundError:
        pass
    except OSError as e:
        print(f"[figma] can't remove {_path()} ({e.strerror or e})")


def board_part():
    """What the board says: whether there is a token (masked), and how its check goes."""
    check, why = STATE["check"], STATE["why"]
    if check == "error" and time.time() - STATE["at"] > ERROR_KEEP:
        check = why = None
    return {"token": mask(STATE["token"]), "check": check, "why": why if check == "error" else None,
            "rejected": bool(STATE["token"] and STATE["rejected"])}


def asks():
    """{doc key: agent} of the islands that ask for a token now."""
    if has_token() and not STATE["rejected"]:
        return {}
    now = time.time()
    return {k: a["agent"] for k, a in ASKS.items() if now - a["at"] < ASK_FOR and k not in DECLINED}


# ─── the REST API ─────────────────────────────────────────────────────────

class FigmaError(Exception):
    """The answer isn't a 200: `status` is Figma's (0: no answer), `why` one of
    rejected, nofile, busy, network."""

    def __init__(self, status, why):
        super().__init__(f"{why} ({status})")
        self.status, self.why = status, why


async def _get(path, token):
    """The JSON of a GET; FigmaError otherwise. The token goes in a header and nowhere
    else, and no error says it."""
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as session:
            async with session.get(API + path, headers={"X-Figma-Token": token}) as r:
                if r.status == 200:
                    try:
                        return await r.json(content_type=None)
                    except ValueError:
                        raise FigmaError(r.status, "network")
                why = ("rejected" if r.status in (401, 403) else "nofile" if r.status == 404
                       else "busy" if r.status == 429 or r.status >= 500 else "rejected")
                raise FigmaError(r.status, why)
    except FigmaError:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
        raise FigmaError(0, "network") from None


def _sound_key(key):
    """A file key that may go in a REST path: what Figma's links carry, nothing else
    (the plugin's `hello` is not trusted to say it)."""
    return isinstance(key, str) and bool(figma_links.FILE_KEY.match(key))


def _file_key(doc):
    """The file key of the window's file, else of any file that has one."""
    live = [info for _, info in bridge._live_plugins() if _sound_key(info.get("fileKey"))]
    for cid, info in bridge._live_plugins():
        if _sound_key(info.get("fileKey")) and bridge._doc_key(cid) == doc:
            return info["fileKey"]
    return live[0]["fileKey"] if live else None


async def check(token, doc):
    """A token from the window: read the file's version history with it. A token
    Figma takes is kept; anything else leaves the one there was."""
    gen = STATE["gen"]
    key = _file_key(doc)
    STATE.update(check="checking", why=None, at=time.time())
    bridge_board.changed()
    why = None
    if not key:
        why = "open"
    else:
        try:
            await _get(f"/v1/files/{key}/versions?page_size=1", token)
        except FigmaError as e:
            why = e.why
            print(f"[figma] the token check failed: {e.why} (HTTP {e.status})")
    if gen != STATE["gen"]:
        return  # a Remove or a newer Save came while Figma was answering: it wins
    if why is None:
        if _keep(token):
            print("[figma] a token was saved")
        STATE.update(token=token, check=None, why=None, rejected=False)
        ASKS.clear()
    else:
        STATE.update(check="error", why=why, at=time.time())
        loop = asyncio.get_running_loop()
        loop.call_later(ERROR_KEEP + 0.1, bridge_board.changed)
    bridge_board.changed()


async def window_message(m):
    """A button of the token block or of the ask. True if `m` was one."""
    mtype = m.get("type")
    if mtype == "figma-token":
        token = m.get("token")
        token = token.strip() if isinstance(token, str) else ""
        STATE["gen"] += 1  # a check on its way no longer counts
        if not _valid(token):
            STATE.update(check="error", why="rejected", at=time.time())
            bridge_board.changed()
            return True
        doc = m.get("doc") if isinstance(m.get("doc"), str) else None
        task = asyncio.get_running_loop().create_task(check(token, doc))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    elif mtype == "figma-token-remove":
        STATE.update(token=None, check=None, why=None, rejected=False)
        STATE["gen"] += 1  # a check on its way must not save it again
        _drop()
        print("[figma] the token was removed")
        bridge_board.changed()
    elif mtype == "ask-hide":
        doc = m.get("doc") if isinstance(m.get("doc"), str) else None
        if doc:
            DECLINED.add(doc)
            ASKS.pop(doc, None)
            bridge_board.changed()
    else:
        return False
    return True


# ─── POST /comments ───────────────────────────────────────────────────────

def _pin(comment):
    """Where a comment is pinned: {node, x, y}. On a frame the point is relative to it,
    on a page it is on the canvas, and an older comment may have just {x, y}."""
    meta = comment.get("client_meta")
    meta = meta if isinstance(meta, dict) else {}
    node = meta.get("node_id")
    path = meta.get("stable_path")
    if not isinstance(node, str) and isinstance(path, list) and path and isinstance(path[-1], str):
        node = path[-1]
    off = meta.get("node_offset") if isinstance(meta.get("node_offset"), dict) else meta
    x, y = off.get("x"), off.get("y")
    num = lambda v: v if isinstance(v, (int, float)) and not isinstance(v, bool) else None  # noqa: E731
    return {"node": node if isinstance(node, str) and node else None, "x": num(x), "y": num(y)}


def _who(comment):
    user = comment.get("user")
    handle = user.get("handle") if isinstance(user, dict) else None
    return str(handle) if handle else "someone"


def _text(value):
    return str(value or "")[:TEXT_MAX]


def threads(comments):
    """(open threads in the order they began, how many resolved ones were left out)."""
    comments = [c for c in comments if isinstance(c, dict) and c.get("id")]
    replies: dict = {}
    for c in comments:
        if c.get("parent_id"):
            replies.setdefault(str(c["parent_id"]), []).append(c)
    roots = [c for c in comments if not c.get("parent_id")]
    roots.sort(key=lambda c: (str(c.get("created_at") or ""), str(c.get("order_id") or "")))
    out, skipped = [], 0
    for c in roots:
        if c.get("resolved_at"):
            skipped += 1
            continue
        mine = sorted(replies.get(str(c["id"]), []), key=lambda r: str(r.get("created_at") or ""))
        out.append({"id": str(c["id"]), "author": _who(c), "at": c.get("created_at"),
                    "message": _text(c.get("message")), "pin": _pin(c),
                    "replies": [{"id": str(r["id"]), "author": _who(r), "at": r.get("created_at"),
                                 "message": _text(r.get("message"))} for r in mine]})
    return out, skipped


def _no_token(asked, declined):
    if declined:
        return ("the designer chose not to add a Figma token (Not now in the Figaro window): work "
                "without the comments")
    if asked:
        return ("no Figma token yet: the designer was asked in the Figaro window (it shows a note in "
                "this file's island, and the token goes in its settings, ⚙). Run `figaro comments` "
                "again once they have added one, or go on without the comments")
    return ("no Figma token yet, and this file isn't open with Figaro, so there is no window to ask in: "
            "tell the designer to add one in the settings (⚙) of the Figaro window in any file, then "
            "run `figaro comments` again, or go on without the comments")


FAILS = {
    "rejected": (403, "Figma didn't accept the token Figaro has: it may have expired, or can't read "
                      "comments, or its account can't open this file. Tell the designer to replace it in "
                      "the Figaro window's settings (⚙ → Figma token): it needs the scopes "
                      "file_comments:read and file_versions:read"),
    "nofile": (409,"Figma has no such file for this token: check the file, and that the token's "
                    "account can open it"),
    "busy": (429, "Figma is busy: try again in a minute"),
    "network": (502, "Figma's server didn't answer: check the connection and try again"),
}


async def comments_handler(request: web.Request) -> web.Response:
    body, error = await bridge_exec._body(request)
    if error:
        return error
    agent = body.get("agent")
    agent = agent.strip()[:40] or None if isinstance(agent, str) else None
    target = body.get("target")
    if target is not None and not isinstance(target, str):
        return web.json_response({"ok": False, "error": "'target' is a Figma link or a file key, as text"},
                                 status=400)
    link = figma_links.parse(target) if target else None
    key = link.key if link else target if target and figma_links.FILE_KEY.match(target) else None
    conn_id, info = bridge_exec.resolve_target(target)
    doc, name = None, None
    if conn_id is not None:
        key, name, doc = info.get("fileKey"), info.get("name"), bridge._doc_key(conn_id)
        if not key:
            return web.json_response({
                "ok": False,
                "error": (f"the plugin in \"{name}\" runs a build that doesn't tell the file's key: "
                          f"update it first: figaro reload")}, status=409)
        if not _sound_key(key):
            return web.json_response({
                "ok": False,
                "error": (f"the plugin in \"{name}\" gave a file key that isn't one: "
                          f"run it again from the menu, or figaro reload")}, status=409)
    elif key is None:
        status = 503 if not bridge._live_plugins() else 409
        return web.json_response({"ok": False, "error": info}, status=status)
    if doc:
        bridge_board.started(doc, agent, read=True)
    token = STATE["token"]
    if not token:
        declined = bool(doc) and doc in DECLINED
        asked = bool(doc) and not declined
        if asked:
            ASKS[doc] = {"agent": bridge_board._who(agent), "at": time.time()}
            asyncio.get_running_loop().call_later(ASK_FOR + 0.1, bridge_board.changed)
            bridge_board.changed()
        return web.json_response({"ok": False, "needs_token": True, "asked": asked, "declined": declined,
                                  "error": _no_token(asked, declined)}, status=403)
    t0 = time.time()
    try:
        data = await _get(f"/v1/files/{key}/comments", token)
    except FigmaError as e:
        print(f"[figma] comments of {key}: {e.why} (HTTP {e.status})")
        status, text = FAILS.get(e.why, FAILS["rejected"])
        if e.why == "rejected":
            STATE["rejected"] = True
            if doc and doc not in DECLINED:
                ASKS[doc] = {"agent": bridge_board._who(agent), "at": time.time()}
                asyncio.get_running_loop().call_later(ASK_FOR + 0.1, bridge_board.changed)
            bridge_board.changed()
        return web.json_response({"ok": False, "error": text, "token_rejected": e.why == "rejected"},
                                 status=status)
    listed = data.get("comments") if isinstance(data, dict) else None
    found, skipped = threads(listed if isinstance(listed, list) else [])
    cut = max(0, len(found) - THREADS_MAX)
    found = found[:THREADS_MAX]
    if STATE["rejected"]:
        STATE["rejected"] = False
        ASKS.clear()
        bridge_board.changed()
    return web.json_response({"ok": True, "file": name, "fileKey": key, "connected": conn_id is not None,
                              "comments": found, "skipped": skipped, "cut": cut,
                              "elapsed_ms": int((time.time() - t0) * 1000)})


def install(app: web.Application):
    app.router.add_post("/comments", comments_handler)
