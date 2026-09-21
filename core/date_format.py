"""
core/date_format.py — one strftime that behaves the same on Linux and Windows.

`%-d` / `%-I` / `%-m` (render the number without its leading zero) are a
glibc extension. They work on the Linux containers that run prod and CI, and
raise `ValueError: Invalid format string` on the Windows dev machine — so a
test suite that renders a date is green in CI and unrunnable on the laptop.

`format_dt` expands the `%-X` directives itself and hands everything else to
the platform's strftime, so there is ONE code path and the rendered string is
identical on both. Use it instead of `.strftime()` anywhere the format string
contains `%-`.
"""
from __future__ import annotations

import re

# `%%` is matched first so an escaped percent can never be read as the start
# of a directive (e.g. '100%%-d' is a literal '%' then '-d', not '%-d').
_NO_PAD = re.compile(r'%%|%-(.)')


def format_dt(value, fmt: str) -> str:
    """`value.strftime(fmt)`, with `%-X` honoured on every platform."""
    def expand(match):
        if match.group(0) == '%%':
            return '%%'
        # Render the padded directive, then drop the padding ourselves.
        # '' -> '0': glibc prints a zero value as one digit, not nothing.
        text = value.strftime('%' + match.group(1)).lstrip('0') or '0'
        # The result is spliced back into the format string, so any literal
        # percent in it has to be re-escaped.
        return text.replace('%', '%%')

    return value.strftime(_NO_PAD.sub(expand, fmt))
