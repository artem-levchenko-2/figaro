"""`figaro inspect` of a component set: what differs between its variants.

The plugin's h.inspect returns the whole set as data, every variant with its
layers. An agent that moves the component into code needs the axes (the
variant properties), one variant read in full (the base), and for the others
only what is different from it. This module works that out from the data
`inspect` returns, so the plugin stays as it is.

- Base: the variant whose name matches the defaults of all variant properties,
  which is the set's default variant; if none does, the first one.
- A difference is any of what `inspect` reads of a layer: size (only the axes
  that are fixed: a hugging or filling one follows its content), position
  (only where the parent has no auto-layout), visibility, auto-layout, padding,
  radius, opacity, fills, strokes, effects and the styles and variables bound
  to them, text, its font and text style, and an instance's component,
  properties and the component properties that drive the layer. The order of
  layers is not compared.
- Layers are matched between variants by their path of names. Layers of the
  same name and type under one parent are matched in order: the second `Icon`
  of one variant with the second `Icon` of another. A layer that one variant
  has and another lacks is a difference of its own ("layer").
- What stays short: each difference is one line with the base's value and the
  other values, each with the variants that have it, written as conditions
  ("Size=S") when every variant with that axis value has it.
"""

from __future__ import annotations

import json

import inspect_text as it

MAX_LINES = 160   # lines of differences in the text
MAX_NAMES = 6     # variants named in one line before "+N more"
NOT_IN_FLOW_ONLY = ("pos",)  # compared only where both variants have it


def analyze(set_node):
    """The variants of a COMPONENT_SET node, read as `inspect` gives it, with
    their children: (analysis, base variant node), or (None, None) when there
    is nothing to compare (no variants read: --depth 0)."""
    variants = [c for c in set_node.get("children") or [] if c.get("type") == "COMPONENT"]
    if not variants:
        return None, None
    defs = {k: d for k, d in (set_node.get("propDefs") or {}).items() if d.get("type") == "VARIANT"}
    props = [_parse(v.get("name")) for v in variants]
    base_i, by = 0, "first variant"
    defaults = {k: str(d.get("default")) for k, d in defs.items()}
    if defaults:
        for i, p in enumerate(props):
            if all(p.get(k) == v for k, v in defaults.items()):
                base_i, by = i, "variant properties' defaults"
                break
    base = variants[base_i]
    axes = {}
    for k, d in defs.items():
        axes[k] = {"values": list(d.get("options") or []), "default": d.get("default")}
    if not axes:
        for p in props:
            for k, v in p.items():
                vals = axes.setdefault(k, {"values": [], "default": props[base_i].get(k)})["values"]
                if v not in vals:
                    vals.append(v)
    others = [i for i in range(len(variants)) if i != base_i]

    base_flat = _flatten(base)
    flats = {i: _flatten(variants[i]) for i in others}
    cells = {}      # (layer key, attribute) -> {"base", "scope", "vals"}
    layers = {}     # layer key -> (id, type, names)
    for key, e in base_flat.items():
        layers[key] = (e["n"].get("id"), e["n"].get("type"), e["names"])

    def put(key, attr, i, raw, base_raw):
        c = cells.setdefault((key, attr), {"base": base_raw, "scope": [], "vals": {}})
        c["scope"].append(i)
        c["vals"][i] = raw

    base_attrs = {key: _attrs(e, key) for key, e in base_flat.items()}
    for i in others:
        flat = flats[i]
        for key, e in base_flat.items():
            if key in flat:
                a, b = base_attrs[key], _attrs(flat[key], key)
                for attr in list(a) + [x for x in b if x not in a]:
                    if attr in NOT_IN_FLOW_ONLY and (attr not in a or attr not in b):
                        continue
                    put(key, attr, i, b.get(attr), a.get(attr))
            elif _comparable(key, flat, base_flat):
                put(key, "layer", i, None, _layer_text(e["n"]))
        for key, e in flat.items():
            if key not in base_flat and _comparable(key, flat, base_flat):
                layers.setdefault(key, (e["n"].get("id"), e["n"].get("type"), e["names"]))
                put(key, "layer", i, _layer_text(e["n"]), None)

    rank = {key: n for n, key in enumerate(layers)}
    differing = set()
    diffs = []
    for (key, attr), c in sorted(cells.items(), key=lambda kv: rank[kv[0][0]]):
        groups = {}
        for i in c["scope"]:
            if _canon(c["vals"][i]) != _canon(c["base"]):
                groups.setdefault(_canon(c["vals"][i]), []).append(i)
        if not groups:
            continue
        lid, ltype, names = layers[key]
        values = []
        for members in groups.values():
            differing.update(members)
            values.append({"value": c["vals"][members[0]],
                           "when": _when(members, c["scope"], props, base_i, variants),
                           "variants": [variants[i].get("name") for i in members]})
        diffs.append({"layer": " › ".join(names) if names else "", "path": list(names),
                      "id": lid, "type": ltype, "attr": attr, "base": c["base"], "values": values})
    analysis = {
        "base": {"id": base.get("id"), "name": base.get("name"), "props": props[base_i], "by": by},
        "axes": axes,
        "variants": [{"id": v.get("id"), "name": v.get("name"), "props": p}
                     for v, p in zip(variants, props)],
        "same": [variants[i].get("name") for i in others if i not in differing],
        "differences": diffs,
    }
    if set_node.get("childrenCut"):
        analysis["unread"] = set_node["childrenCut"]
    return analysis, base


