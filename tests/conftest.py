"""Shared test setup: every test, in every file, starts with a clean bridge.

The bridge keeps its state in module globals. Without this reset a test that
pins the Host check, or leaves a plugin registered, leaks into the next test
file — the fixture used to live in test_bridge.py only.
"""
import os
import sys
from pathlib import Path

import pytest

# Bridges the tests start (some run the real start-bridge.sh) never ask GitHub
# for releases.
os.environ["FIGARO_NO_UPDATE_CHECK"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bridge  # noqa: E402
import bridge_exec  # noqa: E402


@pytest.fixture(autouse=True)
def clean_state(tmp_path, monkeypatch):
    # Checkpoint times are kept in ~/.cache/figaro — never touch the real one.
    monkeypatch.setattr(bridge_exec, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(bridge_exec, "CHECKPOINTS_FILE", tmp_path / "state" / "checkpoints.json")

    def reset():
        for registry in (bridge.PENDING, bridge.PLUGINS, bridge.LOCKS, bridge.ABANDONED,
                         bridge.QUEUE, bridge_exec.CHECKPOINTS):
            registry.clear()
        bridge.ALLOWED_HOSTS = set()
        bridge.UPDATE = None
        bridge_exec._loaded = False
    reset()
    yield
    reset()
