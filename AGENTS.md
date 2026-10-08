# Figaro — notes for contributors and coding agents

Figaro lets AI agents work in Figma Desktop. `figaro.py` (the CLI) sends a script over HTTP to `bridge.py`
(aiohttp on 127.0.0.1:8788), and the bridge passes it over a WebSocket to the Figma development plugin in
`plugin/` (`code.js` runs in Figma's sandbox, `ui.html` is the plugin's bar). The plugin runs the script as
`new Function("figma", "print", "h", body)`. Each Figma file has its own queue: one script at a time per
file, so parallel agents never interleave their edits.

How agents *use* Figaro is the skill in `skill/figaro/` (`SKILL.md` and `references/`), in the open Agent
Skills format. `tools/install.sh` (macOS, Linux) and `tools/install.ps1` (Windows) install the `figaro` command
and link the skill into `~/.claude/skills` (Claude Code) and `~/.agents/skills` (Codex and other agents).

## Setting Figaro up for someone

When a user gives you this repository's link and asks you to set it up:

1. Clone it into `$HOME/figaro`, unless they name another folder; if it is there already, `git pull` in it
   instead. (In PowerShell write `$HOME`: `~` in an argument of `git` makes a folder named `~`.)
2. Run the installer. It is safe to run again, and it never overwrites what it didn't make.
   - macOS, Linux: `bash $HOME/figaro/tools/install.sh`
   - Windows, from PowerShell or Git Bash: `powershell -ExecutionPolicy Bypass -File $HOME/figaro/tools/install.ps1`

   It needs Python 3.9+. On a Mac without it, `xcode-select --install` brings `python3`; on Windows, the
   installer from python.org with *Add python.exe to PATH* ticked; Debian and Ubuntu also need `python3-venv`.
   If the installer prints a note about PATH, follow it; until then call the command by the full path it
   printed.
3. Ask the user to do the two steps only they can do, in Figma Desktop: **Plugins → Development → Import plugin
   from manifest…** with the full path to `plugin/manifest.json`, then **Plugins → Development → Figaro** in
   the file they want to work in. The browser version of Figma can't run development plugins; on Linux the
   desktop app is the unofficial figma-linux.
4. Run `figaro doctor` and fix what it reports, until every line starts with ✓.
5. Tell them that agents get the skill in their next session, and that an agent that runs commands in a
   sandbox has to be allowed to run `figaro` outside it (README, "Let your agent run figaro").

WSL2: install inside WSL with `install.sh`. Figma on Windows reaches the bridge in WSL through `localhost`,
but imports a plugin only from a Windows folder: copy `plugin/` to one (`/mnt/c/Users/<name>/figaro-plugin`)
and import it from there, and copy it again after every update.

## Commands

```sh
python3 -m venv venv && venv/bin/pip install -r requirements-dev.txt   # once after cloning
venv/bin/python -m pytest -q             # bridge, board, update, CLI, queue, fuzz, stress; no Figma needed
/usr/bin/python3 -m venv /tmp/figaro-py39 && /tmp/figaro-py39/bin/pip install -q -r requirements-dev.txt
/tmp/figaro-py39/bin/python -m pytest -q # the same on macOS's own python3 (3.9), the oldest we support
node tests/helpers.test.js               # pure helpers of plugin/code.js on a stub of Figma
node tests/plugin.test.js                # the plugin writes nothing to the file; the document id is the fileKey
node tests/exec.test.js                  # the exec core: change reports, Cmd+Z, read-only, undo, error lines
node tests/commands.test.js              # links, quick reads, --lib, inspect, shot, fonts, auto-layout, find
node tests/ui.test.js                    # the plugin window: islands, buttons; the tone plays only during scripts
node tests/variants.test.js              # h.variant: short names and #ids, "true"/"false"; h.bN with a key

export FIGARO_TEST_FILE="<link to a draft of yours>"   # the live tests need the plugin running in it
venv/bin/python tests/live_exec.py       # the exec core against real Figma, ~15 s
venv/bin/python tests/live_cli.py        # the CLI commands against real Figma, ~30 s
venv/bin/python tests/live_stress.py 20  # the file queue under load, ~10 s

bash tools/install.sh                    # `figaro` in ~/.local/bin, the skill in ~/.claude/skills and ~/.agents/skills
bash tools/install.sh --uninstall        # remove them, only if they are ours
bash start-bridge.sh                     # (re)start the bridge in the background; log in /tmp/figaro-bridge-8788.log
bash start-bridge.sh --stop              # stop it
figaro reload -T "<link>"                # load a changed plugin/ into the running plugin, no re-run by hand
```

