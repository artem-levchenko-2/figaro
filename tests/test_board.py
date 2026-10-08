"""The board every plugin window draws its islands from, the window's buttons,
`POST /done`, and Update and Reload.

A fake plugin plays each window; what the window does with the board is
tests/ui.test.js.

    pytest tests/test_board.py
"""
import asyncio
import json
import subprocess

import pytest

import bridge
import bridge_board
import bridge_update
from test_bridge import make_client, run
from test_exec import CAPS as OLD_CAPS, FakePlugin

CAPS = OLD_CAPS + ("board",)
NEWER = "9.9.9"


def boards(p):
    return [m for m in p.received if m.get("type") == "board"]


def island(p, name):
    """What the last board says about one file."""
    return next(f for f in boards(p)[-1]["files"] if f["name"] == name)


def agents(p, name):
    """The file's agents in the last board, by name."""
    return {a["name"]: a for a in island(p, name)["agents"]}


async def settle():
    await asyncio.sleep(0.15)  # a board goes out 50 ms after a change


class Stoppable(FakePlugin):
    """A script that runs until the bridge aborts it, then fails the way h.ck() makes it."""

    async def _answer(self, m):
        for _ in range(60):
            if any(x.get("type") == "abort" and x.get("id") == m["id"] for x in self.received):
                await self.ws.send_str(json.dumps({
                    "type": "error", "id": m["id"], "line": 3,
                    "text": "aborted: the bridge gave up on this run (timeout or client disconnect)"}))
                return
            await asyncio.sleep(0.05)
        await self.ws.send_str(json.dumps({"type": "result", "id": m["id"], "value": 1}))


def stop(p, doc, agent=None):
    """The Stop button of an agent's row; without `agent`, an older window's Stop."""
    return p.ws.send_str(json.dumps(dict({"type": "stop", "doc": doc}, **({"agent": agent} if agent else {}))))


# ─── what the islands show ────────────────────────────────────────────────

def test_every_window_sees_what_scripts_did_in_every_file():
    made = {"value": 1, "changes": {"created": 3, "deleted": 0, "changed": 0, "styles": 0, "props": {},
                                    "items": [], "top": [{"id": "5:6", "name": "KPI card", "type": "COMPONENT"}]}}

    async def go():
        c = await make_client()
        async with FakePlugin(c, name="Dashboard", key="KEY1", caps=CAPS, reply=lambda m: made) as a, \
                FakePlugin(c, name="UI kit", key="KEY2", caps=CAPS) as b:
            r = await c.post("/exec", json={"code": "x", "target": "Dashboard", "agent": "designer"})
            assert r.status == 200
            await settle()
            f = island(b, "Dashboard")
            assert (f["doc"], f["sig"]) == ("doc:KEY1", "KEY1")
            assert f["recent"][0]["agent"] == "designer" and f["recent"][0]["kind"] == "ok"
            assert f["recent"][0]["summary"] == "Created KPI card"
            assert f["recent"][0]["layers"] == [{"id": "5:6", "name": "KPI card", "type": "COMPONENT"}]
            designer = agents(b, "Dashboard")["designer"]
            assert designer["busy"] is False and designer["since"] <= designer["last"]
            assert (designer["done"], designer["stopped"], designer["error"]) == (None, None, None)
            assert island(a, "UI kit")["agents"] == [] and island(a, "UI kit")["recent"] == []
            board = boards(a)[-1]
            assert board["version"] == bridge.VERSION and board["updating"] is None
            assert board["active_for"] == bridge_board.ACTIVE_FOR
        await c.close()
    run(go())


def test_agents_are_busy_while_their_scripts_run_or_wait():
    async def go():
        c = await make_client()
        async with FakePlugin(c, name="Draft", key="KEY1", caps=CAPS, delay=0.4) as p:
            first = asyncio.ensure_future(c.post("/exec", json={"code": "a", "agent": "designer"}))
            await asyncio.sleep(0.1)
            second = asyncio.ensure_future(c.post("/exec", json={"code": "b"}))
            await settle()
            busy = {n: a["busy"] for n, a in agents(p, "Draft").items()}
            assert busy == {"designer": True, "agent": True}  # running, and waiting in the queue; no -A: "agent"
            assert (await first).status == 200 and (await second).status == 200
            await settle()
            assert not any(a["busy"] for a in agents(p, "Draft").values())
        await c.close()
    run(go())


