---
name: figaro
description: >-
  Work in Figma Desktop through Figaro, the local bridge that runs Figma Plugin API scripts in a file open in
  Figma: read layers (inspect, find, tree), take pictures of them (shot), build and edit frames, components
  and text, use the design system's components, styles and variables, undo a step. Use for any task that reads
  or changes a Figma file: a figma.com link, "in Figma", a mockup, a frame, a component, variants, "draw it in
  Figma", "move this to Figma", "look at the design", "shoot this layer". Writing to Figma goes only through
  Figaro.
---

# Figaro — working in Figma

`figaro` reads and changes a file open in Figma Desktop. The command sends JS (the Figma Plugin API) to the
bridge on `127.0.0.1:8788`, and the bridge hands it to the Figaro plugin running in that file. Each file has
its own queue: one script runs in it at a time, so several agents never mix up their changes.

The references next to this file — read each one when you reach its step:
- `references/helpers.md` — the `h.*` helpers, the script environment, code recipes;
- `references/craft.md` — how to build well: house rules, auto-layout, tokens, components, text;
- `references/pitfalls.md` — errors and traps, with fixes.

## 1. The file

- The user gives a Figma link — pass it as is: `-T "<link>"` in every call, or a layer link in place of an
  id. Always quote links: without quotes zsh breaks on the `?`.
- No link: `figaro targets` lists the connected files (name, fileKey). `-T` takes a key, a link or a name.
  Without `-T` a call goes to the only connected file, and with several connected it fails with 409. So
  always `-T`.
- The file is not connected — the answer says "open <link> in Figma and run Plugins → Development → Figaro
  there". Pass that on to the user and wait; don't poll in a loop. `figaro doctor -T "<link>"` shows where
  the chain breaks.
- "This frame", "the selection" → `figaro sel -T …`; don't ask for ids.
- The CLI starts the bridge by itself. Don't start it in background tasks, and don't stop it unless the user
  asks (then `bash start-bridge.sh --stop` in the Figaro folder).
- Name yourself: `-A <name>` in every call, a short role such as `-A designer`. The user sees it in the
  plugin's window and in the file's queue, and `undo` undoes only what is yours.

## 2. The loop: look → change → look

1. **Look at what is there.** `inspect` the layer you work on and its neighbours; the styles, variables and
   components of the file and its libraries (`craft.md`, "What is already there"). Use what exists.
2. **Change it in one script** (`exec`, section 4). One script is one action on the file and one Cmd+Z step.
3. **Check the report.** After a script the CLI prints `changed: +3 −1 ~14 (fills, characters)` and
   `created: "Card" <link>` — is that what you meant to change?
4. **Look at the picture.** `figaro shot <layer>` after building and after every fix, then open the PNG it
   prints: spacing, alignment, cut-off text and fonts show only in a picture. Numbers (sizes, spacing) come
   from `inspect` or an `-R` script. If you can't open images, say so and ask the user to look.
5. **Fix in place.** Don't rebuild what is already right.
6. **Stop for review** after the first component or block: a link, a shot, what was done. Leave a note in
   Figma too, `figaro wait "Check the card, then answer in the chat"`: the user may be looking at Figma,
   not at the chat. Go on once the user answers.

## 3. Commands

| Command | What it does |
|---|---|
| `figaro inspect <layer> [--depth N]` | everything about a layer: auto-layout, sizes, padding, radii, fills with style and variable names, texts with their styles and segments, instances with their component and properties; at the end, the keys of the components, styles and variables it uses. `--json` or `-o file.json` for data |
| `figaro shot <layer>… [--width N] [--format svg]` | a picture in `/tmp/figaro/shots/<file key>/`; prints the path — open the PNG to see it. A tall layer comes in parts `-p1`, `-p2`…; SVG turns text into outlines, `--svg-text` keeps it text |
| `figaro tree <layer> [--depth 2] [--layout]` | the layer tree: name, type, id, size; `--layout` adds auto-layout |
| `figaro find <layer> name=X` · `name~X` · `type=INSTANCE` · `text~X` | search inside a layer; `--hidden` includes layers hidden in instances |
| `figaro sel` | what the user has selected, with links |
| `figaro link <layer>…` | links to layers for a report (`sel` — to the selected ones) |
| `figaro exec …` | your script (section 4) |
| `figaro text <layer> "text"` | new text for a layer; loads the fonts itself |
| `figaro variant <instance> "Size=L" "Label=Pay now" "Icon=false"` | an instance's variant and properties; short or full names (`Label#12:0`) |
| `figaro clone <layer> --right [--gap 100] [--name N]` | a copy next to the layer: `--left`, `--up`, `--down` |
| `figaro icomp <key>` | an instance of a published library component on the current page |
| `figaro rm <layer>…` | delete; finds every id first, so one wrong id deletes nothing |
| `figaro undo` | undo the file's last script, when that is safe (section 5) |
| `figaro reload` | restart the plugin in a file with the code on disk, after an update |
| `figaro wait "<what to check>"` | a note for the user in the plugin's window: the file's island turns amber with it until your next script in the file. It doesn't wait for an answer: ask in the chat too, then end your turn |
| `figaro targets` · `doctor` · `status` | the connected files · a connection check with advice · the bridge's raw state |
| `figaro clear` | unblock a file after a 504 (`pitfalls.md`) |

`<layer>` is an id (`12:34` or `12-34`), a layer link, `sel` (the selection) or `page` (the current page). Quote
ids with a `;` (`I12:3;4:5`). Every command but `targets` and `status` takes `-T`, `-A`, `--timeout` (seconds)
and `--raw` (the whole JSON answer).

