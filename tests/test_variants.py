"""`figaro inspect` of a component set: the variant axes, one variant in full,
and what the others change (variant_diff.py). The plugin's reply is made up
here; the plugin's own side is unchanged and covered in tests/commands.test.js.

    pytest tests/test_variants.py
"""
import copy
import json

import pytest

import cli_extras
import variant_diff
from test_cli import FakeBridge, cli

KEY = "AbCdEfGhIjKlMnOpQrStUv"


@pytest.fixture(autouse=True)
def no_autostart(monkeypatch):
    monkeypatch.setenv("FIGARO_AUTOSTART", "0")
    monkeypatch.setattr(cli_extras, "_started", False)
    monkeypatch.setattr(cli_extras, "_verdicts", {})


def solid(color, var=None):
    p = {"type": "SOLID", "color": color}
    if var:
        p["var"] = var
    return [p]


def variant(vid, name, label, fill, pad, size, icon=None, hidden_icon=False):
    """One variant of a button: a label, an icon instance, padding."""
    kids = [{"id": vid + ".1", "name": "Label", "type": "TEXT", "w": 20, "h": 16, "x": 16, "y": 8,
             "sizing": "HUG/HUG", "text": label, "fills": solid(fill, "text/" + label.lower()),
             "font": {"family": "Inter Medium", "size": size, "lineHeight": "auto",
                      "letterSpacing": "0px", "align": "LEFT"}}]
    if icon:
        kids.append({"id": vid + ".2", "name": "Icon", "type": "INSTANCE", "w": 16, "h": 16, "x": 0, "y": 0,
                     "sizing": "FIXED/FIXED", "component": icon, "props": {"Size": "16"}})
    node = {"id": vid, "name": name, "type": "COMPONENT", "w": 80 + pad[1], "h": 32, "x": 40, "y": 0,
            "sizing": "HUG/HUG", "radius": 6,
            "layout": {"mode": "HORIZONTAL", "gap": 8, "main": "CENTER", "cross": "CENTER", "pad": pad},
            "fills": solid("#0d99ff", "action/bg"), "key": "k" * 40}
    if hidden_icon:
        node["hiddenKids"] = 1
    node["children"] = kids
    return node


def button_set(with_hidden):
    """Size M/S × State default/hover. The first child is S/hover; the set's
    default (M, default) is the third. Hover swaps the icon and colours the
    label; S pads less and has a smaller font; S/default hides its icon."""
    def make(vid, name, label, fill, pad, size, icon, hid=False):
        v = variant(vid, name, label, fill, pad, size, icon)
        if hid:
            if with_hidden:
                v["children"][1]["visible"] = False
            else:
                del v["children"][1]
                v["hiddenKids"] = 1
        return v

    kids = [
        make("2:1", "Size=S, State=hover", "Go", "#ffffff", [4, 12, 4, 12], 12, "Icon / Arrow"),
        make("2:2", "Size=M, State=hover", "Go", "#ffffff", [8, 16, 8, 16], 14, "Icon / Arrow"),
        make("2:3", "Size=M, State=default", "Go", "#111111", [8, 16, 8, 16], 14, "Icon / Star"),
        make("2:4", "Size=S, State=default", "Go", "#111111", [4, 12, 4, 12], 12, "Icon / Star", hid=True),
    ]
    return {
        "id": "2:0", "name": "Button", "type": "COMPONENT_SET", "w": 400, "h": 200, "key": "s" * 40,
        "propDefs": {"Size": {"type": "VARIANT", "default": "M", "options": ["S", "M"]},
                     "State": {"type": "VARIANT", "default": "default", "options": ["default", "hover"]}},
        "children": kids,
    }


def wrap(node, nodes):
    return {"file": "Sandbox 5$", "fileKey": KEY, "url": f"https://www.figma.com/design/{KEY}/X?node-id=2-0",
            "nodes": nodes, "node": node, "components": {}, "styles": {}, "variables": {}, "collections": {}}


def reply(value):
    return {"ok": True, "value": value}


def inspect_button(monkeypatch, capsys, *flags):
    """Two reads, as the CLI makes them: the usual one, which counts the hidden
    icon, and one with hidden layers, which names it."""
    first = wrap(button_set(False), 14)
    second = wrap(button_set(True), 15)
    fake = FakeBridge(reply(copy.deepcopy(first)), reply(second))  # the CLI adds a key to what it gets
    rc, out, err, calls = cli(monkeypatch, capsys, "inspect", "2:0", *flags, bridge_reply=fake)
    assert rc == 0, err
    return out, calls, first


