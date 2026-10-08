"""The bridge's side of exec — read-only, checkpoints, undo, reload,
quick reads, --lib, links and shots.

The plugin's side is covered by tests/exec.test.js; here a fake plugin
answers, and the tests pin what the bridge sends it and returns.

    pytest tests/test_exec.py
"""
import asyncio
import json
import re
import time

import aiohttp
import pytest

import bridge
import bridge_exec
from bridge_exec import Options
from test_bridge import make_client, run

CAPS = ("changes", "readOnly", "undo", "reload", "checkpoint", "quick", "libs")
KEY = "AbCdEfGhIjKlMnOpQrStUv"  # a file key as Figma makes them: 22 letters and digits
LIB = {"name": "grid.js", "code": "function col(n) { return n * 72; }"}


class FakePlugin:
    """plugin/ui.html: hello with its file and caps, then answers
    exec and undo with whatever `reply(message)` returns."""

    def __init__(self, client, *, name="Draft", key="KEY1", caps=CAPS, plugin=None,
                 reply=None, delay=0.0, on_reload=None):
        self.client = client
        self.name, self.key, self.caps = name, key, list(caps)
        self.plugin = plugin or bridge.expected_plugin_version()
        self.reply = reply or (lambda m: {"value": 1})
        self.delay = delay
        self.on_reload = on_reload
        self.received = []
        self.ws = None
        self._task = None

    def of(self, mtype):
        return [m for m in self.received if m.get("type") == mtype]

    async def __aenter__(self):
        self.ws = await self.client.ws_connect("/plugin", headers={"Origin": "null"})
        await self.ws.send_str(json.dumps({
            "type": "hello", "version": "test", "name": self.name, "fileKey": self.key,
            "docSig": self.key, "plugin": self.plugin, "caps": self.caps}))
        self._task = asyncio.create_task(self._pump())
        await asyncio.sleep(0.1)
        return self

    async def __aexit__(self, *exc):
        if self._task:
            self._task.cancel()
        if self.ws and not self.ws.closed:
            await self.ws.close()

    async def _pump(self):
        async for msg in self.ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            m = json.loads(msg.data)
            self.received.append(m)
            if m.get("type") == "ping":
                await self.ws.send_str(json.dumps({"type": "pong"}))
            elif self.on_reload and (m.get("type") == "reload"
                                     or "__html__" in m.get("code", "")):
                asyncio.create_task(self.on_reload(m))
            elif m.get("type") in ("exec", "undo"):
                asyncio.create_task(self._answer(m))

    async def _answer(self, m):
        await asyncio.sleep(self.delay)
        out = dict(self.reply(m))
        await self.ws.send_str(json.dumps({"type": out.pop("type", "result"), "id": m["id"], **out}))


def post(c, **body):
    body.setdefault("code", "return 1")
    body.setdefault("timeout", 5)
    return c.post("/exec", json=body)


# ─── the exec message ─────────────────────────────────────────────────────

def test_exec_options():
    assert bridge_exec.exec_options({}) == Options(False, None, False, [])
    assert bridge_exec.exec_options({"read_only": True}) == Options(True, None, False, [])
    assert bridge_exec.exec_options({"readOnly": True}) == Options(True, None, False, [])
    assert bridge_exec.exec_options({"checkpoint": "  before the grid  "}) == \
        Options(False, "before the grid", False, [])
    assert bridge_exec.exec_options({"checkpoint": "   "}).checkpoint is None
    assert bridge_exec.exec_options({"checkpoint": False}).checkpoint is False
    assert bridge_exec.exec_options({"quick": True}) == Options(True, None, True, [])
    libs = [{"name": "grid.js", "code": "const a = 1"}, {"code": "const b = 2"}]
    assert bridge_exec.exec_options({"libs": libs}).libs == \
        [{"name": "grid.js", "code": "const a = 1"}, {"name": "lib2", "code": "const b = 2"}]
    for bad in ({"read_only": "yes"}, {"read_only": 1}, {"checkpoint": True},
                {"checkpoint": 5}, {"checkpoint": ["x"]}, {"quick": "yes"},
                {"libs": "const a = 1"}, {"libs": [{"name": "x"}]}, {"libs": ["const a"]},
                {"libs": [{"code": "x" * (bridge_exec.LIBS_SIZE + 1)}]}):
        with pytest.raises(ValueError):
            bridge_exec.exec_options(bad)


