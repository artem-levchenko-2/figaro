"""The bridge leaves by itself when nobody needs it.

With `--idle-exit 3h` (start-bridge.sh passes it) the bridge exits once no
plugin has been connected and no request has come for that long. Nothing is
lost: the figaro CLI starts it again on its next call (cli_extras.autostart),
and a plugin window reconnects by itself.

However it stops, the bridge closes its plugin windows' sockets first, so a
window stops its keep-awake tone at once and can reconnect to a new bridge.
"""

from __future__ import annotations

import asyncio
import os
import re
import signal
import time

from aiohttp import WSCloseCode, web

import bridge

CHECK_EVERY = 60.0
LAST = [time.time()]  # when the bridge was last needed: a request, or a plugin connected


def duration(text):
    """'10800', '180m', '3h', '90s' → seconds (argparse type)."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([smh]?)\s*", str(text))
    if not m:
        raise ValueError(f"not a duration: {text!r} (seconds, or 30m / 3h)")
    return float(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600}[m.group(2)]


@web.middleware
async def touch(request, handler):
    LAST[0] = time.time()
    return await handler(request)


def idle_for(now=None):
    """Seconds since the bridge was last needed; 0 while a plugin is connected."""
    now = time.time() if now is None else now
    if bridge._live_plugins():
        LAST[0] = now
    return now - LAST[0]


def leave(seconds):
    print(f"[idle] no plugin and no requests for {seconds / 3600:g} h — exiting; "
          f"the figaro CLI starts the bridge again when it is needed", flush=True)
    os.kill(os.getpid(), signal.SIGTERM)  # aiohttp's run_app shuts down cleanly on it


async def watch(seconds, every=CHECK_EVERY, exit_fn=None):
    while True:
        await asyncio.sleep(every)
        if idle_for() >= seconds:
            (exit_fn or leave)(seconds)
            return


async def close_plugins(app):
    """On shutdown, close every plugin socket now. Left to aiohttp, a socket
    outlives the stop by up to a minute (until a heartbeat fails or the
    shutdown timeout ends): all that time the window plays its tone and
    doesn't reconnect to a new bridge."""
    sockets = [info["ws"] for info in list(bridge.PLUGINS.values())]
    await asyncio.gather(*(ws.close(code=WSCloseCode.GOING_AWAY, message=b"bridge stopped")
                           for ws in sockets), return_exceptions=True)


def install(app, seconds):
    """Exit after `seconds` idle; 0 or None leaves the bridge running for good."""
    app.middlewares.append(touch)
    app.on_shutdown.append(close_plugins)
    if not seconds:
        return

    async def start(app):
        LAST[0] = time.time()
        app["bridge_idle"] = asyncio.get_running_loop().create_task(watch(seconds))

    async def stop(app):
        task = app.get("bridge_idle")
        if task:
            task.cancel()

    app.on_startup.append(start)
    app.on_cleanup.append(stop)
