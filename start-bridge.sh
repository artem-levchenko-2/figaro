#!/usr/bin/env bash
# Start (or restart) the Figaro bridge in the background — macOS / Linux / WSL.
#
#   bash start-bridge.sh           # start, or restart if it is already running
#   bash start-bridge.sh --stop    # stop it
#
# Runs inside a detached tmux session when tmux is installed (attach to watch it),
# otherwise as a plain background process. macOS has no tmux out of the box, so
# that fallback is what most Macs get. Log: /tmp/figaro-bridge-<port>.log
#
# Python: ./venv/bin/python if the venv exists, else $FIGARO_PYTHON, else
# python3 (macOS has no `python` command). Port: $FIGARO_PORT, default 8788.
#
# The session, log and pid file are per port, so this script never touches a
# bridge on another port. The bridge exits after $FIGARO_IDLE_EXIT (default 3h)
# with no plugin and no requests; the figaro CLI starts it again by running
# this script.
set -u
cd "$(dirname "$0")"

PORT="${FIGARO_PORT:-8788}"
SESSION="figaro-bridge-$PORT"
LOG="/tmp/figaro-bridge-$PORT.log"
PIDFILE="/tmp/figaro-bridge-$PORT.pid"
IDLE="${FIGARO_IDLE_EXIT:-3h}"

if [ -x ./venv/bin/python ]; then
    PY=./venv/bin/python
elif [ -n "${FIGARO_PYTHON:-}" ]; then
    PY="$FIGARO_PYTHON"
elif command -v python3 >/dev/null 2>&1; then
    PY=python3
else
    PY=python
fi

up() { curl -sf "http://127.0.0.1:$PORT/status" >/dev/null 2>&1; }
# Anything at all listening on the port — a bridge, or some other program.
taken() { (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; }

stop() {
    local was=1
    if command -v tmux >/dev/null 2>&1 && tmux has-session -t "$SESSION" 2>/dev/null; then
        tmux kill-session -t "$SESSION"; was=0
    fi
    if [ -f "$PIDFILE" ]; then
        local pid
        pid="$(cat "$PIDFILE")"
        # Kill only if that pid is still a bridge — after a reboot or a crash
        # the file can name a pid that now belongs to another program.
        if ps -p "$pid" -o command= 2>/dev/null | grep -q "bridge.py"; then
            kill "$pid" 2>/dev/null && was=0
        fi
        rm -f "$PIDFILE"
    fi
    # Wait for the port to free up, so a restart doesn't race the old process.
    for _ in $(seq 1 30); do taken || break; sleep 0.1; done
    if taken; then
        if up; then
            echo "[start-bridge] a bridge not started by this script is serving port $PORT."
        else
            echo "[start-bridge] port $PORT is taken by another program."
        fi
        # Only the listener: `lsof -ti tcp:$PORT` also lists Figma itself, which
        # holds a connection to the bridge, and killing that list closes Figma.
        echo "[start-bridge] see what it is:  lsof -nP -iTCP:$PORT -sTCP:LISTEN"
        echo "[start-bridge] stop it only if it is a bridge.py you started:  kill <PID from that list>"
        echo "[start-bridge] or use another port:  FIGARO_PORT=8789 bash start-bridge.sh"
        exit 1
    fi
    return $was
}

if [ "${1:-}" = "--stop" ]; then
    stop && echo "[start-bridge] stopped" || echo "[start-bridge] was not running"
    exit 0
fi

stop || true   # silent, unless the port is held by something else (then it exits)

if ! "$PY" -c "import aiohttp" 2>/dev/null; then
    echo "[start-bridge] $PY cannot import aiohttp. Set up the venv first:"
    echo "    python3 -m venv venv && ./venv/bin/pip install -r requirements.txt"
    exit 1
fi

# -u: without it Python block-buffers stdout into the log, and plugin
# connect/disconnect events stay invisible exactly when you need them.
if command -v tmux >/dev/null 2>&1; then
    # tmux runs the command with its server's environment, which may be older
    # than the FIGARO_* settings (FIGARO_UPDATE_BUTTON, FIGARO_TOKEN_FILE...):
    # hand them over by name.
    SETTINGS=(env)
    for name in $(compgen -e); do
        case "$name" in FIGARO_*) SETTINGS+=("$name=${!name}") ;; esac
    done
    tmux new-session -d -s "$SESSION" \
        "$(printf '%q ' "${SETTINGS[@]}")$PY -u bridge.py --port $PORT --idle-exit $IDLE 2>&1 | tee $LOG"
    how="tmux session '$SESSION' — watch: tmux attach -t $SESSION"
else
    nohup "$PY" -u bridge.py --port "$PORT" --idle-exit "$IDLE" >"$LOG" 2>&1 </dev/null &
    echo $! >"$PIDFILE"
    how="background process $(cat "$PIDFILE") — watch: tail -f $LOG"
fi

# First start of Python + aiohttp can take a few seconds on a cold Mac.
for i in $(seq 1 100); do
    if up; then
        echo "[start-bridge] up on 127.0.0.1:$PORT after $((i * 100))ms ($how)"
        echo "[start-bridge] stop with: bash start-bridge.sh --stop"
        echo "[start-bridge] now run the plugin: Figma > Plugins > Development > Figaro"
        exit 0
    fi
    sleep 0.1
done

echo "[start-bridge] FAILED to start within 10s. Log ($LOG):"
cat "$LOG"
exit 1
