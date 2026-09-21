"""rewards/hrv.py — Heart-rate-variability math for Alpha Thrive.

Pure functions, our own implementation of the standard public-domain
time-domain HRV measures (Task Force of ESC/NASPE, 1996). No third-party
HRV library is linked — these formulas are textbook and licence-free, which
keeps Thrive clear of GPL dependencies (pyVHR / hrv-analysis are NOT used).

Input is a list of RR / inter-beat intervals in **milliseconds** (the gaps
between successive heartbeats), as produced by the finger-PPG scan. No I/O,
no model writes, no identifiers — call from rewards/api_views.py.

Wellness only, never diagnosis. These numbers describe variability and a
coarse stress band; they are not a medical reading.
"""
from __future__ import annotations

from math import sqrt

# Physiologically plausible RR window (ms): ~30–200 bpm. Anything outside is a
# motion artifact / dropped beat and is discarded before any statistic.
_RR_MIN_MS = 300.0
_RR_MAX_MS = 2000.0

# stress_band thresholds on RMSSD (ms), per the Thrive brief.
_RMSSD_CALM = 40.0
_RMSSD_MODERATE = 20.0


def clean_rr(rr_intervals) -> list[float]:
    """Keep only finite RR values inside the plausible window (ms)."""
    out: list[float] = []
    for v in rr_intervals or []:
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if _RR_MIN_MS <= f <= _RR_MAX_MS:
            out.append(f)
    return out


def mean_hr(rr_intervals) -> float | None:
    """Mean heart rate in bpm from RR intervals (ms). None if no clean beats."""
    rr = clean_rr(rr_intervals)
    if not rr:
        return None
    mean_rr = sum(rr) / len(rr)
    if mean_rr <= 0:
        return None
    return 60000.0 / mean_rr


def sdnn(rr_intervals) -> float | None:
    """SDNN — sample standard deviation of NN intervals (ms).

    Needs at least 2 beats. Uses the sample (n-1) estimator, the convention
    for short-term SDNN.
    """
    rr = clean_rr(rr_intervals)
    n = len(rr)
    if n < 2:
        return None
    mean_rr = sum(rr) / n
    var = sum((x - mean_rr) ** 2 for x in rr) / (n - 1)
    return sqrt(var)


def _successive_diffs(rr: list[float]) -> list[float]:
    return [rr[i + 1] - rr[i] for i in range(len(rr) - 1)]


def rmssd(rr_intervals) -> float | None:
    """RMSSD — root mean square of successive RR differences (ms)."""
    rr = clean_rr(rr_intervals)
    if len(rr) < 2:
        return None
    diffs = _successive_diffs(rr)
    ms = sum(d * d for d in diffs) / len(diffs)
    return sqrt(ms)


def pnn50(rr_intervals) -> float | None:
    """pNN50 — percent of successive RR differences greater than 50 ms."""
    rr = clean_rr(rr_intervals)
    if len(rr) < 2:
        return None
    diffs = _successive_diffs(rr)
    over = sum(1 for d in diffs if abs(d) > 50.0)
    return 100.0 * over / len(diffs)


def stress_band(rmssd_value) -> str:
    """Coarse stress band from RMSSD (ms): >40 calm, >20 moderate, else stressed.

    Returns '' when RMSSD is unknown so callers never store a fake band.
    """
    if rmssd_value is None:
        return ''
    if rmssd_value > _RMSSD_CALM:
        return 'calm'
    if rmssd_value > _RMSSD_MODERATE:
        return 'moderate'
    return 'stressed'


def analyze(rr_intervals) -> dict:
    """Convenience: full time-domain summary from RR intervals (ms).

    Returns rounded scalars plus a beats count and a confidence proxy.
    `confidence` is the share of submitted intervals that survived artifact
    filtering — a quick signal-quality hint for the scan (0-1).
    """
    raw = list(rr_intervals or [])
    rr = clean_rr(raw)
    rm = rmssd(rr)
    hr = mean_hr(rr)
    sd = sdnn(rr)
    pn = pnn50(rr)
    confidence = (len(rr) / len(raw)) if raw else 0.0
    return {
        'resting_hr':       round(hr) if hr is not None else None,
        'hrv_sdnn':         round(sd, 1) if sd is not None else None,
        'hrv_rmssd':        round(rm, 1) if rm is not None else None,
        'hrv_pnn50':        round(pn, 1) if pn is not None else None,
        'stress_band':      stress_band(rm),
        'beats':            len(rr),
        'scan_confidence':  round(confidence, 2),
    }
