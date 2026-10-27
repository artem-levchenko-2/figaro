"""The Reviewer agent: the file's switch, the layers agents changed, the check on
`figaro done`, the board's `reviewer`, the designer's answer, `POST /notes` and
`figaro notes`.

A fake plugin answers the check script; what the window draws from the board is
tests/ui.test.js, and h.audit itself is tests/audit.test.js.

    pytest tests/test_review.py
"""
import asyncio
import json
import re
from pathlib import Path

import pytest

import bridge
import bridge_review
import bridge_settings
import figaro
import figaro_token
import review_text
from test_board import CAPS, boards, island, settle
from test_bridge import make_client, run, until
from test_cli import cli
from test_exec import FakePlugin

ROOT = Path(__file__).resolve().parent.parent

CHANGED = {"created": 1, "deleted": 0, "changed": 1, "styles": 1, "props": {"fills": 1},
           "items": [{"id": "5:7", "op": "~", "name": "Title", "type": "TEXT"},
                     {"id": "5:8", "op": "-", "name": "Gone", "type": "FRAME"},
                     {"id": "5:9", "op": "~", "name": "Style", "type": "STYLE"}],
           "top": [{"id": "5:6", "name": "KPI card", "type": "COMPONENT"}]}
FOUND = {"layers": 3, "roots": 1, "skipped": 0, "errors": 0, "partial": None,
         "notes": [{"rule": "unbound-color", "text": "#2F6BFF, bg/brand matches", "count": 2,
                    "ids": ["5:6", "5:7"]},
                   {"rule": "missing-font", "text": "Inter Bold is missing", "count": 1,
                    "ids": ["5:7"]}],
         "info": {"5:6": {"name": "KPI card", "type": "COMPONENT"},
                  "5:7": {"name": "Title", "type": "TEXT"}}}
CLEAN = dict(FOUND, notes=[])


def checks(p):
    """The check scripts a fake plugin got: the reviewer's own."""
    return [m for m in p.of("exec") if m.get("agent") == "reviewer"]


def plugin(c, found=FOUND, cls=FakePlugin, **kw):
    """A fake plugin whose writes report CHANGED and whose check answers `found`."""
    def reply(m):
        if m.get("agent") == "reviewer":
            if callable(found):
                return found(m)
            return {"value": found}
        return {"value": 1, "changes": CHANGED}
    return cls(c, name="Draft", key="KEY1", caps=CAPS, reply=reply, **kw)


def row(p):
    return island(p, "Draft")["reviewer"]


async def turn_on(p, on=True):
    doc = island(p, "Draft")["doc"]
    await p.ws.send_str(json.dumps({"type": "reviewer", "doc": doc, "on": on}))
    assert await until(lambda: row(p)["on"] is on)


async def write(c, agent="designer"):
    r = await c.post("/exec", json={"code": "x", "agent": agent, "target": "Draft"})
    assert r.status == 200


async def done(c, agent="designer"):
    r = await c.post("/done", json={"text": "Look", "agent": agent, "target": "Draft"})
    assert r.status == 200
    return await r.json()


async def answer(p, send):
    await p.ws.send_str(json.dumps({"type": "review-answer", "doc": island(p, "Draft")["doc"],
                                    "send": send}))


async def notes(c, wait=False, agent="designer"):
    r = await c.post("/notes", json={"target": "Draft", "agent": agent, "wait": wait})
    assert r.status == 200
    return await r.json()


def kept():
    return figaro_token.token_path(bridge.PORT).parent / "reviewer.json"


# ─── the file's switch ────────────────────────────────────────────────────

