"""Live stress test of the file queue — run by hand, not by pytest.

    venv/bin/python tests/live_stress.py         # 20 writers
    venv/bin/python tests/live_stress.py 50      # 50 writers

Only in a scratch file of your own (tests/live_file.py), in three parts:

1. Writers: N agents at once do a read-modify-write on a counter kept in the
   file's plugin data (invisible; cleared at the end): read, pause, write + 1.
   Through the file's queue the counter must end at exactly N.
2. Readers: 2N read-only scripts at once, through the queue and then with
   "parallel", which skips it. Both must return the counter; the times show
   what the queue costs reads.
3. Parallel writers: a "parallel" script is read-only. Alone, its
   write is rolled back; alongside others, nobody can tell whose a change
   was, so it is kept with a notice. Either way the call fails or warns.

Nothing but the plugin-data key "figaro-stress" is touched. Needs the bridge
(8788) and the plugin of the current build running in that file.
"""
import json
import random
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import live_file

BRIDGE = live_file.BRIDGE
DRAFT = live_file.key()
KEY = "figaro-stress"
RMW = (f"const v = +(figma.root.getPluginData('{KEY}') || 0);"
       # A pause so concurrent scripts genuinely overlap.
       "await new Promise(r => setTimeout(r, 30));"
       f"figma.root.setPluginData('{KEY}', String(v + 1)); return v + 1")
READ = f"return +(figma.root.getPluginData('{KEY}') || 0)"
RESET = f"figma.root.setPluginData('{KEY}', ''); return 0"

failed = 0


def post(body):
    body = {"target": DRAFT, "checkpoint": False, "timeout": 120, "queue_timeout": 300, **body}
    req = urllib.request.Request(f"{BRIDGE}/exec", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=400) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def at_once(bodies):
    random.shuffle(bodies)
    t0 = time.time()
    with ThreadPoolExecutor(64) as pool:
        results = list(pool.map(post, bodies))
    return time.time() - t0, results


def check(label, ok, detail=None):
    global failed
    if not ok:
        failed += 1
    print(f"{'  ok  ' if ok else ' FAIL '} {label}"
          + ("" if ok or detail is None else f"\n         {json.dumps(detail, ensure_ascii=False)[:600]}"))


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    live_file.connected(DRAFT)
    post({"code": RESET, "agent": "stress"})
    try:
        # 1. writers through the queue
        took, results = at_once([{"code": RMW, "agent": f"stress-{i}"} for i in range(n)])
        bad = [r for s, r in results if s != 200]
        count = post({"code": READ, "read_only": True})[1].get("value")
        check(f"{n} writers in the queue: counter {count} in {took:.1f}s", not bad and count == n,
              bad[:2])

        # 2. readers: the queue against parallel
        reads = [{"code": READ, "read_only": True, "agent": f"reader-{i}"} for i in range(2 * n)]
        took_q, res_q = at_once([dict(b) for b in reads])
        took_p, res_p = at_once([dict(b, parallel=True) for b in reads])
        values = {r.get("value") for s, r in res_q + res_p}
        check(f"{2 * n} readers: queue {took_q:.1f}s, parallel {took_p:.1f}s",
              values == {n} and all(s == 200 for s, r in res_q + res_p), sorted(values, key=str))

        # 3. parallel writers are read-only
        took, results = at_once([{"code": RMW, "parallel": True, "agent": f"rogue-{i}"}
                                 for i in range(5)])
        rolled = sum(1 for s, r in results if r.get("rolled_back"))
        kept = sum(1 for s, r in results if s == 200 and "read-only" in (r.get("notice") or ""))
        check(f"5 parallel writers: {rolled} rolled back, {kept} kept with a notice",
              rolled + kept == 5, [r for s, r in results][:2])
        count_after = post({"code": READ, "read_only": True})[1].get("value")
        if kept == 0:
            check(f"…all rolled back: the counter is still {n}", count_after == n, count_after)
        else:
            print(f"  --   the counter is {count_after}: overlapping writes were kept, as the notice says")
    finally:
        post({"code": RESET, "agent": "stress"})
    check("cleared", post({"code": READ, "read_only": True})[1].get("value") == 0)
    print(f"\n{failed} FAILED" if failed else "\nall stress checks passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