def _parse(name):
    """'Size=S, Color=Red' -> {'Size': 'S', 'Color': 'Red'}; {} if it is not that."""
    props = {}
    for part in str(name).split(","):
        k, eq, v = part.strip().partition("=")
        if not eq or not k.strip():
            return {}
        props[k.strip()] = v.strip()
    return props


def _flatten(root):
    flat = {}

    def go(n, parent, key, names):
        flat[key] = {"n": n, "parent": parent, "names": names, "key": key}
        seen = {}
        for c in n.get("children") or []:
            ident = (c.get("name"), c.get("type"))
            i = seen.get(ident, 0)
            seen[ident] = i + 1
            label = str(c.get("name")) + ("[%d]" % (i + 1) if i else "")
            go(c, n, key + ((ident[0], ident[1], i),), names + (label,))

    go(root, None, (), ())
    return flat


def _comparable(key, flat, base_flat):
    """A layer only one variant has is reported where its parent is in both and
    both read the parent's children: no cut by --depth, nothing under it."""
    parent = key[:-1]
    if not key or parent not in flat or parent not in base_flat:
        return False
    return not (flat[parent]["n"].get("childrenCut") or base_flat[parent]["n"].get("childrenCut"))


def _canon(raw):
    return json.dumps(raw, sort_keys=True, ensure_ascii=False)


def _r(v):
    return round(v, 2) if isinstance(v, float) else v


def _attrs(e, key):
    """The comparable facts of one layer: attribute -> JSON-able value. An
    attribute a layer lacks is None when the others compare against it."""
    n, parent = e["n"], e["parent"]
    root = not key
    a = {"visible": n.get("visible") is not False}
    sh, _, sv = (n.get("sizing") or "FIXED/FIXED").partition("/")
    if "w" in n:
        a["size"] = [_r(n["w"]) if sh == "FIXED" else None, _r(n["h"]) if sv == "FIXED" else None]
    if "x" in n and not root and not (parent is not None and parent.get("layout") and not n.get("absolute")):
        a["pos"] = [_r(n["x"]), _r(n["y"])]
    a["sizing"] = n.get("sizing") or "FIXED/FIXED"
    if n.get("absolute"):
        a["absolute"] = True
    layout = n.get("layout")
    a["layout"] = layout.get("mode") if layout else None
    if layout:
        if layout.get("mode") == "GRID":
            a["grid"] = [layout.get("cols"), layout.get("rows"), layout.get("colGap"), layout.get("rowGap")]
        else:
            a["gap"] = layout.get("gap")
            a["align"] = [layout.get("main"), layout.get("cross")]
            a["wrap"] = [bool(layout.get("wrap")), layout.get("crossGap")]
        a["pad"] = layout.get("pad")
    mm = n.get("minmax")
    if mm and any(v is not None for v in mm):
        a["minmax"] = mm
    a["radius"] = n.get("radius", 0)
    a["clip"] = bool(n.get("clip"))
    a["opacity"] = n.get("opacity", 1)
    styles = n.get("styles") or {}
    a["fill"] = ({"style": styles.get("fill"), "paints": n.get("fills")}
                 if n.get("fills") or styles.get("fill") else None)
    if n.get("strokes") or styles.get("stroke"):
        w = n.get("strokeWeight")
        a["stroke"] = {"weight": [_r(x) for x in w] if isinstance(w, list) else _r(w),
                       "align": n.get("strokeAlign"), "style": styles.get("stroke"),
                       "paints": n.get("strokes")}
    else:
        a["stroke"] = None
    a["effects"] = ({"style": styles.get("effect"), "effects": n.get("effects")}
                    if n.get("effects") or styles.get("effect") else None)
    if styles.get("grid"):
        a["gridStyle"] = styles["grid"]
    for k, v in (n.get("vars") or {}).items():
        a["var." + k] = v
    for k, v in (n.get("modes") or {}).items():
        a["mode." + k] = v
    if n.get("type") == "TEXT":
        a["text"] = n.get("text")
        for k, v in (n.get("font") or {}).items():
            a["font." + k] = v
        for k in ("textStyle", "autoResize", "link"):
            if n.get(k):
                a[k] = n[k]
        if n.get("segments"):
            a["segments"] = n["segments"]
    if n.get("component"):
        a["component"] = n["component"]
    for k, v in (n.get("props") or {}).items():
        a["prop." + k] = v
    for k, v in (n.get("refs") or {}).items():
        a["ref." + k] = v
    if root and n.get("description"):
        a["description"] = n["description"]
    return a