def test_the_switch_is_off_in_every_file_until_turned_on_and_is_kept_by_file_key():
    async def go():
        c = await make_client()
        async with FakePlugin(c, name="Draft", key="KEY1", caps=CAPS) as p, \
                FakePlugin(c, name="Other", key="KEY2", caps=CAPS) as q:
            off = {"on": False, "state": "off", "agent": None, "layers": 0, "since": None, "at": None,
                   "notes": [], "expired": False, "sent": 0, "left": 0}
            assert row(p) == off and island(q, "Other")["reviewer"] == off
            await turn_on(p)
            assert row(p)["state"] == "waiting" and island(q, "Other")["reviewer"]["on"] is False
            assert json.loads(kept().read_text(encoding="utf-8")) == {"KEY1": True}
            assert [f.name for f in kept().parent.glob("reviewer.json*")] == ["reviewer.json"]
        await c.close()

        bridge_review.ON.clear()  # the bridge starts again: the file keeps the switch
        c = await make_client()
        async with FakePlugin(c, name="Draft", key="KEY1", caps=CAPS) as p:
            assert row(p)["on"] is True
            await turn_on(p, False)
            assert json.loads(kept().read_text(encoding="utf-8")) == {}
        await c.close()
    run(go())


def test_the_global_switch_off_takes_the_reviewer_off_every_board():
    async def go():
        c = await make_client()
        async with plugin(c) as p:
            await turn_on(p)
            assert "reviewer" in island(p, "Draft")
            await p.ws.send_str(json.dumps({"type": "set", "name": "reviewer", "on": False}))
            assert await until(lambda: "reviewer" not in island(p, "Draft"))
            await write(c)
            reply = await done(c)
            assert "review" not in reply and not checks(p)  # no check, though the file's switch is on
            await p.ws.send_str(json.dumps({"type": "set", "name": "reviewer", "on": True}))
            assert await until(lambda: "reviewer" in island(p, "Draft") and row(p)["on"])
        await c.close()
    run(go())


# ─── what agents changed ──────────────────────────────────────────────────

def test_the_layers_a_script_changed_are_remembered_per_agent():
    async def go():
        c = await make_client()
        async with plugin(c) as p:
            await write(c, "anna")
            doc = island(p, "Draft")["doc"]
            # the new top-most layer and the changed one; not the deleted one, not a style
            assert list(bridge_review.TRACK[doc]["anna"]) == ["5:6", "5:7"]
            r = await c.post("/exec", json={"code": "x", "agent": "bob", "read_only": True,
                                            "target": "Draft"})
            assert r.status == 200 and "bob" not in bridge_review.TRACK[doc]  # a read changes nothing
            await write(c, "bob")
            assert set(bridge_review.TRACK[doc]) == {"anna", "bob"}
        await c.close()
    run(go())


def test_track_keeps_the_newest_layers_up_to_a_cap(monkeypatch):
    monkeypatch.setattr(bridge_review, "TRACK_MAX", 3)
    items = [{"id": f"1:{i}", "op": "~", "type": "FRAME"} for i in range(5)]
    bridge_review.track("doc:K", "anna", {"items": items})
    bridge_review.track("doc:K", "anna", {"items": items[:1]})
    assert list(bridge_review.TRACK["doc:K"]["anna"]) == ["1:3", "1:4", "1:0"]
    bridge_review.track("doc:K", "anna", None)
    bridge_review.track("doc:K", "anna", {"items": "x", "top": [7]})


# ─── figaro done ──────────────────────────────────────────────────────────

def test_done_with_the_switch_off_checks_nothing():
    async def go():
        c = await make_client()
        async with plugin(c) as p:
            await write(c)
            reply = await done(c)
            assert reply["ok"] is True and "review" not in reply
            assert not checks(p) and not bridge_review.TRACK.get(island(p, "Draft")["doc"], {}).get("designer")
            await turn_on(p)
            reply = await done(c)  # nothing changed since: nothing to check
            assert "review" not in reply and not checks(p)
        await c.close()
    run(go())


