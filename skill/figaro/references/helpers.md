# The `h.*` helpers and the script environment

## Environment

A `figaro exec` script runs as the body of an async function with the parameters `figma`, `print`, `h` and
`lib`.

- **`figma`** — the Plugin API, with guards: `closePlugin()`, `showUI()`, `ui.close()` and replacing
  `ui.onmessage` throw, because they would cut the plugin off from the bridge.
- **`--timeout`** (60 s) — the script's deadline. Calls to `figma.*Async`, `figma.variables.*` and
  `figma.teamLibrary.*` check it themselves: after the deadline a new call throws, and a call Figma never
  answered ends with the error "did not answer before --timeout ran out". Node methods
  (`getMainComponentAsync`, `exportAsync`, `setTextStyleIdAsync`…) and the `h.*` helpers don't know the
  deadline — call `h.ck()` in a long loop.
- **`print(...)`** — lines in the answer's `logs`; the CLI prints them as `log: …`.
- **`return`** — the result: a string, a number or JSON. Return ids, names and numbers. A Figma node does not
  turn into JSON.
- **`lib`** — the functions of the `--lib` files (`{}` without them). A file's top-level `function`, `class`,
  `const` and `module.exports` become `lib.<name>` and see the current script's `figma`, `h` and `print`.
- Both the sync and the async API work, but take async: `await figma.getNodeByIdAsync(id)`, or simpler,
  `await h.node(id)`.
- An error comes back with `line N` of your script, the line itself and a `hint` for the common cases. A
  `// comment` on the last line breaks nothing.

## Finding a layer

| Helper | What it does |
|---|---|
| `await h.node(id)` | a node by id, layer link or `1-11`. An id this file doesn't have fails at once: `no node … in "…"` |
| `await h.resolve(x)` | the same, plus `"page"` (the current page) and `"sel"` (the first selected layer) |
| `h.sel()` | the selection: `[{id, name, type, w, h, chars, url}]` |
| `h.find(root, {name, nameHas, type, text, textHas, hidden})` | a fast search; returns **records** `{id, name, type, w, h, chars}`, not nodes — get the node with `h.node(r.id)`. Skips layers hidden in instances unless `hidden: true` |
| `h.findByName(root, name)` · `h.findAllByName(root, name)` | the node, or the nodes, with this exact name |
| `h.dumpTree(node, {maxDepth, showSize, showText, showLayout})` | the tree as text, like `figaro tree` |
| `h.link(nodeOrId)` | a link to the layer, for a report |

## Reading and seeing

| Helper | What it does |
|---|---|
| `await h.inspect(node, {depth, hidden})` | the data behind `figaro inspect`: layout, fills with style and variable names, texts, instances, keys |
| `await h.shot(node, {format, scale, width, absolute})` | a picture in base64; `absolute: true` exports exactly the layer's size even if children or effects stick out of it (for a picture reused inside another mockup). The CLI is handier: `figaro shot` or `exec --shot` write a file |

## Colour

| Helper | What it does |
|---|---|
| `h.hex("#1a2b3c")` | `{r, g, b}` in the 0..1 range, as Figma wants it |
| `h.solid("#1a2b3c", opacity?)` | a ready fills array: `node.fills = h.solid("#fff")` |

A colour from a token goes through a variable (`h.bF`, "Variables" below) or a style (`setFillStyleIdAsync`,
the recipes below).

## Auto-layout

| Helper | What it does |
|---|---|
| `h.frame(parent, opts)` | an auto-layout frame set up in the right order: into the tree → `layoutMode` → size → HUG → padding. `opts`: `layout` (`"V"`, `"H"`), `w`, `h`, `spacing`, `padding` (a number, `[vertical, horizontal]` or `{top, right, bottom, left}`), `align: {primary, counter}`, `fill` (a hex or `false`), `radius`, `name`. An axis without a number HUGs |
| `await h.fill(node, "x" \| "y" \| "xy")` | FILL; only for a child of an auto-layout frame, so `parent.appendChild(node)` first. Warns if the parent HUGs on that axis |
| `await h.hug(node, "x" \| "y" \| "xy")` | HUG; only an auto-layout frame or a text |
| `await h.fixed(node, w, h)` | a fixed size; `null` leaves that axis as it was (a bare `resize()` makes both axes FIXED) |
| `await h.wrapText(text, width?)` | text that wraps: in auto-layout to the parent's width, otherwise to `width` px. Warns if the text turned into a column |

The auto-layout helpers load the text's fonts themselves and explain what Figma won't allow.

## Text and fonts

| Helper | What it does |
|---|---|
| `await h.setText(node, "text")` | new text; loads the fonts, mixed ones too. The new text takes the first character's style |
| `await h.fonts(target)` | loads the fonts of a layer (with all its ranges), of a `FontName` or of a list of them; doesn't throw, returns `{loaded, missing, note?}` |
| `await h.withFonts(root, async () => { … })` | loads every font in the subtree, then runs the function |
| `await h.fa()` | the installed Font Awesome fonts: `{pro, kit, families}` |

## Components and instances

| Helper | What it does |
|---|---|
| `await h.variant(inst, props)` | `inst.setProperties(props)`. Property names short (`"Label"`, `"Size"`) or full (`"Label#12:0"`); `"true"`/`"false"` for a BOOLEAN become booleans |
| `await h.variantsOf(inst)` | `{current, groups, all}` — which variants the set has |
| `await h.importComp(key)` | a component of a **published** library, by key |
| `h.cloneNext(node, {direction, gap, name})` | a copy next to the node: `right`, `left`, `up`, `down` |

