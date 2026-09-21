"""The March of Progress — where the company is on the evolution strip.

CFO, 2026-09-20: "Remember there is a picture of Darwin's theory where a monkey,
with four different evolutions, becomes a human. Now copy the same thing: show a
human, create some funny four characters, full AI. Every time we reach the
targets it shows what character we are in."

Five stages, left to right, exactly like the famous picture — except it starts
at a human buried in paper and ends at the thing we are trying to become.

Two deliberate choices:

  * **The joke is about the COMPANY, never about a person.** This screen is
    read by the CEO and sits next to a list of named colleagues. "Homo
    Papyrus" is where the business was, not who anybody is. No stage name
    refers to a role, a department or a person.
  * **The stage is earned, not awarded.** It reads the same weighted
    `overall_percent` as everything else on the board — one number, one
    computation. There is no separate "evolution score" that could drift away
    from the real progress and flatter us.
"""
from __future__ import annotations

# Bands are inclusive of `from_percent`, exclusive of the next stage's.
# Five stages so the strip reads like the original picture: four transitional
# creatures and the destination.
STAGES = [
    {
        'index': 0,
        'key': 'papyrus',
        'from_percent': 0,
        'name': 'Homo Papyrus',
        'nickname': 'The Paper Pusher',
        'caption': 'Everything is printed, signed, scanned and typed in again.',
        'tagline': 'We start here. Everybody starts here.',
    },
    {
        'index': 1,
        'key': 'tabulator',
        'from_percent': 20,
        'name': 'Homo Tabulatus',
        'nickname': 'The Spreadsheet Wrangler',
        'caption': 'The paper is gone. The re-typing is not — it just moved into a spreadsheet.',
        'tagline': 'Upright at last, and carrying a laptop.',
    },
    {
        'index': 2,
        'key': 'clickus',
        'from_percent': 40,
        'name': 'Homo Clickus',
        'nickname': 'The Button Presser',
        'caption': 'The systems talk to each other, but a person still has to press go.',
        'tagline': 'Fewer hands, more buttons.',
    },
    {
        'index': 3,
        'key': 'cyborgus',
        'from_percent': 60,
        'name': 'Homo Cyborgus',
        'nickname': 'The Co-Pilot',
        'caption': 'The machine does the work and drafts the decision. A person approves it.',
        'tagline': 'Half of the job is already automatic.',
    },
    {
        'index': 4,
        'key': 'automaticus',
        'from_percent': 80,
        'name': 'Machina Automatica',
        'nickname': 'Fully AI',
        'caption': 'The work runs itself. People do the things only people can do — '
                   'judgement, relationships, and winning customers.',
        'tagline': 'The first AI insurance company in Botswana.',
    },
]


def stage_for(percent: int | float | None) -> dict:
    """Which creature we are today. Never raises, never returns None."""
    value = 0 if percent is None else max(0, min(100, int(percent)))
    current = STAGES[0]
    for stage in STAGES:
        if value >= stage['from_percent']:
            current = stage
    return current


def evolution(percent: int | float | None) -> dict:
    """The whole strip, plus how far to the next creature.

    `percent_into_stage` drives the walking figure's position between one
    silhouette and the next, so the picture moves a little every day rather
    than jumping only at the five thresholds — the point of a living board.
    """
    value = 0 if percent is None else max(0, min(100, int(percent)))
    current = stage_for(value)
    index = current['index']
    is_final = index >= len(STAGES) - 1
    nxt = None if is_final else STAGES[index + 1]

    span_from = current['from_percent']
    span_to = 100 if is_final else nxt['from_percent']
    span = (span_to - span_from) or 1
    into = round((value - span_from) * 100 / span)

    return {
        'percent': value,
        'stages': [
            {**s,
             'reached': value >= s['from_percent'],
             'is_current': s['index'] == index}
            for s in STAGES
        ],
        'current': current,
        'current_index': index,
        'next': nxt,
        'is_final': is_final,
        'percent_into_stage': max(0, min(100, into)),
        'points_to_next': 0 if is_final else max(0, nxt['from_percent'] - value),
        'headline': ('We are here' if not is_final
                     else 'We got there'),
    }