def test_done_checks_the_changed_layers_and_the_notes_wait_for_the_designer():
    async def go():
        c = await make_client()
        async with plugin(c) as p:
            await turn_on(p)
            await write(c)
            reply = await done(c)
            review = reply["review"]
            assert (review["state"], review["layers"], review["notes"]) == ("notes", 3, 2)
            assert review["text"] == (
                "Done. The reviewer is on in this file: it checked the 3 layers you changed and shows "
                "its notes to the designer, not to you.\n"
                "Wait for their answer: figaro notes -A designer --wait   (up to 10 min)")
            (check,) = checks(p)
            assert check["readOnly"] is True and check["quick"] is True
            assert '["5:6", "5:7"]' in check["code"] and "h.audit" in check["code"]
            assert await until(lambda: row(p)["state"] == "notes" and island(p, "Draft")["agents"][0]["done"])
            f = island(p, "Draft")
            assert f["agents"][0]["name"] == "designer" and len(f["agents"]) == 1  # the reviewer is no agent
            assert f["agents"][0]["done"]["text"] == "Look" and f["agents"][0]["busy"] is False
            r = row(p)
            assert (r["state"], r["agent"], r["layers"], r["since"], r["expired"]) == \
                ("notes", "designer", 3, None, False)
            assert [(n["id"], n["rule"], n["title"], n["text"], n["more"]) for n in r["notes"]] == [
                ("n1", "missing-font", "Font is missing", "Inter Bold is missing", 0),
                ("n2", "unbound-color", "Colour isn't a token", "#2F6BFF, bg/brand matches", 0)]
            assert r["notes"][1]["layers"] == [{"id": "5:6", "name": "KPI card", "type": "COMPONENT"},
                                               {"id": "5:7", "name": "Title", "type": "TEXT"}]
            assert f["recent"][0] == {"agent": "reviewer", "at": f["recent"][0]["at"], "kind": "ok",
                                      "summary": "Checked 3 layers", "note": "2 notes"}
            # what was checked is not checked again
            assert "review" not in await done(c) and len(checks(p)) == 1
        await c.close()
    run(go())


def test_a_check_that_finds_nothing_says_all_good():
    async def go():
        c = await make_client()
        async with plugin(c, CLEAN) as p:
            await turn_on(p)
            await write(c)
            review = (await done(c))["review"]
            assert review == {"state": "good", "layers": 3, "notes": 0,
                              "text": "Done. The reviewer checked the 3 layers you changed: all good."}
            assert await until(lambda: (row(p)["state"], row(p)["notes"]) == ("good", []))
            assert island(p, "Draft")["recent"][0]["note"] == "all good"
            assert (await notes(c))["state"] == "none"
        await c.close()
    run(go())


def test_layers_that_are_gone_are_no_check():
    async def go():
        c = await make_client()
        async with plugin(c, dict(CLEAN, layers=0)) as p:
            await turn_on(p)
            await write(c)
            assert "review" not in await done(c)
            assert row(p)["state"] == "waiting"
        await c.close()
    run(go())


def test_a_check_that_fails_says_so_and_is_tried_again_by_the_next_done():
    async def go():
        c = await make_client()
        fail = {"on": True}

        def found(m):
            return {"type": "error", "text": "boom"} if fail["on"] else {"value": FOUND}
        async with plugin(c, found) as p:
            await turn_on(p)
            await write(c)
            review = (await done(c))["review"]
            assert review["state"] == "none" and "couldn't check" in review["text"] and "boom" in review["text"]
            assert row(p)["state"] == "waiting"
            fail["on"] = False
            assert (await done(c))["review"]["state"] == "notes"  # the layers were kept
        await c.close()
    run(go())


class SlowCheck(FakePlugin):
    """The check runs until the bridge aborts it, as a script stopped by h.ck() does."""

    async def _answer(self, m):
        if m.get("agent") != "reviewer":
            return await super()._answer(m)
        for _ in range(100):
            if any(x.get("type") == "abort" and x.get("id") == m["id"] for x in self.received):
                await self.ws.send_str(json.dumps({"type": "error", "id": m["id"],
                                                   "text": "aborted: the bridge gave up on this run"}))
                return
            await asyncio.sleep(0.05)


