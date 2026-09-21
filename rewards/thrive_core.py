"""rewards/thrive_core.py — Alpha Thrive compute, member-parameterised.

Pure-ish orchestration shared by the STAFF endpoints (member resolved by id,
rewards/api_views.py) and the CUSTOMER endpoints (member resolved from the
login session, rewards/customer_views.py). Keeping one source of truth here
means the wellness maths can never drift between the two surfaces.

Wellness only, never diagnosis. Derived numbers only. The coach runs on
DeepSeek -> Gemini (NEVER Anthropic) over anonymised buckets.
"""
from __future__ import annotations

from django.db import transaction

from . import hrv
from .alpha_score import alpha_score
from .models import HealthMetric, RewardMember


# --- AI coach (non-Anthropic) ---------------------------------------------

def coach_complete(system_prompt: str, user_prompt: str) -> tuple[str, str]:
    """DeepSeek -> Gemini only. Anthropic is deliberately excluded (Thrive rule).
    Returns (text, engine); ('', 'none') on total failure so callers fall back."""
    from core.ai_assist import (deepseek_complete, DeepSeekUnavailable,
                                gemini_complete, GeminiUnavailable)
    chain = [('DeepSeek', deepseek_complete, DeepSeekUnavailable),
             ('Gemini',   gemini_complete,   GeminiUnavailable)]
    for name, fn, exc_cls in chain:
        try:
            text = fn(user_prompt, system_prompt=system_prompt)
            if text and text.strip():
                return text.strip(), name
        except exc_cls:
            continue
        except Exception:  # noqa: BLE001 — never let a coach call break the flow
            continue
    return '', 'none'


def age_band(age) -> str:
    try:
        a = int(age)
    except (TypeError, ValueError):
        return 'unknown'
    if a < 30:
        return 'under-30'
    if a < 45:
        return '30-44'
    if a < 60:
        return '45-59'
    return '60-plus'


def activity_bucket(steps) -> str:
    try:
        s = int(steps or 0)
    except (TypeError, ValueError):
        return 'unknown'
    if s >= 8000:
        return 'active'
    if s >= 4000:
        return 'moderate'
    return 'low'


def metrics_for_score(member, metric, *, age=None, gender=None) -> dict:
    """alpha_score input dict from a member + a HealthMetric row. Numbers only;
    age/gender optional + NOT persisted (RewardMember stores neither)."""
    if metric is None:
        return {'age': age, 'gender': gender}
    return {
        'age':              age,
        'gender':           gender,
        'resting_hr':       metric.resting_hr,
        'stress_band':      metric.stress_band or '',
        'respiration_rate': metric.respiration_rate,
        'steps':            metric.steps,
        'sleep_minutes':    metric.sleep_minutes,
    }


# --- compute entrypoints (take a resolved RewardMember) -------------------

def run_scan(member, rr_intervals, *, resting_hr_override=None,
             respiration_rate=None, age=None, gender=None, metric_date=None):
    """Compute HRV from RR intervals, upsert the day's VITALS (idempotent,
    never touches steps/points), and return {vitals, alphaScore, date}.
    Returns None if the scan had too few clean beats (caller -> 422)."""
    summary = hrv.analyze(rr_intervals)
    if summary['beats'] < 2:
        return None

    resting_hr = summary['resting_hr']
    if resting_hr_override is not None:
        try:
            resting_hr = int(resting_hr_override)
        except (TypeError, ValueError):
            pass
    resp = None
    if respiration_rate is not None:
        try:
            resp = float(respiration_rate)
        except (TypeError, ValueError):
            resp = None

    from django.utils import timezone
    the_date = metric_date or timezone.localdate()

    with transaction.atomic():
        member = RewardMember.objects.select_for_update().get(pk=member.pk)
        metric, _ = HealthMetric.objects.get_or_create(
            member=member, date=the_date,
            defaults={'steps': 0, 'points_awarded': 0, 'source': 'Alpha Thrive (PPG)'},
        )
        metric.resting_hr       = resting_hr
        metric.hrv_sdnn         = summary['hrv_sdnn']
        metric.hrv_rmssd        = summary['hrv_rmssd']
        metric.hrv_pnn50        = summary['hrv_pnn50']
        metric.respiration_rate = resp
        metric.stress_band      = summary['stress_band']
        metric.scan_confidence  = summary['scan_confidence']
        metric.save(update_fields=['resting_hr', 'hrv_sdnn', 'hrv_rmssd', 'hrv_pnn50',
                                   'respiration_rate', 'stress_band', 'scan_confidence',
                                   'updated_at'])

    score = alpha_score(metrics_for_score(member, metric, age=age, gender=gender))
    return {
        'vitals': {
            'restingHr':       resting_hr,
            'hrvSdnn':         summary['hrv_sdnn'],
            'hrvRmssd':        summary['hrv_rmssd'],
            'hrvPnn50':        summary['hrv_pnn50'],
            'respirationRate': resp,
            'stressBand':      summary['stress_band'],
            'beats':           summary['beats'],
            'scanConfidence':  summary['scan_confidence'],
        },
        'alphaScore': score,
        'date':       the_date.isoformat(),
    }


