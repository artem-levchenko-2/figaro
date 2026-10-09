"""The shared secret of the bridge's HTTP API: where it lives, and how to read it.

The bridge writes a fresh token to a file readable only by the user each time it
starts; the CLI reads it and sends it as `X-Figaro-Token`. A web page can't read
the file, and neither can another user of the machine.
"""
import os
from pathlib import Path


def token_path(port):
    # One file per port, so two bridges on two ports never overwrite each other.
    override = os.environ.get("FIGARO_TOKEN_FILE")
    return Path(override) if override else Path.home() / ".figaro" / f"token-{port}"


def read_token(port):
    """The token, or None when the file is missing or empty."""
    try:
        return token_path(port).read_text(encoding="utf-8").strip() or None
    except OSError:
        return None