def test_malformed_options_are_a_400():
    async def go():
        c = await make_client()
        async with FakePlugin(c) as p:
            r = await post(c, read_only="yes")
            assert r.status == 400 and "read_only" in (await r.json())["error"]
            r = await post(c, checkpoint=True)
            assert r.status == 400 and "checkpoint" in (await r.json())["error"]
            assert p.of("exec") == []
        await c.close()
    run(go())


def test_first_writer_in_a_file_gets_a_checkpoint_then_none_within_the_hour():
    async def go():
        c = await make_client()
        async with FakePlugin(c) as p:
            for _ in range(2):
                assert (await post(c, agent="anna")).status == 200
            first, second = p.of("exec")
            assert first["checkpoint"] == "Figaro · before changes by anna"
            assert first["readOnly"] is False and first["agent"] == "anna"
            assert "checkpoint" not in second
        await c.close()
    run(go())


def test_reads_make_no_checkpoint_and_parallel_means_read_only():
    async def go():
        c = await make_client()
        async with FakePlugin(c) as p:
            await post(c, read_only=True)
            await post(c, parallel=True)
            await post(c)
            ro, par, writer = p.of("exec")
            assert ro["readOnly"] is True and "checkpoint" not in ro
            assert par["readOnly"] is True and "checkpoint" not in par
            assert writer["checkpoint"] == "Figaro · before changes by an agent"
        await c.close()
    run(go())


def test_a_label_forces_a_checkpoint_and_false_skips_one():
    async def go():
        c = await make_client()
        async with FakePlugin(c) as p:
            await post(c, checkpoint=False)
            await post(c)
            await post(c, checkpoint="before the grid")
            await post(c, read_only=True, checkpoint="just looking")
            skipped, due, forced, forced_read = p.of("exec")
            assert "checkpoint" not in skipped
            assert due["checkpoint"] == "Figaro · before changes by an agent"
            assert forced["checkpoint"] == "Figaro · before the grid"
            assert forced_read["checkpoint"] == "Figaro · just looking"
        await c.close()
    run(go())


def test_checkpoint_time_outlives_a_bridge_restart():
    async def go():
        c = await make_client()
        async with FakePlugin(c, key="KEY1") as p:
            await post(c)
            saved = json.loads(bridge_exec.CHECKPOINTS_FILE.read_text())
            assert list(saved) == ["file:KEY1"]

            bridge_exec.CHECKPOINTS.clear()  # a new bridge process
            bridge_exec._loaded = False
            await post(c)
            assert "checkpoint" not in p.of("exec")[1]

            bridge_exec.CHECKPOINTS_FILE.write_text(json.dumps({"file:KEY1": time.time() - 3700}))
            bridge_exec.CHECKPOINTS.clear()
            bridge_exec._loaded = False
            await post(c)
            assert "checkpoint" in p.of("exec")[2]
        await c.close()
    run(go())


def test_a_failed_checkpoint_is_retried_by_the_next_writer():
    def reply(m):
        if m.get("checkpoint"):
            return {"value": 1, "checkpoint": {"error": "no edit access", "title": m["checkpoint"]}}
        return {"value": 1}

    async def go():
        c = await make_client()
        async with FakePlugin(c, reply=reply) as p:
            r = await post(c)
            assert (await r.json())["checkpoint"]["error"] == "no edit access"
            await post(c)
            assert all("checkpoint" in m for m in p.of("exec"))
        await c.close()
    run(go())


def test_queued_writers_on_a_new_file_make_one_checkpoint():
    async def go():
        c = await make_client()
        async with FakePlugin(c, delay=0.2) as p:
            rs = await asyncio.gather(*(post(c, agent=f"a{i}") for i in range(3)))
            assert [r.status for r in rs] == [200, 200, 200]
            assert sum("checkpoint" in m for m in p.of("exec")) == 1
        await c.close()
    run(go())


def test_quick_read_has_no_checkpoint_and_says_quick():
    async def go():
        c = await make_client()
        async with FakePlugin(c) as p:
            await post(c, quick=True, checkpoint="ignored")
            await post(c)
            quick, writer = p.of("exec")
            assert quick["quick"] is True and quick["readOnly"] is True
            assert "checkpoint" not in quick
            assert "quick" not in writer and "checkpoint" in writer  # still the first writer
        await c.close()
    run(go())