def test_stop_in_the_reviewers_row_ends_the_check():
    async def go():
        c = await make_client()
        async with plugin(c, cls=SlowCheck) as p:
            await turn_on(p)
            await write(c)
            task = asyncio.ensure_future(done(c))
            assert await until(lambda: checks(p) and row(p)["state"] == "checking")
            assert row(p)["layers"] == 2 and row(p)["since"] is not None
            await p.ws.send_str(json.dumps({"type": "stop", "doc": island(p, "Draft")["doc"],
                                            "agent": "reviewer"}))
            reply = await asyncio.wait_for(task, 5)
            assert reply["review"] == {"state": "none", "layers": 2, "notes": 0, "text":
                                       "The designer stopped the reviewer's check, so nothing was reviewed."}
            assert not bridge_review.TRACK[island(p, "Draft")["doc"]].get("designer")  # not checked again
            assert await until(lambda: row(p)["state"] == "waiting")
            assert await until(lambda: any(m.get("type") == "abort" for m in p.received))
            assert island(p, "Draft")["agents"][0]["stopped"] is None  # no agent was stopped
            assert (await notes(c))["state"] == "none"
        await c.close()
    run(go())


# ─── the designer's answer ────────────────────────────────────────────────

def test_the_designer_sends_notes_and_the_agent_reads_them_once():
    async def go():
        c = await make_client()
        async with plugin(c) as p:
            await turn_on(p)
            await write(c)
            await done(c)
            await answer(p, ["n2"])
            assert await until(lambda: row(p)["state"] == "sent")
            r = row(p)
            assert (r["sent"], r["left"], r["notes"], r["agent"]) == (1, 1, [], "designer")
            recent = island(p, "Draft")["recent"][0]
            assert (recent["agent"], recent["summary"], recent["note"]) == \
                ("you", "Sent 1 note to designer", "1 left as is")
            got = await notes(c)
            assert (got["state"], got["total"], got["left"]) == ("sent", 2, 1)
            (note,) = got["notes"]
            assert note["title"] == "Colour isn't a token"
            assert note["layers"][0]["url"].startswith("https://www.figma.com/design/KEY1/")
            assert "node-id=5-6" in note["layers"][0]["url"]
            assert (await notes(c))["state"] == "none"  # once
        await c.close()
    run(go())


def test_all_fine_sends_nothing():
    async def go():
        c = await make_client()
        async with plugin(c) as p:
            await turn_on(p)
            await write(c)
            await done(c)
            await answer(p, [])
            assert await until(lambda: row(p)["state"] == "sent")
            assert (row(p)["sent"], row(p)["left"]) == (0, 2)
            recent = island(p, "Draft")["recent"][0]
            assert (recent["agent"], recent["summary"]) == ("you", "All fine, nothing sent to designer")
            assert (await notes(c))["state"] == "fine"
            await answer(p, ["n1"])  # nothing waits any more
            await settle()
            assert (await notes(c))["state"] == "none"
        await c.close()
    run(go())


def test_the_agent_waits_for_the_answer_and_stops_after_ten_minutes(monkeypatch):
    monkeypatch.setattr(bridge_review, "HOLD", 0.4)
    monkeypatch.setattr(bridge_review, "EXPIRE", 1.0)

    async def go():
        c = await make_client()
        async with plugin(c) as p:
            await turn_on(p)
            await write(c)
            await done(c)
            assert (await notes(c))["state"] == "pending"  # without wait: at once
            waiting = asyncio.ensure_future(notes(c, wait=True))
            assert (await waiting)["state"] == "pending"  # held HOLD seconds, then asks again
            quick = asyncio.ensure_future(notes(c, wait=True))
            await asyncio.sleep(0.1)
            await answer(p, ["n1", "n2"])
            got = await asyncio.wait_for(quick, 3)  # the answer ends the wait at once
            assert got["state"] == "sent" and len(got["notes"]) == 2 and got["left"] == 0

            await write(c)
            await done(c)
            assert await until(lambda: row(p)["state"] == "notes" and not row(p)["expired"])
            assert await until(lambda: row(p)["expired"], 3)  # the window offers Copy for chat
            assert (await notes(c, wait=True))["state"] == "expired"
            await answer(p, ["n1"])  # they send after all: the agent still gets it
            got = await until_notes(c, "sent")
            assert got["state"] == "sent" and await until(lambda: row(p)["expired"] is False)
        await c.close()
    run(go())


