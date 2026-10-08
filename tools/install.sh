#!/usr/bin/env bash
# Install the `figaro` command and the figaro agent skill, for this user. Safe
# to run again: it only fixes what is missing or stale, and never overwrites a
# file or folder it did not make.
#
#   bash tools/install.sh                # ~/.local/bin/figaro, and the skill in
#                                        # ~/.claude/skills and ~/.agents/skills
#   bash tools/install.sh --uninstall    # remove them (only if they are ours)
#   bash tools/install.sh --skills DIR   # the skill in DIR instead; repeat it for
#                                        # several folders
#   bash tools/install.sh --bin DIR --skills DIR   # elsewhere, e.g. for a test
#
# The command runs this checkout's CLI with its venv (made here if missing), so
# it works from any folder and always talks to this checkout's bridge. The skill
# is a link to skill/figaro: Claude Code reads ~/.claude/skills, and Codex and
# other agents read ~/.agents/skills.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
BIN="$HOME/.local/bin"
SKILL_DIRS=()
MODE=install
need() { [ $# -ge 2 ] || { echo "install.sh: $1 needs a folder" >&2; exit 2; }; }
while [ $# -gt 0 ]; do
    case "$1" in
        --bin) need "$@"; BIN="$2"; shift 2 ;;
        --skills) need "$@"; SKILL_DIRS+=("$2"); shift 2 ;;
        --uninstall) MODE=uninstall; shift ;;
        -h|--help) awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"; exit 0 ;;
        *) echo "install.sh: unknown argument $1" >&2; exit 2 ;;
    esac
done

[ ${#SKILL_DIRS[@]} -gt 0 ] || SKILL_DIRS=("$HOME/.claude/skills" "$HOME/.agents/skills")

CMD="$BIN/figaro"
SKILL="$REPO/skill/figaro"
MARK="# figaro: installed by $REPO/tools/install.sh"

ours_cmd() { [ -f "$CMD" ] && grep -qF "$MARK" "$CMD"; }
ours_link() { [ -L "$1" ] && [ "$(readlink "$1")" = "$SKILL" ]; }

if [ "$MODE" = uninstall ]; then
    if ours_cmd; then rm "$CMD"; echo "removed $CMD"; elif [ -e "$CMD" ]; then echo "left $CMD: not ours"; fi
    for dir in "${SKILL_DIRS[@]}"; do
        link="$dir/figaro"
        if ours_link "$link"; then rm "$link"; echo "removed $link"
        elif [ -e "$link" ] || [ -L "$link" ]; then echo "left $link: not ours"; fi
    done
    exit 0
fi

# 1. The venv: the bridge needs aiohttp, `shot` cuts tall pictures with Pillow.
if [ ! -x "$REPO/venv/bin/python" ]; then
    echo "making the venv: $REPO/venv"
    python3 -m venv "$REPO/venv"
fi
if ! "$REPO/venv/bin/python" -c "import aiohttp, PIL" 2>/dev/null; then
    echo "installing requirements.txt into the venv"
    "$REPO/venv/bin/pip" install -q -r "$REPO/requirements.txt"
fi

# 2. The command. A file of someone else's is left alone.
mkdir -p "$BIN"
if [ -e "$CMD" ] && ! ours_cmd; then
    echo "install.sh: $CMD exists and is not ours — move it away and run this again" >&2
    exit 1
fi
NEW="$(printf '#!/bin/sh\n%s\nexec "%s" "%s" "$@"\n' "$MARK" "$REPO/venv/bin/python" "$REPO/figaro.py")"
if [ "$(cat "$CMD" 2>/dev/null)" != "$NEW" ]; then
    printf '%s\n' "$NEW" > "$CMD"
    chmod +x "$CMD"
    echo "command: $CMD"
else
    echo "command: $CMD (already there)"
fi

# 3. The skill: a link in each folder, so it changes together with the code. A
# folder that holds someone else's figaro is skipped, and the others still get
# theirs.
FAILED=0
for dir in "${SKILL_DIRS[@]}"; do
    link="$dir/figaro"
    mkdir -p "$dir"
    if ours_link "$link"; then
        echo "skill: $link (already there)"
    elif [ -e "$link" ] || [ -L "$link" ]; then
        echo "install.sh: $link exists and is not a link to $SKILL — move it away and run this again" >&2
        FAILED=1
    else
        ln -s "$SKILL" "$link"
        echo "skill: $link -> $SKILL"
    fi
done

case ":$PATH:" in
    *":$BIN:"*) ;;
    *) echo "note: $BIN is not on your PATH yet. For zsh, the macOS default:"
       echo "    echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc && exec zsh"
       echo "or call $CMD by its full path" ;;
esac
exit "$FAILED"