def test_a_set_prints_the_axes_one_variant_and_the_differences(monkeypatch, capsys):
    out, calls, _ = inspect_button(monkeypatch, capsys)
    # the usual read first, then one with the hidden layers, only because one is hidden
    assert 'h.inspect(await h.resolve("2:0"), {"depth": 8, "hidden": false})' in calls[0]["code"]
    assert '"hidden": true' in calls[1]["code"] and calls[1]["quick"] is True
    lines = out.splitlines()
    assert lines[0] == '"Button" COMPONENT_SET 2:0 in "Sandbox 5$" · 14 layers'
    assert any(l.startswith("  ┆ prop Size VARIANT") for l in lines)
    assert any(l.startswith("  ┆ prop State VARIANT") for l in lines)
    # the base is the variant of the defaults, not the first child
    assert "base variant: Size=M, State=default — the set's default" in lines
    shown = out.split("base variant:")[1].split("\n\n")[0]
    assert "Size=M, State=default [COMPONENT] 2:3" in shown and "Size=S, State=hover" not in shown
    assert "all 3 other variants differ from the base (Size=M, State=default):" in lines
    # what every variant with Size=S has, in one line, as a condition
    assert "  padding: 8 16 → 4 12 · Size=S" in lines
    assert "  font.size: 14 → 12 · Size=S" in lines
    assert "  fill: #111111 (text/go) → #ffffff (text/go) · State=hover" in lines
    # a swapped instance and a layer hidden in one variant (seen only by the second read)
    assert "  component: Icon / Star → Icon / Arrow · State=hover" in lines
    assert "  visible: visible → hidden · Size=S, State=default" in lines
    # sizes that follow the content (hug) are not differences; the variant's x and y are not either
    assert not any(l.startswith(("  size:", "  position:")) for l in lines)
    assert not any("same as the base" in l for l in lines)
    # the rest of inspect's text is still there


def test_a_set_json_keeps_the_keys_and_adds_variants(monkeypatch, capsys):
    out, calls, first = inspect_button(monkeypatch, capsys, "--json")
    data = json.loads(out)
    variants = data.pop("variants")
    assert data == first  # everything inspect gave stays as it was; the new key is the only addition
    assert variants["base"] == {"id": "2:3", "name": "Size=M, State=default",
                                "props": {"Size": "M", "State": "default"}, "by": "variant properties' defaults"}
    assert variants["axes"] == {"Size": {"values": ["S", "M"], "default": "M"},
                                "State": {"values": ["default", "hover"], "default": "default"}}
    assert [v["name"] for v in variants["variants"]][:2] == ["Size=S, State=hover", "Size=M, State=hover"]
    assert variants["same"] == []
    pad = next(d for d in variants["differences"] if d["attr"] == "pad")
    assert pad["layer"] == "" and pad["type"] == "COMPONENT" and pad["id"] == "2:3"
    assert pad["base"] == [8, 16, 8, 16]
    assert pad["values"] == [{"value": [4, 12, 4, 12], "when": ["Size=S"],
                              "variants": ["Size=S, State=hover", "Size=S, State=default"]}]
    icon = next(d for d in variants["differences"] if d["attr"] == "component")
    assert (icon["layer"], icon["path"], icon["id"]) == ("Icon", ["Icon"], "2:3.2")
    assert icon["values"][0]["value"] == "Icon / Arrow"


def test_summary_of_a_set_written_to_a_file(monkeypatch, capsys, tmp_path):
    dest = tmp_path / "set.json"
    fake = FakeBridge(reply(wrap(button_set(False), 14)), reply(wrap(button_set(True), 15)))
    rc, out, err, calls = cli(monkeypatch, capsys, "inspect", "2:0", "-o", str(dest), bridge_reply=fake)
    assert rc == 0
    assert json.loads(out)["variantCount"] == 4
    assert json.loads(dest.read_text())["variants"]["base"]["name"] == "Size=M, State=default"


def test_no_second_read_when_nothing_is_hidden(monkeypatch, capsys):
    s = button_set(True)
    for v in s["children"]:
        v["children"][1].pop("visible", None)
    rc, out, err, calls = cli(monkeypatch, capsys, "inspect", "2:0", bridge_reply=reply(wrap(s, 14)))
    assert rc == 0 and len(calls) == 1
    assert "all 3 other variants differ" in out


