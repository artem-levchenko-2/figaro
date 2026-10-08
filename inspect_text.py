"""`figaro inspect` as compact text.

The plugin's h.inspect (plugin/code.js) returns the subtree as data; this
prints it for an agent to read: one line per layer with what differs from the
defaults, the styled parts of a text under it, and at the end the ids and keys
of the components, styles and variables the subtree uses. `--json` / `-o` give
the data itself.
"""

from __future__ import annotations

ARROWS = {"HORIZONTAL": "→", "VERTICAL": "↓", "GRID": "▦"}
MAX_LINES = 400


def render(value, max_lines=MAX_LINES):
    node = value.get("node") or {}
    out = [f'"{node.get("name")}" {node.get("type")} {node.get("id")} in "{value.get("file")}"'
           f" · {_count(value.get('nodes'), 'layer')}"
           + (" (cut)" if value.get("cut") else "")]
    if value.get("url"):
        out.append(value["url"])
    tree = []
    _walk(node, 0, None, tree)
    if len(tree) > max_lines:
        rest = len(tree) - max_lines
        tree = tree[:max_lines] + [f"… {_count(rest, 'more line')} — use a smaller --depth, "
                                   "inspect a branch, or --json"]
    return "\n".join(out + [""] + tree + _keys(value))


def _walk(n, depth, parent, lines):
    pad = "  " * depth
    lines.append(pad + _line(n, parent))
    sub = pad + "  ┆ "
    for seg in n.get("segments") or []:
        lines.append(sub + _segment(seg))
    if n.get("segmentsCut"):
        lines.append(sub + f"… {_count(n['segmentsCut'], 'more text part')}")
    for name, d in (n.get("propDefs") or {}).items():
        line = f"prop {name} {d.get('type')} = {_value(d.get('default'))}"
        if d.get("options"):
            line += " · " + ", ".join(map(str, d["options"]))
        lines.append(sub + line)
    for child in n.get("children") or []:
        _walk(child, depth + 1, n, lines)
    if n.get("childrenCut"):
        why = "layer limit" if n.get("children") else "past --depth"
        lines.append(sub + f"… {_count(n['childrenCut'], 'more child', 'more children')} ({why})")


def _line(n, parent):
    head = f"{n.get('name')} [{n.get('type')}] {n.get('id')}"
    if "w" in n:
        head += f" {_num(n['w'])}×{_num(n['h'])}"
    in_flow = parent is not None and parent.get("layout") and not n.get("absolute")
    if "x" in n and not in_flow:
        head += f" @{_num(n['x'])},{_num(n['y'])}"
    parts = [head]
    if n.get("visible") is False:
        parts.append("hidden")
    layout = n.get("layout")
    if layout:
        parts.append(_layout(layout))
    sizing = n.get("sizing")
    if sizing and sizing != "FIXED/FIXED":
        parts.append(sizing.lower().replace("/", "×"))
    if n.get("absolute"):
        parts.append("absolute")
    mm = n.get("minmax")
    if mm and any(v is not None for v in mm):
        names = ("minW", "maxW", "minH", "maxH")
        parts.append(" ".join(f"{k} {_num(v)}" for k, v in zip(names, mm) if v is not None))
    if n.get("radius"):
        r = n["radius"]
        parts.append("r " + (" ".join(map(_num, r)) if isinstance(r, list) else _num(r)))
    if n.get("clip"):
        parts.append("clip")
    if n.get("opacity") is not None:
        parts.append(f"opacity {n['opacity']}")
    styles = n.get("styles") or {}
    if n.get("fills"):
        parts.append("fill " + _style(styles.get("fill")) + _paints(n["fills"]))
    if n.get("strokes"):
        w = n.get("strokeWeight")
        w = "/".join(map(_num, w)) if isinstance(w, list) else _num(w)
        parts.append(f"stroke {w} {str(n.get('strokeAlign', '')).lower()} " + _style(styles.get("stroke"))
                     + _paints(n["strokes"]))
    if n.get("effects"):
        parts.append(_style(styles.get("effect")) + ", ".join(_effect(e) for e in n["effects"]))
    if styles.get("grid"):
        parts.append("grid " + _style(styles["grid"]).strip())
    if n.get("type") == "TEXT":
        parts.extend(_text(n))
    if n.get("component"):
        parts.append(f"⟐ {n['component']}")
    if n.get("props"):
        parts.append(", ".join(f"{k.split('#')[0]}={_value(v)}" for k, v in n["props"].items()))
    if n.get("refs"):
        parts.append("← " + ", ".join(f"{k} {v}" for k, v in n["refs"].items()))
    if n.get("key"):
        parts.append(f"◆ key {n['key']}" + (" (library)" if n.get("remote") else ""))
    if n.get("description"):
        parts.append(f"description: {n['description'][:80]}")
    if n.get("vars"):
        parts.append("vars " + ", ".join(f"{k}={v}" for k, v in n["vars"].items()))
    if n.get("modes"):
        parts.append("modes " + ", ".join(f"{k}={v}" for k, v in n["modes"].items()))
    if n.get("hiddenKids"):
        parts.append(f"+{n['hiddenKids']} hidden")
    return " · ".join(p for p in parts if p)