On Windows, in PowerShell:

```powershell
python -m venv venv; venv\Scripts\pip install -r requirements-dev.txt
venv\Scripts\python -m pytest -q        # adds tests/test_install_ps1.py and a real start with start-bridge.ps1
powershell -ExecutionPolicy Bypass -File tools\install.ps1   # figaro.exe in %USERPROFILE%\.local\bin (put on PATH), junctions to the skill
powershell -ExecutionPolicy Bypass -File tools\install.ps1 -Uninstall
.\start-bridge.ps1 -Restart              # or -Stop; log in %TEMP%\figaro-bridge-8788.log
```

CI (`.github/workflows/tests.yml`) runs pytest on Linux and Windows with Python 3.9 and 3.13, the Node tests,
and both installers; nothing else tests Windows, so check the run after changing anything it touches.

A smoke test of the skill — a fresh Claude Code session that has only the skill and the command does a small
task in your draft:

```sh
T=$(mktemp -d) && bash tools/install.sh --bin $T/bin --skills $T/proj/.claude/skills && cd $T/proj && \
  PATH="$T/bin:$PATH" claude -p "<a small task with a link to your draft>" --max-turns 60 \
  --allowedTools Bash Read Skill Glob Grep Write --output-format stream-json --verbose > $T/run.jsonl
```

Then read `run.jsonl` (commands, errors, the final report) and clean up what it made in the draft.

## Where things are

- `figaro.py` — the CLI: commands, how replies are printed, `exec` flags (`-R`, `--checkpoint`,
  `--no-checkpoint`, `--lib`, `--shot`).
- `cli_extras.py` — links in arguments, starting the bridge on demand, `inspect`, `shot`, `link`, `--lib`,
  `exec --shot`; `inspect_text.py` — the text `inspect` prints.
- `bridge.py` — HTTP and WebSocket, routing to files, the queue and lock per file, Origin/Host checks, the
  release check, `ERROR_HINTS`.
- `bridge_board.py` — what every plugin window shows (the `board`: each file's agents — at work from their
  first script until five quiet minutes or `figaro done`, its note, a failed last script, the user's Stop —
  and recent changes), its buttons (Stop for one agent, Update and Reload, Reload) and `POST /done`.
- `bridge_update.py` — Update and Reload from the window: waits until no file runs a script,
  `git pull --ff-only`, reloads the plugins that run older code, restarts the bridge (`os.execv`; on Windows
  `start-bridge.ps1 -Restart`). Scripts sent meanwhile get a 503 and run nothing.
- `bridge_exec.py` — the bridge's side of exec: the fields it sends (`readOnly`, `checkpoint`, `quick`,
  `libs` — a library's code goes once, then its hash), when checkpoints are due, links as targets,
  `POST /undo`, `POST /reload`.
- `bridge_idle.py` — the bridge exits after `--idle-exit` with no plugins and no requests; on any exit it
  closes the plugins' connections at once.
- `figma_links.py` — Figma links: the file key and node id in a link, links to layers for reports.
- `plugin/` — `manifest.json`, `code.js` (the `HELPERS` object behind `h.*`, running scripts with a deadline,
  change tracking, undo, selecting a layer from the window), `ui.html` (the window: one island per file).
- `skill/figaro/` — `SKILL.md` (the loop, the commands, safety, house rules) and `references/`:
  `helpers.md`, `craft.md`, `pitfalls.md`. The recipes in them were checked against real Figma.
- `tools/install.sh` — the command and the skill's links (`~/.claude/skills`, `~/.agents/skills`); never
  overwrites what it didn't make. `tools/install.ps1` does the same on Windows: `figaro.exe` is the launcher
  pip makes in the venv from `pyproject.toml` (an editable install, used for nothing else), copied to the bin
  folder — an .exe, because a .cmd file would hand Figma links to cmd.exe, which cuts them at `&`. The skill
  links are junctions, which need no admin rights.
  `start-bridge.sh` / `start-bridge.ps1` — start and stop the bridge (tmux or nohup; per-port session, log
  and pid file).
