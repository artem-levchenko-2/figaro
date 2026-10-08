<div align="center">

<img src="assets/banner.svg" alt="Figaro" width="100%">

# Figaro

### Put your coding agent to work in Figma

*Give Claude Code a link to a frame and a task. It reads the real file, builds
with your components, styles and variables, checks a picture of what it made,
and stops for your review. Everything runs on your Mac.*

**Figaro** is the barber of Seville, the fixer the whole town calls for at
once: *Figaro here, Figaro there.* This one answers in every Figma file you
open.

[![License](https://img.shields.io/badge/license-MIT-64748b?style=flat)](LICENSE)
[![Figma Desktop](https://img.shields.io/badge/Figma-Desktop-a259ff?style=flat)](https://www.figma.com/downloads/)
[![macOS](https://img.shields.io/badge/macOS-ready-0acf83?style=flat)](#install)
[![Python](https://img.shields.io/badge/python-3.9%2B-1abcfe?style=flat)](#install)
[![Claude Code](https://img.shields.io/badge/Claude_Code-skill_included-f24e1e?style=flat)](#your-first-task)

&nbsp;

[![Install](https://img.shields.io/badge/⬇_Install-a259ff?style=for-the-badge)](#install)
[![First task](https://img.shields.io/badge/💬_First_task-1e1e1e?style=for-the-badge)](#your-first-task)
[![Report a bug](https://img.shields.io/badge/🐞_Report_a_bug-1e1e1e?style=for-the-badge)](https://github.com/artem-levchenko-2/figaro/issues/new)
[![Changelog](https://img.shields.io/badge/📋_Changelog-1e1e1e?style=for-the-badge)](CHANGELOG.md)

</div>

## Why

### Screenshots show an agent the design. Figaro hands it the file.

An agent that only sees a picture has to guess the spacing, the fonts and the
component behind every button. Figaro lets it work inside the Figma file you
have open, with the same Plugin API a Figma plugin gets. It reads the real
auto-layout, styles and variables, and builds with them.

- 🔗&nbsp; **Paste a link, get to work.** Copy a link to any layer and hand it
  to your agent. Every command takes Figma links.
- 👀&nbsp; **It looks before and after.** The agent reads a layer down to its
  tokens and component keys, and takes a picture of what it built to check it.
- 🧩&nbsp; **It builds the way your team does.** Real components and variants,
  your text styles and variables instead of hex codes, auto-layout instead of
  loose frames. The skill that comes with Figaro teaches the agent these rules.
- 🛟&nbsp; **It is hard to break things.** Before the first change Figaro saves
  a version in the file's history, every script reports what it changed, and
  the last one can be undone.
- 👯&nbsp; **Several agents, several files.** Each file has its own queue, so
  agents working side by side never mix up their changes.
- 🏠&nbsp; **It stays on your Mac.** No Figma token, no account, no cloud: a
  small local server and a plugin you import once.

Free and open source, for Figma Desktop on macOS. Windows works too, with less
testing.

---

## Contents

- ⬇️&nbsp; [Install](#install)
- 💬&nbsp; [Your first task](#your-first-task)
- 🧰&nbsp; [What it can do](#what-it-can-do)
- 🛟&nbsp; [Safety net](#safety-net)
- ⚙️&nbsp; [How it works](#how-it-works)
- 🩺&nbsp; [If something is off](#if-something-is-off)
- 🔒&nbsp; [Privacy](#privacy)
- 🛠️&nbsp; [Development](#development)

---

## Install

You need a Mac with **[Figma Desktop](https://www.figma.com/downloads/)**. The
browser version of Figma can't run development plugins. Figaro also uses Git
and Python 3.9+, which come with Apple's developer tools: if `git --version`
answers in Terminal, you're set, and if not, `xcode-select --install` adds them.

**1. Get Figaro.** Paste this into Terminal:

```sh
git clone https://github.com/artem-levchenko-2/figaro.git ~/figaro
bash ~/figaro/tools/install.sh
```

If it says `~/.local/bin` is not on your PATH, run the line it prints.

**2. Add the plugin to Figma.** Once, in Figma Desktop: **Plugins → Development
→ Import plugin from manifest…**, then pick `~/figaro/plugin/manifest.json`.
In the file dialog, ⇧⌘G lets you type the path.

**3. Run it in your file.** Open the file and choose **Plugins → Development →
Figaro**. A slim bar appears and shows the file's name once it's connected.
Keep it open while you work; ⌥⌘P runs it again next time.

**4. Check it.** In Terminal:

```sh
figaro doctor
```

Every line should start with ✓. If one doesn't, it says what to do.

That's all. Now give your agent its [first task](#your-first-task).

<details>
<summary>🧭 <b>What the installer does</b></summary>

<br>

- Makes `~/figaro/venv` with Figaro's two Python packages, `aiohttp` and
  `Pillow`.
- Writes the `figaro` command to `~/.local/bin`. It runs the code in
  `~/figaro` from any folder.
- Links the agent skill into `~/.claude/skills/figaro`, so every Claude Code
  session on your Mac has it.

Running it again is safe: it fixes only what is missing, and it never
overwrites a file it didn't make. If Terminal can't find `figaro`, put
`~/.local/bin` on your PATH:

```sh
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc && exec zsh
```

</details>

<details>
<summary>🔄 <b>Update or remove</b></summary>

<br>

```sh
cd ~/figaro && git pull
bash start-bridge.sh                 # restart the bridge with the new code
figaro reload -T "<link to a file>"  # load the new plugin code into a running plugin
```

If `plugin/manifest.json` changed, import the plugin again (step 2). When a new
release is out, the plugin's bar says so.

To remove Figaro, run `bash ~/figaro/tools/install.sh --uninstall`, remove the
plugin under **Plugins → Development → Manage plugins in development**, and
delete `~/figaro`.

</details>

<details>
<summary>🪟 <b>Windows</b></summary>

<br>

The bridge and the command are plain Python, and `start-bridge.ps1` starts the
bridge on Windows. There is no installer, so set it up by hand in PowerShell,
in the folder you cloned:

```powershell
python -m venv venv; .\venv\Scripts\pip install -r requirements.txt
.\start-bridge.ps1
.\venv\Scripts\python figaro.py doctor
```

For Claude Code, copy `skill\figaro` to `%USERPROFILE%\.claude\skills\figaro`.
Windows gets less testing than macOS.

</details>

---

## Your first task

In Figma, right-click a layer and choose **Copy link to selection** (⌘L). Then
open Claude Code in any folder and ask for something with that link:

| You ask | The agent |
| :-- | :-- |
| *Look at ‹link› and tell me what's inconsistent* | Reads the frame's auto-layout, styles and variables, and answers with links to the layers it means |
| *Build a Tag component next to ‹link›: our palette's colours, an optional icon. Stop when the first one is ready* | Builds it from your variables and text styles, checks a picture of it, and stops for your review |
| *Fill the table in ‹link› with twenty realistic orders* | Writes the texts, loading their fonts, and keeps the table's styles |
| *Tidy up the layer names in ‹link›* | Names each layer for what it is, and reports every change |

The agent works in a loop: it looks, changes the file in one script, reads the
report of what changed, checks a picture of the result, and stops with links
for your review. Try your first tasks in a draft.

<details>
<summary>👯 <b>Several agents in one file</b></summary>

<br>

Each file has a queue, so scripts from different agents run one at a time and
never mix. Give each agent a name with `-A designer` or
`FIGARO_AGENT=designer`: the name shows in the queue, and `figaro undo` takes
back only that agent's work.

</details>

<details>
<summary>🤖 <b>Other agents</b></summary>

<br>

Any agent that can run shell commands can use Figaro: Codex, Cursor, Gemini CLI
or a script of your own. Point it at
[`skill/figaro/SKILL.md`](skill/figaro/SKILL.md), the playbook Claude Code
reads.

</details>

<details>
<summary>📚 <b>What the skill teaches</b></summary>

<br>

[`SKILL.md`](skill/figaro/SKILL.md) is the playbook: look, change, look again,
and stop for review. Its `references/` hold the
[helpers](skill/figaro/references/helpers.md), the
[craft rules](skill/figaro/references/craft.md) (real components, tokens
instead of hex, an even grid) and the
[pitfalls](skill/figaro/references/pitfalls.md), each with its fix.

</details>

---

## What it can do

Your agent drives Figma through the `figaro` command, and so can you. Here it
reads a card, changes a title and takes a picture:

```console
$ figaro inspect "https://www.figma.com/design/AbCd/Shop?node-id=12-34"
"Card" FRAME 12:34 in "Shop" · 3 layers
https://www.figma.com/design/AbCd/Shop?node-id=12-34

Card [FRAME] 12:34 320×200 · ↓ gap 12 pad 24 16 · r 12 · fill 'Card' #ffffff (bg/card)
  Title [TEXT] 12:35 288×24 · "Order summary" · Inter Semi Bold 20/24 · style 'H3'
  Button [INSTANCE] 12:36 120×40 · ⟐ Button / Size=M · Label="Pay now", Size="M"
…

$ figaro text 12:35 "Your order"
  changed: ~1 (characters)
  checkpoint: "Figaro · before changes by an agent" — File → Version history
{
  "id": "12:35",
  "before": "Order summary",
  "after": "Your order"
}
  (38ms)

$ figaro shot 12:34
/tmp/figaro/shots/AbCd/12-34.png  640×400 · "Card" 2×
```

| Command | What it does |
| :-- | :-- |
| `figaro inspect <layer>` | Reads a layer: layout, styles, variables, keys |
| `figaro shot <layer>` | Saves a picture of it for the agent to open |
| `figaro exec` | Runs a script with the whole Plugin API |
| `figaro text <layer> "…"` | Changes a text, loading its fonts |
| `figaro undo` | Takes back the last script, when that is safe |
| `figaro doctor` | Checks the bridge, the plugin and the file |

A `<layer>` is a link, an id such as `12:34`, `sel` for your selection, or
`page` for the current page. When the plugin runs in more than one file, `-T`
picks the file by its link, its key or a part of its name.

<details>
<summary>📖 <b>Every command</b></summary>

<br>

```text
Look
  figaro inspect <layer>          everything about a layer; --json for data
  figaro shot <layer>…            a PNG of each layer, tall ones in parts; --format svg
  figaro tree <layer>             the layer tree; --layout adds auto-layout
  figaro find <layer> name~Card   search by name=, name~, type=, text= and text~
  figaro sel                      what you have selected, with links
  figaro link <layer>…            links to layers

Change
  figaro exec …                   run a script (see Scripts below)
  figaro text <layer> "New text"  set a text, with its fonts loaded
  figaro variant <instance> "Size=L" "Label=Pay now"
                                  switch variants and set properties by short names
  figaro clone <layer> --right    a copy beside it; --left, --up, --down, --gap
  figaro icomp <key>              an instance of a published library component
  figaro rm <layer>…              delete layers; one wrong id and nothing is deleted
  figaro undo                     take back the file's last script, when that is safe

Keep it running
  figaro doctor                   check the bridge, the plugin and the file
  figaro targets                  the files where the plugin runs
  figaro status                   the bridge's raw state, as JSON
  figaro reload                   load new plugin code into a running plugin
  figaro clear                    free a file after a script ran out of time
```

`figaro <command> --help` lists every option.

</details>

<details>
<summary>🧪 <b>Scripts: the whole Plugin API</b></summary>

<br>

A script is the body of an async function. It gets `figma` (the
[Plugin API](https://www.figma.com/plugin-docs/)), `h` (Figaro's helpers),
`print(…)` and `lib` (your own `--lib` files), and whatever it returns comes
back to you.

```sh
figaro exec -T "<link to the file>" --stdin --shot <<'JS'
const page = await h.node("page");
const card = h.frame(page, {
  layout: "V", w: 360, spacing: 8, padding: 24, radius: 12, name: "Card",
});
const title = figma.createText();
card.appendChild(title);
await h.setText(title, "Hello from Figaro");
await h.wrapText(title);
return { id: card.id };
JS
```

```text
  changed: +2
  created: "Card" https://www.figma.com/design/AbCd/Shop?node-id=88-12
{
  "id": "88:12"
}
  (164ms)
/tmp/figaro/shots/AbCd/88-12.png  720×176 · "Card" 2×
```

- `-R` runs a script read-only: if it changes anything, the change is rolled
  back and the call fails.
- `--shot` takes a picture of what the script returned or created.
- An error comes back with the line of your script that threw and, for known
  problems, a `hint:`.
- The helpers are described in
  [`helpers.md`](skill/figaro/references/helpers.md).

</details>

<details>
<summary>⚙️ <b>Settings</b></summary>

<br>

Everything works without settings. When you need them:

| Variable | Default | What it does |
| :-- | :-- | :-- |
| `FIGARO_AGENT` | — | The caller's name in the file's queue, the same as `-A` |
| `FIGARO_AUTOSTART` | `1` | `0` keeps the command from starting the bridge |
| `FIGARO_IDLE_EXIT` | `3h` | How long the bridge waits with no plugin and no requests before it exits |
| `FIGARO_LIB` | — | `--lib` files for every `exec`, separated by `:` |
| `FIGARO_SHOTS` | `/tmp/figaro/shots` | Where `shot` saves pictures |
| `FIGARO_PLUGIN_WAIT` | `6` | Seconds the command waits for the plugin after starting the bridge |
| `FIGARO_STATE_DIR` | `~/.cache/figaro` | Where the time of each file's last checkpoint is kept |
| `FIGARO_NO_UPDATE_CHECK` | — | `1` stops the bridge from checking GitHub for a new release |

The plugin talks to port 8788 only, as its manifest allows nothing else, so
keep the bridge there.

</details>

---

## Safety net

Figaro works in your real files, and Figma has one ⌘Z for the whole file. So it
keeps four promises:

- **A version before the first change.** Before a script first changes a file,
  and then every hour, Figaro saves a version in **File → Version history**,
  named *Figaro · before changes by …*
- **A report after every script.** What was created, deleted and changed, with
  links to the new layers.
- **Read-only when you ask.** If a script run with `-R` changes anything, the
  change is rolled back and the call fails.
- **An undo that knows when to refuse.** `figaro undo` takes back the last
  script only if it is yours and nobody has changed the file since. Otherwise
  it says so and points you to ⌘Z or the version history.

<details>
<summary>🔍 <b>The fine print</b></summary>

<br>

| Guard | What it does |
| :-- | :-- |
| One&nbsp;script&nbsp;at&nbsp;a&nbsp;time | Scripts in one file wait their turn. A script that runs out of time keeps the file until it ends, instead of racing the next one |
| Local&nbsp;only | The bridge listens on `127.0.0.1` and turns away requests from web pages: it checks `Origin` and `Host` |
| A&nbsp;version&nbsp;on&nbsp;demand | `--checkpoint "before the grid"` saves one before a risky script |

Two things Figma itself doesn't report: changes to **variables** and
**deleted components**. They are missing from the change report, `-R` can't
roll them back, and `undo` refuses after a script that made them.

</details>

---

## How it works

Three small pieces, all on your Mac. When your agent runs `figaro shot sel`:

1. **The `figaro` command** turns it into a few lines of JavaScript and sends
   them to the bridge.
2. **The bridge**, a small server at `127.0.0.1:8788`, finds the file, waits
   for that file's turn and passes the script on. The command starts the bridge
   when it isn't running, and the bridge leaves by itself after three idle
   hours.
3. **The Figaro plugin** in that file runs the script with Figma's Plugin API,
   notes what changed and sends back the answer, usually in a fraction of a
   second.

<details>
<summary>🔌 <b>Under the hood</b></summary>

<br>

- The command talks to the bridge over HTTP, and the bridge to each plugin over
  a WebSocket. Every file where you run the plugin has its own connection and
  its own queue.
- The plugin is a development plugin: you import it from your copy of Figaro,
  nothing is published, and no Figma token is involved. It knows a file by the
  file's key, so it never writes anything of its own into your design.
- While scripts run, the plugin's bar plays a tone you can't hear. Without it,
  Figma throttles a window in the background, and each step can take up to a
  minute. The tone stops three minutes after the last script, or as soon as
  the bridge stops.
- The bridge and the command are Python, with `aiohttp`. The plugin is plain
  JavaScript and HTML, in `plugin/`.

</details>

---

## If something is off

Start with `figaro doctor`. It checks the bridge, the plugin and the file, and
at the first broken link it says what to do.

<details>
<summary>🩺 <b>Common problems and their fixes</b></summary>

<br>

| Problem | What to do |
| :-- | :-- |
| **Plugin&nbsp;not&nbsp;connected** | Run **Plugins → Development → Figaro** in that file. `figaro doctor` names the broken link |
| **"Specify&nbsp;a&nbsp;target"** | The plugin runs in several files, so add `-T "<link>"`. `figaro targets` lists them |
| **First&nbsp;call&nbsp;fails** | The plugin hasn't connected yet: a Figma window in the background can take up to a minute. Run the command again |
| **504,&nbsp;out&nbsp;of&nbsp;time** | It may still be running, so look at the file before you retry. If the file stays blocked after the script has ended, run `figaro clear -T <file>` |
| **"Older&nbsp;build"** | The plugin in that file runs older code. Run `figaro reload -T <file>`, or run the plugin again |
| **zsh:&nbsp;no&nbsp;matches&nbsp;found** | Put the link in quotes |
| **Mac&nbsp;won't&nbsp;sleep** | Figma "plays audio": the silent tone that keeps a background Figma quick. It stops three minutes after the last script, or as soon as the bridge stops |
| **Port&nbsp;8788&nbsp;taken** | `lsof -nP -iTCP:8788 -sTCP:LISTEN` shows who listens. Stop only a `bridge.py` you started, with `bash start-bridge.sh --stop`. Never `kill` everything `lsof -ti` prints: Figma itself is on that list |

More cases, each with its fix, are in
[`pitfalls.md`](skill/figaro/references/pitfalls.md).

</details>

---

## Privacy

Figaro has no server of its own, no account and no telemetry. Your files, your
scripts and the pictures it takes stay on your Mac and in Figma.

- The bridge listens on `127.0.0.1` only and turns away requests from web
  pages.
- Pictures from `figaro shot` are saved in `/tmp/figaro/shots`.
- Versions saved before changes go into the file's version history, where
  everyone with access to the file can see them.
- Figaro makes one request on its own: every six hours the bridge reads this
  repository's release tags on GitHub, so the plugin's bar can tell you about a
  new version. It sends nothing about you or your files, and
  `FIGARO_NO_UPDATE_CHECK=1` turns it off.

---

## Development

[AGENTS.md](AGENTS.md) explains how the code is laid out and the rules for
changing it. The tests need no Figma:

```sh
python3 -m venv venv && venv/bin/pip install -r requirements-dev.txt
venv/bin/python -m pytest -q
for t in tests/*.test.js; do node "$t" || break; done
```

<details>
<summary>🧪 <b>Live tests in real Figma</b></summary>

<br>

They make, change and remove layers, so give them a draft of your own with the
plugin running in it:

```sh
export FIGARO_TEST_FILE="<link to your draft>"
venv/bin/python tests/live_exec.py
venv/bin/python tests/live_cli.py
venv/bin/python tests/live_stress.py 20
```

</details>

---

## License

Figaro is free software under the [MIT license](LICENSE).

<div align="center">
<br>

Found a bug or missing something?
[Open an issue](https://github.com/artem-levchenko-2/figaro/issues/new)

</div>
