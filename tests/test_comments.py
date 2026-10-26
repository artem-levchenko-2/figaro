"""Figma comments: the token the designer gives in the window's settings, the check
that reads it, the ask for it on an island, `POST /comments`, and `figaro comments`.

Figma's REST API is a fake server here: no test reaches api.figma.com. What the window
does with the token block is tests/ui.test.js; the script that finds a pin's layer is
tests/test_comments_script.py.

    pytest tests/test_comments.py
"""
import asyncio
import json
import os
import stat
import sys

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

import bridge
import bridge_board
import bridge_comments
import cli_extras
import comments_text
import figaro
import figaro_token
from test_board import CAPS, boards, island, settle
from test_bridge import make_client, run
from test_exec import FakePlugin

TOKEN = "figd_" + "x" * 30 + "a1c4"
MASKED = "figd_…a1c4"
KEY = "K1aaaaaaaaaaaaaaaaaaaa"


def frame_pin(**extra):
    return dict({"id": "10", "parent_id": "", "user": {"handle": "Dana"}, "created_at": "2026-10-26T15:12:19.827Z",
                 "resolved_at": None, "message": "make this bigger", "order_id": "1",
                 "client_meta": {"node_id": "4:12", "node_offset": {"x": 120, "y": 48}, "stable_path": ["1:2", "4:12"]}},
                **extra)


def page_pin():
    return {"id": "11", "parent_id": "", "user": {"handle": "Sam"}, "created_at": "2026-10-26T16:00:00.000Z",
            "resolved_at": None, "message": "test comment for Figaro", "order_id": "2", "reactions": [],
            "client_meta": {"node_id": "1:2", "node_offset": {"x": 3288, "y": 924}, "stable_path": ["1:2"]}}


def reply(**extra):
    return dict({"id": "12", "parent_id": "10", "user": {"handle": "Sam"}, "created_at": "2026-10-26T15:30:00.000Z",
                 "resolved_at": None, "message": "agreed", "order_id": "3", "client_meta": None}, **extra)


class FakeFigma:
    """api.figma.com: the version history and the comments of one file, for one token."""

    def __init__(self, monkeypatch, *, token=TOKEN, comments=None, status=None):
        self.token, self.comments, self.status = token, comments if comments is not None else [], status
        self.seen = []        # (path, X-Figma-Token) of every request
        self.monkeypatch = monkeypatch
        self.server = None
        self.gate = None      # an Event: the version history waits for it

    async def __aenter__(self):
        app = web.Application()
        app.router.add_get("/v1/files/{key}/versions", self.versions)
        app.router.add_get("/v1/files/{key}/comments", self.listing)
        self.server = TestServer(app)
        await self.server.start_server()
        self.monkeypatch.setattr(bridge_comments, "API", str(self.server.make_url("")).rstrip("/"))
        return self

    async def __aexit__(self, *exc):
        await self.server.close()

    def answer(self, request, body):
        got = request.headers.get("X-Figma-Token")
        self.seen.append((request.path, got))
        if self.status:
            return web.json_response({"status": self.status, "err": "no"}, status=self.status)
        if got != self.token:  # what Figma answers a bad token (checked live)
            return web.json_response({"status": 401, "err": "Invalid token"}, status=401)
        return web.json_response(body)

    async def versions(self, request):
        if self.gate:
            await self.gate.wait()
        return self.answer(request, {"versions": [], "pagination": {}})

    async def listing(self, request):
        return self.answer(request, {"comments": self.comments})


def window(p, **m):
    return p.ws.send_str(json.dumps(m))


def token_file():
    return figaro_token.figma_token_path(bridge.PORT)


def figma_of(p):
    return boards(p)[-1]["figma"]


async def save(p, token=TOKEN, doc="doc:K1aaaaaaaaaaaaaaaaaaaa"):
    await window(p, type="figma-token", token=token, doc=doc)
    await asyncio.sleep(0.4)


# ─── the token ────────────────────────────────────────────────────────────

