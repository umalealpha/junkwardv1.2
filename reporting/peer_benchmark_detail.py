"""
peer_benchmark_detail.py — the line-level layer under peer_benchmark.py.

The headline benchmark answers "how do we compare". This answers "on which
line, and in which class of business". Three things live here that the
summary cannot show:

1. TRUE COST BASE. Peers report expenses in two places — inside insurance
   service expenses (directly attributable) and below the insurance service
   result (other operating expenses). Comparing only the second understates
   them badly: Insure Guard's below-the-line expenses are P14.7m, but its real
   cost base is P34.2m. Every expense ratio here uses both.

2. CLASS OF BUSINESS. A blended loss ratio hides everything that matters.
   Alpha Direct's blended gross loss ratio is 57.3%, which reads healthy. The
   motor book runs at 85.8% and personal-lines motor at 104.5%, while
   non-motor runs at 38.0% and Instant Insurance at 2.4%.

3. CESSION BY CLASS. The market cedes its catastrophe-exposed property and
   specialised lines and retains motor. Alpha Direct does the reverse.

Sources: Alpha Direct from the CFO's MA workbooks and the FY26 expense
analysis; peers from the signed FY2025 AFS. Class figures for Alpha Direct are
NINE MONTHS (Jul 2025 - Mar 2026, MA-Mar2026-ADIC-9M.xlsx, sheets 'Loss Ratio
view' and 'Premium Analysis view') because the June 2026 pack carries no class
analysis. They are labelled as such everywhere and must not be mixed with the
twelve-month totals.
"""
from __future__ import annotations

UNITS = 'Mn BWP'

# Provisions sit outside the running cost base — see the note above.
ADIC_PROVISIONS = 5.484

# ---------------------------------------------------------------------------
# 1. RUNNING COST BASE — expenses above AND below the insurance service result
# ---------------------------------------------------------------------------
# (staff inside ISE, staff below the line, other inside ISE, other below)
#
# EXCLUDED, deliberately: claims, acquisition and commission, and — for Alpha
# Direct — the P5.484m of provisions. Provisions are a credit-loss charge, not
# a running cost, and most peers do not disclose one at all, so including ours
# would compare us against a blank. The combined ratio in peer_benchmark.py
# DOES include them, because they land on the underwriting result. Two
# questions, two numerators; see `cost_base_analysis()` for both.

COST_BASE = {
    'Alpha Direct': dict(
        revenue=133.819, staff_ise=0.0, staff_below=13.175,
        other_ise=0.0, other_below=20.051,
        note='The MA pack reports all running costs below gross profit. '
             'Other below = operating expenses 31.381 plus depreciation 1.845, '
             'less staff 13.175. Excludes P5.484m of provisions — see the note '
             'on the two expense bases.'),
    'BIC': dict(
        revenue=718.048, staff_ise=41.024, staff_below=18.754,
        other_ise=18.991, other_below=23.209,
        note='Note 13.2 attributable expenses 60.015 and note 13.1 other '
             'operating 41.963. Note 14 total staff 56.492 does not tie to the '
             'two note sub-lines (59.778) — a discrepancy in the source.'),
    'Hollard': dict(
        revenue=444.463, staff_ise=48.690, staff_below=1.198,
        other_ise=70.310 - 48.690, other_below=21.782 - 1.198,
        note='Directly attributable expenses 70.310, non-attributable 21.782.'),
    'Old Mutual': dict(
        revenue=304.906, staff_ise=16.683, staff_below=0.0,
        other_ise=33.943, other_below=2.756 + 0.919 + 0.799,
        note='Reported in thousands. The P33.9m group management fee is the '
             'largest single cost and sits inside insurance service expenses.'),
    'Phoenix': dict(
        revenue=125.555, staff_ise=0.0, staff_below=8.062,
        other_ise=0.0, other_below=20.616 - 8.062,
        note='No functional split of staff cost disclosed.'),
    'Bryte': dict(
        revenue=165.637, staff_ise=6.009, staff_below=12.162,
        other_ise=8.656 - 6.009, other_below=25.098 - 12.162,
        note="Reported in thousands, scanned source."),
    'Insure Guard': dict(
        revenue=76.350, staff_ise=8.904, staff_below=6.042,
        other_ise=19.584 - 8.904, other_below=14.660 - 6.042,
        note='Half the cost base sits inside insurance service expenses. '
             'Below-the-line expenses alone would show 19.2% instead of 44.9%.'),
    'WestSure': dict(
        revenue=75.663, staff_ise=0.0, staff_below=7.352,
        other_ise=0.0, other_below=(16.417 - 7.352) + 11.021,
        note='Includes P11.0m commission expense, which WestSure presents '
             'below the insurance service result. Distressed — excluded from '
             'medians.'),
    'Sunshine': dict(
        revenue=26.191, staff_ise=None, staff_below=None,
        other_ise=13.970, other_below=3.147,
        note='Publishes no staff-cost note at all.'),
}