def test_an_agent_at_work_keeps_its_run_of_scripts(monkeypatch):
    async def go():
        c = await make_client()
        async with FakePlugin(c, name="Draft", key="KEY1", caps=CAPS) as p:
            await c.post("/exec", json={"code": "a", "agent": "designer"})
            await settle()
            since = agents(p, "Draft")["designer"]["since"]
            await c.post("/exec", json={"code": "b", "agent": "designer"})
            await settle()
            a = agents(p, "Draft")["designer"]
            assert a["since"] == since and a["last"] > since  # one run: the window's clock goes on
            await c.post("/done", json={"agent": "designer"})
            await c.post("/exec", json={"code": "c", "agent": "designer"})
            await settle()
            assert agents(p, "Draft")["designer"]["since"] > since  # done, then a new run
            since = agents(p, "Draft")["designer"]["since"]
            monkeypatch.setattr(bridge_board, "ACTIVE_FOR", 0.1)
            await asyncio.sleep(0.2)
            await c.post("/exec", json={"code": "d", "agent": "designer"})
            await settle()
            assert agents(p, "Draft")["designer"]["since"] > since  # quiet for longer: a new run too
        await c.close()
    run(go())


def test_a_failed_script_stays_until_its_agent_gets_past_it():
    fail = {"type": "error", "text": "Cannot read properties of null (reading 'fills')", "line": 14}

    async def go():
        c = await make_client()
        reply = lambda m: fail if m["code"] == "bad" else {"value": 1}  # noqa: E731
        async with FakePlugin(c, name="Draft", key="KEY1", caps=CAPS, reply=reply) as p:
            await c.post("/exec", json={"code": "bad", "agent": "designer"})
            await settle()
            error = agents(p, "Draft")["designer"]["error"]
            assert (error["line"], error["text"]) == (14, fail["text"])
            assert {k: island(p, "Draft")["recent"][0][k] for k in ("kind", "summary", "note")} == \
                {"kind": "err", "summary": "Script failed", "note": "line 14"}
            await c.post("/exec", json={"code": "ok", "agent": "icons"})
            await settle()
            assert agents(p, "Draft")["designer"]["error"] is not None  # another agent can't fix it
            await c.post("/exec", json={"code": "ok", "agent": "designer"})
            await settle()
            assert agents(p, "Draft")["designer"]["error"] is None
            await c.post("/exec", json={"code": "bad", "agent": "designer"})
            await c.post("/done", json={"agent": "designer", "text": "Gave up on the fills"})
            await settle()
            assert agents(p, "Draft")["designer"]["error"] is None  # it said it is done
        await c.close()
    run(go())


def test_done_shows_the_note_until_the_agent_runs_again():
    async def go():
        c = await make_client()
        async with FakePlugin(c, name="Draft", key="KEY1", caps=CAPS) as p:
            r = await c.post("/done", json={"text": "  Check the card\n and say go ", "agent": "designer"})
            assert r.status == 200
            assert await r.json() == {"ok": True, "file": "Draft", "text": "Check the card and say go"}
            await settle()
            assert agents(p, "Draft")["designer"]["done"]["text"] == "Check the card and say go"
            await c.post("/exec", json={"code": "x", "agent": "icons"})
            await settle()
            assert agents(p, "Draft")["designer"]["done"] is not None  # someone else's script
            await c.post("/exec", json={"code": "x", "agent": "designer"})
            await settle()
            assert agents(p, "Draft")["designer"]["done"] is None  # the agent is back at work
            r = await c.post("/done", json={"text": " "})
            assert r.status == 200 and (await r.json())["text"] is None  # done without a note
            await settle()
            assert agents(p, "Draft")["agent"]["done"]["text"] is None
            r = await c.post("/done", json={"text": 5})
            assert r.status == 400
        async with FakePlugin(c, name="Old", key="KEY2", caps=OLD_CAPS):
            r = await c.post("/done", json={"text": "hi", "target": "Old"})
            assert r.status == 409 and "figaro reload" in (await r.json())["error"]
        await c.close()
    run(go())


