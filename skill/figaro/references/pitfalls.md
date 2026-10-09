# Pitfalls of Figaro and the Figma Plugin API

Read the `hint:` in the answer first: the bridge knows most of the cases below.

## Connection and the queue

| What you see | What it is | What to do |
|---|---|---|
| `connection: [Errno 1] Operation not permitted` | your own sandbox keeps commands off the network, 127.0.0.1 included (Codex, Cursor) | run `figaro` outside the sandbox: ask the user to approve that, and to allow `figaro` for good |
| `missing or wrong X-Figaro-Token` (401) | the command didn't find the token the bridge saved when it started: it runs with another home folder (a sandbox, another user), or the file was removed | nothing ran. `figaro doctor` names the file and what to do; in a sandbox, run `figaro` outside it |
| `plugin not connected` (503), "open … in Figma and run … Figaro there" | the plugin is not running in that file | tell the user: open the link in Figma and run Plugins → Development → Figaro (⌘⌥P repeats the last plugin). Don't poll in a loop |
| `N files connected — specify a target` (409), "ambiguous" | several files, and no target or an ambiguous one | `-T` with a key or a link; `figaro targets` |
| `file busy` (503) | another agent has held the file longer than `--queue-timeout` | nothing ran — retry later; the message says who holds it |
| `Figaro is updating` (503) | the user pressed Update in the plugin's window: the bridge pulls the new version and restarts | nothing ran — run the command again in ~10 s |
| `stopped: the user pressed Stop` (409) | the user stopped you from the plugin's window | what your script changed before the Stop stays (`changed:`); with "did not run", nothing ran. Don't run it again: ask the user what to do |
| `(waited 4.2s in the file's queue)` | you waited in the queue | normal; keep your scripts short so others don't wait for you |
| 504 timeout | the script didn't finish within `--timeout` — most often a long loop without `h.ck()` | it may still be running and writing. Don't retry blindly: look at the result with `inspect` or `shot`. Until the abandoned script ends, the file answers 409; once you are sure it has ended — `figaro clear -T …`. Then use smaller batches |
| `figma.…() did not answer before --timeout ran out — the script was stopped` | Figma never answered the call on the line it names | the file is free, but don't repeat that call. It happens with `figma.teamLibrary` and import by key — take what the file already has by id from `figaro inspect` |
| `plugin disconnected mid-request` | the plugin restarted (Figma restarted it after a code change, or the user closed its window) | check what the script managed to do (`inspect`) and run it again; `undo` remembers nothing from before a restart |
| `⚠ … runs an older build — figaro reload` | Figma runs old plugin code | `figaro reload -T <file>`; if that fails, ask the user to press Reload in the plugin's window or to restart the plugin |
| `⚠ Figaro X is released (this is Y) — tell the user` | a new release is out | tell the user once, in your report. Don't update Figaro yourself unless they ask: the window's Update waits until no agent's script runs |
| the first call is slow, "the plugin has not connected yet" | the bridge has just started, and a Figma window in the background takes up to a minute to connect | retry in a few seconds |
| `zsh: no matches found: https://…` | a link without quotes | quote links |
| `zsh: = not found` | the command line starts with `=` (`echo ======`) | use `echo ---` as a divider |

## Ids and files

- **`no node 12:34 in "…"`** — a layer from another file (check `-T`) or a deleted one. The id in a link is
  `node-id=12-34` → `12:34`; Figaro understands both.
- **Ids inside an instance** (`I12:3;4:5`) change when the instance is detached or gets another component.
  After `detachInstance()` or `swapComponent()` find layers again from a stable parent.
- **Search by id, not by name**, when other people edit the file: a name may change between your calls.
- `findOne(n => n.name.startsWith("06"))` also finds a text "06" — limit the search to `root.children` or a
  `type`.

## Auto-layout

The helpers `h.frame`, `h.fill`, `h.hug`, `h.fixed` and `h.wrapText` already do everything in the right
order. If you write it by hand:

- **The order**: into the tree (`parent.appendChild`) → `layoutMode` → `resize()` → sizing modes → padding.
  Figma silently ignores what was set before `layoutMode`.
- **`resize()` makes both axes FIXED** and resets `layoutGrow`, HUG and `textAutoResize`. Size first, modes
  after; or `h.fixed(node, w, null)`.
