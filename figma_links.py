"""Figma links.

A link names a file by its key and a layer by `node-id`:

    https://www.figma.com/design/<key>/<slug>?node-id=1-11&t=…
    https://www.figma.com/file/<key>/<slug>?node-id=1%3A11          (older form)
    https://www.figma.com/design/<key>/branch/<branch key>/<slug>   (a branch is a file of its own)
    https://figma.com/proto/<key>/…, /board/<key>/…, /slides/<key>/…

So `-T <link>` picks the file, a link (or a bare `1-11`) works wherever a node
id does, and replies link back to layers. No imports beyond the standard
library: the CLI loads this on every call.
"""

from __future__ import annotations

import re
from typing import NamedTuple
from urllib.parse import parse_qs, quote, unquote, urlsplit

_HOST = re.compile(r"^(?:https?://)?(?:[\w-]+\.)?figma\.com/", re.I)
_PATH = re.compile(r"^/(?:design|file|proto|board|slides|deck|make|site)/([A-Za-z0-9]{10,})"
                   r"(?:/branch/([A-Za-z0-9]{10,}))?")
# A node id as Figma writes it: "1:11", "I1:2;3:4" (a layer inside an
# instance). In links the colons are dashes: "1-11", "I1-2;3-4".
_NODE = re.compile(r"I?\d+[:-]\d+(?:;\d+[:-]\d+)*")
# File keys are 22 letters and digits — what a bare key in -T looks like.
FILE_KEY = re.compile(r"^[A-Za-z0-9]{22}$")


class Link(NamedTuple):
    key: str            # the file's key: what figma.fileKey says in that file
    node: str | None    # the layer in API form ("1:11"), if the link has one
    url: str            # the link as given


def node_id(text):
    """'1-11', '1:11', 'I1-2;3-4', '1%3A11' → the API form ('1:11', 'I1:2;3:4');
    None when `text` is not a node id."""
    if not isinstance(text, str):
        return None
    s = unquote(text.strip())
    if not _NODE.fullmatch(s):
        return None
    return re.sub(r"(\d+)-(\d+)", r"\1:\2", s)


def parse(text):
    """A Figma link → Link; None when `text` is not one."""
    if not isinstance(text, str) or not _HOST.match(text.strip()):
        return None
    url = text.strip()
    parts = urlsplit(url if re.match(r"^https?://", url, re.I) else "https://" + url)
    m = _PATH.match(parts.path)
    if not m:
        return None
    node = parse_qs(parts.query).get("node-id")
    return Link(m.group(2) or m.group(1), node_id(node[0]) if node else None, url)


def slug(name):
    """The file-name part of a link, as Figma writes it: "Sandbox 5$" → Sandbox-5-."""
    return re.sub(r"[^A-Za-z0-9]+", "-", name or "") or "Untitled"


def node_url(file_key, file_name, node):
    """A clickable link to one layer; None without a file key."""
    if not file_key or not node:
        return None
    return (f"https://www.figma.com/design/{file_key}/{slug(file_name)}"
            f"?node-id={quote(str(node).replace(':', '-'), safe='')}")


def open_hint(target):
    """What to do when `target` — a link or a file key — is not connected."""
    link = parse(target)
    if link:
        url = link.url
    elif isinstance(target, str) and FILE_KEY.match(target):
        url = f"https://www.figma.com/design/{target}"
    else:
        return None
    return f"open {url} in Figma and run Plugins → Development → Figaro there"