def test_the_plugin_window_passes_every_exec_field_on(monkeypatch):
    """plugin/ui.html hands the sandbox a message with its fields listed one by
    one, so a field the bridge sends but the window does not list never
    arrives — quick and libs did not, at first."""
    monkeypatch.setitem(bridge.PLUGINS, "conn", {"docSig": "D1"})
    fields = set(bridge_exec.exec_fields("conn", "anna", Options(True, "label", False, [LIB])))
    fields |= set(bridge_exec.exec_fields("conn", "anna", Options(True, None, True, [LIB])))
    assert fields == {"readOnly", "quick", "agent", "checkpoint", "libs"}
    html = (bridge.PLUGIN_CODE.parent / "ui.html").read_text(encoding="utf-8")
    forwarded = re.search(r'toSandbox\(\{\s*type: "exec",(.*?)\}\);', html, re.S).group(1)
    for key in fields:
        assert f"{key}: msg.{key}" in forwarded, key


# ─── --lib ────────────────────────────────────────────────────────────────


def test_a_lib_is_sent_once_then_by_its_hash():
    async def go():
        c = await make_client()
        async with FakePlugin(c) as p:
            for _ in range(2):
                assert (await post(c, libs=[LIB])).status == 200
            first, second = (m["libs"] for m in p.of("exec"))
            assert first[0]["code"] == LIB["code"] and first[0]["name"] == "grid.js"
            assert "code" not in second[0]
            assert first[0]["hash"] == second[0]["hash"] and len(first[0]["hash"]) == 16
            # a changed file is a new hash, sent in full
            await post(c, libs=[dict(LIB, code=LIB["code"] + "\n")])
            third = p.of("exec")[2]["libs"][0]
            assert "code" in third and third["hash"] != first[0]["hash"]
        await c.close()
    run(go())


def test_a_lib_the_plugin_lost_is_sent_again():
    calls = []

    def reply(m):
        calls.append(m)
        if len(calls) == 2:  # the plugin was restarted: it no longer has the code
            return {"type": "error", "text": "lib grid.js is not loaded in the plugin", "stack": None,
                    "libMissing": [m["libs"][0]["hash"]]}
        return {"value": 1}

    async def go():
        c = await make_client()
        async with FakePlugin(c, reply=reply) as p:
            await post(c, libs=[LIB])
            r = await post(c, libs=[LIB])
            assert r.status == 500 and (await r.json())["lib_missing"] is True
            await post(c, libs=[LIB])
            assert ["code" in m["libs"][0] for m in p.of("exec")] == [True, False, True]
        await c.close()
    run(go())


def test_each_plugin_window_gets_the_lib_code():
    async def go():
        c = await make_client()
        async with FakePlugin(c, name="A", key="KEYA") as a, FakePlugin(c, name="B", key="KEYB") as b:
            await post(c, target="KEYA", libs=[LIB])
            await post(c, target="KEYB", libs=[LIB])
            assert "code" in a.of("exec")[0]["libs"][0]
            assert "code" in b.of("exec")[0]["libs"][0]
        await c.close()
    run(go())


def test_libs_need_a_build_that_has_them():
    async def go():
        c = await make_client()
        async with FakePlugin(c, caps=CAPS[:5]) as p:
            r = await post(c, libs=[LIB])
            assert r.status == 409 and "figaro reload" in (await r.json())["error"]
            assert p.of("exec") == []
            assert (await post(c)).status == 200  # without --lib it still works
        await c.close()
    run(go())


def test_an_error_inside_a_lib_names_its_line():
    lib_error = {"name": "grid.js", "line": 3, "source": "return n.width * k;"}

    async def go():
        c = await make_client()
        async with FakePlugin(c, reply=lambda m: {"type": "error", "text": "TypeError: x",
                                                 "stack": None, "lib": lib_error}):
            body = await (await post(c, libs=[LIB])).json()
            assert body["lib"] == lib_error
        await c.close()
    run(go())


# ─── links ────────────────────────────────────────────────────────────────

def test_a_figma_link_is_a_target():
    async def go():
        c = await make_client()
        async with FakePlugin(c, name="Other", key="ZzZzZzZzZzZzZzZzZzZzZz"), \
                FakePlugin(c, key=KEY) as p:
            url = f"https://www.figma.com/design/{KEY}/Sandbox?node-id=1-11&t=abc"
            r = await post(c, target=url)
            assert r.status == 200 and len(p.of("exec")) == 1
            assert (await c.post("/clear", json={"target": url})).status == 200
        await c.close()
    run(go())


