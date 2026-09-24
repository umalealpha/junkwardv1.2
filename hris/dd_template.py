"""hris/dd_template.py — the BLANK Development Dialogue competency template.

35 of the 42 current dialogues carry no `dd.sections` at all: they were created
from the nine-box seed, not from an appraisal workbook. The crash guard shipped
in bd62814c stopped those records dying when opened, but it left the manager
staring at an empty review with nothing to score.

The blank template is DERIVED from the workbook seed
(hris/data/talent_cockpit_seed.json) rather than retyped here, so the competency
names, perspectives, attribute wording and weights can never drift from the real
Alpha Direct framework.

Two rules this module exists to obey, both learned the hard way on this feature:

  * it FILLS, it never REPLACES. `seed_into` merges the competency structure
    into the dialogue it is given and leaves everything else alone. A `dd` also
    carries the personal development plan, career aspirations, development
    priorities and measures, the manager's comments and the rating — assigning a
    fresh dict over it would silently destroy all of that and answer 200.
  * it copies by ALLOWLIST. The template is derived from ONE identified person's
    real appraisal, so anything not named below never leaves their record —
    including a field added to the seed shape years from now.
"""
from __future__ import annotations

import copy
import json
import os
from functools import lru_cache

_SEED = os.path.join(os.path.dirname(__file__), 'data', 'talent_cockpit_seed.json')

# The ONLY fields copied out of the seed. Every score, every piece of evidence
# and every comment is left behind by construction, not by remembering to blank it.
_ROW_KEEP = ('perspective', 'attributes', 'weight')
_VALUE_KEEP = ('value', 'behaviours', 'weight')

# The empty assessment fields a blank row/value carries, so the app renders the
# same shape it does for a workbook-imported dialogue.
_ROW_BLANK = {'sbi': '', 'target': '', 'start': '', 'finish': '',
              'manager': None, 'employee': None, 'comments': ''}
_VALUE_BLANK = {'selfText': '', 'self': None, 'manager': None, 'raw': None,
                'comments': ''}


@lru_cache(maxsize=1)
def _template() -> dict:
    """The richest seeded dialogue's STRUCTURE, with no assessment content."""
    # Deliberately unguarded. The seed ships in the repo and in the image; if it
    # is missing or malformed the deploy is broken and that must be loud.
    with open(_SEED, encoding='utf-8') as fh:
        people = json.load(fh)

    def richness(person):
        dd = person.get('dd') or {}
        return sum(len(s.get('rows') or []) for s in (dd.get('sections') or []))

    best = max(people, key=richness) if people else None
    if not richness(best or {}):
        # Loud on purpose. An empty template would hand every manager a blank
        # review that LOOKS like working software — the failure this ends.
        raise RuntimeError(
            f'dd_template: no competency structure in the seed at {_SEED}')

    sections = []
    for sec in best['dd'].get('sections') or []:
        rows = []
        for row in sec.get('rows') or []:
            new = {k: row.get(k) for k in _ROW_KEEP}
            new.update(_ROW_BLANK)
            rows.append(new)
        sections.append({'name': sec.get('name', ''),
                         'weight': sec.get('weight'), 'rows': rows})
    values = []
    for val in best['dd'].get('values') or []:
        new = {k: val.get(k) for k in _VALUE_KEEP}
        new.update(_VALUE_BLANK)
        values.append(new)
    return {'sections': sections, 'values': values}


def blank_dd() -> dict:
    """A fresh, unscored competency + values structure, safe to mutate."""
    return copy.deepcopy(_template())


def needs_template(dd) -> bool:
    """True when a dialogue has no competency structure to score.

    Deliberately narrow: an ODD shape (not a dict) is left alone, and a record
    that already holds one section is never touched.
    """
    if dd is None:
        return True
    if not isinstance(dd, dict):
        return False
    return not (dd.get('sections') or [])


def seed_into(dd):
    """Return `dd` with the competency structure filled in — nothing replaced.

    The personal development plan, career aspirations, development priorities
    and measures, the manager's comments, the rating and anything else already
    on the dialogue are carried through untouched. `values` is filled only when
    it is genuinely empty. An odd (non-dict) shape is returned unchanged.
    """
    if not needs_template(dd):
        return dd
    merged = dict(dd) if isinstance(dd, dict) else {}
    template = blank_dd()
    merged['sections'] = template['sections']
    if not (merged.get('values') or []):
        merged['values'] = template['values']
    return merged
