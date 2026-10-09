"""Shared test setup: every test, in every file, starts with a clean bridge.

The bridge keeps its state in module globals. Without this reset a test that
pins the Host check, or leaves a plugin registered, leaks into the next test
file — the fixture used to live in test_bridge.py only.
"""
import os
import sys
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient

# Bridges the tests start (some run the real start-bridge.sh) never ask GitHub
# for releases.
os.environ["FIGARO_NO_UPDATE_CHECK"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bridge  # noqa: E402
import bridge_board  # noqa: E402
import bridge_exec  # noqa: E402
import bridge_update  # noqa: E402


TEST_TOKEN = "test-token"


@pytest.fixture(autouse=True)
def clean_state(tmp_path, tmp_path_factory, monkeypatch):
    # The bridge wants its token on every request that changes something: every
    # test client sends it, unless a test removes it (tests/test_token.py).
    monkeypatch.setattr(bridge, "TOKEN", TEST_TOKEN)
    # The CLI's token file: never the real ~/.figaro.
    token_file = tmp_path_factory.mktemp("figaro-home") / "token"
    token_file.write_text(TEST_TOKEN)
    monkeypatch.setenv("FIGARO_TOKEN_FILE", str(token_file))
    orig_init = TestClient.__init__

    def init(self, *args, **kwargs):
        kwargs.setdefault("headers", {"X-Figaro-Token": TEST_TOKEN})
        orig_init(self, *args, **kwargs)
    monkeypatch.setattr(TestClient, "__init__", init)
    # Checkpoint times are kept in ~/.cache/figaro — never touch the real one.
    monkeypatch.setattr(bridge_exec, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(bridge_exec, "CHECKPOINTS_FILE", tmp_path / "state" / "checkpoints.json")

    def reset():
        for registry in (bridge.PENDING, bridge.PLUGINS, bridge.LOCKS, bridge.ABANDONED,
                         bridge.QUEUE, bridge_exec.CHECKPOINTS, bridge_board.FILES,
                         bridge_update.FAILED):
            registry.clear()
        bridge.ALLOWED_HOSTS = set()
        bridge.UPDATE = None
        bridge_exec._loaded = False
        bridge_board._send_task = None
        bridge_update.STATE.update(step=None, what=None, to=None)
        bridge_update.RESTART.update(argv=None, port=8788)
    reset()
    yield
    reset()