- **FILL** — only for a child of an auto-layout frame, and only after `appendChild`. A FILL child of a HUG
  parent collapses: give the parent FIXED or FILL.
- **HUG** — only for an auto-layout frame or a text.
- **`counterAxisAlignItems` has no `STRETCH`**: to stretch a child across, `h.fill(child, "x")`.
- **The text turned into a column one character wide** — its width is 0: `h.wrapText(text)` in auto-layout or
  `h.wrapText(text, 320)`. Check `width > 0` afterwards.
- **A FIXED frame clips new children** when it has `clipsContent`: after adding them, HUG on that axis.
- **An absolute child** (`layoutPositioning = "ABSOLUTE"`) can't FILL; badges and overlays — yes, the rest —
  no.
- **Don't mix** `layoutSizingHorizontal/Vertical` (`FIXED`, `HUG`, `FILL`) with `primaryAxisSizingMode` /
  `counterAxisSizingMode` (`FIXED`, `AUTO`): they are two ways of saying the same thing.

## Text and fonts

- **`unloaded font`** — the font isn't loaded: `h.setText`, or `h.fonts(node)` before editing text
  properties.
- **`could not be loaded`** — this font isn't installed on this computer. `h.fonts(node)` tells you which are
  missing.
- **Icon fonts** (Font Awesome and the like): an icon is a text holding the glyph's name (`chart-network`);
  in a font that isn't installed it shows as letters stacked in a column, "S O L I D". The fix is to switch
  `fontName` to an installed icon font and leave the text alone. `h.fa()` lists the installed Font Awesome
  fonts.
- **Mixed fonts** (`fontName === figma.mixed`) — `h.setText` and `h.fonts` load them; new text takes the
  first character's style. To keep the formatting of parts, edit by ranges (`helpers.md`).
- **Text in an instance**: when the component has a TEXT property, change it with
  `h.variant(inst, {Label: "…"})`, not through `characters` inside — that keeps the link to the property.
- **Timeouts on bulk text** (2 to 5 minutes in past runs): ~25 layers per script, and fonts once at the start
  (`h.fonts(root)`).
- A text style's font must be loaded too before you set the style: `h.fonts([text.fontName,
  style.fontName])`.

## Fills, variables, styles

- **`Cannot assign to read only property`** — `fills`/`strokes` are frozen: copy the array
  (`JSON.parse(JSON.stringify(node.fills))`), change it and assign it whole. To bind — `h.bF`/`h.bS`.
- **`setBoundVariableForPaint` returns a new paint** — it has to be assigned; `h.bF` does that itself.
- **Empty `fills`** — nothing to bind: first `node.fills = h.solid("#000")`, then `h.bF(node, 0, v)`.
- **A fill's opacity** lives in `paint.opacity`, not in the variable's colour: don't lose it when you
  overwrite the fill.
- **The report doesn't see changes to variables**: `-R` won't roll them back, and `undo` after such a script
  refuses. Read the variables in the script itself, before and after.
- **What the file already has — by id, not by key.** The components, styles and variables (its own and its
  libraries') that the file's layers use, `figaro inspect` prints with their ids:
  `(await h.node(id)).createInstance()`, `setFillStyleIdAsync(id)`, `h.bF(node, 0, id)`. Import by key takes
  only what is published in a library enabled for the file. In a draft it may fail even for what the file
  already uses: `importStyleByKeyAsync` never answers, and a variable fails with
  `could not find variable with key`.
- **A library can't be listed through the Plugin API**: `figma.teamLibrary` gives only variables, and
  sometimes it never answers at all.

## Components

- **`New parent is an instance`** — an instance can't take new layers. A one-off state — `inst.detachInstance()`
  and edit inside (a house rule); a change for everyone — in the main component.
- **`clone()` loses `componentPropertyReferences`** — hook the properties up again after cloning.
- **A nested instance's properties don't show on the parent** until `nested.isExposedInstance = true`.
- **`addComponentProperty` returns the full name** (`Label#12:0`) — keep it, don't guess it.
- **After `combineAsVariants` the variants lie on top of each other** — lay them out in a grid.
- **`Could not find a component property with name`** — `setProperties` wants the full name with the emoji
  and the `#id` (`✒️ Label#164:0`; `inspect` of the set prints them). Use `h.variant(inst, {Label: "…"})` or
  `figaro variant`: they find the full name from a short one (`Label`) or from the `#id` (`Label#164:0`).