def _layer_text(n):
    """A layer one variant has and another lacks, as a line without its id."""
    line = it._line(n, None)
    prefix = f"{n.get('name')} [{n.get('type')}] {n.get('id')}"
    text = f"[{n.get('type')}]" + (line[len(prefix):] if line.startswith(prefix) else "")
    inside = _count(n) - 1
    return text + (f" (+{it._count(inside, 'layer')} inside)" if inside else "")


def _count(n):
    return 1 + sum(_count(c) for c in n.get("children") or [])


def _when(members, scope, props, base_i, variants):
    """The variants as conditions on the axes, where every variant of the
    comparison that has an axis value is among them and the base does not
    have it; the rest by name. 'Size=S', 'Color=Red & Size=L'."""
    members = set(members)
    names = [variants[i].get("name") for i in sorted(members)]
    if len(members) == 1 or any(not props[i] for i in members):
        return names
    base_p = props[base_i]
    conds = {}   # label -> variants of the comparison that match it
    axes = sorted({k for i in members for k in props[i]})
    for k in axes:
        for val in sorted({props[i].get(k) for i in members}):
            if base_p.get(k) != val:
                conds[f"{k}={val}"] = {i for i in scope if props[i].get(k) == val}
    for n, k in enumerate(axes):
        for k2 in axes[n + 1:]:
            for i in sorted(members):
                v1, v2 = props[i].get(k), props[i].get(k2)
                if base_p.get(k) == v1 or base_p.get(k2) == v2:
                    continue
                conds[f"{k}={v1} & {k2}={v2}"] = {j for j in scope if props[j].get(k) == v1
                                                  and props[j].get(k2) == v2}
    left, out = set(members), []
    usable = {c: s for c, s in conds.items() if len(s) >= 2 and s <= members}
    while left:
        best = max(usable, key=lambda c: (len(usable[c] & left), -len(usable[c]), c), default=None)
        if best is None or not usable[best] & left:
            break
        out.append(best)
        left -= usable[best]
    out += [variants[i].get("name") for i in sorted(left)]
    return out


# ─── text ────────────────────────────────────────────────────────────────

def render(value, analysis, base, max_lines=it.MAX_LINES, max_diff=MAX_LINES):
    node = value.get("node") or {}
    out = it.header(value)
    top = {k: v for k, v in node.items() if k not in ("children", "childrenCut", "hiddenKids")}
    tree = []
    it._walk(top, 0, None, tree)
    out += [""] + tree
    b = analysis["base"]
    why = "the set's default" if b["by"].startswith("variant") else "the first one"
    out += ["", f"base variant: {b['name']} — {why}"]
    lines = []
    it._walk(base, 1, node, lines)
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"… {it._count(len(lines) - max_lines, 'more line')} — "
                                     "use a smaller --depth or --json"]
    out += lines
    out += ["", _summary(analysis)]
    out += _diff_lines(analysis, max_diff)
    same = analysis["same"]
    if same and analysis["differences"]:
        out.append("")
        out.append("same as the base: " + _names(same))
    return "\n".join(out + it._keys(value))