def test_a_file_that_is_not_open_says_how_to_open_it():
    async def go():
        c = await make_client()
        async with FakePlugin(c, key=KEY):
            other = "QqQqQqQqQqQqQqQqQqQqQq"
            url = f"https://www.figma.com/design/{other}/Landing?node-id=2-3"
            body = await (await post(c, target=url)).json()
            assert f"open {url} in Figma" in body["error"] and "Figaro" in body["error"]
            body = await (await post(c, target=other)).json()
            assert f"https://www.figma.com/design/{other} " in body["error"]
            body = await (await post(c, target="no such file")).json()
            assert "open http" not in body["error"]  # a name has no link to give
        await c.close()
    run(go())


def test_new_and_changed_layers_come_with_links():
    items = [{"op": "+", "id": "1:2", "name": "Card", "type": "FRAME"},
             {"op": "~", "id": "I3:4;5:6", "name": "Label", "type": "TEXT"},
             {"op": "−", "id": "7:8", "name": "Old", "type": "FRAME"},
             {"op": "+", "id": "S:abc", "name": "Brand", "type": "STYLE"},
             {"op": "~", "id": "0:0", "name": "Sandbox 5$", "type": "DOCUMENT"}]

    async def go():
        c = await make_client()
        async with FakePlugin(c, name="Sandbox 5$", key=KEY,
                            reply=lambda m: {"value": 1, "changes": {"created": 1, "items": items}}):
            body = await (await post(c)).json()
            got = body["changes"]["items"]
            assert got[0]["url"] == f"https://www.figma.com/design/{KEY}/Sandbox-5-?node-id=1-2"
            assert got[1]["url"].endswith("?node-id=I3-4%3B5-6")
            assert not any("url" in item for item in got[2:])
        await c.close()
    run(go())


def test_the_outermost_new_layers_get_links_too():
    top = [{"id": "5:1", "name": "Button", "type": "COMPONENT_SET"}]

    async def go():
        c = await make_client()
        async with FakePlugin(c, name="Sandbox 5$", key=KEY,
                            reply=lambda m: {"value": 1, "changes": {"created": 7, "items": [], "top": top}}):
            body = await (await post(c)).json()
            assert body["changes"]["top"] == [dict(top[0], op="+",
                                                   url=f"https://www.figma.com/design/{KEY}/Sandbox-5-?node-id=5-1")]
        await c.close()
    run(go())


def test_a_shot_is_not_printed_twice():
    shot = {"kind": "shot", "id": "1:2", "format": "PNG", "size": 3, "data": "AAAA"}

    async def go():
        c = await make_client()
        async with FakePlugin(c, reply=lambda m: {"value": [shot, dict(shot, id="1:3")]}):
            body = await (await post(c)).json()
            assert body["value"][0]["data"] == "AAAA"
            assert body["result"] == "shot 1:2 PNG 3 B; shot 1:3 PNG 3 B"
        await c.close()
    run(go())


@pytest.mark.parametrize("error, needle", [
    ('in appendChild: Cannot move node. New parent is an instance or is inside of an instance',
     "detachInstance"),
    ('in set_counterAxisAlignItems: Invalid enum value. Expected \'MIN\' | \'MAX\', received \'STRETCH\'',
     "h.fill"),
    ('The font "Font Awesome 6 Pro Solid" could not be loaded', "h.fa()"),
    ('in set_fontName: Cannot use unloaded font "Roboto Mono Regular". Please call figma.loadFontAsync',
     "h.setText"),
    ("Unable to establish connection to Figma after 10 seconds", "h.node(id)"),
    # what a fresh agent hit in a smoke test
    ("in setProperties: Could not find a component property with name: 'Right Icon#164:41'", "h.variant"),
    ('could not find variable with key "58931feeac5c3df23dc96d949f8b7c43fd6b84a1"', "VariableID"),
])
def test_hints_for_what_agents_hit_most(error, needle):
    assert needle in bridge.find_hint(error)


def test_a_changed_helper_module_makes_the_bridge_outdated(monkeypatch, tmp_path):
    code = []
    for name in ("bridge.py", "bridge_exec.py", "bridge_idle.py", "figma_links.py"):
        (tmp_path / name).write_text(name)
        code.append(tmp_path / name)
    monkeypatch.setattr(bridge, "_BRIDGE_CODE", code)
    monkeypatch.setattr(bridge, "BRIDGE_DIGEST", [bridge._file_digest(p) for p in code])
    assert bridge.bridge_outdated() is False
    (tmp_path / "bridge_exec.py").write_text("changed")
    assert bridge.bridge_outdated() is True