async def until_notes(c, state):
    got = await notes(c)
    assert got["state"] == state, got
    return got


def test_notes_of_another_agent_are_not_yours_and_a_newer_check_replaces_them():
    async def go():
        c = await make_client()
        async with plugin(c) as p:
            await turn_on(p)
            await write(c, "anna")
            await done(c, "anna")
            assert (await notes(c, agent="bob"))["state"] == "none"
            await write(c, "bob")
            await done(c, "bob")
            assert await until(lambda: row(p)["agent"] == "bob")
            assert (await notes(c, agent="anna"))["state"] == "withdrawn"
            assert (await notes(c, agent="bob"))["state"] == "pending"
        await c.close()
    run(go())


def test_turning_the_file_switch_off_withdraws_the_notes():
    async def go():
        c = await make_client()
        async with plugin(c) as p:
            await turn_on(p)
            await write(c)
            await done(c)
            await turn_on(p, False)
            assert row(p)["state"] == "off" and row(p)["notes"] == []
            assert (await notes(c))["state"] == "withdrawn"
        await c.close()
    run(go())


# ─── what the check script does ───────────────────────────────────────────

def test_notes_are_merged_by_rule_and_text_in_the_order_of_the_rules():
    found = {"notes": [
        {"rule": "default-name", "text": "named Frame 1", "count": 1, "ids": ["1:1"]},
        {"rule": "low-contrast", "text": "2.3:1", "count": 2, "ids": ["1:2", "1:3"]},
        {"rule": "low-contrast", "text": "2.3:1", "count": 8, "ids": ["1:3", "1:4", "1:5", "1:6", "1:7"]},
        {"rule": "nonsense", "text": "x", "ids": []}, {"rule": "zero-size"}, "x"],
        "info": {"1:2": {"name": "Caption", "type": "TEXT"}}}
    merged = bridge_review._merge(found)
    assert [(n["id"], n["rule"], n["title"]) for n in merged] == [
        ("n1", "low-contrast", "Text contrast is too low"), ("n2", "default-name", "Layer has a default name")]
    low = merged[0]
    assert [l["id"] for l in low["layers"]] == ["1:2", "1:3", "1:4", "1:5", "1:6"]
    assert low["more"] == 5 and low["layers"][0] == {"id": "1:2", "name": "Caption", "type": "TEXT"}
    assert low["layers"][1]["name"] == "Untitled"  # a layer that went away meanwhile
    assert bridge_review._merge({}) == []


def test_every_rule_of_h_audit_has_a_title():
    code = (ROOT / "plugin" / "code.js").read_text(encoding="utf-8")
    block = re.search(r"const AUDIT_RULES = \[(.*?)\];", code, re.S).group(1)
    assert re.findall(r'"([a-z-]+)"', block) == list(bridge_review.TITLES)


def test_the_check_script_is_valid_javascript_with_the_ids_in_it(tmp_path):
    import shutil
    import subprocess
    code = bridge_review._script(['1:2', 'I5:6;7:8', '"quoted"'])
    assert 'const ids = ["1:2", "I5:6;7:8", "\\"quoted\\""];' in code and "__" not in code
    node = shutil.which("node")
    if node:
        # a file, not /dev/stdin: Linux runners hand node a pipe it can't open by path, Windows has none
        script = tmp_path / "check.js"
        script.write_text("(async function (h, figma) {" + code + "});", encoding="utf-8")
        r = subprocess.run([node, "--check", str(script)], text=True, capture_output=True)
        assert r.returncode == 0, r.stderr


# ─── the CLI ──────────────────────────────────────────────────────────────

SENT = {"ok": True, "state": "sent", "total": 3, "left": 1, "file": "Draft", "notes": [
    {"id": "n1", "title": "Colour isn't a token", "text": "#2F6BFF, bg/brand matches", "more": 2,
     "layers": [{"id": "I2:1068", "name": "Get started", "type": "INSTANCE", "url": "https://x/1"}]},
    {"id": "n2", "title": "Font is missing", "text": "Inter Bold is missing", "more": 0,
     "layers": [{"id": "5:7", "name": "Title", "type": "TEXT", "url": None}]}]}


