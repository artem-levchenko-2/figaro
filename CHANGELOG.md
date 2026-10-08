# Changelog

Notable changes, newest first. Versions follow [semver](https://semver.org/): what is versioned is what
scripts and agents depend on — the CLI, the bridge's HTTP API and the `h.*` helpers.

After an update, `figaro doctor` says what to restart: the bridge, or the plugin's code
(`figaro reload -T <file>`). If `plugin/manifest.json` changed, import it in Figma again.

## [Unreleased]

## [1.1.0] — 2026-10-08

### Added

- **Islands in the plugin's window.** The slim bar became a window with one black island per file: its own
  file first, then each file where agents are at work, until ten quiet minutes after their last script. An
  island names the agent running a script, its time and the queue; open it for the agents, the recent changes
  with their layers (a click on a layer of this file selects it and zooms to it) and **Stop**, which ends the
  running script in that file. The window grows and shrinks with its islands.
- **`figaro wait "<text>"`**: an agent leaves the user a note in the window. The file's island turns amber
  with it until the agent's next script there, or until the user dismisses it.
- **Update and Reload.** When a new release is out, the window offers it under its header. Once no file runs
  a script, the bridge pulls the release (`git pull --ff-only`), reloads the plugins and restarts itself;
  scripts sent meanwhile get a 503 and run nothing. When it can't pull (local changes, local commits, no
  connection), the window says why and links to the release. **Reload** does the same without the pull,
  when the code on disk is newer than what runs.

### Changed

- A failed script keeps its file's island red until the same agent's next script succeeds, or until the user
  dismisses it, instead of a two-second flash.
- A script stopped from the window fails with a 409 that says the user pressed Stop.

### Upgrading from 1.0.0

- Run `git pull` in the Figaro folder, restart the bridge (`bash start-bridge.sh`, on Windows
  `.\start-bridge.ps1 -Restart`) and run the plugin again in each open file, or `figaro reload -T <file>`.
  From now on the window's **Update and Reload** does it for you.

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