def _summary(a):
    n = len(a["variants"]) - 1
    if n == 0:
        return "this set has one variant: nothing to compare"
    base = a["base"]["name"]
    if not a["differences"]:
        return (f"the other {it._count(n, 'variant')} {'is' if n == 1 else 'are'} the same as the base "
                f"({base}) in everything inspect reads, apart from the name")
    changed = n - len(a["same"])
    if n == 1:
        head = f"the other variant differs from the base ({base})"
    elif changed == n:
        head = f"all {n} other variants differ from the base ({base})"
    else:
        head = f"{changed} of {n} other variants differ from the base ({base})"
    if a.get("unread"):
        head += f"; {it._count(a['unread'], 'more variant')} not read (layer limit), the rest are compared"
    return head + ":"


def _diff_lines(a, limit):
    out = []
    last = None
    for d in a["differences"]:
        layer = (d["layer"] or "the variant itself", d["type"])
        if layer != last:
            out.append(f"{layer[0]} [{layer[1]}]" if d["layer"] else layer[0])
            last = layer
        out += _diff(d)
    if len(out) > limit:
        out = out[:limit] + [f"… {it._count(len(out) - limit, 'more line')} of differences — use --json"]
    return out


def _diff(d):
    attr, base = d["attr"], d["base"]
    label = "  " + _label(attr) + ": "
    if len(d["values"]) == 1:
        v = d["values"][0]
        return [label + f"{show(attr, base)} → {show(attr, v['value'])} · {_names(v['when'])}"]
    lines = [label + show(attr, base)]
    for v in d["values"]:
        lines.append(f"    → {show(attr, v['value'])} · {_names(v['when'])}")
    return lines


def _names(names):
    shown = ", ".join(names[:MAX_NAMES])
    return shown + (f" +{len(names) - MAX_NAMES} more" if len(names) > MAX_NAMES else "")


def _label(attr):
    if attr == "pos":
        return "position"
    if attr == "pad":
        return "padding"
    head, dot, rest = attr.partition(".")
    return head + dot + rest.split("#")[0] if dot else attr


def show(attr, raw):
    if attr == "layer":
        return "absent" if raw is None else raw
    if raw is None:
        return "none"
    head = attr.partition(".")[0]
    if attr == "visible":
        return "visible" if raw else "hidden"
    if attr == "size":
        return "×".join("auto" if v is None else _n(v) for v in raw)
    if attr == "pos":
        return f"@{_n(raw[0])},{_n(raw[1])}"
    if attr == "sizing":
        return raw.lower().replace("/", "×")
    if attr == "layout":
        return it.ARROWS.get(raw, str(raw)) + " " + str(raw).lower()
    if attr == "align":
        return "/".join(str(x).lower() for x in raw)
    if attr == "wrap":
        return f"wrap {_n(raw[1])}" if raw[0] else "no wrap"
    if attr == "pad":
        return it._pad(raw)
    if attr == "grid":
        return f"{raw[0]}×{raw[1]} gap {_n(raw[2])}/{_n(raw[3])}"
    if attr in ("absolute", "clip"):
        return "yes" if raw else "no"
    if attr == "minmax":
        return " ".join(f"{k} {_n(v)}" for k, v in zip(("minW", "maxW", "minH", "maxH"), raw) if v is not None)
    if attr == "radius":
        return " ".join(map(_n, raw)) if isinstance(raw, list) else _n(raw)
    if attr == "fill":
        return (it._style(raw.get("style")) + _paints(raw.get("paints"))).strip() or "none"
    if attr == "stroke":
        w = raw.get("weight")
        w = "/".join(map(_n, w)) if isinstance(w, list) else _n(w)
        return f"{w} {str(raw.get('align') or '').lower()} {it._style(raw.get('style'))}{_paints(raw.get('paints'))}"
    if attr == "effects":
        fx = ", ".join(it._effect(e) for e in raw.get("effects") or [])
        return (it._style(raw.get("style")) + fx).strip() or "none"
    if attr == "segments":
        return "; ".join(it._segment(s) for s in raw)
    if attr == "text":
        shown = raw if len(raw) <= 60 else raw[:59] + "…"
        return it._value(shown.replace("\n", "⏎"))
    if head == "prop":  # a text or a boolean of an instance's property: quoted as inspect does
        return it._value(raw)
    return raw if isinstance(raw, str) else it._value(raw) if isinstance(raw, bool) else _n(raw)


def _paints(paints):
    return it._paints(paints) if paints else "none"


def _n(v):
    return it._num(_r(v))