def test_variants_that_differ_in_nothing(monkeypatch, capsys):
    s = button_set(True)
    for v in s["children"]:  # the same padding, colours, font and icon in all four
        v.update(copy.deepcopy(s["children"][2]), id=v["id"], name=v["name"])
    rc, out, err, calls = cli(monkeypatch, capsys, "inspect", "2:0", bridge_reply=reply(wrap(s, 14)))
    assert rc == 0
    assert "the other 3 variants are the same as the base (Size=M, State=default) in everything inspect reads, " \
           "apart from the name" in out
    rc, out, err, calls = cli(monkeypatch, capsys, "inspect", "2:0", "--json", bridge_reply=reply(wrap(s, 14)))
    v = json.loads(out)["variants"]
    assert v["differences"] == [] and v["same"] == ["Size=S, State=hover", "Size=M, State=hover",
                                                    "Size=S, State=default"]


def icon_set(second, third=None):
    """A set whose variants have two layers called Icon. `second` is the
    second Icon of the variant `B`; a variant `C` has only one Icon."""
    def icon(vid, color):
        return {"id": vid, "name": "Icon", "type": "VECTOR", "w": 8, "h": 8, "x": 0, "y": 0,
                "fills": solid(color)}

    def v(vid, name, icons):
        return {"id": vid, "name": name, "type": "COMPONENT", "w": 20, "h": 10,
                "children": [icon(vid + "." + str(i), c) for i, c in enumerate(icons)]}

    kids = [v("3:1", "Kind=A", ["#111111", "#222222"]), v("3:2", "Kind=B", ["#111111", second])]
    if third is not None:
        kids.append(v("3:3", "Kind=C", third))
    return {"id": "3:0", "name": "Badge", "type": "COMPONENT_SET", "key": "s" * 40,
            "propDefs": {"Kind": {"type": "VARIANT", "default": "A", "options": ["A", "B", "C"]}},
            "children": kids}


def test_layers_of_the_same_name_are_matched_in_order(monkeypatch, capsys):
    s = icon_set("#333333", ["#444444"])
    rc, out, err, calls = cli(monkeypatch, capsys, "inspect", "3:0", bridge_reply=reply(wrap(s, 7)))
    assert rc == 0
    lines = out.splitlines()
    # B's second Icon is compared with A's second; C's only Icon with A's first
    assert "Icon[2] [VECTOR]" in lines
    i = lines.index("Icon[2] [VECTOR]")
    assert lines[i + 1] == "  fill: #222222 → #333333 · Kind=B"
    assert "  layer: [VECTOR] 8×8 @0,0 · fill #222222 → absent · Kind=C" in lines
    assert "Icon [VECTOR]" in lines
    j = lines.index("Icon [VECTOR]")
    assert lines[j + 1] == "  fill: #111111 → #444444 · Kind=C"
    rc, out, err, calls = cli(monkeypatch, capsys, "inspect", "3:0", "--json", bridge_reply=reply(wrap(s, 7)))
    diffs = json.loads(out)["variants"]["differences"]
    assert [(d["layer"], d["path"], d["attr"]) for d in diffs] == [
        ("Icon", ["Icon"], "fill"), ("Icon[2]", ["Icon[2]"], "fill"), ("Icon[2]", ["Icon[2]"], "layer")]


def test_conditions_cover_the_whole_axis_value_or_name_variants():
    s = button_set(True)
    s["children"].append(variant("2:5", "Size=L, State=default", "Go", "#111111", [12, 20, 12, 20], 16, "Icon / Star"))
    s["propDefs"]["Size"]["options"] = ["S", "M", "L"]
    analysis, base = variant_diff.analyze(s)
    pad = next(d for d in analysis["differences"] if d["attr"] == "pad")
    # S pads 4 12 in two variants, L 12 20 in one: a condition for S, the name for the single L
    assert [(v["value"], v["when"]) for v in pad["values"]] == [
        ([4, 12, 4, 12], ["Size=S"]), ([12, 20, 12, 20], ["Size=L, State=default"])]


def test_the_first_variant_is_the_base_without_defaults():
    s = button_set(True)
    del s["propDefs"]
    analysis, base = variant_diff.analyze(s)
    assert base["id"] == "2:1" and analysis["base"]["by"] == "first variant"
    assert analysis["axes"] == {"Size": {"values": ["S", "M"], "default": "S"},
                                "State": {"values": ["hover", "default"], "default": "hover"}}