A component the file already has (its own or a library's; `inspect` prints its id) needs no import:
`(await h.node(id)).createInstance()`.

## Variables

| Helper | What it does |
|---|---|
| `await h.var_(idOrKey)` | a variable by id (`VariableID:…`; also a library variable the file already uses — `inspect` prints its id) or by key (imports it) |
| `await h.importVar(key)` | a variable of an enabled library, by key; Figma imports only published ones |
| `await h.bF(node, i, v)` · `await h.bS(node, i, v)` | bind fill or stroke number `i` to a variable (an id, a key or the variable itself) |
| `await h.bN(node, prop, v)` | bind a number: `itemSpacing`, `counterAxisSpacing`, `paddingTop`…, `topLeftRadius`…, `width`, `height`, `minWidth`…, `strokeWeight`, `opacity` |

A text's font to a variable: `text.setBoundVariable("fontSize", v)` — and the same for `fontFamily`,
`fontStyle`, `fontWeight`, `lineHeight`, `letterSpacing` and `paragraphSpacing`.

## Control

`h.ck()` throws as soon as the script's `--timeout` has passed. Call it on every iteration of a long loop:
`figma.*Async` calls check the deadline themselves, but node methods and `h.*` don't, so without `h.ck()` the
loop keeps going while the next script waits for the file. `h.aborted()` is the same without throwing: `true`
or `false`.

## Recipes

**A card from the file's styles** (the style names come from `inspect` of the layers around it)

```js
const texts = await figma.getLocalTextStylesAsync();
const paints = await figma.getLocalPaintStylesAsync();
function pick(list, name) {
  const s = list.find((x) => x.name === name);
  if (!s) throw new Error('no style "' + name + '"; there are: ' + list.map((x) => x.name).join(", "));
  return s;
}
const heading = pick(texts, "Heading/Medium");
const surface = pick(paints, "Surface/Card"), ink = pick(paints, "Text/Primary");

const card = h.frame(await h.node(PARENT), { layout: "V", w: 360, spacing: 8, padding: 24, fill: false,
  radius: 12, name: "Card" });                    // no number for the height — HUG
await card.setFillStyleIdAsync(surface.id);       // the colour from a style, not a hex
const title = figma.createText();
card.appendChild(title);
await h.fonts([title.fontName, heading.fontName]); // the style's font — before setTextStyleIdAsync
await title.setTextStyleIdAsync(heading.id);
await title.setFillStyleIdAsync(ink.id);           // and the text's colour
await h.setText(title, "Payouts");
await h.wrapText(title);                           // to the card's width
return { id: card.id };
```

Spacing and radius take variables the same way: `await h.bN(card, "itemSpacing", v)`, `"paddingTop"`…,
`"topLeftRadius"`… ("Variables" above).

**A library's style, component and variable** (`figaro inspect` of a layer that uses them prints their ids
and keys)

```js
// already in the file — by id
await frame.setFillStyleIdAsync(STYLE_ID);       // "S:…,2739:8"; also setTextStyleIdAsync, setStrokeStyleIdAsync
const inst = (await h.node(COMP_ID)).createInstance();
parent.appendChild(inst);
await h.variant(inst, { Size: "L", Label: "Pay now", Icon: false });
await h.bN(frame, "topLeftRadius", VAR_ID);      // "VariableID:…"

// not in the file yet — by key, from a library enabled for the file
const s = await figma.importStyleByKeyAsync(STYLE_KEY);
const set = await figma.importComponentSetByKeyAsync(SET_KEY);
const v = await figma.variables.importVariableByKeyAsync(VAR_KEY);
```

Import by key takes only what is published in an enabled library, and sometimes Figma never answers it at
all: give the script `--timeout 20`.

**The variables of the enabled libraries** (the collections list holds variables only; components can't be
listed this way). Give it `--timeout 20`: sometimes Figma never answers `teamLibrary` at all — then the script
ends with an error, and you take the variables' keys from the layers that already use them
(`figaro inspect`).

```js
const cols = await figma.teamLibrary.getAvailableLibraryVariableCollectionsAsync();
const out = [];
for (const c of cols) {
  const vars = await figma.teamLibrary.getVariablesInLibraryCollectionAsync(c.key);
  out.push({ library: c.libraryName, collection: c.name, vars: vars.map((v) => v.name + " " + v.resolvedType + " " + v.key) });
}
return out;
```

**Formatting inside a line** (as in the source: bold, italic, code, links)

```js
const t = await h.node(ID);
await h.setText(t, "Pay with cards or bank transfers.");
const i = t.characters.indexOf("bank transfers");
const n = "bank transfers".length;
await figma.loadFontAsync({ family: "Inter", style: "Semi Bold" });
t.setRangeFontName(i, i + n, { family: "Inter", style: "Semi Bold" });
t.setRangeHyperlink(i, i + n, { type: "URL", value: "https://example.com" });
t.setRangeTextDecoration(i, i + n, "UNDERLINE");
```

A text style on a range is `await t.setRangeTextStyleIdAsync(start, end, styleId)`, a colour is
`t.setRangeFills(start, end, h.solid("#1e35ff"))`. Read the existing formatting with
`t.getStyledTextSegments(["fontName", "fontSize", "fills", "hyperlink", "textDecoration"])` (`inspect` shows
it too).

**Changing many texts in batches** — ~25 layers per call, safe to run again:

```js
const map = { "12:1": "Payouts", "12:2": "Cards" /* … ≤ 25 */ };
const done = [];
for (const [id, text] of Object.entries(map)) {
  const n = await h.node(id);
  if (n.characters !== text) await h.setText(n, text);
  done.push(id);
}
return done.length;
```
