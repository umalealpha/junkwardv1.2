"""Default weighted steps and pure progress calculation for claims lifecycle.

The progress function takes step states and settings, computes the completion
percent, traffic light colour, current step and holder without relying on a
database, clock, or Django models.
"""

from decimal import Decimal, ROUND_HALF_UP

DEFAULT_STEPS = [
    {'key': 'reported', 'weight': 5, 'holder': 'Claims', 'target_days': 2},
    {'key': 'documents', 'weight': 10, 'holder': 'Customer', 'target_days': 5},
    {'key': 'assessment', 'weight': 15, 'holder': 'Assessor', 'target_days': 5},
    {'key': 'liability', 'weight': 15, 'holder': 'Claims', 'target_days': 3},
    {'key': 'repair', 'weight': 15, 'holder': 'Repairer', 'target_days': 10},
    {'key': 'parts', 'weight': 5, 'holder': 'Parts', 'target_days': 4},
    {'key': 'finance', 'weight': 10, 'holder': 'Finance', 'target_days': 3},
    {'key': 'approval', 'weight': 10, 'holder': 'Management', 'target_days': 2},
    {'key': 'review', 'weight': 10, 'holder': 'Claims', 'target_days': 3},
    {'key': 'close', 'weight': 5, 'holder': 'Claims', 'target_days': 2},
]

__all__ = ["DEFAULT_STEPS", "progress"]


def progress(steps):
    applicable_total = Decimal('0')
    done_total = Decimal('0')
    steps_done = 0
    current = None
    current_step = ''

    for step in steps:
        if not step['applies']:
            continue

        weight = Decimal(str(step['weight']))
        applicable_total += weight

        if step['done']:
            done_total += weight
            steps_done += 1
        elif current is None:
            current = step
            current_step = step['key']

    if applicable_total:
        percent = (done_total / applicable_total * 100).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
    else:
        percent = Decimal('0.00')

    holder = ''
    # A finished claim holds nobody, so there is no current step to colour. It
    # says so by name: an empty string is not a colour, and an empty string in a
    # renderer falls into whichever branch happens to be written first.
    light = 'done'

    if current is not None:
        holder = current['holder']
        days_in_step = int(current['days_in_step'])
        target_days = int(current['target_days'])

        # Green up to and including the target, amber up to and including double
        # it, red past that — the same boundaries claims_lifecycle.clocks uses,
        # so one colour means one thing across the whole screen.
        if days_in_step <= target_days:
            light = 'green'
        elif days_in_step <= 2 * target_days:
            light = 'amber'
        else:
            light = 'red'

    return {
        'percent': percent,
        'steps_done': steps_done,
        'light': light,
        'holder': holder,
        'current_step': current_step,
    }