def test_a_set_read_to_depth_zero_has_no_variants_key(monkeypatch, capsys):
    s = button_set(True)
    del s["children"]
    s["childrenCut"] = 4
    rc, out, err, calls = cli(monkeypatch, capsys, "inspect", "2:0", "--depth", "0",
                              bridge_reply=reply(wrap(s, 1)))
    assert rc == 0 and len(calls) == 1 and "base variant" not in out
    rc, out, err, calls = cli(monkeypatch, capsys, "inspect", "2:0", "--depth", "0", "--json",
                              bridge_reply=reply(wrap(s, 1)))
    assert "variants" not in json.loads(out)


def test_layers_cut_by_depth_are_not_reported_as_missing(monkeypatch, capsys):
    s = button_set(True)
    for v in s["children"]:
        v.pop("children")
        v["childrenCut"] = 2
    rc, out, err, calls = cli(monkeypatch, capsys, "inspect", "2:0", "--depth", "1", bridge_reply=reply(wrap(s, 5)))
    assert rc == 0 and "layer:" not in out and "  padding: 8 16 → 4 12 · Size=S" in out.splitlines()


def test_a_set_that_hit_the_layer_limit_says_so(monkeypatch, capsys):
    s = button_set(True)
    s["children"] = s["children"][:3]
    s["childrenCut"] = 1
    rc, out, err, calls = cli(monkeypatch, capsys, "inspect", "2:0", bridge_reply=reply(dict(wrap(s, 40), cut=True)))
    assert "1 more variant not read (layer limit)" in out


def test_a_long_set_stays_short():
    """Five sizes × ten colours: the colour of the fill is one line per colour, the size one per size."""
    colors = [f"c{i}" for i in range(10)]
    sizes = [str(s) for s in (8, 12, 16, 20, 24)]
    kids = []
    for z in sizes:
        for c in colors:
            kids.append({"id": f"9:{z}{c}", "name": f"Size={z}, Color={c}", "type": "COMPONENT",
                         "w": int(z), "h": int(z), "sizing": "FIXED/FIXED", "x": 0, "y": 0,
                         "fills": solid("#0000" + str(10 + colors.index(c))),
                         "children": [{"id": f"9:{z}{c}.1", "name": "Dot", "type": "ELLIPSE", "w": int(z), "h": int(z),
                                       "sizing": "FIXED/FIXED", "x": 0, "y": 0}]})
    s = {"id": "9:0", "name": "Dot", "type": "COMPONENT_SET", "children": kids,
         "propDefs": {"Size": {"type": "VARIANT", "default": "8", "options": sizes},
                      "Color": {"type": "VARIANT", "default": "c0", "options": colors}}}
    analysis, base = variant_diff.analyze(s)
    fills = next(d for d in analysis["differences"] if d["layer"] == "" and d["attr"] == "fill")
    assert len(fills["values"]) == 9  # a colour each, c0 being the base's
    assert all(len(v["variants"]) == 5 and v["when"] == [v["variants"][0].split(", ")[1]] for v in fills["values"])
    sizes_d = next(d for d in analysis["differences"] if d["layer"] == "" and d["attr"] == "size")
    assert [v["when"] for v in sizes_d["values"]] == [["Size=12"], ["Size=16"], ["Size=20"], ["Size=24"]]
    text = variant_diff.render(wrap(s, 51), analysis, base)
    assert len(text.splitlines()) < 60


# ─── the second read fails, layers that were not read, rounding, notes ───

def test_a_failed_second_read_still_prints_the_variants_without_hidden_layers(monkeypatch, capsys):
    for failure in ({"ok": False, "error": "timeout: the script took too long"}, {"ok": False}):
        fake = FakeBridge(reply(wrap(button_set(False), 14)), failure)
        rc, out, err, calls = cli(monkeypatch, capsys, "inspect", "2:0", bridge_reply=fake)
        assert rc == 0 and len(calls) == 2
        assert err.count("figaro: the read of the variants' hidden layers failed") == 1
        assert "base variant: Size=M, State=default" in out
        assert "  padding: 8 16 → 4 12 · Size=S" in out.splitlines()
        assert "not compared: hidden layers (they could not be read)" in out
        assert " → absent or hidden · Size=S, State=default" in out  # hidden there, maybe: not read
        fake = FakeBridge(reply(wrap(button_set(False), 14)), failure)
        rc, out, err, calls = cli(monkeypatch, capsys, "inspect", "2:0", "--json", bridge_reply=fake)
        assert rc == 0 and "hidden layers failed" in err
        data = json.loads(out)  # stdout stays JSON; the warning went to stderr
        assert data["variants"]["hiddenRead"] is False and data["node"]["id"] == "2:0"


