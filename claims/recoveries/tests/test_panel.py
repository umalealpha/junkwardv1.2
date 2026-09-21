"""Objective check for normalise_panel_name.

The register's "Appointed To" column is doing three jobs at once. It holds law
firms, debt collectors, the third party's own insurer, AND payment methods
("Direct Deposit", "Debit Order", "Orange Money"). Spelling variants split one
firm into two counterparties, which is why no recovery scorecard is possible today.

The module must (a) merge the variants and (b) refuse to let a payment method or
an insurer become a panel member.
"""
import pytest

from claims.recoveries.panel import PanelKind, normalise_panel_name


def test_salbany_case_variants_merge_to_one_counterparty():
    a = normalise_panel_name('SALBANY & TORTO')     # 100 rows
    b = normalise_panel_name('sALBANY & TORTO')     # 7 rows — same firm, split today
    c = normalise_panel_name('Salbany and Torto')
    assert a.name == b.name == c.name == 'Salbany & Torto'
    assert a.kind is PanelKind.LAW_FIRM


def test_jeremiah_tladi_spelling_variants_merge():
    a = normalise_panel_name('Jeremiah Tladi & Co.')   # 9 rows
    b = normalise_panel_name('Jereiah Tladi and Co')   # 6 rows — misspelt, split today
    assert a.name == b.name == 'Jeremiah Tladi & Co'
    assert a.kind is PanelKind.LAW_FIRM


def test_kelobang_typo_is_corrected():
    r = normalise_panel_name('Kelobang Godisang Attoneys')   # "Attoneys" in the register
    assert r.name == 'Kelobang Godisang Attorneys'
    assert r.kind is PanelKind.LAW_FIRM


@pytest.mark.parametrize('raw,expected', [
    ('Minchin & Kelly', 'Minchin & Kelly'),
    ('MINCHIN AND KELLY', 'Minchin & Kelly'),
    ('Akheel/Desai', 'Akheel / Desai'),
    ('AKHEEL', 'Akheel / Desai'),
    ('Woodward Legal Services', 'Woodward Legal Services'),
    ('mbikiwa legal practice', 'Mbikiwa Legal Practice'),
])
def test_known_law_firms(raw, expected):
    r = normalise_panel_name(raw)
    assert r.name == expected
    assert r.kind is PanelKind.LAW_FIRM


@pytest.mark.parametrize('raw,expected', [
    ('COLLECTION AFRICA', 'Collection Africa'),
    ('Collection Africa', 'Collection Africa'),
    ('5T Debt Collectors', '5T Debt Collectors'),
])
def test_debt_collectors(raw, expected):
    r = normalise_panel_name(raw)
    assert r.name == expected
    assert r.kind is PanelKind.DEBT_COLLECTOR


@pytest.mark.parametrize('raw', ['HOLLARD', 'Hollard', 'BRYTE', 'OLD MUTUAL'])
def test_third_party_insurers_are_flagged_as_insurers_not_panel_members(raw):
    """These are the third party's insurer, not somebody we appointed. Idea 6
    (bulk settlement per insurer) depends on telling them apart."""
    assert normalise_panel_name(raw).kind is PanelKind.INSURER


@pytest.mark.parametrize('raw', ['Direct Deposit', 'Debit Order', 'Orange Money', 'DIRECT DEPOSIT'])
def test_payment_methods_must_never_become_panel_members(raw):
    """Field misuse in the register. These must be quarantined, not onboarded
    onto the approved panel."""
    r = normalise_panel_name(raw)
    assert r.kind is PanelKind.PAYMENT_METHOD
    assert r.is_panel_member is False


@pytest.mark.parametrize('raw', [None, '', '   ', 'N/A', 'n/a', 'None', 'NONE'])
def test_blank_and_na_mean_nobody_is_appointed(raw):
    r = normalise_panel_name(raw)
    assert r.kind is PanelKind.NONE
    assert r.is_panel_member is False


def test_free_text_note_is_not_a_panel_member():
    """Real register value — somebody typed a sentence into the field."""
    r = normalise_panel_name('COMMUNICATED WITH CLIENT WHO SAID HE IS INSURED UNDER HOLLARD')
    assert r.is_panel_member is False
    assert r.kind in (PanelKind.UNKNOWN, PanelKind.NOTE)


def test_law_firms_and_collectors_are_panel_members():
    assert normalise_panel_name('SALBANY & TORTO').is_panel_member is True
    assert normalise_panel_name('COLLECTION AFRICA').is_panel_member is True


def test_unknown_firm_is_kept_but_titlecased_and_marked_unknown():
    """An unrecognised name is still a real counterparty — keep it, flag it for
    review rather than silently dropping the case."""
    r = normalise_panel_name('SOME NEW ATTORNEYS')
    assert r.name == 'Some New Attorneys'
    assert r.kind is PanelKind.UNKNOWN


def test_non_string_input_is_tolerated():
    assert normalise_panel_name(0).is_panel_member is False
    assert normalise_panel_name(46299.0).is_panel_member is False
