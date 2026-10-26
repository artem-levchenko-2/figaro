# Craft: building in Figma so it passes review

How to keep the house rules of `SKILL.md` (section 6). They come from real reviews of agents' work in Figma;
where Figma's own advice (its skills, its MCP server) disagrees with them, they win. A reviewer judges the
result: crooked spacing, lost formatting and needless variants show at a glance.

## What is already there

Before the work — one read-only script (`-R`): the file's styles and variables and the components already on
the page. Build on what it returns. 300 instances take ~0.2 s.

```js
const root = await h.node(ROOT);                     // the page or frame you work on
const skip = figma.skipInvisibleInstanceChildren;
figma.skipInvisibleInstanceChildren = true;          // faster; restored at the end
try {
  const comps = {};
  for (const inst of root.findAllWithCriteria({ types: ["INSTANCE"] }).slice(0, 300)) {
    h.ck();                                          // node methods don't know the deadline
    const main = await inst.getMainComponentAsync();
    if (!main) continue;
    const top = main.parent && main.parent.type === "COMPONENT_SET" ? main.parent : main;
    const c = comps[top.name] || (comps[top.name] = { key: top.key, library: main.remote, uses: 0, example: inst.id });
    c.uses++;
  }
  return {
    text: (await figma.getLocalTextStylesAsync()).map((s) => s.name),
    paint: (await figma.getLocalPaintStylesAsync()).map((s) => s.name),
    effect: (await figma.getLocalEffectStylesAsync()).map((s) => s.name),
    variables: (await figma.variables.getLocalVariableCollectionsAsync())
      .map((c) => c.name + ": " + c.modes.map((m) => m.name).join("/") + " · " + c.variableIds.length),
    components: comps,
  };
} finally {
  figma.skipInvisibleInstanceChildren = skip;
}
```

Library styles and variables don't show here: `figaro inspect` of the layers that use them prints their
names, ids and keys. Library variables can also be listed through `figma.teamLibrary` (`helpers.md`).

## Start from the UI that exists

- **A screen or block like one that exists** — clone it (`figaro clone <layer> --right`) and edit the copy.
  That keeps the spacing, styles and instances you would otherwise guess at. Start from a finished page like
  the one you need, not from tokens.
- **A page that lives in a library as one instance** — instance → copy → `detachInstance()` → edit the
  sections. After detaching, the ids inside change: find layers again from the copy.
- **Put new things next to the old, not on top**: `h.cloneNext`, or `x` = the neighbour's right edge + 100.
  Not on the page the user is working on right now, unless they asked.
- Name layers for what they are (`Card`, `Price`), not `Frame 12`.

## Tokens from the start

