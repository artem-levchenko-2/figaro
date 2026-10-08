"""Figma links (figma_links.py).

    pytest tests/test_links.py
"""
import pytest

import figma_links

KEY = "AbCdEfGhIjKlMnOpQrStUv"


@pytest.mark.parametrize("url, key, node", [
    (f"https://www.figma.com/design/{KEY}/Sandbox-5-?node-id=1-11", KEY, "1:11"),
    (f"https://www.figma.com/design/{KEY}/Sandbox-5-?node-id=1-11&t=Xy12-0", KEY, "1:11"),
    (f"https://www.figma.com/file/{KEY}/Sandbox?node-id=1%3A11", KEY, "1:11"),
    (f"https://www.figma.com/design/{KEY}/UI?node-id=I5-6%3B7-8", KEY, "I5:6;7:8"),
    (f"https://www.figma.com/design/{KEY}", KEY, None),
    (f"https://www.figma.com/design/{KEY}/Sandbox-5-", KEY, None),
    (f"www.figma.com/design/{KEY}/UI?node-id=2-3", KEY, "2:3"),
    (f"figma.com/proto/{KEY}/UI?node-id=2-3&starting-point-node-id=2%3A3", KEY, "2:3"),
    (f"https://www.figma.com/board/{KEY}/Notes", KEY, None),
    (f"https://www.figma.com/design/{KEY}/branch/BrAnChKeY0123456789xyz/UI?node-id=4-5",
     "BrAnChKeY0123456789xyz", "4:5"),
    (f"  https://www.figma.com/design/{KEY}/UI?node-id=1-11  ", KEY, "1:11"),
])
def test_parse(url, key, node):
    link = figma_links.parse(url)
    assert (link.key, link.node) == (key, node)


@pytest.mark.parametrize("text", [
    "Sandbox 5$", KEY, "1:11", "https://example.com/design/AbCdEfGhIjKlMnOpQrStUv/x",
    "https://www.figma.com/files/recents", "https://www.figma.com/design/short/x",
    "https://notfigma.com/design/AbCdEfGhIjKlMnOpQrStUv/x", None, 5,
])
def test_parse_refuses_what_is_not_a_link(text):
    assert figma_links.parse(text) is None


@pytest.mark.parametrize("text, node", [
    ("1:11", "1:11"), ("1-11", "1:11"), ("1%3A11", "1:11"), (" 12-345 ", "12:345"),
    ("I1-2;3-4", "I1:2;3:4"), ("I1:2;3:4", "I1:2;3:4"),
    ("sel", None), ("page", None), ("1", None), ("1-", None), ("a-1", None), ("", None), (None, None),
])
def test_node_id(text, node):
    assert figma_links.node_id(text) == node


def test_node_url_round_trips():
    url = figma_links.node_url(KEY, "Sandbox 5$", "I1:2;3:4")
    assert url == f"https://www.figma.com/design/{KEY}/Sandbox-5-?node-id=I1-2%3B3-4"
    assert figma_links.parse(url).node == "I1:2;3:4"
    assert figma_links.node_url(None, "Sandbox 5$", "1:2") is None
    assert figma_links.node_url(KEY, "Sandbox 5$", None) is None


def test_slug():
    assert figma_links.slug("Sandbox 5$") == "Sandbox-5-"
    assert figma_links.slug("Landing — v2") == "Landing-v2"
    assert figma_links.slug("") == figma_links.slug(None) == "Untitled"


def test_open_hint():
    url = f"https://www.figma.com/design/{KEY}/UI?node-id=1-11"
    assert figma_links.open_hint(url) == (f"open {url} in Figma and run "
                                          "Plugins → Development → Figaro there")
    assert f"https://www.figma.com/design/{KEY} " in figma_links.open_hint(KEY)
    assert figma_links.open_hint("Sandbox") is None
    assert figma_links.open_hint(None) is None