# ---------------------------------------------------------------------------
# 2. ALPHA DIRECT EXPENSE LINES — FY25 vs FY26
# ---------------------------------------------------------------------------
# Source: EXPENSE ANALYSIS JUNE 2026.xlsb, sheet 'FY25 vs FY26 Analysis',
# section 4 FULL LINE-ITEM DETAIL. ELEVEN MONTHS (Jul-May) on both sides, so
# the two columns are comparable to each other but NOT to the 12-month MA
# total of 31.381 — the workbook's own basis, kept as published.
EXPENSE_LINES: list[tuple[str, str, float, float]] = [
    # (group, line, FY25, FY26) — Pula, not millions
    ('People', 'Salaries and wages',            7_469_211,  9_376_555),
    ('People', 'Bonus pay',                     1_128_152,  1_216_245),
    ('People', 'Pension - employees',             612_964,    763_807),
    ('People', 'Directors salary',                632_500,    632_500),
    ('People', 'Medical aid',                     263_514,    302_448),
    ('People', 'Leave pay and severance',         214_007,    257_779),
    ('People', 'Staff welfare',                   718_054,    678_147),
    ('People', 'Training levy (BQA)',             246_683,    282_108),
    ('People', 'Assessor salary',                 182_448,    223_790),
    ('People', 'Employee benefits',                53_603,          0),
    ('People', 'Training and development',         59_196,     15_182),
    ('Technology', 'Software maintenance',      1_896_293,  1_958_072),
    ('Technology', 'Risk AI management fees',   2_238_767,  1_731_586),
    ('Technology', 'Amazon Web Services',       1_054_185,    990_519),
    ('Technology', 'ICT network services',        135_152,    148_667),
    ('Technology', 'Licensing fee',                69_255,    151_169),
    ('Distribution', 'Realpay paygate',         1_342_432,  1_929_980),
    ('Distribution', 'DPO paygates',              255_191,    353_188),
    ('Distribution', 'Insurance in a box',        204_459,    530_474),
    ('Distribution', 'BONU admin',                221_238,    430_271),
    ('Distribution', 'BONU acquisition',        1_550_000,          0),
    ('Distribution', 'Broker entertainment',      179_611,    173_106),
    ('Distribution', 'Acquisition cost',           35_710,     71_620),
    ('Professional', 'Consultancy fees',          748_415,  2_187_973),
    ('Professional', 'Audit fees',                682_088,    948_017),
    ('Professional', 'HR consultancy',            330_000,    210_000),
    ('Professional', 'Professional fees - other', 188_809,    140_165),
    ('Professional', 'Legal expense',             240_585,     50_691),
    ('Professional', 'Salvage management fees',    96_491,     96_491),
    ('Professional', 'Secretarial fees',          107_127,          0),
    ('Professional', 'Board fees',                 48_500,     30_000),
    ('Marketing', 'Advertising and promotions',   729_491,    968_875),
    ('Marketing', 'Marketing expense',            711_554,    861_873),
    ('Premises', 'Rent and rates',              1_460_354,    758_029),
    ('Premises', 'Utilities',                     101_917,    281_030),
    ('Premises', 'Repair and maintenance',        225_978,    207_087),
    ('Premises', 'Cleaning',                      114_956,    121_342),
    ('Premises', 'Office expense',                159_861,    176_100),
    ('Communications', 'Telephone usage',         489_552,    339_839),
    ('Communications', 'Cellphone usage',         413_820,    394_928),
    ('Communications', 'Internet fees',           160_145,    179_082),
    ('Communications', 'Postage and delivery',     84_450,    189_643),
    ('Travel', 'Fuel expense',                    247_868,    307_053),
    ('Travel', 'Travel expense',                  670_420,    187_135),
    ('Travel', 'Fleet management',                 14_316,     45_380),
    ('Regulatory', 'NBFIRA levy',                 379_674,     18_847),
    ('Regulatory', 'Tax penalty and interest',     69_577,    148_663),
    ('Regulatory', 'License and permits',           4_164,     38_161),
    ('Other', 'Bank charges',                     218_434,    202_400),
    ('Other', 'Printing and reproduction',         29_764,     37_035),
    ('Other', 'Office stationery',                 46_581,     33_375),
    ('Other', 'Insurance expense',                 65_703,     15_682),
    ('Other', 'Meals and entertainment',           23_797,     19_949),
    ('Other', 'Workshops and conferences',         32_664,     19_400),
    ('Other', 'Dues and subscriptions',            22_661,     11_469),
    ('Other', 'Gifts and donations',                8_000,     46_730),
    ('Other', 'Charitable donations',               5_000,      5_386),
    ('Other', 'Health care operating',                  0,     49_191),
    ('Other', 'Health care expenses',                   0,     31_299),
    ('Other', '10th year anniversary',            166_523,     -3_344),
    ('Other', 'Design expenses',                    4_000,          0),
]
EXPENSE_BASIS = 'Eleven months, July to May, on both sides — the expense ' \
                'workbook\'s own basis. Not comparable to the 12-month MA total.'