- `tests/` — pytest (`test_*.py`), Node (`*.test.js`), live (`live_*.py`, with `live_file.py`), and
  `plugin_fingerprint.json`.
- `assets/banner.svg` — the README's banner, drawn by `assets/banner.py` (run it after changing the
  letters or the colours).
- `assets/hero.png` — the README's picture: the plugin's real window (`plugin/ui.html` with a made-up board)
  and notes pointing at it, shot at 2x; its corners match the banner's. Redo it when the window changes.

## Rules

- **Changed a command, a helper or a behaviour — update the skill in the same commit.** `tests/test_install.py`
  checks that the skill names every CLI command. The installed skill is a symlink to `skill/figaro/`, so an
  uncommitted edit there is live in every agent's session at once: don't leave it half done.
- **The skill is for any agent.** Claude Code, Codex, Cursor and others read the same `SKILL.md`, so it names
  no agent's own tools: "open the PNG", not "open it with Read".
- **Any change to `plugin/code.js` or `plugin/ui.html`** → bump `PLUGIN_VERSION` in `plugin/code.js` (date
  and counter, `2026-10-08.1`) and add its line to `tests/plugin_fingerprint.json` — the failing
  `pytest tests/test_plugin_version.py` prints it. Without that the bridge can't tell that Figma runs an
  older plugin. A changed `manifest.json` has to be imported in Figma again.
- **`plugin/ui.html` passes the exec fields to the sandbox by name.** A new field in
  `bridge_exec.exec_fields` must be added there too, or it never arrives; `tests/test_exec.py` catches it.
- **The plugin never writes to the document.** The document id is `figma.fileKey`, which a development
  plugin gets thanks to `"enablePrivatePluginApi": true`; `tests/plugin.test.js` checks that nothing calls
  `setPluginData` on the file. Figaro runs in shared libraries too.
- **`quick` is for the CLI's built-in readers only.** The plugin doesn't track what a quick script changes,
  so code from outside must never run as `quick`. A `-R` script costs ~150 ms more and is safe.
- **Python 3.9** — the `python3` of Apple's Command Line Tools, what a Mac without Homebrew runs. No `match`,
  no `zip(strict=…)`, no nested same-kind quotes inside f-strings (3.12+); `X | None` only in annotations of
  modules with `from __future__ import annotations`. The 3.9 run in Commands checks it.
- **`start-bridge.ps1` and `tools/install.ps1` stay pure ASCII:** Windows PowerShell 5.1 reads a BOM-less
  script as ANSI (`tests/test_launch.py` checks). In them, don't redirect a program's stderr while
  `$ErrorActionPreference` is `Stop`: 5.1 turns it into an error that stops the script.
- **Experiment only in a draft of your own.** Figma has one Cmd+Z for the whole file, and a script can touch
  hundreds of layers. The live tests take the file from `FIGARO_TEST_FILE`.
- **Never stop the bridge with `lsof -ti tcp:8788 | xargs kill`:** that list includes Figma itself, which
  holds a connection to the bridge. Find the listener (`lsof -nP -iTCP:8788 -sTCP:LISTEN`), check that it is
  `bridge.py`, and stop only that PID — or `bash start-bridge.sh --stop`.
- **Code from other projects:** MIT code may be reused with its copyright line kept. Figma's official skills
  have no license — take ideas from them, not text.

## Releasing

1. Move the notes under `## [Unreleased]` in `CHANGELOG.md` to a new `## [X.Y.Z] — YYYY-MM-DD` section.
2. Set `VERSION` in `bridge.py` and `version` in `pyproject.toml` to `X.Y.Z` (`tests/test_bridge.py` checks
   both against the changelog).
3. Commit, then tag and push: `git tag vX.Y.Z && git push origin main vX.Y.Z`.
4. Publish the release on GitHub with its changelog section as the notes, each bullet on one line (release
   notes turn every line break into a break on the page):

   ```sh
   awk '/^## \[X.Y.Z\]/ {f = 1; next} /^## \[/ {f = 0} f' CHANGELOG.md | perl -0pe 's/\n +(?=[^\s-])/ /g' |
     gh release create vX.Y.Z --title "Figaro X.Y.Z" --notes-file -
   ```