def _layout(l):
    if l.get("mode") == "GRID":
        s = f"▦ {l.get('cols')}×{l.get('rows')} gap {_num(l.get('colGap'))}/{_num(l.get('rowGap'))}"
    else:
        s = f"{ARROWS.get(l.get('mode'), l.get('mode'))} gap {_num(l.get('gap'))}"
        main, cross = l.get("main"), l.get("cross")
        if (main, cross) != ("MIN", "MIN"):
            s += f" align {str(main).lower()}/{str(cross).lower()}"
        if l.get("wrap"):
            s += f" wrap {_num(l.get('crossGap'))}"
    pad = l.get("pad") or []
    if any(pad):
        s += " pad " + _pad(pad)
    return s


def _pad(p):
    t, r, b, l = p
    if t == r == b == l:
        return _num(t)
    if t == b and r == l:
        return f"{_num(t)} {_num(r)}"
    return " ".join(map(_num, p))


def _paints(paints):
    if paints == "mixed":
        return "mixed"
    return ", ".join(_paint(p) for p in paints)


def _paint(p):
    t = p.get("type")
    if t == "SOLID":
        s = p.get("color", "")
    elif str(t).startswith("GRADIENT"):
        s = t.replace("GRADIENT_", "").lower() + " " + " → ".join(p.get("stops") or [])
    elif t == "IMAGE":
        s = f"image {str(p.get('scaleMode', '')).lower()}"
    else:
        s = str(t).lower()
    if p.get("opacity") is not None:
        s += f" {round(p['opacity'] * 100)}%"
    if p.get("var"):
        s += f" ({p['var']})"
    return s


def _effect(e):
    s = e.get("type", "").lower().replace("_", " ")
    if e.get("offset"):
        s += " " + " ".join(map(_num, e["offset"]))
    if e.get("radius"):
        s += f" r{_num(e['radius'])}"
    if e.get("spread"):
        s += f" s{_num(e['spread'])}"
    if e.get("color"):
        s += " " + e["color"]
    if e.get("vars"):
        s += " (" + ", ".join(f"{k}={v}" for k, v in e["vars"].items()) + ")"
    return s


def _text(n):
    text = n.get("text") or ""
    shown = text if len(text) <= 80 else text[:79] + "…"
    parts = [_value(shown.replace("\n", "⏎"))]
    f = n.get("font") or {}
    font = f"{f.get('family')} {_num(f.get('size'))}"
    lh = f.get("lineHeight")
    if lh and lh != "auto":
        font += "/" + str(lh).removesuffix("px")
    ls = f.get("letterSpacing")
    if ls and ls not in ("0px", "0%"):
        font += f" ls {ls}"
    if f.get("align") and f["align"] != "LEFT":
        font += f" {f['align'].lower()}"
    if f.get("case"):
        font += f" {f['case'].lower()}"
    parts.append(font)
    if n.get("textStyle"):
        parts.append(f"style '{n['textStyle']}'")
    if n.get("autoResize") and n["autoResize"] != "WIDTH_AND_HEIGHT":
        parts.append(f"auto {n['autoResize'].lower()}")
    if n.get("link"):
        parts.append(f"link {n['link']}")
    if n.get("missingFont"):
        parts.append("⚠ font missing")
    return parts


def _segment(s):
    shown = s.get("text", "")
    line = f"{_value(shown.replace(chr(10), '⏎'))} {s.get('font')} {_num(s.get('size'))}"
    if s.get("style"):
        line += f" '{s['style']}'"
    if s.get("fills"):
        line += " " + _paints(s["fills"])
    for key in ("decoration", "case", "list"):
        if s.get(key):
            line += f" {str(s[key]).lower()}"
    if s.get("link"):
        line += f" link {s['link']}"
    return line


def _keys(value):
    out = []
    comps = value.get("components") or {}
    styles = value.get("styles") or {}
    variables = value.get("variables") or {}
    if comps or styles or variables:
        # Import by key can fail or hang even for what the file already uses.
        out += ["", "in this file use the id: (await h.node(id)).createInstance(), "
                "node.set…StyleIdAsync(id), h.bF/h.bN(…, id); the key is for import…ByKeyAsync "
                "in another file"]
    if comps:
        out += ["", "components:"]
        for name, c in comps.items():
            line = f"  {name} —{_id(c)} key {c.get('key')}"
            if c.get("setKey"):
                line += f" · set '{c.get('set')}' {c['setKey']}"
            out.append(line + _where(c))
    if styles:
        out += ["", "styles:"]
        out += [f"  {name} {s.get('type')} —{_id(s)} key {s.get('key')}{_where(s)}"
                for name, s in styles.items()]
    if variables:
        out += ["", "variables:"]
        out += [f"  {name} {v.get('type')} · {v.get('collection')} —{_id(v)} key {v.get('key')}{_where(v)}"
                for name, v in variables.items()]
    colls = value.get("collections") or {}
    if colls:
        out += ["", "variable collections:"]
        out += [f"  {name}: {', '.join(c.get('modes') or [])} — key {c.get('key')}{_where(c)}"
                for name, c in colls.items()]
    return out


def _id(x):
    return f" id {x['id']} ·" if x.get("id") else ""


def _where(x):
    return " · library" if x.get("remote") else " · local"


def _style(name):
    return f"'{name}' " if name else ""


def _count(n, one, many=None):
    """1 layer, 3 layers; 1 more child, 3 more children."""
    return f"{n} {one}" if n == 1 else f"{n} {many or one + 's'}"


def _value(v):
    if isinstance(v, str):
        return '"' + v + '"'
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _num(v):
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)