- **`resetOverrides`** is deprecated — use `removeOverrides`.
- **Figma doesn't report deleting a component or a component set**: it won't be in the report, `-R` won't
  roll it back, and `undo` after a script that only deleted components refuses. Check with `figaro tree`
  afterwards.
- **Figma records every `addComponentProperty` as a separate Cmd+Z step.** `undo` and `-R` go back step by
  step until everything the script created is gone. Properties added to an **existing** component can't be
  checked that way — `undo` warns; compare with `inspect`.
- **`inst.mainComponent = other` drops the overrides**, `inst.swapComponent(other)` keeps them.

## Scripts

- **A script failed halfway** — what it did before the error stays in the file (`changed:` above the error).
  Run `figaro undo` and then the fixed script, or add only what is missing. The same script once more makes
  a duplicate.
- **The answer is `Done` or `null`** — you forgot the `return`.
- **`not a function`** — the method was renamed or exists only as an async version (`getNodeByIdAsync`,
  `setTextStyleIdAsync`, `getMainComponentAsync`). Check the name.
- **`figma.createAutoLayout()`, `node.query()`, `node.set()` and `node.screenshot()`** from Figma's own advice
  exist only in its MCP server — the Plugin API doesn't have them.
- **Building needs no `figma.setCurrentPageAsync`**: `figma.create*()` puts a node on the current page, and
  `page.appendChild(node)` or `h.frame(page, …)` moves it where it belongs; to read another page,
  `await page.loadAsync()`. A switched page jumps on the user's screen — don't switch it unless they ask.
- **`setTimeout` in a script** holds the file for everyone: don't wait inside a script.
- **A big `return`** (thousands of nodes) is slow: return only what you need.
- **An error in a `--lib` library** prints as `file.js, line N`; `libMissing` — the plugin restarted, and the
  CLI sends the library again by itself.

## Annotations

- **`node.annotations` returns a new array on every read**, so `array.includes(item)` with an item from an
  earlier read never matches. Compare by label text, and read it once into a variable inside the script.
- **An item comes back with both `label` and `labelMarkdown`.** When you write it back, pass one of them
  (`{label}`), not the object you read.
- **A layer inside an instance may refuse an annotation** (Figma refused a second one on an Input instance).
  Put it on the nearest ancestor that accepts it.
- **An annotation on a hidden layer, or inside a hidden parent, doesn't show in Dev Mode.** Check the
  `visible` chain up to the frame; `h.annotations` reports it for you.

## Undo

- **`undo` refuses** when something changed in the file after the script (the user or another agent), when
  the script isn't yours (`--force` only if the user says so), or when Figma reported no changes but the
  script may have changed variables or deleted components. Then — Cmd+Z in Figma, or a version from
  File → Version history.
- **`undone: … (3 Cmd+Z steps)`** — Figma recorded the script as several steps, and all of them were undone.
  `⚠ not everything was undone` — look at what is left and tell the user.
- **Figma has one Cmd+Z for the whole file**: it undoes the last step, whoever made it.
- **`… took back a step that was not the script's`** — Figma reported changes during the script that were
  not its own, and the undo meant for them took back someone else's step. Only the user can bring it back:
  ask them at once to press Cmd+Shift+Z in Figma.
- **`⚠ N changed in the file while the script ran … not by the script`** — the code only reads, so Figma or
  someone in the file made those changes, and nothing was rolled back. In a large file Figma updates
  instances on a page the first time a script loads it; the same script once more gives a clean answer.
- **`undo` remembers scripts only until the plugin restarts.**

## Pictures

- **`shot` captures what Figma shows**: a layer that sticks out of a frame with `clipsContent` comes out cut.
- **A picture takes in what sticks out of the layer**: a shadow, an outside stroke, the children of a frame
  that doesn't clip. A 200×100 card with a shadow comes out 248×148, and a text layer is cut to its letters
  (a 300×40 box holding "Hi" gives 10×9). `--absolute` (`h.shot(node, {absolute: true})`) gives exactly the
  layer's own box in every format: for a picture that goes into another mockup or must match the layer.
  To check that nothing sticks out, shoot without it.
- **Don't shoot a whole long page** — shoot it in sections: each one reads better. The CLI cuts a tall layer
  into parts of up to 1600 px.
- **Don't group or ungroup layers for an export**: that once dropped sections out of their frame. `shot`
  changes nothing in the file.
