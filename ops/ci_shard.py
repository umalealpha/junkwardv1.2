"""Print the app labels for one CI shard, so the guardrail suite can run across
several runners at once instead of queuing on one.

    python ops/ci_shard.py --shard 1 --of 4
    -> core ledger claims hris ...

WHY THIS EXISTS. On 2026-09-09 the suite took 29 of the job's 31 minutes. A
35-minute gate is what drove parallel sessions to push straight to main rather
than wait for it (192 pushes in a day, not one completed run on main). Running
it on the runner's own cores only took it to 20 min — GitHub's standard runner
is small. Splitting across runners is the change that actually moves it.

THE RULE THIS MUST NOT BREAK. On 2026-07-26 the CFO widened the gate from an
explicit app allow-list to the WHOLE suite, because 437 of 1370 tests across 19
apps sat off that list and 11 tests stayed red on main for weeks unnoticed. A
hard-coded shard list would reintroduce exactly that hole: a new app in nobody's
list is a new app nobody tests.

So the labels are DISCOVERED at run time from Django's own app registry — never
typed out. Add an app and it lands in a shard by itself. `test_ci_shards.py`
asserts that property, and goes red if any installed local app stops being
covered.

Balancing is greedy longest-first over a cheap static weight (the number of
`def test_` lines in each app). Perfect balance is not the point; not having one
runner carry ten minutes more than the others is.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]


def _setup_django():
    sys.path.insert(0, str(REPO))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'alpha_finance.settings')
    import django
    django.setup()


def local_app_labels() -> list[str]:
    """Every app that lives in this repo — read from the registry, never typed.

    Third-party and Django's own apps are excluded by path: their tests are not
    ours to gate, and running them would only slow every shard down.
    """
    from django.apps import apps
    labels = []
    for cfg in apps.get_app_configs():
        try:
            path = pathlib.Path(cfg.path).resolve()
        except (TypeError, ValueError):
            continue
        if REPO in path.parents and 'site-packages' not in str(path):
            labels.append(cfg.label)
    return sorted(labels)


_MEASURED: dict[str, float] | None = None


def _measured() -> dict[str, float]:
    """Seconds per app, measured from a real single-machine run.

    Counting tests was the first attempt and it balanced the COUNTS almost
    perfectly — 1656/1653/1653/1652 — and the runners still finished 5 minutes
    apart (14m06 / 10m11 / 11m38 / 09m02). Tests are not equally expensive:
    hris averages 0.28s and integrations 0.06s, nearly 5x. Balancing the thing
    that is not the cost does nothing for the clock.

    Measured from run 34387231042 (single runner, no --parallel, so the log
    timestamps are real rather than buffered flushes): 1367s across 49 apps,
    of which hris 412, taskboard 259 and core 154 are 60% of the whole suite.

    Refresh it by re-running tools/measure_shard_weights.py against a fresh
    single-runner log. Stale numbers only cost a little balance, never coverage.
    """
    global _MEASURED
    if _MEASURED is None:
        import json
        path = REPO / 'ops' / 'ci_shard_weights.json'
        try:
            _MEASURED = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            _MEASURED = {}
    return _MEASURED


def test_count(label: str) -> int:
    """How many tests an app declares. Crude on purpose: no import, cannot fail
    on a broken module."""
    from django.apps import apps
    root = pathlib.Path(apps.get_app_config(label).path)
    total = 0
    for py in root.rglob('test*.py'):
        try:
            total += py.read_text(encoding='utf-8', errors='ignore').count('def test_')
        except OSError:
            continue
    return total


def weight(label: str) -> float:
    """Cost of an app, in seconds.

    Measured where a measurement exists; otherwise estimated from its test count
    at the suite's average pace (~0.2s a test). The fallback matters: without it
    a NEW app would weigh nothing and land on an already-full runner. It is an
    estimate, never an exclusion — coverage never depends on this number.
    """
    m = _measured()
    if label in m:
        return float(m[label])
    return max(test_count(label) * 0.2, 0.1)


def shards(n: int) -> list[list[str]]:
    """Greedy longest-first bin packing: heaviest app to the lightest shard."""
    labels = local_app_labels()
    buckets: list[list[str]] = [[] for _ in range(n)]
    loads = [0] * n
    for label in sorted(labels, key=lambda x: (-weight(x), x)):
        i = loads.index(min(loads))
        buckets[i].append(label)
        loads[i] += weight(label)
    return [sorted(b) for b in buckets]


def main() -> int:
    p = argparse.ArgumentParser(description='Print app labels for one CI shard.')
    p.add_argument('--shard', type=int, required=True, help='1-based shard number.')
    p.add_argument('--of', type=int, required=True, help='How many shards in total.')
    p.add_argument('--show-weights', action='store_true', help='Diagnostics, not for CI.')
    o = p.parse_args()

    if o.of < 1 or not (1 <= o.shard <= o.of):
        print(f'shard {o.shard} of {o.of} is not a shard', file=sys.stderr)
        return 2

    _setup_django()
    buckets = shards(o.of)

    if o.show_weights:
        for i, b in enumerate(buckets, 1):
            secs = sum(weight(x) for x in b)
            print(f'shard {i}: {secs:7.0f}s  {len(b):3d} apps  {sorted(b)[:3]}')
        return 0

    print(' '.join(buckets[o.shard - 1]))
    return 0


if __name__ == '__main__':
    sys.exit(main())