@pytest.mark.parametrize("changes, expected", [
    ({"created": 4, "top": [{"id": "5:6", "name": "Card", "type": "FRAME", "op": "+"}]},
     {"summary": "Created Card", "layers": [{"id": "5:6", "name": "Card", "type": "FRAME"}]}),
    ({"created": 9, "top": [{"id": f"1:{i}", "name": f"Card {i}", "type": "FRAME"} for i in range(5)],
      "topMore": 2},
     {"summary": "Created 7 layers", "more": 4,
      "layers": [{"id": f"1:{i}", "name": f"Card {i}", "type": "FRAME"} for i in range(3)]}),
    ({"changed": 14, "props": {"fills": 14, "strokes": 2, "effects": 1},
      "items": [{"op": "~", "id": "1:1", "name": "arrow-up", "type": "INSTANCE"},
                {"op": "~", "id": "S:1", "name": "Brand", "type": "STYLE"}]},
     {"summary": "Changed 14 layers", "note": "fills, strokes", "more": 13,
      "layers": [{"id": "1:1", "name": "arrow-up", "type": "INSTANCE"}]}),
    ({"changed": 1, "props": {"name": 1}, "items": [{"op": "~", "id": "2:2", "name": "Filters", "type": "FRAME"}]},
     {"summary": "Renamed 1 layer", "layers": [{"id": "2:2", "name": "Filters", "type": "FRAME"}]}),
    ({"deleted": 3}, {"summary": "Deleted 3 layers", "layers": []}),
    ({"styles": 2}, {"summary": "Changed 2 styles", "layers": []}),
    ({"created": 0, "changed": 0}, None),
    (None, None),
])
def test_what_a_script_did_in_a_few_words(changes, expected):
    assert bridge_board.summary(changes) == expected


# ─── Stop ─────────────────────────────────────────────────────────────────

def test_stop_from_any_window_ends_the_agents_script_with_a_409():
    async def go():
        c = await make_client()
        async with Stoppable(c, name="Draft", key="KEY1", caps=CAPS) as p, \
                FakePlugin(c, name="Other", key="KEY2", caps=CAPS) as other:
            req = asyncio.ensure_future(c.post("/exec", json={"code": "loop", "agent": "designer",
                                                              "target": "Draft"}))
            await asyncio.sleep(0.2)
            await stop(other, "doc:KEY1", "designer")
            r = await req
            body = await r.json()
            assert r.status == 409 and body["stopped"] is True and "pressed Stop" in body["error"]
            assert "line" not in body  # where the Stop caught it is no bug of the script
            assert p.of("abort")
            await settle()
            designer = agents(p, "Draft")["designer"]
            assert designer["stopped"] and designer["error"] is None
            assert island(p, "Draft")["recent"][0]["kind"] == "stop"
            # It got its 409 already: once the user answers it, its scripts run.
            assert (await c.post("/exec", json={"code": "x", "agent": "designer", "target": "Draft"})).status == 200
            await settle()
            assert agents(p, "Draft")["designer"]["stopped"] is None  # back at work
        await c.close()
    run(go())


def test_stop_is_for_one_agent():
    async def go():
        c = await make_client()
        async with Stoppable(c, name="Draft", key="KEY1", caps=CAPS) as p:
            designer = asyncio.ensure_future(c.post("/exec", json={"code": "loop", "agent": "designer"}))
            await asyncio.sleep(0.2)
            icons = asyncio.ensure_future(c.post("/exec", json={"code": "loop", "agent": "icons"}))
            await asyncio.sleep(0.1)
            await stop(p, "doc:KEY1", "icons")  # waiting in the queue behind designer
            await asyncio.sleep(0.2)
            await stop(p, "doc:KEY1", "designer")
            assert (await designer).status == 409
            r = await icons
            body = await r.json()
            assert r.status == 409 and "did not run" in body["error"]
            assert len(p.of("exec")) == 1  # only designer's script reached the plugin
        await c.close()
    run(go())


def test_stop_between_scripts_refuses_the_next_one_once(monkeypatch):
    async def go():
        c = await make_client()
        async with FakePlugin(c, name="Draft", key="KEY1", caps=CAPS) as p:
            await c.post("/exec", json={"code": "a", "agent": "designer"})
            await stop(p, "doc:KEY1", "designer")
            await settle()
            assert agents(p, "Draft")["designer"]["stopped"]
            assert (await c.post("/exec", json={"code": "b", "agent": "icons"})).status == 200  # not icons
            for parallel in (True, False):
                await stop(p, "doc:KEY1", "designer")
                r = await c.post("/exec", json={"code": "b", "agent": "designer", "parallel": parallel})
                body = await r.json()
                assert r.status == 409 and body["stopped"] is True and "did not run" in body["error"]
            assert [m["code"] for m in p.of("exec")] == ["a", "b"]  # nothing of designer's ran
            await settle()
            assert agents(p, "Draft")["designer"]["stopped"]  # still stopped: it asks the user now
            assert (await c.post("/exec", json={"code": "c", "agent": "designer"})).status == 200
            monkeypatch.setattr(bridge_board, "HOLD", 0.1)
            await stop(p, "doc:KEY1", "designer")
            await asyncio.sleep(0.2)
            assert (await c.post("/exec", json={"code": "d", "agent": "designer"})).status == 200  # too late
        await c.close()
    run(go())


