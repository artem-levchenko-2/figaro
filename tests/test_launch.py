"""The pieces agree on how they find each other, and launching is safe.

The bridge, the CLI, both launchers and the plugin must agree on one default
port. The plugin must be able to read figma.fileKey and must never write into a
document by itself. The update check looks at this project's own releases. The
launcher must never suggest killing whatever holds the port: that list includes
Figma itself.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 8788


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_every_default_port_agrees():
    manifest = json.loads(read("plugin/manifest.json"))
    assert manifest["networkAccess"]["allowedDomains"] == [
        f"http://localhost:{PORT}", f"ws://localhost:{PORT}"]
    assert f'const SERVER = "ws://localhost:{PORT}/plugin";' in read("plugin/ui.html")
    assert re.search(rf'"--port", type=int, default={PORT}\b', read("bridge.py"))
    assert f'os.environ.get("FIGARO_PORT", "{PORT}")' in read("figaro.py")
    assert f'PORT="${{FIGARO_PORT:-{PORT}}}"' in read("start-bridge.sh")
    assert f"[int]$Port = {PORT}," in read("start-bridge.ps1")


def test_plugin_can_read_the_file_key():
    manifest = json.loads(read("plugin/manifest.json"))
    assert manifest["name"] == "Figaro"
    assert manifest.get("enablePrivatePluginApi") is True


def test_plugin_never_writes_into_the_document_by_itself():
    # Only the scripts agents run may change a file; the plugin itself is also
    # run in shared libraries. tests/plugin.test.js checks the same at runtime.
    code = read("plugin/code.js")
    for call in ("setPluginData(", "setSharedPluginData(", "setRelaunchData("):
        assert call not in code, f"plugin/code.js calls {call}"


def test_update_check_offers_only_this_projects_releases():
    bridge = read("bridge.py")
    repo = re.search(r'^REPO = "([^"]+)"', bridge, re.M).group(1)
    # The plugin opens only release pages of the same repository.
    assert f'startsWith("https://github.com/{repo}/")' in read("plugin/code.js")
    assert 'if not os.environ.get("FIGARO_NO_UPDATE_CHECK")' in bridge


def test_launcher_leaves_other_bridges_and_figma_alone():
    sh = read("start-bridge.sh")
    assert "xargs kill" not in sh
    assert 'SESSION="figaro-bridge-$PORT"' in sh
    assert 'LOG="/tmp/figaro-bridge-$PORT.log"' in sh
    assert 'grep -q "bridge.py"' in sh  # a stale pid file is not killed blindly