# ---------------------------------------------------------------------------
# 3. ALPHA DIRECT BY CLASS OF BUSINESS — NINE MONTHS to March 2026
# ---------------------------------------------------------------------------
# Source: MA-Mar2026-ADIC-9M.xlsx, sheet 'Loss Ratio view'. NINE MONTHS, not
# twelve — the June 2026 pack carries no class analysis. Never mix these with
# the 12-month figures in peer_benchmark.py.
CLASS_BASIS = 'Nine months, July 2025 to March 2026'

# Nine-month acquisition cost for Instant Insurance, from the same Mar-2026
# pack as the premium and claims. The twelve-month figure is P5.736m and
# pairing it with nine-month premium would break the document's own rule.
INSTANT_INSURANCE_ACQUISITION_9M = 4.992

ADIC_CLASSES: list[dict] = [
    dict(name='Corporate Lines Non-Motor', premium=26.409, claims=14.981, motor=False),
    dict(name='Corporate Lines Motor',     premium=25.176, claims=18.522, motor=True),
    dict(name='Instant Insurance',         premium=19.238, claims=0.453,  motor=False),
    dict(name='Personal Lines Motor',      premium=16.508, claims=17.257, motor=True),
    dict(name='Union Legal Insurance',     premium=5.165,  claims=4.000,  motor=False),
    dict(name='CAR',                       premium=1.820,  claims=0.000,  motor=False),
    dict(name='Personal Lines Non-Motor',  premium=1.506,  claims=1.216,  motor=False),
    dict(name='Alpha SA',                  premium=0.197,  claims=0.000,  motor=False),
    dict(name='Health Insurance',          premium=0.078,  claims=0.023,  motor=False),
]

