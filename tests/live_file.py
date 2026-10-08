"""Where the live tests run: the scratch Figma file named by FIGARO_TEST_FILE.

The live tests make, change and remove layers, so give them a file of your own
that nothing depends on — never a real design or a shared library. Open it in
Figma Desktop, run Plugins → Development → Figaro in it, then:

    FIGARO_TEST_FILE=<file key or link> venv/bin/python tests/live_exec.py
"""
import json
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import figma_links  # noqa: E402

PORT = os.environ.get("FIGARO_PORT", "8788")
BRIDGE = f"http://127.0.0.1:{PORT}"


def key():
    """The test file's key, from FIGARO_TEST_FILE (a key or any link into it)."""
    raw = os.environ.get("FIGARO_TEST_FILE", "").strip()
    link = figma_links.parse(raw)
    found = link.key if link else raw if figma_links.FILE_KEY.match(raw) else None
    if not found:
        sys.exit("set FIGARO_TEST_FILE to the key or a link of a scratch Figma file of your own "
                 "(the live tests change it), with the Figaro plugin running in it")
    return found


def connected(file_key):
    """The file's entry in the bridge's /status, and the status; exits if it is not there."""
    try:
        with urllib.request.urlopen(BRIDGE + "/status", timeout=5) as r:
            status = json.load(r)
    except OSError as e:
        sys.exit(f"no bridge on {BRIDGE}: {e} — start it: bash start-bridge.sh")
    for f in status.get("files") or []:
        if f.get("fileKey") == file_key:
            return f, status
    sys.exit(f"the test file {file_key} is not connected to the bridge on {PORT} — open it in "
             "Figma Desktop and run Plugins → Development → Figaro")


def url(entry):
    """The file's link, as Figma writes it."""
    return f"https://www.figma.com/design/{entry['fileKey']}/{figma_links.slug(entry.get('name'))}"