def test_a_token_figma_takes_is_kept_in_a_file_of_its_own_and_nowhere_else(monkeypatch):
    async def go():
        async with FakeFigma(monkeypatch) as fig:
            c = await make_client()
            async with FakePlugin(c, name="A", key=KEY, caps=CAPS) as p:
                assert figma_of(p) == {"token": None, "check": None, "why": None, "rejected": False}
                await save(p)
                assert figma_of(p) == {"token": MASKED, "check": None, "why": None, "rejected": False}
                assert ("/v1/files/K1aaaaaaaaaaaaaaaaaaaa/versions", TOKEN) in fig.seen  # checked on this file's version history
                path = token_file()
                assert path.read_text(encoding="utf-8") == TOKEN
                assert path.parent == figaro_token.token_path(bridge.PORT).parent
                if os.name != "nt":
                    assert stat.S_IMODE(path.stat().st_mode) == 0o600
                assert [f.name for f in path.parent.glob("figma-token*")] == ["figma-token"]  # no .part left
                # never in the settings file, the board, /status, a reply or a log
                assert not (path.parent / "settings.json").exists()
                for m in p.received:
                    assert TOKEN not in json.dumps(m)
                status = await c.get("/status")
                assert TOKEN not in await status.text()
                targets = await c.get("/targets")
                assert TOKEN not in await targets.text()
                root = await c.get("/")
                assert TOKEN not in await root.text()
            await c.close()
            # the bridge starts again: the file keeps it
            bridge_comments.reset()
            c = await make_client()
            assert bridge_comments.has_token() and bridge_comments.board_part()["token"] == MASKED
            await c.close()
    run(go())


def test_a_token_figma_refuses_is_not_kept(monkeypatch):
    async def go():
        async with FakeFigma(monkeypatch, token="figd_other") as fig:
            c = await make_client()
            async with FakePlugin(c, name="A", key=KEY, caps=CAPS) as p:
                await save(p)
                assert figma_of(p) == {"token": None, "check": "error", "why": "rejected", "rejected": False}
                assert not token_file().exists()
                assert not bridge_comments.has_token()
                for m in p.received:
                    assert TOKEN not in json.dumps(m)
                fig.token = TOKEN  # the designer pastes it again, now a good one
                await save(p)
                assert figma_of(p)["token"] == MASKED and figma_of(p)["check"] is None
            await c.close()
    run(go())


@pytest.mark.parametrize("status, why", [(404, "nofile"), (429, "busy"), (500, "busy"), (401, "rejected"), (403, "rejected")])
def test_the_check_says_why_figma_didnt_take_it(monkeypatch, status, why):
    async def go():
        async with FakeFigma(monkeypatch, status=status):
            c = await make_client()
            async with FakePlugin(c, name="A", key=KEY, caps=CAPS) as p:
                await save(p)
                assert figma_of(p)["check"] == "error" and figma_of(p)["why"] == why
            await c.close()
    run(go())


def test_the_check_says_when_figma_doesnt_answer(monkeypatch):
    async def go():
        server = TestServer(web.Application())
        await server.start_server()
        url = str(server.make_url("")).rstrip("/")
        await server.close()  # nothing listens there now
        monkeypatch.setattr(bridge_comments, "API", url)
        c = await make_client()
        async with FakePlugin(c, name="A", key=KEY, caps=CAPS) as p:
            await save(p)
            # Windows tries a refused connection again for about two seconds before it gives up
            for _ in range(100):
                if figma_of(p)["check"] != "checking":
                    break
                await asyncio.sleep(0.1)
            assert figma_of(p) == {"token": None, "check": "error", "why": "network", "rejected": False}
        await c.close()
    run(go())


def test_the_check_needs_a_file_with_a_key(monkeypatch):
    async def go():
        async with FakeFigma(monkeypatch) as fig:
            c = await make_client()
            async with FakePlugin(c, name="A", key=None, caps=CAPS) as p:
                await save(p)
                assert figma_of(p)["why"] == "open" and fig.seen == []
            await c.close()
    run(go())


def test_a_token_that_is_no_token_never_goes_to_figma(monkeypatch):
    async def go():
        async with FakeFigma(monkeypatch) as fig:
            c = await make_client()
            async with FakePlugin(c, name="A", key=KEY, caps=CAPS) as p:
                for bad in ("", "   ", "two words", "x" * 600, "figd_" + chr(0x431), 42, None):
                    await window(p, type="figma-token", token=bad, doc="doc:K1aaaaaaaaaaaaaaaaaaaa")
                await settle()
                assert fig.seen == [] and not bridge_comments.has_token()
            await c.close()
    run(go())


def test_remove_forgets_the_token_in_every_window(monkeypatch):
    async def go():
        async with FakeFigma(monkeypatch):
            c = await make_client()
            async with FakePlugin(c, name="A", key=KEY, caps=CAPS) as a, \
                    FakePlugin(c, name="B", key="K2aaaaaaaaaaaaaaaaaaaa", caps=CAPS) as b:
                await save(a)
                assert figma_of(b)["token"] == MASKED
                await window(b, type="figma-token-remove")
                await settle()
                for p in (a, b):
                    assert figma_of(p)["token"] is None
                assert not token_file().exists()
            await c.close()
    run(go())