# ---------------------------------------------------------------------------
# 4. CESSION BY CLASS — the structural comparison
# ---------------------------------------------------------------------------
# Only BIC publishes ceded premium by class. Hollard publishes by treaty type.
# Alpha Direct's cession is set by the signed treaty slips, not the AFS.
BIC_CESSION = [  # (class, insurance revenue, premium ceded)
    ('Specialised lines', 231.518, 177.431),
    ('Motor',             196.152,   3.929),
    ('Property',          174.969, 138.639),
    ('Liability',          43.071,  16.008),
    ('Engineering',        36.327,  22.986),
    ('Accident',           22.652,   2.719),
    ('Other',              13.359,   3.322),
]
# (treaty, ceded, recovered, change in estimates, net cost). The change in
# estimates column is why ceded less recovered does not equal the net cost:
# facultative 77.191 - 20.180 = 57.011, less 0.980 of estimate changes = 56.032.
HOLLARD_CESSION_BY_TREATY = [
    ('Excess of loss', 19.655,  0.000,  0.000,  19.655),
    ('Facultative',    77.191, 20.180,  0.980,  56.032),
    ('Treaty',          9.565, 27.941, -0.784, -17.592),
]
ADIC_TREATIES = [
    dict(name='Motor whole-account quota share', cession='80% ceded, 20% retained',
         commission='Sliding scale: 41.0% at a loss ratio of 56% or below, '
                    '39.5% at 57.5%, falling to a floor of 24.0% at 75% and '
                    'above. Loss ratio cap 80%. Provisional 25% for the first '
                    'three quarters, adjusted on the underwriting year.',
         reinsurers='Grand Re 30%, GIC Re 22.5%, FM Re 14.5%, P&C Re 13%, '
                    'retention 20%'),
    dict(name='General quota share', cession='30% ceded, retention max P6m',
         commission='35% ceding commission plus 28.5% profit commission on the '
                    'underwriting year, management expenses 7.5%, losses '
                    'carried forward to extinction. First calculation 24 '
                    'months after inception.',
         reinsurers='Munich Re of Africa 40%, GIC Re 22.5%, FM Re 10%, '
                    'Grand Re 10%, P&C Re 7.5%, Continental Re 5%, Kuwait Re 5%'),
    dict(name='Fire surplus', cession='4 lines over a P10m retention, '
                                      'capacity P50m',
         commission='Not stated on the summary slip',
         reinsurers='Same panel as the general quota share'),
    dict(name='Motor catastrophe excess of loss',
         cession='Layer 1 P4.7m over P0.3m; Layer 2 P5m over P5m',
         commission='Non-proportional — no ceding commission',
         reinsurers='Munich Re of Africa 40%, GIC Re 22.5%, FM Re 10%, '
                    'Grand Re 10%, P&C Re 7.5%, Continental Re 5%, Kuwait Re 5%'),
    dict(name='Non-motor risk and catastrophe excess of loss',
         cession='Layer 1 P5.7m over P0.3m; Layer 2 P18m over P6m; '
                 'Layer 3 P10m over P24m',
         commission='Non-proportional — no ceding commission',
         reinsurers='Same panel'),
]

# Blended ceding commission actually earned, FY26: 21.683 / 80.774.
ADIC_CEDING_COMMISSION_RATE = round(21.683 / 80.774 * 100, 1)


