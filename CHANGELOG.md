# Changelog

Notable changes, newest first. Versions follow [semver](https://semver.org/): what is versioned is what
scripts and agents depend on — the CLI, the bridge's HTTP API and the `h.*` helpers.

After an update, `figaro doctor` says what to restart: the bridge, or the plugin's code
(`figaro reload -T <file>`). If `plugin/manifest.json` changed, import it in Figma again.

## [Unreleased]

## [1.1.2] — 2026-10-09

### Fixed

- A read-only script (`-R`) no longer rolls back what it didn't change (#1). Figma reports every change made
  while a script runs without saying who made it: the instances it updates when a script first loads a page
  of a large file, or the user's own edits meanwhile, came as the script's, and the undo meant for them took
  back the last step of the user or another agent. Figaro now reads the script's code: while code that only
  reads runs, what changes in the file is told in a `⚠` line and left alone, and `undo` doesn't take it for
  the script's step. If an undo takes back a step that wasn't the script's, the answer says so and asks for
  Cmd+Shift+Z in Figma, which brings it back.

## [1.1.1] — 2026-10-09

### Changed

- The plugin's window shows every file where Figaro runs, quiet or not: its own file first, then the others in
  the order they connected. Before, another file showed only while agents worked there and ten minutes after.
- In the plugin's window an agent's name is a little heavier, and the note it leaves is grey like its status,
  so the names stand out.

## [1.1.0] — 2026-10-09

### Added

- **Islands in the plugin's window.** The slim bar became a window with one island per file, black in
  Figma's dark theme and white in its light one: its own file first, then each file where agents are at work,
  until ten quiet minutes after their last script. Open an island for its agents and the recent changes with
  their layers (a click on a layer of this file selects it and zooms to it); it unfolds and moves the islands
  under it down. Each agent has one row that says what it is doing: at work from its first script until five
  quiet minutes pass or it says it is done, done with its note, stopped by you, or failed when its last script
  failed. The error of its last script stays under its name until a script of its succeeds. The window grows
  and shrinks with its islands, and its last line shows the Figaro version.
- **Stop** on the row of an agent at work, shown while you point at the row. Its running script ends and fails
  with a 409 that says the user pressed Stop; pressed between its scripts, its next script in the file is
  refused with that 409 and runs nothing. The other agents go on.
- **`figaro done "<note>"`**: an agent says it is done in a file, for now. Its row shows the note until its
  next script there that can change the file: a last look with `shot` or `link` leaves it.
- **Update.** When a new release is out, an **Update to X.Y.Z** button appears next to the version. Once no
  file runs a script, the bridge pulls the release (`git pull --ff-only`), reloads the plugins and restarts
  itself; scripts sent meanwhile get a 503 and run nothing. When it can't pull (local changes, local commits,
  no connection), the window says why and links to the release. **Reload** in the same place does the same
  without the pull, when the code on disk is newer than what runs.

### Changed

- The plugin's window is titled **Figaro Relay**. In Figma's menu the plugin is still **Figaro**.
- A failed script no longer flashes the bar red for two seconds: the window lists it with the file's recent
  changes and puts the error under the agent's name, and an agent that goes quiet after one shows as failed.

### Upgrading from 1.0.0

- Run `git pull` in the Figaro folder, restart the bridge (`bash start-bridge.sh`, on Windows
  `.\start-bridge.ps1 -Restart`) and run the plugin again in each open file, or `figaro reload -T <file>`.
  From now on the window's **Update** button does it for you.

## [1.0.0] — 2026-10-08

The first release of Figaro.

### Added

- **The `figaro` command**: `exec`, `inspect`, `shot`, `tree`, `find`, `sel`, `link`, `text`, `variant`,
  `clone`, `icomp`, `rm`, `undo`, `reload`, `doctor`, `targets`, `status` and `clear`. Figma links work
  wherever a file or a layer is expected.
- **The bridge**: one queue per Figma file, checkpoints in the file's version history, a change report for
  every script, read-only runs that roll back, a guarded `undo`, hints for known errors. The CLI starts it on
  demand (`start-bridge.sh`, or `start-bridge.ps1` on Windows), and it exits after three idle hours. When an
  agent's sandbox keeps the CLI off the network, the error says so.
- **The Figaro plugin** with thirty `h.*` helpers for auto-layout, text and fonts, variables, components and
  variants, and a deadline for every script.
- **An agent skill** (`skill/figaro`) in the open Agent Skills format, for Claude Code, Codex, Cursor and
  other agents, and installers that install the command and link the skill into `~/.claude/skills` and
  `~/.agents/skills`: `tools/install.sh` for macOS and Linux, `tools/install.ps1` for Windows. An agent can
  set Figaro up from the repository's link: `AGENTS.md` tells it how.
- **macOS, Windows and Linux.** On Windows the command is a real `figaro.exe`, so Figma links with `&` pass
  through PowerShell, cmd and Git Bash; scripts on stdin are read as UTF-8, and pictures go to
  `%TEMP%\figaro\shots`. On Linux, Figma's desktop app is the unofficial figma-linux. CI runs the tests on
  Linux and Windows.