def test_done_lifts_a_stop():
    async def go():
        c = await make_client()
        async with FakePlugin(c, name="Draft", key="KEY1", caps=CAPS) as p:
            await stop(p, "doc:KEY1", "designer")
            await c.post("/done", json={"agent": "designer", "text": "Stopped as you asked"})
            await settle()
            designer = agents(p, "Draft")["designer"]
            assert designer["stopped"] is None and designer["done"]["text"] == "Stopped as you asked"
            assert (await c.post("/exec", json={"code": "x", "agent": "designer"})).status == 200
        await c.close()
    run(go())


def test_an_older_windows_stop_ends_whatever_runs_in_the_file():
    async def go():
        c = await make_client()
        async with FakePlugin(c, name="Draft", key="KEY1", caps=CAPS, delay=0.4) as p:
            req = asyncio.ensure_future(c.post("/exec", json={"code": "tight loop"}))
            await asyncio.sleep(0.15)
            await stop(p, "doc:KEY1")
            r = await req
            # The script ran to its end despite the Stop; its agent still has to hear of it.
            assert r.status == 409 and "pressed Stop" in (await r.json())["error"]
            assert (await c.post("/exec", json={"code": "x"})).status == 200  # it was told: no hold
        await c.close()
    run(go())


# ─── Update and Reload ────────────────────────────────────────────────────

@pytest.fixture
def quick_update(monkeypatch):
    """Update and Reload without git, pip or a real restart."""
    calls = []

    async def pull(folder=None):
        calls.append("pull")
        await asyncio.sleep(0.2)  # the windows see this step
        return None

    async def restart():
        calls.append("restart")
        return True

    monkeypatch.setattr(bridge_update, "pull_release", pull)
    monkeypatch.setattr(bridge_update, "restart", restart)
    monkeypatch.setattr(bridge_update, "RELOAD_WAIT", 0.1)
    monkeypatch.setattr(bridge_update, "RESTART_GRACE", 0.05)
    bridge.UPDATE = {"latest": NEWER, "current": bridge.VERSION, "url": bridge.release_url(NEWER)}
    return calls


async def until(test, seconds=3.0):
    for _ in range(int(seconds / 0.05)):
        if test():
            return True
        await asyncio.sleep(0.05)
    return False


def test_update_waits_for_the_scripts_then_pulls_reloads_and_restarts(quick_update, monkeypatch):
    monkeypatch.setattr(bridge, "bridge_outdated", lambda: True)

    async def go():
        c = await make_client()
        async with FakePlugin(c, name="Old", key="KEY1", caps=CAPS, plugin="2000-01-01.1", delay=0.5) as p:
            req = asyncio.ensure_future(c.post("/exec", json={"code": "x", "agent": "designer"}))
            await asyncio.sleep(0.1)
            await p.ws.send_str(json.dumps({"type": "update-now"}))
            await settle()
            assert quick_update == []  # a script runs: the update waits
            assert boards(p)[-1]["updating"] == {"step": "wait", "what": "update", "to": NEWER}
            assert (await req).status == 200
            assert await until(lambda: "restart" in quick_update)
            assert quick_update == ["pull", "restart"]
            reload = p.of("reload")
            assert reload and "PLUGIN_VERSION" in reload[0]["code"] and reload[0]["html"]
            steps = [b["updating"]["step"] for b in boards(p) if b.get("updating")]
            assert steps.index("wait") < steps.index("pull") < steps.index("restart")
        await c.close()
    run(go())