def test_remove_while_a_check_runs_wins_over_the_check(monkeypatch):
    async def go():
        async with FakeFigma(monkeypatch) as fig:
            gate = fig.gate = asyncio.Event()
            c = await make_client()
            async with FakePlugin(c, name="A", key=KEY, caps=CAPS) as p:
                await window(p, type="figma-token", token=TOKEN, doc="doc:" + KEY)
                await settle()
                assert figma_of(p)["check"] == "checking"
                await window(p, type="figma-token-remove")
                await settle()
                gate.set()
                await asyncio.sleep(0.4)
                assert not bridge_comments.has_token() and not token_file().exists()
                assert figma_of(p)["token"] is None and figma_of(p)["check"] is None
            await c.close()
    run(go())


def test_remove_handled_before_the_check_first_runs_still_wins(monkeypatch):
    async def go():
        async with FakeFigma(monkeypatch):
            c = await make_client()
            async with FakePlugin(c, name="A", key=KEY, caps=CAPS) as p:
                # nothing between the two yields, so the check's task hasn't run yet
                await bridge_comments.window_message({"type": "figma-token", "token": TOKEN, "doc": "doc:" + KEY})
                await bridge_comments.window_message({"type": "figma-token-remove"})
                await asyncio.sleep(0.4)
                assert not bridge_comments.has_token() and not token_file().exists()
                assert figma_of(p)["token"] is None
            await c.close()
    run(go())


def test_a_newer_save_wins_over_a_check_that_started_before_it(monkeypatch):
    async def go():
        async with FakeFigma(monkeypatch) as fig:
            gate = fig.gate = asyncio.Event()
            c = await make_client()
            async with FakePlugin(c, name="A", key=KEY, caps=CAPS) as p:
                await window(p, type="figma-token", token="figd_first", doc="doc:" + KEY)
                await settle()
                await window(p, type="figma-token", token=TOKEN, doc="doc:" + KEY)
                await settle()
                gate.set()
                await asyncio.sleep(0.4)
                # the first one, which Figma refuses, must not leave an error over the second
                assert figma_of(p) == {"token": MASKED, "check": None, "why": None, "rejected": False}
                assert token_file().read_text(encoding="utf-8") == TOKEN
            await c.close()
    run(go())


def test_a_failed_write_leaves_no_part_file(monkeypatch):
    async def go():
        async with FakeFigma(monkeypatch):
            c = await make_client()
            async with FakePlugin(c, name="A", key=KEY, caps=CAPS) as p:
                def boom(*a, **k):
                    raise OSError(28, "No space left on device")
                monkeypatch.setattr(os, "replace", boom)
                await save(p)
                assert not list(token_file().parent.glob("figma-token*"))
                assert bridge_comments.has_token()  # kept until the bridge restarts
            await c.close()
    run(go())


# ─── POST /comments ───────────────────────────────────────────────────────

def test_threads_keep_open_comments_with_their_replies_and_count_the_resolved():
    listed = [reply(id="13", created_at="2026-10-26T15:45:00.000Z", message="done soon"), frame_pin(), page_pin(), reply(),
              frame_pin(id="20", resolved_at="2026-10-26T17:00:00.000Z"),
              {"id": "30", "parent_id": "", "user": {"handle": "Old"}, "created_at": "2025-01-01T00:00:00.000Z",
               "message": "an old pin", "client_meta": {"x": 10.5, "y": 20}},
              {"id": "31", "parent_id": "", "message": "no pin, no user", "created_at": "2026-01-01T00:00:00Z",
               "client_meta": {"stable_path": ["5:6", "5:9"]}}, "junk"]
    found, skipped = bridge_comments.threads(listed)
    assert skipped == 1
    assert [t["id"] for t in found] == ["30", "31", "10", "11"]  # by when they began
    by = {t["id"]: t for t in found}
    assert [r["message"] for r in by["10"]["replies"]] == ["agreed", "done soon"]  # by when they came
    assert by["10"]["pin"] == {"node": "4:12", "x": 120, "y": 48}
    assert by["11"]["pin"] == {"node": "1:2", "x": 3288, "y": 924}
    assert by["30"]["pin"] == {"node": None, "x": 10.5, "y": 20}      # an older comment: a bare point
    assert by["31"]["pin"] == {"node": "5:9", "x": None, "y": None}   # only the path says where
    assert by["31"]["author"] == "someone"


def comments_of(c, **body):
    return c.post("/comments", json=body)