def test_variants_cut_by_depth_are_not_called_the_same(monkeypatch, capsys):
    s = button_set(True)
    for v in s["children"]:
        v.pop("children")
        v["childrenCut"] = 2
        v["layout"]["pad"] = [8, 16, 8, 16]
    rc, out, err, calls = cli(monkeypatch, capsys, "inspect", "2:0", "--depth", "1", bridge_reply=reply(wrap(s, 5)))
    assert rc == 0
    assert "the same as the base" not in out and "same as the base:" not in out
    assert "no differences from the base (Size=M, State=default) in the layers that were read" in out
    assert "not compared: layers below --depth or the layer limit, in Size=S, State=hover; " in out
    data = json.loads(cli(monkeypatch, capsys, "inspect", "2:0", "--depth", "1", "--json",
                          bridge_reply=reply(wrap(s, 5)))[1])["variants"]
    assert data["same"] == [] and len(data["cut"]) == 4


def test_a_variant_cut_by_the_layer_limit_is_not_the_same_either():
    s = button_set(True)
    for v in s["children"]:
        v.update(copy.deepcopy(s["children"][2]), id=v["id"], name=v["name"])
    s["children"][1]["children"][0]["childrenCut"] = 3  # one layer deep inside was not read
    analysis, base = variant_diff.analyze(s)
    assert analysis["same"] == ["Size=S, State=hover", "Size=S, State=default"]
    assert analysis["cut"] == ["Size=M, State=hover"]
    text = variant_diff.render(wrap(s, 9), analysis, base)
    assert "same as the base: Size=S, State=hover; Size=S, State=default" in text
    assert "not compared: layers below --depth or the layer limit, in Size=M, State=hover" in text


def comp(vid, name, kids=(), pad=None, **extra):
    node = {"id": vid, "name": name, "type": "COMPONENT", "w": 40, "h": 40, "x": 0, "y": 0, "children": list(kids)}
    if pad is not None:
        node["layout"] = {"mode": "HORIZONTAL", "gap": 2, "main": "MIN", "cross": "MIN", "pad": pad}
    node.update(extra)
    return node


def box(bid, name, x=0, **extra):
    return dict({"id": bid, "name": name, "type": "FRAME", "w": 8, "h": 8, "x": x, "y": 0}, **extra)


def a_set(*variants, axis="Kind"):
    names = [v["name"].split("=")[1] for v in variants]
    return {"id": "7:0", "name": "S", "type": "COMPONENT_SET", "children": list(variants),
            "propDefs": {axis: {"type": "VARIANT", "default": names[0], "options": names}}}


def test_a_condition_never_covers_a_variant_that_does_not_share_the_change():
    pad = [4, 4, 4, 4]
    kw = dict(pad=[8, 8, 8, 8])
    s = {"id": "7:0", "name": "S", "type": "COMPONENT_SET", "propDefs": {
        "Size": {"type": "VARIANT", "default": "M", "options": ["M", "S", "L"]},
        "State": {"type": "VARIANT", "default": "q", "options": ["q", "x", "y", "z"]}},
        "children": [comp("1", "Size=M, State=q", **kw), comp("2", "Size=S, State=x", pad=pad),
                     comp("3", "Size=S, State=y", pad=pad), comp("4", "Size=S, State=z", **kw),
                     comp("5", "Size=L, State=x", pad=pad)]}
    analysis, base = variant_diff.analyze(s)
    (d,) = [d for d in analysis["differences"] if d["attr"] == "pad"]
    # Size=S would also name Size=S, State=z, which keeps the base's padding
    assert d["values"][0]["when"] == ["State=x", "Size=S, State=y"]


def test_a_layer_missing_from_a_cut_variant_is_not_reported_absent(monkeypatch, capsys):
    base = comp("1", "Kind=a", [box("1.1", "x"), box("1.2", "y")])
    cut = comp("2", "Kind=b", [box("2.1", "x")], childrenCut=1)  # y was not read
    analysis, _ = variant_diff.analyze(a_set(base, cut))
    assert analysis["differences"] == [] and analysis["cut"] == ["Kind=b"] and analysis["same"] == []
    # and the other way: the base was cut, the variant has a layer the base's read lacks
    analysis, _ = variant_diff.analyze(a_set(comp("1", "Kind=a", [box("1.1", "x")], childrenCut=1),
                                             comp("2", "Kind=b", [box("2.1", "x"), box("2.2", "y")])))
    assert analysis["differences"] == []
    # whole layers missing with nothing cut are reported
    analysis, _ = variant_diff.analyze(a_set(base, comp("2", "Kind=b", [box("2.1", "x")])))
    assert [(d["layer"], d["attr"]) for d in analysis["differences"]] == [("y", "layer")]


