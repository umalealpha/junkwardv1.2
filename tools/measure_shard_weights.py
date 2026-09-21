"""Re-measure ops/ci_shard_weights.json from a real CI log.

    gh api repos/<org>/<repo>/actions/jobs/<job-id>/logs > single.log
    python tools/measure_shard_weights.py single.log > ops/ci_shard_weights.json

USE A SINGLE-RUNNER LOG WITH NO --parallel. Under `--parallel` the workers
buffer their output and flush it in bursts, so consecutive test lines land
milliseconds apart and the timings are fiction. On a single runner each line is
printed as the test finishes and the gaps are the real durations. Job
34387231042 (2026-09-09, 29m45s) is the log these numbers came from.

Why this exists: the first split balanced TEST COUNTS almost perfectly — 1656 /
1653 / 1653 / 1652 — and the runners still finished five minutes apart, because
hris averages 0.28s a test and integrations 0.06s. Balancing the thing that is
not the cost does nothing for the clock.

Weights only affect BALANCE. Coverage comes from the app registry in
ops/ci_shard.py, so a stale file here costs a few minutes, never a missed app.
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import re
import sys

# "2026-09-09T18:24:26.8322393Z test_x (app.module.Class.test_x) ... ok"
LINE = re.compile(r'^﻿?(\S+Z)\s+\S+ \(([A-Za-z0-9_]+)\.')
SANE_MAX_S = 120.0     # a longer gap is database setup or a stalled runner


def measure(path: str) -> dict[str, float]:
    cost: collections.Counter[str] = collections.Counter()
    prev: tuple[dt.datetime, str] | None = None
    with open(path, encoding='utf-8', errors='ignore') as fh:
        for raw in fh:
            m = LINE.match(raw)
            if not m:
                continue
            stamp = dt.datetime.fromisoformat(m.group(1).lstrip('﻿').replace('Z', '+00:00'))
            app = m.group(2)
            if prev is not None:
                gap = (stamp - prev[0]).total_seconds()
                # A test's duration is the gap BEFORE the next line is printed,
                # so it belongs to the previous test's app, not this one.
                if 0 <= gap < SANE_MAX_S:
                    cost[prev[1]] += gap
            prev = (stamp, app)
    return {app: round(secs, 1) for app, secs in cost.items()}


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    weights = measure(sys.argv[1])
    if not weights:
        print('no test lines found — is this a single-runner log with -v2?', file=sys.stderr)
        return 1
    total = sum(weights.values())
    print(json.dumps(weights, indent=0, sort_keys=True))
    print(f'{len(weights)} apps, {total:.0f}s ({total / 60:.1f} min)', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