def test_comments_lists_the_open_ones_with_their_pins(monkeypatch):
    async def go():
        async with FakeFigma(monkeypatch, comments=[frame_pin(), page_pin(), reply(),
                                                    frame_pin(id="20", resolved_at="2026-10-26T17:00:00Z")]) as fig:
            c = await make_client()
            async with FakePlugin(c, name="Draft", key=KEY, caps=CAPS) as p:
                await save(p)
                r = await comments_of(c, agent="designer")
                body = await r.json()
                assert r.status == 200 and body["ok"] and body["connected"] is True
                assert body["file"] == "Draft" and body["fileKey"] == KEY and body["skipped"] == 1
                assert [t["id"] for t in body["comments"]] == ["10", "11"]
                assert body["comments"][0]["replies"][0]["message"] == "agreed"
                assert TOKEN not in json.dumps(body)
                assert fig.seen[-1] == ("/v1/files/K1aaaaaaaaaaaaaaaaaaaa/comments", TOKEN)
            await c.close()
    run(go())


def test_comments_of_a_file_that_isnt_open_are_listed_without_a_window(monkeypatch):
    async def go():
        async with FakeFigma(monkeypatch, comments=[page_pin()]) as fig:
            c = await make_client()
            async with FakePlugin(c, name="Other", key="K2aaaaaaaaaaaaaaaaaaaa", caps=CAPS) as p:
                await save(p, doc="doc:K2aaaaaaaaaaaaaaaaaaaa")
                key = "AbCdEfGhIjKlMnOpQrStUv"
                r = await comments_of(c, target=f"https://www.figma.com/design/{key}/Sandbox?node-id=1-2")
                body = await r.json()
                assert r.status == 200 and body["connected"] is False and body["fileKey"] == key
                assert fig.seen[-1][0] == f"/v1/files/{key}/comments"
                r = await comments_of(c, target=key)
                assert (await r.json())["fileKey"] == key
                r = await comments_of(c, target="No such file")  # not a key, not connected: as every command
                assert r.status == 409
            await c.close()
    run(go())


def test_comments_with_no_file_open_and_none_named_is_503():
    async def go():
        c = await make_client()
        r = await comments_of(c)
        assert r.status == 503
        await c.close()
    run(go())


def test_without_a_token_the_designer_is_asked_on_the_files_island(monkeypatch):
    async def go():
        async with FakeFigma(monkeypatch, comments=[page_pin()]):
            c = await make_client()
            async with FakePlugin(c, name="Draft", key=KEY, caps=CAPS) as p, \
                    FakePlugin(c, name="Other", key="K2aaaaaaaaaaaaaaaaaaaa", caps=CAPS) as q:
                r = await comments_of(c, target="Draft", agent="designer")
                body = await r.json()
                assert r.status == 403 and body["needs_token"] and body["asked"] and not body["declined"]
                assert "designer was asked in the Figaro window" in body["error"]
                assert "again" in body["error"] and "without the comments" in body["error"]
                await settle()
                assert island(p, "Draft")["ask"] == {"agent": "designer"}
                assert island(q, "Other")["ask"] is None
                # a token comes: the ask is gone, and the next call works
                await save(q, doc="doc:K2aaaaaaaaaaaaaaaaaaaa")
                assert island(p, "Draft")["ask"] is None
                r = await comments_of(c, target="Draft", agent="designer")
                assert r.status == 200
            await c.close()
    run(go())


def test_not_now_stops_the_asking_until_the_bridge_restarts(monkeypatch):
    async def go():
        async with FakeFigma(monkeypatch):
            c = await make_client()
            async with FakePlugin(c, name="Draft", key=KEY, caps=CAPS) as p, \
                    FakePlugin(c, name="Other", key="K2aaaaaaaaaaaaaaaaaaaa", caps=CAPS) as q:
                await comments_of(c, target="Draft", agent="designer")
                await comments_of(c, target="Other", agent="writer")
                await settle()
                assert island(p, "Draft")["ask"] and island(q, "Other")["ask"]
                await window(p, type="ask-hide", doc="doc:K1aaaaaaaaaaaaaaaaaaaa")
                await settle()
                assert island(p, "Draft")["ask"] is None and island(q, "Other")["ask"] == {"agent": "writer"}
                r = await comments_of(c, target="Draft", agent="designer")
                body = await r.json()
                assert r.status == 403 and body["declined"] and not body["asked"]
                assert "chose not to add a Figma token" in body["error"] and "without" in body["error"]
                await settle()
                assert island(p, "Draft")["ask"] is None  # not asked again
                assert island(q, "Other")["ask"] == {"agent": "writer"}  # another file is
            await c.close()
            bridge_comments.reset()  # the bridge restarts
            c = await make_client()
            async with FakePlugin(c, name="Draft", key=KEY, caps=CAPS) as p:
                r = await comments_of(c, target="Draft")
                assert (await r.json())["asked"] is True
            await c.close()
    run(go())


