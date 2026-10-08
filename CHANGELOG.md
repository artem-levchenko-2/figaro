# Changelog

Notable changes, newest first. Versions follow [semver](https://semver.org/): what is versioned is what
scripts and agents depend on — the CLI, the bridge's HTTP API and the `h.*` helpers.

After an update, restart the bridge (`bash start-bridge.sh`) and load the new plugin code
(`figaro reload -T <file>`, or run the plugin again). If `plugin/manifest.json` changed, import it in Figma
again.

## [Unreleased]

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
  other agents, and `tools/install.sh`, which installs the command and links the skill into
  `~/.claude/skills` and `~/.agents/skills`.