def test_reload_refreshes_only_what_is_older(quick_update, monkeypatch):
    monkeypatch.setattr(bridge, "bridge_outdated", lambda: False)

    async def go():
        c = await make_client()
        async with FakePlugin(c, name="Old", key="KEY1", caps=CAPS, plugin="2000-01-01.1") as old, \
                FakePlugin(c, name="New", key="KEY2", caps=CAPS) as new:
            await new.ws.send_str(json.dumps({"type": "reload-all"}))
            assert await until(lambda: old.of("reload"))
            assert await until(lambda: bridge_update.STATE["step"] is None)
            assert not new.of("reload") and quick_update == []  # no pull, no restart
        async with FakePlugin(c, name="New", key="KEY2", caps=CAPS) as new:
            await new.ws.send_str(json.dumps({"type": "reload-all"}))  # nothing older: nothing to do
            await settle()
            assert bridge_update.STATE["step"] is None and not bridge_update.FAILED
            assert not new.of("reload")
        await c.close()
    run(go())


def test_a_pull_that_fails_says_why_and_offers_the_release(monkeypatch):
    async def pull(folder=None):
        return "local changes", "git pull: error: Your local changes would be overwritten by merge"
    monkeypatch.setattr(bridge_update, "pull_release", pull)
    bridge.UPDATE = {"latest": NEWER, "current": bridge.VERSION, "url": bridge.release_url(NEWER)}

    async def go():
        c = await make_client()
        async with FakePlugin(c, caps=CAPS) as p:
            await p.ws.send_str(json.dumps({"type": "update-now"}))
            await settle()
            board = boards(p)[-1]
            assert board["updating"] is None
            assert board["failed"] == {
                "what": "update", "error": "local changes", "url": bridge.release_url(NEWER),
                "detail": "git pull: error: Your local changes would be overwritten by merge"}
        await c.close()
    run(go())


def test_a_script_sent_during_the_restart_is_refused_and_safe_to_repeat():
    async def go():
        c = await make_client()
        async with FakePlugin(c, caps=CAPS) as p:
            bridge_update.STATE.update(step="restart", what="update", to=NEWER)
            for body in ({"code": "x"}, {"code": "x", "parallel": True}):
                r = await c.post("/exec", json=body)
                assert r.status == 503 and (await r.json())["updating"] is True
            assert not p.of("exec")
            bridge_update.STATE.update(step=None)
            assert (await c.post("/exec", json={"code": "x"})).status == 200
        await c.close()
    run(go())


def _git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@example.com",
                    "-c", "commit.gpgsign=false", *args], cwd=str(cwd), check=True, capture_output=True)


def test_pull_release_fast_forwards_and_refuses_local_changes(tmp_path):
    origin, work, figaro = tmp_path / "origin.git", tmp_path / "work", tmp_path / "figaro"
    _git(tmp_path, "init", "--bare", str(origin))
    _git(tmp_path, "clone", str(origin), str(work))
    _git(work, "symbolic-ref", "HEAD", "refs/heads/main")
    (work / "requirements.txt").write_text("aiohttp\n")
    (work / "a.txt").write_text("1\n")
    _git(work, "add", ".")
    _git(work, "commit", "-m", "1.0.0")
    _git(work, "push", "-u", "origin", "main")
    _git(tmp_path, "clone", "-b", "main", str(origin), str(figaro))

    def release(text):
        (work / "a.txt").write_text(text)
        _git(work, "commit", "-am", text)
        _git(work, "push")

    release("2\n")
    assert run(bridge_update.pull_release(figaro)) is None
    assert (figaro / "a.txt").read_text() == "2\n"
    release("3\n")
    (figaro / "a.txt").write_text("mine\n")
    why, detail = run(bridge_update.pull_release(figaro))
    assert why == "local changes" and detail.startswith("git pull: error: Your local changes")
    assert run(bridge_update.pull_release(tmp_path))[0] == "not a git clone"


@pytest.mark.parametrize("out, why", [
    ("Updating 1..2\nerror: Your local changes to the following files would be overwritten by merge:",
     "local changes"),
    ("hint: Diverging branches can't be fast-forwarded\nfatal: Not possible to fast-forward, aborting.",
     "local commits"),
    ("There is no tracking information for the current branch.", "no upstream"),
    ("fatal: unable to access 'https://github.com/x/y/': Could not resolve host: github.com", "offline"),
    ("fatal: something new", "git pull failed"),
])
def test_why_a_pull_failed(out, why):
    # few words: the window's row has room for about 30 letters
    assert bridge_update.why_pull_failed(out) == why
    assert len("Couldn't update: " + why) <= 32