def test_a_file_that_isnt_open_cant_be_asked(monkeypatch):
    async def go():
        c = await make_client()
        async with FakePlugin(c, name="Other", key="K2aaaaaaaaaaaaaaaaaaaa", caps=CAPS) as p:
            r = await comments_of(c, target="AbCdEfGhIjKlMnOpQrStUv")
            body = await r.json()
            assert r.status == 403 and body["needs_token"] and body["asked"] is False
            assert "isn't open with Figaro" in body["error"] and "Figaro window" in body["error"]
            await settle()
            assert island(p, "Other")["ask"] is None
        await c.close()
    run(go())


def test_the_ask_goes_after_a_while(monkeypatch):
    async def go():
        c = await make_client()
        async with FakePlugin(c, name="Draft", key=KEY, caps=CAPS) as p:
            await comments_of(c, target="Draft", agent="designer")
            await settle()
            assert island(p, "Draft")["ask"]
            monkeypatch.setattr(bridge_comments, "ASK_FOR", 0)
            await bridge_board.send()
            await asyncio.sleep(0.1)
            assert island(p, "Draft")["ask"] is None
        await c.close()
    run(go())


def test_a_token_figma_stops_taking_is_said_to_the_designer_not_the_agent(monkeypatch):
    async def go():
        async with FakeFigma(monkeypatch) as fig:
            c = await make_client()
            async with FakePlugin(c, name="Draft", key=KEY, caps=CAPS) as p:
                await save(p)
                fig.token = "figd_changed"  # revoked or expired since
                r = await comments_of(c, target="Draft")
                body = await r.json()
                assert r.status == 403 and body["token_rejected"] is True
                assert "file_comments:read" in body["error"] and "Figma token" in body["error"]
                assert TOKEN not in json.dumps(body)
                fig.status = 429
                r = await comments_of(c, target="Draft")
                assert r.status == 429 and "try again" in (await r.json())["error"]
                fig.status = 404
                r = await comments_of(c, target="Draft")
                assert r.status == 409 and "no such file" in (await r.json())["error"]
            await c.close()
    run(go())


def test_comments_when_figma_doesnt_answer_is_502(monkeypatch):
    async def go():
        server = TestServer(web.Application())
        await server.start_server()
        url = str(server.make_url("")).rstrip("/")
        await server.close()
        monkeypatch.setattr(bridge_comments, "API", url)
        c = await make_client()
        async with FakePlugin(c, name="Draft", key=KEY, caps=CAPS):
            bridge_comments.STATE["token"] = TOKEN
            r = await comments_of(c, target="Draft")
            assert r.status == 502 and TOKEN not in await r.text()
        await c.close()
    run(go())


def test_a_plugin_that_doesnt_tell_the_file_key_is_asked_to_reload():
    async def go():
        c = await make_client()
        async with FakePlugin(c, name="Draft", key=None, caps=CAPS):
            r = await comments_of(c, target="Draft")
            assert r.status == 409 and "figaro reload" in (await r.json())["error"]
        await c.close()
    run(go())


def test_the_bridge_names_the_route_and_wants_the_api_token():
    async def go():
        c = await make_client()
        root = await (await c.get("/")).json()
        assert "POST /comments" in root["endpoints"]
        r = await c.post("/comments", json={}, headers={"X-Figaro-Token": "wrong"})
        assert r.status == 401
        await c.close()
    run(go())


# ─── figaro comments ──────────────────────────────────────────────────────

THREADS = [
    {"id": "10", "author": "Dana", "at": "2026-10-26T15:12:19.827Z", "message": "make this bigger\nand bluer",
     "pin": {"node": "4:12", "x": 120, "y": 48},
     "replies": [{"id": "12", "author": "Sam", "at": "2026-10-26T15:30:00.000Z", "message": "agreed"}]},
    {"id": "11", "author": "Sam", "at": "2026-10-26T16:00:00.000Z", "message": "test comment for Figaro",
     "pin": {"node": "1:2", "x": 3288, "y": 924}, "replies": []},
]
LISTED = {"ok": True, "file": "Sandbox 5$", "fileKey": "AbCdEfGhIjKlMnOpQrStUv", "connected": True,
          "comments": THREADS, "skipped": 2}