The readers `inspect`, `shot`, `tree`, `find`, `sel`, `link` and `doctor` change nothing and answer within
milliseconds. `text`, `variant`, `clone`, `icomp` and `rm` write to the file, as `exec` does. Without `figaro`
on PATH: `<Figaro folder>/venv/bin/python <Figaro folder>/figaro.py …` (on Windows, `venv\Scripts\python`).

## 4. The script

```sh
figaro exec -T "<link>" --stdin <<'JS'
const parent = await h.node("12:34");
const card = h.frame(parent, { layout: "V", spacing: 16, padding: 24, name: "Card" });
const title = figma.createText();
card.appendChild(title);
await h.setText(title, "Payouts");
return { id: card.id };
JS
```

- A script is the body of an async function. It gets `figma`, `h` (`helpers.md`), `print(...)` (`log:` lines
  in the answer) and `lib` (`--lib`). Its result is what `return` gives back: ids, names and numbers, not
  nodes or whole trees.
- `<<'JS'` in quotes keeps the shell away from `$` and backticks in the code. Or use `-f script.js`: when the
  user allowed `figaro` by a command rule (Codex), a plain `figaro …` line runs without asking and a heredoc
  doesn't. PowerShell has no heredoc, and its pipe loses letters outside ASCII: there, always use `-f`.
- One script is one transaction: read and change in the same call. Between two calls the user or another
  agent may change the file.
- Keep a script under ~10 s: it holds the file, and everyone else waits. Bulk edits go in batches of ~25
  layers, with `h.ck()` on every step of a long loop (`helpers.md`, "Environment"). Don't wait inside a script
  with `setTimeout`.
- `-R` — the script only reads: if it changed anything, the change is rolled back and the call fails. Run
  every script that only looks with `-R`; several of them in a row can also take `--parallel` and skip the
  queue.
- `--shot` — a picture right after the script: of the layer whose id the script returned (`return { id }`),
  or else of what it created. Put it after `--stdin` or `-f`, not before inline code, or it takes the code for
  an id.
- `--lib my.js` — your own functions for several scripts (`lib.<name>`). The plugin keeps the file compiled:
  don't paste the library into every script.
- An error prints `line N: <the script's line>` and `hint:` — read the hint, it knows the common cases.
- A script that failed leaves in the file whatever it did before the error (`changed:` above the error): run
  `figaro undo` and then the fixed script, or add only what is missing. The same script once more makes a
  duplicate.

## 5. Safety

- **A checkpoint is saved by itself**: before the first script that writes to the file, then once an hour —
  a version "Figaro · before changes by …" in File → Version history, which everyone with access to the file
  sees. Before a risky bulk edit add `--checkpoint "before <what>"`.
- **`figaro undo -T …`** undoes the file's last script only if it changed something, nothing in the file has
  changed since, and the script is yours. Otherwise it refuses — then tell the user: Cmd+Z in Figma, or a
  version from the history. A `⚠` warning after `undone:` — read it and check the file.
- **Figma does not report changes to variables, nor deleted components**: the report won't show them, `-R`
  won't roll them back, and `undo` after such a script refuses. Check variables in the script itself, before
  and after; don't delete components or component sets unless the user asks.
- **Shared libraries (a team UI kit) are read-only**, even when the task is about the kit: build in a working
  file or a draft and stop for review — what goes into the library is the user's call. Never publish. What
  the file already uses from the kit, take by id from `inspect`; the rest by key (`helpers.md`).
- **Don't delete or recreate layers that have comments**: a comment is pinned to the layer's id. Put new
  things inside.
- **Leave the user's screen alone**: `figma.setCurrentPageAsync`, `figma.viewport.*` and
  `figma.currentPage.selection = …` switch their page, camera and selection. Put a new node straight into its
  parent (`h.frame(page, …)`, `page.appendChild(node)`) and show the result with a link in the report.
- **The user may edit the file while your script runs**, and their changes land in your report. Keep scripts
  short.
- **The user can press Stop** in the plugin's window. Your script ends, and the call fails with `stopped:
  the user pressed Stop…` (409); what it changed before that stays in the file. Don't run it again: ask the
  user what to do.
- **504** — the script may still be running and writing. Don't retry blindly: look at the file first
  (`pitfalls.md`).
- **Don't edit Figaro's own code from another project**: with Figma's Hot reload on, a write to its `plugin/`
  folder restarts the plugin in every file within a second and cuts other agents' scripts short. Found a
  bug — describe it to the user.

## 6. House rules

In detail, with recipes — `references/craft.md`. In short:

1. Use what exists: the components, styles and variables of the file and its libraries, the UI already built.
   Don't invent new tokens or components unless asked.
2. Variants only for real differences: style, size, state. The rest are properties: BOOLEAN (show an icon),
   TEXT (a label), INSTANCE_SWAP (which icon), nested instances.
3. Bind colours, spacing and radii to variables and styles as you build. A hex only where there is no
   token — not "hard-code first, bind later".
4. Text as in the source: text styles, with bold, italic, code and links inside the line.
5. An even grid, neighbours of one size, real text instead of "TBC".
6. Icons the way the file draws them: icon components or an icon font. A broken icon-font glyph — change the
   font, keep the glyph name.
7. A one-off state — detach the instance and edit inside it, no overlays on top.
8. One component or block first — then stop for review.
9. Comments in Figma only when the user asks for them.

## 7. The report

Short, in the user's language: what was done, links to the layers (the `created:` line or `figaro link`), a
shot, what is left. If something didn't work, say so.