def alpha_for_member(member, *, age=None, gender=None) -> dict:
    """Latest-metric Alpha Thrive score payload for a member."""
    metric = member.health_metrics.order_by('-date').first()
    score = alpha_score(metrics_for_score(member, metric, age=age, gender=gender))
    return {
        'score':     score,
        'tier':      member.tier,
        'points':    member.points_balance,
        'asOf':      metric.date.isoformat() if metric else None,
        'hasVitals': bool(metric and metric.resting_hr is not None),
    }


def trend_for_member(member) -> dict:
    """Linear wellness-trend stub over the member's last (<=14) days."""
    rows = list(member.health_metrics.order_by('-date')[:14])
    rows.reverse()
    points = [{'date': m.date.isoformat(),
               'score': alpha_score(metrics_for_score(member, m))['score']} for m in rows]
    slope = 0.0
    n = len(points)
    if n >= 2:
        xs = list(range(n)); ys = [p['score'] for p in points]
        mx = sum(xs) / n; my = sum(ys) / n
        denom = sum((x - mx) ** 2 for x in xs)
        if denom:
            slope = sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / denom
    direction = 'improving' if slope > 0.5 else 'declining' if slope < -0.5 else 'flat'
    return {'points': points, 'slope': round(slope, 2), 'direction': direction}


def coach_for_member(member, *, age=None) -> dict:
    """One anonymised-bucket wellness nudge (non-Anthropic), static fallback."""
    from core.ai_assist import is_safe_for_ai
    metric = member.health_metrics.order_by('-date').first()
    score = alpha_score(metrics_for_score(member, metric, age=age))
    stress = (metric.stress_band if metric else '') or 'unknown'
    buckets = {
        'age_band':   age_band(age),
        'stress':     stress,
        'activity':   activity_bucket(metric.steps if metric else None),
        'score_band': score['band'],
    }
    static = {
        'stressed': 'Your readings suggest some stress. Try five slow breaths, a short '
                    'walk, and water — small resets add up. 💧',
        'moderate': 'Steady going. A 10-minute walk and a glass of water now will lift '
                    'your score by tomorrow. 🚶',
        'calm':     'Lovely — you are in a calm zone. Keep the rhythm: move a little, '
                    'hydrate, and rest well tonight. 🌙',
    }
    nudge = static.get(stress, 'Keep it simple today: move a little, drink water, and '
                               'breathe slowly. Scan again tomorrow to watch your Alpha '
                               'Thrive score grow. 🌱')
    engine = 'static'
    system_prompt = (
        'You are Alpha Thrive, a friendly Botswana wellness coach for an insurance '
        'rewards app. Give exactly ONE short, warm, encouraging nudge (under 220 '
        'characters). WELLNESS ONLY — never diagnose, never mention illness, medication '
        'or medical advice. You may use one emoji. You are given only anonymised '
        'wellness buckets, never a person.')
    user_prompt = (
        f'Member buckets: age band {buckets["age_band"]}, stress {buckets["stress"]}, '
        f'activity {buckets["activity"]}, wellness score band {buckets["score_band"]}. '
        f'Write one encouraging nudge.')
    if is_safe_for_ai(user_prompt).safe:
        text, used = coach_complete(system_prompt, user_prompt)
        if text:
            nudge, engine = text, used
    return {'nudge': nudge, 'engine': engine, 'buckets': buckets, 'scoreBand': score['band']}