ANSWERS = [
    {"id": "10", "node": {"id": "4:12", "name": "Pricing", "type": "FRAME"}, "page": {"id": "1:2", "name": "Page 1"},
     "at": {"x": 520, "y": 348},
     "under": [{"id": "4:20", "name": "Card", "type": "FRAME"},
               {"id": "4:21", "name": "Get started", "type": "TEXT", "text": "Get started"}]},
    {"id": "11", "node": {"id": "1:2", "name": "Page 1", "type": "PAGE"}, "page": {"id": "1:2", "name": "Page 1"},
     "at": {"x": 3288, "y": 924}, "under": [],
     "nearest": [{"id": "1:55", "name": "Hero", "type": "FRAME", "away": 366},
                 {"id": "1:56", "name": "Footer", "type": "FRAME", "away": 900}]},
]


def run_cli(monkeypatch, capsys, *replies, argv=("comments",)):
    from test_cli import FakeBridge, cli
    return cli(monkeypatch, capsys, *argv, bridge_reply=FakeBridge(*replies))


def test_the_report_names_each_pins_layer_with_a_link(monkeypatch, capsys):
    code, out, err, calls = run_cli(monkeypatch, capsys, LISTED, {"ok": True, "value": ANSWERS})
    assert code == 0, err
    assert out.splitlines()[0] == '2 open comments in "Sandbox 5$" (2 resolved, left out)'
    link = "https://www.figma.com/design/AbCdEfGhIjKlMnOpQrStUv/Sandbox-5-?node-id="
    # a frame pin: the frame, its link, the point, the deepest layer under it, and the frame-vs-text nuance
    assert "#10  Dana  2026-10-26 15:12 UTC" in out
    assert "  | make this bigger\n  | and bluer" in out
    assert f'pinned to FRAME "Pricing" 4:12  {link}4-12 (page "Page 1"), 120 px right and 48 px down from its corner' in out
    assert f'deepest layer under the pin: TEXT "Get started" 4:21  {link}4-21  text: "Get started"' in out
    assert "path below the pinned layer: Card > Get started" in out
    assert "not necessarily the one the comment is about" in out
    # a reply
    assert "reply, Sam, 2026-10-26 15:30 UTC:\n    | agreed" in out
    # a pin on a page's empty canvas: the page, the point, nothing under it, the nearest layers
    assert 'on the page "Page 1" (1:2) at canvas (3288, 924)' in out
    assert "no layer is under the pin: it is on the page's empty canvas" in out
    assert f'nearest layers: FRAME "Hero" 1:55  {link}1-55 (366 px away); FRAME "Footer" 1:56' in out
    assert "A comment is often pinned to a frame, not to the layer it talks about" in out
    assert set(calls[0] or {}) <= {"target", "agent"}  # nothing else goes to the bridge, and no token


def test_the_report_sends_the_script_for_the_pins_as_a_quick_read(monkeypatch, capsys):
    from test_cli import FakeBridge
    sent = FakeBridge(LISTED, {"ok": True, "value": ANSWERS})
    monkeypatch.setattr(figaro, "_request", lambda method, path, payload=None, timeout=65: (
        sent.calls.append((path, payload)) or (200, [LISTED, {"ok": True, "value": ANSWERS}][len(sent.calls) - 1])))
    monkeypatch.setattr(sys, "argv", ["figaro", "comments"])
    with pytest.raises(SystemExit):
        figaro.main()
    (path1, _), (path2, exec_body) = sent.calls
    assert path1 == "/comments" and path2 == "/exec"
    assert exec_body["quick"] is True
    assert '"node": "4:12"' in exec_body["code"] and '"node": "1:2"' in exec_body["code"]


def test_no_open_comments_and_no_token_lines_in_the_report(monkeypatch, capsys):
    empty = dict(LISTED, comments=[], skipped=0)
    code, out, err, calls = run_cli(monkeypatch, capsys, empty)
    assert code == 0 and out.strip() == 'no open comments in "Sandbox 5$"' and len(calls) == 1  # nothing to look up


def test_without_layer_names_the_report_has_ids_and_says_so(monkeypatch, capsys):
    code, out, err, _ = run_cli(monkeypatch, capsys, LISTED, {"ok": False, "error": "Figma isn't answering"})
    assert code == 0
    assert "pinned to 4:12 at (120, 48) in it" in out and "pinned to 1:2 at (3288, 924) in it" in out
    assert "layer names are missing: Figma isn't answering" in err
    closed = dict(LISTED, connected=False)
    code, out, err, calls = run_cli(monkeypatch, capsys, closed)
    assert code == 0 and len(calls) == 1 and "listed by id only" in err