# ─── the reply ────────────────────────────────────────────────────────────

def test_reply_carries_what_the_plugin_reported():
    changes = {"created": 1, "deleted": 0, "changed": 2, "styles": 0,
               "props": {"fills": 2}, "items": []}

    def reply(m):
        return {"type": "error", "text": "read-only: the script changed 3 — rolled back", "stack": None,
                "line": 3, "col": 5, "source": "n.fills = []", "changes": changes,
                "rolledBack": True, "notice": "a notice from the plugin"}

    async def go():
        c = await make_client()
        async with FakePlugin(c, reply=reply):
            r = await post(c, read_only=True)
            body = await r.json()
            assert r.status == 500
            assert (body["line"], body["col"], body["source"]) == (3, 5, "n.fills = []")
            assert body["changes"] == changes
            assert body["rolled_back"] is True
            assert body["notice"] == "a notice from the plugin"
        await c.close()
    run(go())


def test_plugin_notice_joins_the_version_notice():
    async def go():
        c = await make_client()
        async with FakePlugin(c, plugin="2000-01-01.1",
                            reply=lambda m: {"value": 1, "notice": "a notice from the plugin"}):
            body = await (await post(c)).json()
            assert "figaro reload" in body["notice"]
            assert body["notice"].endswith("; a notice from the plugin")
        await c.close()
    run(go())


def test_root_lists_undo_and_reload():
    async def go():
        c = await make_client()
        endpoints = (await (await c.get("/")).json())["endpoints"]
        assert "POST /undo" in endpoints and "POST /reload" in endpoints
        await c.close()
    run(go())


# ─── /undo ────────────────────────────────────────────────────────────────

def test_undo_goes_to_the_plugin_with_agent_and_force():
    undone = {"undone": {"agent": "anna", "ago": "5 s ago", "changes": "+1 ~2"}, "left": 0}

    async def go():
        c = await make_client()
        async with FakePlugin(c, reply=lambda m: {"value": undone}) as p:
            r = await c.post("/undo", json={"agent": "anna", "force": True})
            assert r.status == 200
            assert (await r.json())["value"] == undone
            (m,) = p.of("undo")
            assert (m["agent"], m["force"]) == ("anna", True)
        await c.close()
    run(go())


def test_undo_waits_its_turn_in_the_file_queue():
    async def go():
        c = await make_client()
        async with FakePlugin(c, delay=0.3) as p:
            writer = asyncio.ensure_future(post(c, agent="anna"))
            await asyncio.sleep(0.05)
            r = await c.post("/undo", json={})
            assert (await writer).status == 200
            assert r.status == 200 and (await r.json())["queued_ms"] >= 200
            assert [m["type"] for m in p.received if m["type"] in ("exec", "undo")] == \
                ["exec", "undo"]
        await c.close()
    run(go())


def test_plugin_refusal_comes_back_as_the_error():
    refusal = "undo: nothing to undo"

    async def go():
        c = await make_client()
        async with FakePlugin(c, reply=lambda m: {"type": "error", "text": refusal, "stack": None}):
            r = await c.post("/undo", json={})
            assert r.status == 500 and (await r.json())["error"] == refusal
        await c.close()
    run(go())


def test_undo_needs_a_build_that_can_undo():
    async def go():
        c = await make_client()
        async with FakePlugin(c, caps=()) as p:
            r = await c.post("/undo", json={})
            assert r.status == 409 and "figaro reload" in (await r.json())["error"]
            assert p.of("undo") == []
        await c.close()
    run(go())


def test_undo_is_refused_while_a_timed_out_script_may_still_run():
    async def go():
        c = await make_client()
        async with FakePlugin(c) as p:
            (cid,) = list(bridge.PLUGINS)
            bridge.ABANDONED["rid-old"] = {"conn": cid, "doc": bridge._doc_key(cid),
                                           "t0": time.time(), "timeout": 5}
            r = await c.post("/undo", json={})
            assert r.status == 409 and p.of("undo") == []
        await c.close()
    run(go())