def test_position_counts_only_where_the_parent_has_no_auto_layout():
    flow_a = comp("1", "Kind=a", [box("1.1", "x", x=0)], pad=[0, 0, 0, 0])
    flow_b = comp("2", "Kind=b", [box("2.1", "x", x=6)], pad=[0, 0, 0, 0])
    analysis, _ = variant_diff.analyze(a_set(flow_a, flow_b))
    assert analysis["differences"] == []  # an auto-layout puts it there
    free_a = comp("1", "Kind=a", [box("1.1", "x", x=0)])
    free_b = comp("2", "Kind=b", [box("2.1", "x", x=6)])
    analysis, _ = variant_diff.analyze(a_set(free_a, free_b))
    assert [(d["layer"], d["attr"], d["values"][0]["value"]) for d in analysis["differences"]] == [("x", "pos", [6, 0])]
    # in a flow in one variant and free in the other: no position to compare, the layout differs
    analysis, _ = variant_diff.analyze(a_set(flow_a, free_b))
    assert [d["attr"] for d in analysis["differences"]] == ["layout", "gap", "align", "wrap", "pad"]
    # the variants of a set lie side by side: their own x and y say nothing
    far = comp("2", "Kind=b", [box("2.1", "x")], x=500, y=90)
    assert variant_diff.analyze(a_set(free_a, far))[0]["differences"] == []


def test_numbers_are_compared_and_shown_with_the_same_rounding():
    a = comp("1", "Kind=a", [box("1.1", "x", radius=2)], pad=[4, 4, 4, 4])
    a["layout"]["gap"] = 2
    b = comp("2", "Kind=b", [box("2.1", "x", radius=2.004)], pad=[4.001, 4, 4, 4])
    b["layout"]["gap"] = 2.004
    analysis, base = variant_diff.analyze(a_set(a, b))
    assert analysis["differences"] == [] and analysis["same"] == ["Kind=b"]
    b["layout"]["gap"] = 2.5
    b["layout"]["pad"] = [4.3333, 4, 4, 4]
    analysis, base = variant_diff.analyze(a_set(a, b))
    lines = variant_diff.render(wrap(a_set(a, b), 3), analysis, base).splitlines()
    assert "  gap: 2 → 2.5 · Kind=b" in lines and "  padding: 4 → 4.33 4 4 4 · Kind=b" in lines


def test_a_set_with_vectors_says_their_paths_are_not_compared():
    vec = {"id": "1.1", "name": "mark", "type": "VECTOR", "w": 4, "h": 4, "x": 0, "y": 0}
    s = a_set(comp("1", "Kind=a", [vec], pad=[1, 1, 1, 1]), comp("2", "Kind=b", [dict(vec, id="2.1")], pad=[2, 2, 2, 2]))
    analysis, base = variant_diff.analyze(s)
    assert analysis["vectors"] is True
    assert "vector paths are not compared, only their size, position and paints" in variant_diff.render(
        wrap(s, 3), analysis, base)
    assert "vectors" not in variant_diff.analyze(a_set(comp("1", "Kind=a", [box("1.1", "x")]),
                                                       comp("2", "Kind=b", [box("2.1", "x")])))[0]


def test_names_with_commas_are_separated_by_semicolons():
    kids = [comp(str(i), f"Size={z}, Color={c}", pad=[i, i, i, i]) for i, (z, c) in
            enumerate([("8", "ok"), ("8", "idle"), ("6", "err"), ("6", "run")], start=1)]
    kids[1]["layout"]["pad"] = kids[0]["layout"]["pad"]  # same as the base
    kids[3]["layout"]["pad"] = kids[0]["layout"]["pad"]
    s = {"id": "7:0", "name": "S", "type": "COMPONENT_SET", "children": kids, "propDefs": {
        "Size": {"type": "VARIANT", "default": "8", "options": ["8", "6"]},
        "Color": {"type": "VARIANT", "default": "ok", "options": ["ok", "idle", "err", "run"]}}}
    analysis, base = variant_diff.analyze(s)
    text = variant_diff.render(wrap(s, 5), analysis, base)
    assert "same as the base: Size=8, Color=idle; Size=6, Color=run" in text
