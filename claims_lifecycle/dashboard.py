"""Claims performance dashboard payload builder.

Splits performance and money data by role. Users without a money role receive
no money keys in their payload; underwriters receive only their own book.
"""

PERFORMANCE_KEYS = frozenset([
    'claims_by_step',
    'lights',
    'average_completion',
    'stuck_under_half',
    'clocks',
    'notification_to_first_contact',
    'repudiation_rate',
    'repudiation_duration',
    'assessor_turnaround',
    'salvage_in_yard',
    'salvage_invoices_not_raised',
    'reopened_after_closing',
])

MONEY_KEYS = frozenset([
    'settlements',
    'supplier_invoices',
    'average_settlement',
    'salvage_recovered',
    'excess_collected',
    'by_underwriter',
])

_FULL_MONEY_ROLES = frozenset(['claims', 'finance', 'operations', 'executive'])


def build_dashboard(facts, roles=(), underwriter_id=None):
    dashboard = {}

    for key in PERFORMANCE_KEYS:
        if key in facts:
            dashboard[key] = facts[key]

    if any(role in _FULL_MONEY_ROLES for role in roles):
        for key in MONEY_KEYS:
            if key in facts:
                dashboard[key] = facts[key]
    elif 'underwriting' in roles:
        by_underwriter = facts.get('by_underwriter', {})
        book = by_underwriter.get(underwriter_id) if underwriter_id else None
        if book is not None:
            dashboard['settlements'] = book.get('settlements')
            dashboard['by_underwriter'] = {underwriter_id: book}

    return dashboard