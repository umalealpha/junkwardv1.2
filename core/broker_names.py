"""One canonical way to decide that two agency records are the same broker.

Graphite's `agencies` table has no uniqueness control — it holds only `id` and
`name`, so the same firm can be, and has been, created twice. On 2026-09-08 there
were 8 such groups across 17 records and 370 policies, four of them the SAME name
under the database's own collation (FinSef 11/34, Kgare 12/41, Letsema 14/44,
Nnawalt 13/47).

A split broker is not cosmetic. Premium lands on one record and its claims on the
other, so a loss ratio computed per record is wrong in both directions — and the
CFO's 70% rule escalates on that ratio.

Two records are the same broker when either matches:
  * the TRADING name after "t/a" is the same — this is what catches Redhill,
    filed as both "Hilrange Enterprises" and "Hildrage Enterprises"; or
  * the legal name matches once case, punctuation, and the words every broker
    shares (insurance, brokers, pty, ltd, holdings…) are stripped.

This lives in one place ON PURPOSE. Two reports that merge brokers by two
different rules will disagree about who is over 70%, and the wrong one will be
believed — the same trap as a guard that parses a figure differently from the
total it guards.

Merging here is a WORKAROUND for the source data. Once Underwriting merges the
duplicate records in Graphite, these keys simply stop matching anything and the
reports carry on unchanged.
"""
from __future__ import annotations

import re

# Words that appear in half the register and so carry no identity.
_NOISE = re.compile(
    r"\b(insurance|assurance|brokers?|broking|brokerage|agency|agencies|"
    r"enterprises?|holdings?|investments?|services?|solutions?|consultancy|"
    r"consultants?|managers?|management|risk|group|company|co|pty|ltd|limited|"
    r"proprietary|inc|incorporated|botswana|branch)\b",
    re.I)

# "t/a", "t / a", "trading as" — everything after it is the name people use.
_TRADING_AS = re.compile(r"\bt\s*/?\s*a\b\s*(?P<name>.+)$|\btrading\s+as\b\s*(?P<n2>.+)$",
                         re.I)


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _NOISE.sub(" ", (text or "").lower()))


def merge_key(name: str) -> str:
    """The key two records of the same broker share.

    Falls back to the squashed full name, so a record that normalises to nothing
    (a name made entirely of noise words) keeps its own identity rather than
    collapsing into every other such record.
    """
    raw = (name or "").strip()
    if not raw:
        return ""

    m = _TRADING_AS.search(raw)
    if m:
        traded = _squash(m.group("name") or m.group("n2") or "")
        if traded:
            return traded

    return _squash(raw) or re.sub(r"[^a-z0-9]", "", raw.lower())


def merge_rows(rows: list, name_field: str, sum_fields: tuple) -> list:
    """Collapse rows that are the same broker, summing the money and counts.

    The longest display name wins — it is the fullest version of the firm's name
    and the one a reader will recognise.
    """
    merged: dict = {}
    for row in rows:
        key = merge_key(row.get(name_field))
        held = merged.get(key)
        if held is None:
            merged[key] = dict(row)
            continue
        if len(str(row.get(name_field) or "")) > len(str(held.get(name_field) or "")):
            held[name_field] = row.get(name_field)
        for f in sum_fields:
            held[f] = (held.get(f) or 0) + (row.get(f) or 0)
    return list(merged.values())