def test_notes_layer_lines_follow_the_text_column_of_wide_numbers():
    import review_text
    notes = [{"title": f"T{i}", "text": "x", "more": 1 if i == 10 else 0,
              "layers": [{"id": "1:1", "name": "Payouts", "type": "TEXT"}]} for i in range(1, 11)]
    lines = review_text.render({"state": "sent", "notes": notes, "total": 10, "left": 0}, "designer")
    at = lines.index("10. T10: x")
    assert lines[at + 1] == '    "Payouts" (TEXT) 1:1' and lines[at + 2] == "    and 1 more layer"
    assert lines[lines.index("9. T9: x") + 1] == '   "Payouts" (TEXT) 1:1'


def test_notes_prints_what_the_designer_sent(monkeypatch, capsys):
    rc, out, err, calls = cli(monkeypatch, capsys, "notes", "-A", "designer", "-T", "KEY1", bridge_reply=SENT)
    assert rc == 0 and calls == [{"target": "KEY1", "agent": "designer", "wait": False}]
    assert out.splitlines() == [
        "The designer sent you 2 of the reviewer's 3 notes:",
        "",
        "1. Colour isn't a token: #2F6BFF, bg/brand matches",
        "   \"Get started\" (INSTANCE) I2:1068  https://x/1",
        "   and 2 more layers",
        "2. Font is missing: Inter Bold is missing",
        "   \"Title\" (TEXT) 5:7",
        "",
        "They left 1 as it is. Fix these two, then run figaro done again."]


@pytest.mark.parametrize("reply, expected", [
    ({"state": "fine"}, "The designer looked at the reviewer's notes: all fine, nothing to fix."),
    ({"state": "expired"}, review_text.WAITED),
    ({"state": "none"}, "No notes from the reviewer for designer in this file."),
    ({"state": "pending"}, "The designer hasn't answered yet. Wait for it: figaro notes -A designer "
                           "--wait   (up to 10 min)"),
    ({"state": "withdrawn"}, "The reviewer's notes for you were withdrawn (the reviewer was turned off, "
                             "or it checked newer work). Finish your work."),
])
def test_notes_other_answers(monkeypatch, capsys, reply, expected):
    rc, out, _, _ = cli(monkeypatch, capsys, "notes", "-A", "designer", bridge_reply=dict(reply, ok=True))
    assert rc == 0 and out.strip() == expected
    assert "10 minutes" in review_text.WAITED and "figaro notes" in review_text.WAITED


def test_notes_wait_asks_again_until_the_answer(monkeypatch, capsys):
    pending = {"ok": True, "state": "pending"}
    fake = __import__("test_cli").FakeBridge(pending, pending, {"ok": True, "state": "fine"})
    rc, out, _, calls = cli(monkeypatch, capsys, "notes", "-A", "designer", "--wait", bridge_reply=fake)
    assert rc == 0 and len(calls) == 3 and all(c["wait"] is True for c in calls)
    assert "all fine" in out


def test_notes_wait_gives_up_after_its_minutes(monkeypatch, capsys):
    import cli_extras
    monkeypatch.setattr(cli_extras, "NOTES_WAIT", -1)
    rc, out, _, calls = cli(monkeypatch, capsys, "notes", "-A", "designer", "--wait",
                            bridge_reply={"ok": True, "state": "pending"})
    assert rc == 0 and len(calls) == 1 and out.strip() == review_text.WAITED


def test_notes_from_an_old_bridge(monkeypatch, capsys):
    class Old:
        def __call__(self, *a, **k):
            return 404, {"ok": False, "error": "not found"}
    monkeypatch.setattr(figaro, "_request", Old())
    monkeypatch.setattr("sys.argv", ["figaro", "notes"])
    with pytest.raises(SystemExit) as e:
        figaro.main()
    assert e.value.code == 1
    assert "older than `figaro notes`" in capsys.readouterr().err