def class_analysis() -> dict:
    """Alpha Direct by class, with the motor/non-motor split that the blended
    loss ratio hides."""
    rows = []
    for c in ADIC_CLASSES:
        lr = round(c['claims'] / c['premium'] * 100, 1) if c['premium'] else None
        rows.append(dict(name=c['name'], premium=c['premium'], claims=c['claims'],
                         loss_ratio=lr, motor=c['motor'],
                         margin=round(c['premium'] - c['claims'], 3)))
    total_p = round(sum(c['premium'] for c in ADIC_CLASSES), 3)
    for r in rows:
        r['share_of_book'] = round(r['premium'] / total_p * 100, 1)
    motor_p = sum(c['premium'] for c in ADIC_CLASSES if c['motor'])
    motor_c = sum(c['claims'] for c in ADIC_CLASSES if c['motor'])
    non_p = total_p - motor_p
    non_c = round(sum(c['claims'] for c in ADIC_CLASSES), 3) - motor_c
    return dict(
        basis=CLASS_BASIS,
        classes=sorted(rows, key=lambda r: -r['premium']),
        total_premium=total_p,
        total_claims=round(sum(c['claims'] for c in ADIC_CLASSES), 3),
        blended_loss_ratio=round(sum(c['claims'] for c in ADIC_CLASSES) / total_p * 100, 1),
        motor=dict(premium=round(motor_p, 3), claims=round(motor_c, 3),
                   loss_ratio=round(motor_c / motor_p * 100, 1),
                   share=round(motor_p / total_p * 100, 1)),
        non_motor=dict(premium=round(non_p, 3), claims=round(non_c, 3),
                       loss_ratio=round(non_c / non_p * 100, 1),
                       share=round(non_p / total_p * 100, 1)),
        instant_insurance_acquisition_9m=INSTANT_INSURANCE_ACQUISITION_9M,
    )


def motor_commission_entitlement(loss_ratio: float) -> dict:
    """Where a given motor loss ratio sits on the signed sliding scale.

    This exists because the scale is easy to read backwards. A LOW loss ratio
    earns a HIGH commission; the 41% top rate needs a loss ratio at or below
    56%. Reading it the other way round turns a warning sign into an imagined
    receivable, which is exactly the mistake this function prevents.
    """
    scale = [(56.0, 41.0), (57.5, 39.5), (59.0, 38.0), (60.5, 36.5),
             (62.0, 36.0), (63.5, 34.5), (65.0, 33.0), (66.5, 31.5),
             (68.0, 30.0), (69.5, 29.5), (71.0, 28.0), (72.5, 26.5),
             (74.0, 25.0), (75.0, 24.0)]
    commission = 24.0            # floor
    for threshold, rate in scale:
        if loss_ratio <= threshold:
            commission = rate
            break
    # What the reinsurer actually earns: 100 less the loss ratio less the
    # commission it pays back. The "reinsurer margin" column printed on the
    # slip is a parameter of the scale at each rung, NOT the outcome at our
    # loss ratio — at 85.8% the reinsurer is 9.8 points under water.
    reinsurer_result = round(100.0 - loss_ratio - commission, 1)
    return dict(
        loss_ratio=loss_ratio,
        commission=commission,
        reinsurer_result=reinsurer_result,
        reinsurer_result_at_cap=round(100.0 - 80.0 - 24.0, 1),
        at_floor=commission == 24.0,
        above_cap=loss_ratio > 80.0,
        provisional=25.0,
        note=('At or above a 75% loss ratio the scale pays its 24% floor, and '
              'the treaty caps the loss ratio it will carry at 80%. A book '
              'running above the cap is not owed a commission top-up — it is '
              'a book the reinsurer is losing money on, and that is a renewal '
              'risk, not a receivable.')
    )