def test_the_report_when_a_layer_is_gone_or_a_pin_has_no_layer(monkeypatch, capsys):
    threads = [dict(THREADS[0], id="1", pin={"node": "9:9", "x": 1, "y": 2}),
               dict(THREADS[0], id="2", pin={"node": None, "x": 10, "y": 20}, replies=[])]
    code, out, err, _ = run_cli(monkeypatch, capsys, dict(LISTED, comments=threads, skipped=0),
                                {"ok": True, "value": [{"id": "1", "gone": True}]})
    assert "pinned to 9:9, which is not in the file any more (deleted)" in out
    assert "pinned to no layer, at canvas point (10, 20): the page isn't known" in out


def test_a_frame_with_nothing_under_the_pin_lists_its_children(monkeypatch, capsys):
    answer = {"id": "10", "node": {"id": "4:12", "name": "Pricing", "type": "FRAME"}, "page": {"id": "1:2", "name": "P"},
              "at": {"x": 5, "y": 5}, "under": [], "kids": 14,
              "children": [{"id": "4:13", "name": "Title", "type": "TEXT", "text": "Plans"}]}
    code, out, err, _ = run_cli(monkeypatch, capsys, dict(LISTED, comments=[THREADS[0]]), {"ok": True, "value": [answer]})
    assert "its children (first 1 of 14):" in out and 'TEXT "Title" 4:13  text: "Plans"' in out


def test_no_token_the_agent_is_told_how_to_go_on(monkeypatch, capsys):
    ask = {"ok": False, "needs_token": True, "asked": True, "declined": False, "error": bridge_comments._no_token(True, False)}
    code, out, err, _ = run_cli(monkeypatch, capsys, ask)
    assert code == 1 and out == ""
    assert "the designer was asked in the Figaro window" in err and "without the comments" in err
    declined = {"ok": False, "needs_token": True, "declined": True, "error": bridge_comments._no_token(False, True)}
    code, out, err, _ = run_cli(monkeypatch, capsys, declined)
    assert code == 1 and "chose not to add a Figma token" in err


def test_an_older_bridge_has_no_comments(monkeypatch, capsys):
    monkeypatch.setattr(figaro, "_request", lambda *a, **k: (404, {"ok": False, "error": "404: Not Found"}))
    monkeypatch.setattr(sys, "argv", ["figaro", "comments"])
    with pytest.raises(SystemExit) as done:
        figaro.main()
    err = capsys.readouterr().err
    assert done.value.code == 1
    assert "older than `figaro comments`" in err and "restart it" in err and "start-bridge" in err


def test_the_file_is_a_link_a_key_or_dash_t(monkeypatch, capsys):
    from test_cli import FakeBridge, KEY, URL
    for argv, target in ((("comments", URL), URL), (("comments", KEY), KEY), (("comments", "-T", "Draft"), "Draft"),
                         (("comments", "x", "-T", "Draft"), "Draft")):
        fake = FakeBridge(dict(LISTED, comments=[], skipped=0))
        monkeypatch.setattr(figaro, "_request", fake)
        monkeypatch.setattr(figaro, "TARGET", None)
        monkeypatch.setattr(sys, "argv", ["figaro", *argv])
        with pytest.raises(SystemExit):
            figaro.main()
        capsys.readouterr()
        assert fake.calls[0]["target"] == target


def test_raw_gives_the_json_with_the_pins(monkeypatch, capsys):
    code, out, err, _ = run_cli(monkeypatch, capsys, LISTED, {"ok": True, "value": ANSWERS}, argv=("comments", "--raw"))
    body = json.loads(out)
    assert code == 0 and body["comments"][0]["id"] == "10" and body["pins"]["11"]["under"] == []


def test_every_command_names_comments_in_the_help():
    assert "comments" in figaro.DISPATCH
    assert "figaro comments" in figaro.__doc__


def test_the_times_are_read_as_figma_writes_them():
    assert comments_text.when("2026-10-26T15:12:19.827Z") == "2026-10-26 15:12 UTC"
    assert comments_text.when("soon") == "soon" and comments_text.when(None) == ""


def test_the_bridge_code_digest_covers_the_new_module():
    assert any(p.name == "bridge_comments.py" for p in bridge._BRIDGE_CODE)