def test_a_build_that_ends_a_stuck_call_gets_a_moment_past_the_timeout():
    """A build with "gate" ends a script stuck in a Figma call at its own
    deadline and answers a moment later: the caller gets that error, not a 504,
    and the file is not interlocked. An older build gets a 504 at the timeout."""
    stuck = {"type": "error", "text": "aborted: figma.teamLibrary.x() did not answer before --timeout ran out — …"}

    async def go():
        c = await make_client()
        async with FakePlugin(c, caps=CAPS + ("gate",), reply=lambda m: stuck, delay=0.5):
            t0 = time.time()
            r = await c.post("/exec", json={"code": "x", "timeout": 0.3})
            body = await r.json()
            assert r.status == 500 and "did not answer before --timeout ran out" in body["error"], body
            assert "inspect" in body["hint"]
            assert time.time() - t0 < 0.3 + bridge_exec.GATE_GRACE
            assert bridge.ABANDONED == {}
        async with FakePlugin(c, reply=lambda m: stuck, delay=0.5):
            r = await c.post("/exec", json={"code": "x", "timeout": 0.3})
            assert r.status == 504
        await c.close()
    run(go())


# ─── /reload ──────────────────────────────────────────────────────────────

def reloader(client, holder, *, comes_back=True, version=None):
    """What a reload does in Figma: the window is replaced, and the new one
    connects with the build from disk."""
    async def on_reload(m):
        holder["message"] = m
        await holder["old"].ws.close()
        if comes_back:
            await asyncio.sleep(0.2)
            holder["new"] = FakePlugin(client, plugin=version)
            await holder["new"].__aenter__()
    return on_reload


def test_reload_sends_the_code_on_disk_and_waits_for_the_new_window():
    async def go():
        c = await make_client()
        holder = {}
        old = FakePlugin(c, plugin="2000-01-01.1", on_reload=reloader(c, holder))
        holder["old"] = await old.__aenter__()
        r = await c.post("/reload", json={"target": "KEY1"})
        body = await r.json()
        assert r.status == 200, body
        assert body["plugin"] == bridge.expected_plugin_version() and body["file"] == "Draft"
        m = holder["message"]
        assert m["type"] == "reload"
        assert m["code"] == bridge.PLUGIN_CODE.read_text(encoding="utf-8")
        assert m["html"] == (bridge.PLUGIN_CODE.parent / "ui.html").read_text(encoding="utf-8")
        await holder["new"].__aexit__()
        await c.close()
    run(go())


def test_reload_starts_a_build_without_reload_through_a_plain_script():
    async def go():
        c = await make_client()
        holder = {}
        old = FakePlugin(c, plugin="2000-01-01.1", caps=(), on_reload=reloader(c, holder))
        holder["old"] = await old.__aenter__()
        r = await c.post("/reload", json={})
        assert r.status == 200
        m = holder["message"]
        assert m["type"] == "exec"
        assert m["code"].startswith("new Function('figma', '__html__', ")
        assert m["code"].endswith("return 'reloading';")
        await holder["new"].__aexit__()
        await c.close()
    run(go())


def test_reload_that_does_not_come_back_is_a_504(monkeypatch):
    monkeypatch.setattr(bridge_exec, "RELOAD_WAIT", 0.5)

    async def go():
        c = await make_client()
        holder = {}
        old = FakePlugin(c, on_reload=reloader(c, holder, comes_back=False))
        holder["old"] = await old.__aenter__()
        r = await c.post("/reload", json={})
        assert r.status == 504 and "did not come back" in (await r.json())["error"]
        await c.close()
    run(go())


def test_a_window_on_the_old_build_does_not_count_as_reloaded(monkeypatch):
    monkeypatch.setattr(bridge_exec, "RELOAD_WAIT", 0.8)

    async def go():
        c = await make_client()
        holder = {}
        old = FakePlugin(c, plugin="2000-01-01.1",
                       on_reload=reloader(c, holder, version="2000-01-01.1"))
        holder["old"] = await old.__aenter__()
        r = await c.post("/reload", json={})
        assert r.status == 504
        await holder["new"].__aexit__()
        await c.close()
    run(go())


def test_reload_that_fails_inside_the_plugin_says_why():
    async def go():
        c = await make_client()
        holder = {}

        async def on_reload(m):  # code.js answers when the new code throws
            await holder["p"].ws.send_str(json.dumps(
                {"type": "error", "id": m["id"], "text": "reload: SyntaxError: unexpected token"}))

        holder["p"] = await FakePlugin(c, on_reload=on_reload).__aenter__()
        r = await c.post("/reload", json={})
        body = await r.json()
        assert r.status == 500 and "SyntaxError" in body["error"]
        await holder["p"].__aexit__()
        await c.close()
    run(go())