- Colour: a style (`await node.setFillStyleIdAsync(style.id)`) or a variable (`await h.bF(node, 0, v)`).
  Spacing, radii, sizes: `await h.bN(node, "itemSpacing", v)`. Text: a text style (`helpers.md`, "A card
  from the file's styles").
- Take them from `inspect` of the layers around: the same background, the same text — by the id it prints. A
  key only for what the file doesn't have yet.
- A hex only where there really is no token, and say so in the report. Not "hex first, bind later".
- A new text takes a text style whole. On a text that already has its own look, the style replaces its font,
  size, spacing and in-line formatting: attach it when they match or when that is the point (`pitfalls.md`,
  "Text and fonts").
- A theme (light, dark) is variable modes: `frame.setExplicitVariableModeForCollection(collection, modeId)`,
  not component variants.

## Auto-layout

- Everything with children is auto-layout: `h.frame` (it knows the order of the settings). Absolute `x`/`y`
  only for badges and overlays (`layoutPositioning = "ABSOLUTE"`).
- HUG — buttons, chips, the height of cards. FILL — rows, fields, text blocks in a column. FIXED — the page
  width (1440) and icons.
- Text that wraps — `h.wrapText(text)`; a one-line label — HUG.
- Neighbours of one size: cards in a row — FILL with the same gap, or GRID (`layoutMode = "GRID"`,
  `gridColumnCount`). Stickers between 220 and 270 px wide look scattered; four columns of 280 px don't.
- After building — `shot`, and look at the picture: cut-off text, spacing that collapsed and layers that stick
  out of their frame show only in a picture.

## A component with properties

Verified live. Variants are copies of one frame with different styles; properties go on the set **after**
`combineAsVariants`, and in each variant a layer is hooked up by its name. The style names are examples —
take yours from `inspect`.

```js
const page = await h.node(PAGE);
const texts = await figma.getLocalTextStylesAsync(), paints = await figma.getLocalPaintStylesAsync();
function style(list, name) {
  const s = list.find((x) => x.name === name);
  if (!s) throw new Error('no style "' + name + '"; there are: ' + list.map((x) => x.name).join(", "));
  return s;
}
const labelStyle = style(texts, "Label/Medium");
const ICON = { family: "Font Awesome 7 Free", style: "Solid" };   // the file's icon font; h.fa() lists them
await h.fonts([labelStyle.fontName, ICON, { family: "Inter", style: "Regular" }]);

async function variant(name, bg, ink) {                            // name: "Style=Primary"
  const f = h.frame(page, { layout: "H", spacing: 8, padding: [10, 16], align: { counter: "CENTER" },
    fill: false, radius: 8, name });
  await f.setFillStyleIdAsync(style(paints, bg).id);
  const icon = figma.createText();
  f.appendChild(icon);
  icon.name = "Icon";
  icon.fontName = ICON;
  icon.characters = "arrow-right";                                 // an icon is the glyph's name
  await icon.setFillStyleIdAsync(style(paints, ink).id);
  const label = figma.createText();
  f.appendChild(label);
  label.name = "Label";
  await label.setTextStyleIdAsync(labelStyle.id);
  await h.setText(label, "Button");
  await label.setFillStyleIdAsync(style(paints, ink).id);
  return figma.createComponentFromNode(f);
}

const set = figma.combineAsVariants([
  await variant("Style=Primary", "Surface/Primary", "Text/On Primary"),
  await variant("Style=Secondary", "Surface/Secondary", "Text/Primary"),
], page);
set.name = "Button";
const labelProp = set.addComponentProperty("Label", "TEXT", "Button");   // the full name: "Label#12:0"
const iconProp = set.addComponentProperty("Icon", "BOOLEAN", true);
for (const v of set.children) {
  v.findOne((n) => n.name === "Label").componentPropertyReferences = { characters: labelProp };
  v.findOne((n) => n.name === "Icon").componentPropertyReferences = { visible: iconProp };
}
set.layoutMode = "HORIZONTAL";                       // the variants lie on top of each other — lay them out
set.itemSpacing = 24;
set.paddingTop = set.paddingRight = set.paddingBottom = set.paddingLeft = 24;
set.primaryAxisSizingMode = set.counterAxisSizingMode = "AUTO";
return { id: set.id };
```

- `figaro inspect <set>` shows `prop Label#… TEXT` on the set and `← characters Label#…` on the layer — that
  is how you see the property is hooked up. An instance: `set.defaultVariant.createInstance()`, then
  `h.variant(inst, { Style: "Secondary", Label: "Pay now", Icon: false })`.
- An icon component instead of a glyph is INSTANCE_SWAP: `set.addComponentProperty("Icon", "INSTANCE_SWAP",
  iconComponent.id)`, and on the icon's instance `componentPropertyReferences = { mainComponent: key }`.
- A nested instance with its own properties (an `icon` instance from a UI kit) —
  `nested.isExposedInstance = true`, and its properties show on the parent.
- Figma records every `addComponentProperty` as a separate Cmd+Z step; `undo` and `-R` already allow for it.
- A tag in 12 colours with an optional icon is 12 variants and an `Icon` property, not 36 variants.
- Size × style × state beyond ~30 combinations — split it into a base component and wrappers.

## Moving a design over from code or a live page

- When the code's tokens mirror the file's variables, take the variable with the same name.
- Copy the text word for word, with its formatting — as ranges (`helpers.md`, "Formatting inside a line"),
  with the same heading levels; a quote stays a quote. If the level you need has no style, say so instead of
  taking the next one.
- Compare a shot of the result with the source side by side, block by block.