def cost_base_analysis() -> dict:
    """Total cost base per insurer, counting both places expenses are reported."""
    rows = []
    for name, d in COST_BASE.items():
        si, sb, oi, ob = d['staff_ise'], d['staff_below'], d['other_ise'], d['other_below']
        # Sunshine publishes no staff-cost note, so its staff and total are
        # unknown — not zero. Its below-the-line figure is still real.
        staff = None if si is None else round(si + sb, 3)
        total = None if staff is None else round(staff + oi + ob, 3)
        below_only = None if sb is None else round(sb + ob, 3)
        rows.append(dict(
            name=name, revenue=d['revenue'],
            staff_ise=si, staff_below=sb, staff_total=staff,
            other_ise=oi, other_below=ob,
            below_line_only=below_only, total_cost=total,
            staff_pct=None if staff is None else round(staff / d['revenue'] * 100, 1),
            total_pct=None if total is None else round(total / d['revenue'] * 100, 1),
            below_only_pct=(None if below_only is None
                            else round(below_only / d['revenue'] * 100, 1)),
            note=d['note'], is_us=(name == 'Alpha Direct'),
        ))
    healthy = [r for r in rows if not r['is_us'] and r['name'] != 'WestSure']

    def med(key):
        v = sorted(r[key] for r in healthy if r[key] is not None)
        if not v: return None
        m = len(v) // 2
        return round(v[m] if len(v) % 2 else (v[m-1] + v[m]) / 2, 1)

    return dict(rows=sorted(rows, key=lambda r: (not r['is_us'], -r['revenue'])),
                median_staff_pct=med('staff_pct'),
                median_total_pct=med('total_pct'),
                provisions=ADIC_PROVISIONS,
                running_cost_base=33.226,
                underwriting_cost_base=38.710,
                basis_note=(
                    'Running cost base excludes provisions so that the peer '
                    'comparison is like-for-like. Adding Alpha Direct\'s '
                    'P5.484m of provisions gives the P38.710m underwriting '
                    'cost base used by the combined ratio.'))


def expense_lines() -> dict:
    """Alpha Direct's own cost lines, grouped, largest group first."""
    groups: dict[str, list] = {}
    for group, line, fy25, fy26 in EXPENSE_LINES:
        groups.setdefault(group, []).append(
            dict(line=line, fy25=fy25, fy26=fy26,
                 change=fy26 - fy25,
                 change_pct=round((fy26 - fy25) / fy25 * 100, 1) if fy25 else None))
    total26 = sum(l['fy26'] for g in groups.values() for l in g)
    out = []
    for g, lines in groups.items():
        g25 = sum(l['fy25'] for l in lines)
        g26 = sum(l['fy26'] for l in lines)
        out.append(dict(group=g, fy25=g25, fy26=g26, change=g26 - g25,
                        share=round(g26 / total26 * 100, 1),
                        lines=sorted(lines, key=lambda l: -l['fy26'])))
    return dict(basis=EXPENSE_BASIS, total_fy25=sum(g['fy25'] for g in out),
                total_fy26=total26, groups=sorted(out, key=lambda g: -g['fy26']))


def peer_profiles() -> list[dict]:
    """The eight statements, in the order the report reads them: our size twin
    first, then largest to smallest, with the distressed peer last but never
    dropped."""
    from reporting.peer_profiles import PROFILES
    order = ['Phoenix', 'BIC', 'Hollard', 'Old Mutual', 'Bryte',
             'Insure Guard', 'WestSure', 'Sunshine']
    out = []
    for key in order:
        p = dict(PROFILES[key])
        p['key'] = key
        p['flag'] = ('twin' if key == 'Phoenix'
                     else 'distressed' if key == 'WestSure' else '')
        out.append(p)
    return out


def build_detail() -> dict:
    return dict(units=UNITS, cost_base=cost_base_analysis(),
                profiles=peer_profiles(),
                expenses=expense_lines(), classes=class_analysis(),
                bic_cession=[dict(cls=c, revenue=r, ceded=d,
                                  pct=round(d / r * 100, 1))
                             for c, r, d in BIC_CESSION],
                hollard_cession=[dict(treaty=t, ceded=c, recovered=r,
                                      estimates=e, net=n)
                                 for t, c, r, e, n in HOLLARD_CESSION_BY_TREATY],
                adic_treaties=ADIC_TREATIES,
                adic_ceding_commission_rate=ADIC_CEDING_COMMISSION_RATE,
                motor_entitlement=motor_commission_entitlement(85.8))