def test_a_target_that_is_not_text_is_400(monkeypatch):
    async def go():
        async with FakeFigma(monkeypatch) as fig:
            c = await make_client()
            async with FakePlugin(c, name="Draft", key=KEY, caps=CAPS):
                bridge_comments.STATE["token"] = TOKEN
                for bad in (5, ["Draft"], {"a": 1}, True):
                    r = await comments_of(c, target=bad)
                    assert r.status == 400 and "'target'" in (await r.json())["error"]
                assert fig.seen == []
            await c.close()
    run(go())


def test_a_file_key_from_the_plugin_that_is_not_a_key_never_goes_to_figma(monkeypatch):
    async def go():
        async with FakeFigma(monkeypatch) as fig:
            c = await make_client()
            for bad in ("../../v1/me", "abc/def", "K1aaaaaaaaaaaaaaaaaaaa?x=1", KEY + "\n"):
                async with FakePlugin(c, name="Draft", key=bad, caps=CAPS) as p:
                    bridge_comments.STATE["token"] = TOKEN
                    r = await comments_of(c, target="Draft")
                    assert r.status == 409 and "isn't one" in (await r.json())["error"]
                    bridge_comments.STATE["token"] = None
                    await window(p, type="figma-token", token=TOKEN, doc="doc:" + str(bad))
                    await settle()
                    assert figma_of(p)["check"] == "error" and figma_of(p)["why"] == "open"
            assert fig.seen == []
            await c.close()
    run(go())


def test_a_saved_token_figma_refuses_shows_as_rejected_and_asks_again(monkeypatch):
    async def go():
        async with FakeFigma(monkeypatch, comments=[page_pin()]) as fig:
            c = await make_client()
            async with FakePlugin(c, name="Draft", key=KEY, caps=CAPS) as p:
                await save(p)
                assert island(p, "Draft")["ask"] is None
                fig.token = "figd_changed"
                r = await comments_of(c, target="Draft", agent="designer")
                assert r.status == 403 and (await r.json())["token_rejected"] is True
                await settle()
                assert figma_of(p)["rejected"] is True and figma_of(p)["token"] == MASKED
                assert island(p, "Draft")["ask"] == {"agent": "designer"}
                assert TOKEN not in json.dumps(p.received[-1])
                # a new token that works clears both
                fig.token = "figd_" + "y" * 30 + "b2d3"
                await save(p, token=fig.token)
                assert figma_of(p)["rejected"] is False and island(p, "Draft")["ask"] is None
            await c.close()
    run(go())


def test_remove_clears_a_rejected_token(monkeypatch):
    async def go():
        async with FakeFigma(monkeypatch) as fig:
            c = await make_client()
            async with FakePlugin(c, name="Draft", key=KEY, caps=CAPS) as p:
                await save(p)
                fig.token = "figd_changed"
                await comments_of(c, target="Draft")
                await window(p, type="figma-token-remove")
                await settle()
                assert figma_of(p) == {"token": None, "check": None, "why": None, "rejected": False}
            await c.close()
    run(go())


def test_a_403_on_the_comments_is_not_a_refused_token(monkeypatch):
    async def go():
        async with FakeFigma(monkeypatch) as fig:
            c = await make_client()
            async with FakePlugin(c, name="Draft", key=KEY, caps=CAPS) as p:
                await save(p)
                fig.status = 403  # a scope the token lacks, or a file its account can't open
                r = await comments_of(c, target="Draft", agent="designer")
                assert r.status == 403 and "scopes" in (await r.json())["error"]
                await settle()
                assert figma_of(p)["rejected"] is False and figma_of(p)["token"] == MASKED
                assert island(p, "Draft")["ask"] is None
            await c.close()
    run(go())


def test_threads_left_out_by_the_limit_are_counted(monkeypatch):
    monkeypatch.setattr(bridge_comments, "THREADS_MAX", 2)

    async def go():
        many = [frame_pin(id=str(100 + i), created_at=f"2026-10-26T15:12:{i:02d}Z") for i in range(5)]
        async with FakeFigma(monkeypatch, comments=many):
            c = await make_client()
            async with FakePlugin(c, name="Draft", key=KEY, caps=CAPS) as p:
                await save(p)
                body = await (await comments_of(c, target="Draft")).json()
                assert len(body["comments"]) == 2 and body["cut"] == 3
            await c.close()
    run(go())


def test_the_report_says_how_many_open_comments_were_left_out():
    text = comments_text.render(dict(LISTED, comments=LISTED["comments"][:1], cut=7), None)
    assert "7 more open, left out" in "\n".join(text)
    assert "more open" not in "\n".join(comments_text.render(dict(LISTED, comments=LISTED["comments"][:1]), None))