Every six hours a running bridge reads this repository's `vX.Y.Z` tags from GitHub (`REPO` in `bridge.py`)
and, when there is a newer one, the plugin bar offers an Update button that opens the release. A private
repository answers 404, and the check stays silent.

## Figma facts that cost time

- **Hot reload.** With Figma's *Plugins → Development → Hot reload plugin* on, any write to `plugin/code.js`
  or `ui.html` (even `touch`) restarts the plugin in every file within a second. A running script is cut off
  ("plugin disconnected mid-request") and `undo` forgets everything. Don't touch `plugin/` while agents work.
- **The window is 320 px wide and as tall as its islands**: `ui.html` measures itself and `code.js` calls
  `figma.ui.resize`. The header above it (icon, title, close button) is Figma's: a plugin can put nothing
  there, and its title changes only with a new `figma.showUI`, which restarts the window.
- **The keep-awake tone.** While scripts run, the window plays an inaudible tone (20 Hz, −66 dBFS); without it
  Chromium throttles a background Figma to up to a minute per step. Meanwhile macOS doesn't sleep
  (`pmset -g assertions` shows Figma "Playing audio"). The tone stops 3 minutes after the last script or
  ~2 s after the bridge goes away — that is why the bridge closes the plugins' connections on exit
  (`bridge_idle.close_plugins`).
- **`getNodeByIdAsync` for a layer inside an instance** (`I2:1068;2:1041`) once hung for 10 s ("Unable to
  establish connection") while `figma.getNodeById` found it. So `h.node` and the script's
  `figma.getNodeByIdAsync` ask synchronously first (`nodeHere`).
- **A Figma call that never answers ends at the deadline.** Every `*Async` call through the script's `figma`
  races `makeGate`: on `--timeout` or abort the script fails with "did not answer before --timeout ran out",
  and the bridge waits `GATE_GRACE` (1 s) more, so the agent gets that error rather than a 504 and the file
  isn't blocked. Node methods (`getMainComponentAsync`) aren't covered. `figma.teamLibrary` and import by key
  are the usual culprits; in a draft, import by key may fail even for what the file already uses.
- **`documentchange` doesn't see variables:** `-R` can't roll their changes back, and `undo` refuses after a
  script that may have changed them.
- **Deleting a component or a component set isn't reported** in `documentchange`: it is missing from the
  report, `-R` can't roll it back, and `undo` refuses after a script that only deleted components
  (`.remove()` is in `VARIABLE_WRITES`).
- **Every `addComponentProperty` is a separate Cmd+Z step.** `undo` and `-R` go back step by step while
  traces of the script remain — created layers present, deleted ones absent (`revertSteps`) — and never past
  a step that doesn't touch its layers. Properties of an existing component can't be checked this way; the
  answer warns.
- **`undo` walks back through the scripts of this plugin run**, whoever ran them under the same `-A`. A second
  `undo` may undo someone else's cleanup — read "can still undo".
- **Checkpoints go to the file's version history**, visible to everyone with access. The time of the last one
  per file is kept in `~/.cache/figaro/checkpoints.json`; the tests use a temporary folder
  (`tests/conftest.py`).
- **`figaro reload` into a build without `reload` in its `caps`** runs as an ordinary script; on an error or a
  504, restart the plugin by hand.
- **The CLI starts the bridge** when nothing listens on 8788 (`cli_extras.autostart`) and waits up to 6 s for
  the plugin. A background Figma window can take a minute to connect — then the first call says "run it
  again". `FIGARO_AUTOSTART=0` keeps the bridge stopped. The bridge exits after 3 idle hours.
- **`shot` captures what Figma shows:** a layer that sticks out of a frame with `clipsContent` comes out cut.
  Cutting tall pictures into parts needs Pillow; without it there is one file and a warning.
- **Figma's official skills call `figma.createAutoLayout()`, `node.query()` and `node.set()`** — APIs of their
  MCP server only, not the public Plugin API. Check against the Plugin API typings before porting a recipe.
