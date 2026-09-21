"""
core/entity_metadata_schema.py — jurisdiction-scoped Company.metadata keys.

CFO directive 2026-05-18: FSP (FSCA Financial Services Provider number)
and CIPC (Companies and Intellectual Property Commission registration)
are South African regulatory artefacts and have no meaning on a
Botswana entity. Stop the ADSA pipeline import from polluting the BW
entities' metadata with empty/N-A FSP / CIPC rows.

Each entry here maps a metadata KEY → the ISO-2 country code(s) it is
valid in. The seed_entity_metadata command refuses to write a key onto
a Company whose `country` is not in the allow-list, and Company.clean()
raises ValidationError if anyone tries to set it directly via Django
admin.

Keys NOT in this dict are jurisdiction-agnostic (e.g. `notes`,
`directors`) and may be set on any entity.
"""
from __future__ import annotations


# ISO-2 country codes per key. Tuple lets us extend ('ZA', 'NA') etc.
COUNTRY_SCOPED_KEYS: dict[str, tuple[str, ...]] = {
    # South Africa — FSCA + CIPC + SARS VAT
    'fsp_number':    ('ZA',),
    'fsp':           ('ZA',),                # ADSA pipeline shorthand
    'cipc':          ('ZA',),
    'cipc_number':   ('ZA',),
    'fsca_status':   ('ZA',),
    'sars_vat':      ('ZA',),
    'sars_vat_no':   ('ZA',),

    # Botswana — NBFIRA + CIPA + BURS
    'nbfira_number': ('BW',),
    'cipa_number':   ('BW',),
    'burs_tin':      ('BW',),

    # Eswatini, Lesotho, Zambia stubs — wire as the group expands.
    # 'frsa_number': ('SZ',),
    # 'rsl_number':  ('LS',),
}


def is_key_allowed(key: str, country: str | None) -> bool:
    """True if `key` may appear on a Company whose country is `country`."""
    scope = COUNTRY_SCOPED_KEYS.get((key or '').strip().lower())
    if scope is None:
        return True
    if not country:
        return False
    return country.upper() in scope


def violations_for(country: str | None, metadata: dict | None) -> list[str]:
    """Return a list of metadata keys that violate the country scope.

    Used by Company.clean() and the seed command's --dry-run output.
    """
    if not metadata:
        return []
    bad: list[str] = []
    for key in metadata.keys():
        if not is_key_allowed(key, country):
            bad.append(key)
    return bad