def test_done_prints_the_reviewers_line(monkeypatch, capsys):
    text = "Done. The reviewer checked the 2 layers you changed: all good."
    reply = {"ok": True, "file": "Draft", "text": "Look",
             "review": {"state": "good", "layers": 2, "notes": 0, "text": text}}
    rc, out, _, _ = cli(monkeypatch, capsys, "done", "Look", bridge_reply=reply)
    assert rc == 0 and out.splitlines()[-1] == text
    rc, out, _, _ = cli(monkeypatch, capsys, "done", "Look",
                        bridge_reply={"ok": True, "file": "Draft", "text": "Look"})
    assert rc == 0 and len(out.splitlines()) == 1


# ─── a done that goes away, a window that closes, a stale answer, a forgotten agent ───

def test_a_done_cancelled_mid_check_puts_the_row_and_the_layers_back():
    async def go():
        c = await make_client()
        async with plugin(c, cls=SlowCheck) as p:
            await turn_on(p)
            await write(c)
            doc = island(p, "Draft")["doc"]
            task = asyncio.ensure_future(done(c))
            assert await until(lambda: checks(p) and row(p)["state"] == "checking")
            task.cancel()  # the agent's call went away: its client closed the connection
            await asyncio.gather(task, return_exceptions=True)
            assert await until(lambda: row(p)["state"] == "waiting")
            assert list(bridge_review.TRACK[doc]["designer"]) == ["5:6", "5:7"]
            p.__class__ = FakePlugin  # the next check answers
            p.reply = lambda m: {"value": FOUND if m.get("agent") == "reviewer" else 1}
            assert (await done(c))["review"]["state"] == "notes"
        await c.close()
    run(go())


def test_a_window_that_closes_while_the_check_waits_is_no_answer():
    async def go():
        c = await make_client()
        async with plugin(c) as p:
            await turn_on(p)
            await write(c)
            conn = next(iter(bridge.PLUGINS))
            doc = island(p, "Draft")["doc"]
            bridge_review._row(doc)
            gone = bridge.PLUGINS.pop(conn)  # as if its window went while the check was queued
            request = type("R", (), {"transport": type("T", (), {"is_closing": lambda self: False})()})()
            found, why = await bridge_review._check(request, conn, doc, ["5:6"])
            assert found is None and why == "the file's window closed"
            bridge.PLUGINS[conn] = gone
        await c.close()
    run(go())


def test_an_answer_to_replaced_notes_is_ignored_and_one_without_at_works():
    async def go():
        c = await make_client()
        async with plugin(c) as p:
            await turn_on(p)
            await write(c)
            await done(c)
            doc = island(p, "Draft")["doc"]
            assert await until(lambda: row(p)["state"] == "notes")
            at = row(p)["at"]
            assert at == bridge_review.ASKING[doc]["at"]
            await p.ws.send_str(json.dumps({"type": "review-answer", "doc": doc, "send": ["n1"], "at": at - 5}))
            await settle()
            assert row(p)["state"] == "notes" and (await notes(c))["state"] == "pending"
            await p.ws.send_str(json.dumps({"type": "review-answer", "doc": doc, "send": ["n1"], "at": at}))
            assert await until(lambda: row(p)["state"] == "sent")
            await write(c)
            await done(c)
            await answer(p, [])  # an older window: no at
            assert await until(lambda: row(p)["state"] == "sent" and row(p)["left"] == 2)
        await c.close()
    run(go())


def test_forgetting_an_agent_withdraws_its_notes_and_layers():
    async def go():
        c = await make_client()
        async with plugin(c) as p:
            await turn_on(p)
            await write(c)
            await done(c)
            await write(c, "bob")
            doc = island(p, "Draft")["doc"]
            r = await c.post("/forget", json={"target": "Draft", "agents": ["designer", "bob"]})
            assert (await r.json())["forgotten"] == ["designer", "bob"]
            assert await until(lambda: row(p)["state"] == "waiting" and row(p)["notes"] == [])
            assert "bob" not in bridge_review.TRACK.get(doc, {}) and doc not in bridge_review.ASKING
            assert (await notes(c))["state"] == "none"
        await c.close()
    run(go())
