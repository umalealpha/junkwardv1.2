"""rewards/alpha_score.py — the Alpha Thrive wellness score.

Turns a member's derived numbers (resting HR, HRV stress band, respiration,
steps, sleep) plus an age/gender baseline into a single 0-100 wellness score
with a transparent factor breakdown. Pure function, no I/O, no identifiers.

Design notes:
  - WELLNESS, NOT DIAGNOSIS. The score and bands describe lifestyle wellness;
    they are not a medical assessment and carry no clinical claim.
  - Transparent + additive on purpose: every factor exposes its own sub-score,
    weight and a plain-language note so the UI can colour-code the breakdown
    and a 39-year auditor can trace exactly how the number was formed.
  - Missing inputs score NEUTRAL (60) rather than zero, so a scan that only
    carries vitals (HR + HRV + respiration, no steps/sleep) still yields a
    sensible score instead of being dragged down by absent data.
"""
from __future__ import annotations

# Factor weights — sum to 1.0.
_WEIGHTS = {
    'hrv':         0.30,
    'resting_hr':  0.25,
    'activity':    0.20,
    'sleep':       0.15,
    'respiration': 0.10,
}

_NEUTRAL = 60.0  # sub-score used when a factor has no data.

# Illustrative cohort baselines by age band (gender-neutral). Reference only —
# "how a typical member your age scores", not a clinical norm.
_PEER_BY_AGE = ((30, 72.0), (45, 68.0), (60, 64.0))
_PEER_DEFAULT = 60.0


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _resting_hr_factor(resting_hr, age) -> tuple[float, str]:
    """Lower resting HR (within a healthy floor) scores higher."""
    if resting_hr is None:
        return _NEUTRAL, 'No heart-rate reading yet.'
    hr = float(resting_hr)
    # Best band 55–65 bpm → 100. Penalise above 65 and below 50.
    if hr < 40 or hr > 130:
        return 20.0, f'{int(hr)} bpm is outside the typical resting range.'
    if 55 <= hr <= 65:
        score = 100.0
    elif hr < 55:
        score = _clamp(100 - (55 - hr) * 2.5)      # very low: mild taper
    else:
        score = _clamp(100 - (hr - 65) * 2.0)      # elevated: steeper taper
    note = f'Resting heart rate {int(hr)} bpm.'
    return score, note


def _hrv_factor(stress_band) -> tuple[float, str]:
    """HRV stress band → sub-score. Higher variability (calm) scores higher."""
    band = (stress_band or '').lower()
    table = {'calm': (100.0, 'Heart-rate variability looks calm.'),
             'moderate': (65.0, 'Moderate stress in your variability.'),
             'stressed': (35.0, 'Variability suggests higher stress.')}
    if band in table:
        return table[band]
    return _NEUTRAL, 'No variability reading yet.'


def _respiration_factor(respiration_rate) -> tuple[float, str]:
    """Normal resting respiration 12–20 bpm scores highest."""
    if respiration_rate is None:
        return _NEUTRAL, 'No breathing-rate reading yet.'
    rr = float(respiration_rate)
    if 12 <= rr <= 20:
        score = 100.0
    elif rr < 12:
        score = _clamp(100 - (12 - rr) * 6)
    else:
        score = _clamp(100 - (rr - 20) * 5)
    return score, f'Breathing rate {rr:.0f} breaths/min.'


def _activity_factor(steps) -> tuple[float, str]:
    """Steps toward a 10k/day reference. Linear to 100 at 10,000."""
    if steps is None:
        return _NEUTRAL, 'No step count yet.'
    s = max(0, int(steps))
    score = _clamp(s / 10000.0 * 100.0)
    return score, f'{s:,} steps today.'


def _sleep_factor(sleep_minutes) -> tuple[float, str]:
    """7–9 h sleep scores highest; short or very long sleep tapers down."""
    if sleep_minutes is None:
        return _NEUTRAL, 'No sleep data yet.'
    hours = float(sleep_minutes) / 60.0
    if 7 <= hours <= 9:
        score = 100.0
    elif hours < 7:
        score = _clamp(hours / 7.0 * 100.0)
    else:
        score = _clamp(100 - (hours - 9) * 12)
    return score, f'{hours:.1f} h sleep.'


def _band_for(score: float) -> str:
    if score >= 80:
        return 'Excellent'
    if score >= 65:
        return 'Good'
    if score >= 50:
        return 'Fair'
    return 'Needs attention'


def _peer_avg(age) -> float:
    if age is None:
        return _PEER_DEFAULT
    try:
        a = int(age)
    except (TypeError, ValueError):
        return _PEER_DEFAULT
    for ceiling, val in _PEER_BY_AGE:
        if a < ceiling:
            return val
    return _PEER_DEFAULT


def alpha_score(metrics: dict) -> dict:
    """Compute the Alpha Thrive score from a metrics dict.

    Accepted keys (all optional): age, gender, resting_hr, stress_band,
    respiration_rate, steps, sleep_minutes.

    Returns: {score:int 0-100, band:str, factors:[...], peer_avg:float}
    where each factor is {key,label,score,weight,note}.
    """
    m = metrics or {}
    age = m.get('age')

    raw = {
        'hrv':         (_hrv_factor(m.get('stress_band')),         'Stress / HRV'),
        'resting_hr':  (_resting_hr_factor(m.get('resting_hr'), age), 'Resting heart rate'),
        'activity':    (_activity_factor(m.get('steps')),          'Activity'),
        'sleep':       (_sleep_factor(m.get('sleep_minutes')),     'Sleep'),
        'respiration': (_respiration_factor(m.get('respiration_rate')), 'Breathing'),
    }

    factors = []
    total = 0.0
    for key, ((sub, note), label) in raw.items():
        weight = _WEIGHTS[key]
        total += sub * weight
        factors.append({
            'key':    key,
            'label':  label,
            'score':  round(sub),
            'weight': weight,
            'note':   note,
        })

    score = int(round(_clamp(total)))
    return {
        'score':    score,
        'band':     _band_for(score),
        'factors':  factors,
        'peer_avg': _peer_avg(age),
    }
