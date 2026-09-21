#!/usr/bin/env python
"""
seed_replace_adic_fy26.py — replace the ADIC FY26 YTD JE with a 9-month
(Jul 2025 – Mar 2026) version pulled fresh from Odoo.

The previous JE was dated 2026-04-30 with 10 months of data, but the CFO
asked for a Mar-31 cutoff to align with the other subsidiaries' FY26 YTD
JEs (which all sit at 2026-03-31).

After this runs:
  - Any prior ADIC FY26 YTD JE (entry_date 2026-04-30, description
    starting "FY26 YTD GL") is deleted along with its lines
  - One new JE is posted at 2026-03-31 with 203 net-position lines,
    Dr = Cr = BWP 1,010,712,360.74, tagged to company=ADIC

Idempotent — re-running is a no-op once the new JE exists.

Run:
    python manage.py shell < ops/seeds/seed_replace_adic_fy26.py
"""
from calendar import monthrange
from datetime import date
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from core.models import Company
from ledger.models import Account, FiscalPeriod, JournalEntry, JournalEntryLine

ZERO = Decimal('0.00')
User = get_user_model()

OLD_ENTRY_DATE = date.fromisoformat('2026-04-30')
NEW_ENTRY_DATE = date.fromisoformat('2026-03-31')
NEW_DESCRIPTION = 'FY26 YTD GL — 9 months Jul 2025 – Mar 2026 from Odoo direct pull'

# 203 lines, balanced at Dr=Cr=BWP 1,010,712,360.74.
# Source: Odoo company_id=4 (ADIC), date_from=2025-07-01, date_to=2026-03-31.
# Code 48374600014924 (stray BWP 78 from Odoo) is routed to 999999 (suspense).
LINES = [
    ('100001', 235410.92, 19473412.57),
    ('100003', 0, 66415623.09),
    ('100004', 0, 5283859.45),
    ('100006', 2113461.08, 0),
    ('100007', 0, 197379.67),
    ('100009', 109192.47, 5564252.13),
    ('100010', 0, 1819692.61),
    ('100012', 0, 78480),
    ('101000', 4703.43, 0),
    ('101005', 4452041.14, 816881.7),
    ('101006', 41260972.56, 0),
    ('101007', 7302758.84, 0),
    ('101008', 4175938.38, 394500.6),
    ('101010', 2915677.54, 0),
    ('101011', 42457.73, 0),
    ('101502', 90780461.72, 90779961.72),
    ('101503', 90781011.72, 90781011.72),
    ('102001', 876711, 5705925.37),
    ('103000', 2149587.44, 0),
    ('103003', 173261, 0),
    ('103004', 78947.37, 0),
    ('103005', 140144.08, 0),
    ('103006', 5536631.44, 1966683.8),
    ('103008', 162859.85, 0),
    ('103010', 4871379.15, 0),
    ('103012', 46860372.25, 5864842.05),
    ('103013', 32700.29, 0),
    ('103014', 4292908.18, 5400),
    ('104000', 0, 507358.31),
    ('104001', 11495.81, 178853.19),
    ('104004', 0, 596540.15),
    ('104007', 0, 29430.26),
    ('104008', 1459122.88, 2180564.38),
    ('104009', 0, 3185408.98),
    ('104010', 0, 30839759.97),
    ('104013', 0, 1620442.37),
    ('104014', 3166476.52, 2594360.16),
    ('104016', 0, 14453.33),
    ('105002', 0, 523067.3),
    ('105004', 0, 1643539.5),
    ('106000', 340249.57, 2769852.1),
    ('106001', 0, 10315243.15),
    ('106002', 0, 2046682.62),
    ('106003', 0, 1427557.3),
    ('107000', 4445709.21, 436780.64),
    ('107005', 2596871.84, 0),
    ('107006', 792578.88, 0),
    ('107007', 1654080.35, 0),
    ('107008', 780885.97, 0),
    ('107010', 157330.05, 64648.07),
    ('107011', 362009.91, 0),
    ('109002', 153091.4, 1898.24),
    ('110004', 17687.5, 0),
    ('110005', 985894.4, 363186.2),
    ('110006', 247481.42, 5079),
    ('110008', 517500, 0),
    ('110009', 522329.92, 204926.59),
    ('110010', 7674864.7, 54124.73),
    ('110011', 1130900.43, 0),
    ('110012', 750, 0),
    ('110016', 3442.13, 0),
    ('111000', 0.02, 59463.56),
    ('111001', 219, 0),
    ('111008', 220073.21, 0),
    ('111010', 153076.63, 0),
    ('111011', 97804.66, 0),
    ('111012', 39146.58, 0),
    ('111013', 16500, 0),
    ('111014', 131865.55, 0),
    ('111015', 910396.59, 0),
    ('111016', 5385.87, 0),
    ('111017', 46730.02, 0),
    ('111020', 20018, 0),
    ('111021', 228051.59, 0),
    ('111022', 1526647.13, 98000),
    ('111023', 16826.75, 0),
    ('111024', 37431.82, 18584.41),
    ('111026', 15682.3, 0),
    ('111027', 72356.17, 0),
    ('111028', 147839.44, 0),
    ('111029', 234483.97, 0),
    ('111030', 151322.01, 207540.41),
    ('111032', 16843.51, 0),
    ('111034', 19400, 0),
    ('111035', 31920.31, 0),
    ('111036', 197593.81, 4923.52),
    ('111037', 153588.11, 279.42),
    ('111038', 29148.03, 0),
    ('111039', 2112.51, 0),
    ('111041', 216346.36, 0),
    ('111043', 47516.97, 0),
    ('111045', 18798.87, 0),
    ('112000', 121636.44, 0),
    ('112001', 1523040.9, 187720.7),
    ('112002', 1491905.48, 0),
    ('112003', 743606.53, 0),
    ('113002', 2101818.57, 0),
    ('114000', 298972.62, 131.92),
    ('114001', 137536.17, 0),
    ('114002', 257098.8, 0),
    ('115001', 833661.53, 0),
    ('115002', 614904.63, 6612),
    ('116002', 583370.22, 9300),
    ('117000', 1653001.54, 0),
    ('117001', 210000, 0),
    ('118001', 522373.89, 0),
    ('118005', 144210.22, 0),
    ('118006', 62521.14, 0),
    ('118008', 0, 112398),
    ('118745', 118093.74, 0),
    ('119000', 5727701.73, 2632525),
    ('122001', 1352329.51, 0),
    ('123001', 0.57, 17117.86),
    ('124000', 0, 138860.25),
    ('124001', 0, 2229770.86),
    ('124005', 10459, 0),
    ('135', 6717336.05, 14135995),
    ('147', 11891912.61, 2981202.36),
    ('183', 55238.48, 0),
    ('200001', 0, 491648.47),
    ('20019', 89.55, 0),
    ('201001', 20000, 0),
    ('201002', 493321.26, 0),
    ('201003', 2632525, 5727701.73),
    ('201006', 50, 0),
    ('201007', 795067.36, 0),
    ('201008', 183302.23, 0),
    ('201010', 313832.96, 0),
    ('202001', 4388838.59, 4336149.63),
    ('202006', 203601.91, 0),
    ('202007', 90350.95, 49524.46),
    ('203001', 358515.8, 340278.73),
    ('204002', 0, 60000),
    ('204004', 1058910, 1120447.5),
    ('205001', 12101286.55, 876711),
    ('205002', 0, 6395361.18),
    ('208001', 2981202.36, 11891912.61),
    ('208002', 3220276, 0),
    ('208003', 66319951.63, 68546732.01),
    ('208004', 14135995, 6717336.05),
    ('208005', 4229255.86, 4960021.55),
    ('208008', 24380.2, 32700.29),
    ('209001', 16663639.57, 15646384.63),
    ('210002', 475000, 0),
    ('211001', 248542.17, 423852.54),
    ('211002', 1431400.8, 1129768),
    ('211003', 77549.88, 0),
    ('212001', 2769852.1, 340249.56),
    ('2120015', 29430.26, 0),
    ('212002', 2243889.12, 0),
    ('212003', 2483805.14, 1918360.84),
    ('212004', 1822387.83, 540744.93),
    ('212006', 3653817.54, 9028310.15),
    ('212007', 5418469.13, 8350312.6),
    ('212008', 1943785.03, 2915677.54),
    ('212010', 3486169.76, 0),
    ('212012', 8556789.3, 8662758.79),
    ('212015', 1017630.01, 974707.38),
    ('212016', 26264.27, 42457.73),
    ('213001', 10870.67, 156566.96),
    ('213002', 0, 17687.5),
    ('213003', 0, 44633.22),
    ('214001', 340448, 340448),
    ('214002', 29872614.49, 30053045.7),
    ('214003', 0, 6033),
    ('215001', 1734656.49, 1775248.3),
    ('215002', 0, 49589),
    ('215003', 7098572.72, 7098573.48),
    ('215004', 483486.97, 1046386.39),
    ('215008', 561217.27, 380000),
    ('220002', 50679.09, 0),
    ('220003', 394780.2, 11298),
    ('220004', 57163.61, 0),
    ('230001', 0, 575811.46),
    ('230002', 839, 284869.58),
    ('240001', 596068.9, 596068.9),
    ('260001', 1873635.03, 1863201.71),
    ('280001', 4980000.02, 4734106.81),
    ('280003', 8101578.44, 4876550.76),
    ('280004', 0, 4299374.13),
    ('280005', 8549929.87, 13023995.71),
    ('280006', 121577281.61, 119359979.73),
    ('280007', 65383570.2, 66034934.1),
    ('280008', 53.67, 20005.32),
    ('280009', 123913.44, 128799.37),
    ('280010', 1130001.29, 1190113.63),
    ('280012', 200718.51, 202337.72),
    ('280013', 5278.11, 3723.47),
    ('280014', 0, 89.55),
    ('280017', 92081011.72, 92080611.72),
    ('280023', 112061, 50889.58),
    ('280024', 5385075.94, 4116697.78),
    ('280029', 6033, 0),
    ('290001', 6024281.77, 6472591.31),
    ('290002', 83965292.9, 85244338.36),
    ('290003', 23359031.58, 23359032.22),
    ('290005', 2075619.58, 40972.53),
    ('29004', 18085.19, 43972.03),
    ('303', 283000, 0),
    ('999999', 78, 0),
    ('78', 4800.61, 0),
    ('91', 13134.05, 8676.84),
    ('92', 10124.05, 8908.68),
]


def _get_admin():
    u = User.objects.filter(is_superuser=True).first() or User.objects.first()
    if not u:
        raise SystemExit('No User in DB; create a superuser before running.')
    return u


def main():
    user = _get_admin()
    adic = Company.objects.get(code='ADIC')

    # Skip if the new JE is already in place.
    if JournalEntry.objects.filter(
        entry_date=NEW_ENTRY_DATE, description=NEW_DESCRIPTION
    ).exists():
        print(f'  Skipping — JE already exists at {NEW_ENTRY_DATE}')
        return

    with transaction.atomic():
        old = JournalEntry.objects.filter(
            entry_date=OLD_ENTRY_DATE,
            description__startswith='FY26 YTD GL',
            company=adic,
        )
        old_pks = list(old.values_list('pk', flat=True))
        if old_pks:
            JournalEntryLine.objects.filter(journal_entry_id__in=old_pks).delete()
            JournalEntry.objects.filter(pk__in=old_pks).delete()
            print(f'  Removed {len(old_pks)} prior ADIC FY26 YTD JE(s).')
        else:
            print('  No prior ADIC FY26 YTD JE found — posting fresh.')

        period_name = NEW_ENTRY_DATE.strftime('%Y-%m')
        last_day = monthrange(NEW_ENTRY_DATE.year, NEW_ENTRY_DATE.month)[1]
        period, _ = FiscalPeriod.objects.get_or_create(
            period_name=period_name,
            defaults={
                'start_date': NEW_ENTRY_DATE.replace(day=1),
                'end_date':   NEW_ENTRY_DATE.replace(day=last_day),
                'status':     FiscalPeriod.Status.OPEN,
            },
        )
        if period.status != FiscalPeriod.Status.OPEN:
            period.status = FiscalPeriod.Status.OPEN
            period.save(update_fields=['status'])
            print(f'  Reopened fiscal period {period_name} for posting.')

        je = JournalEntry.objects.create(
            entry_date=NEW_ENTRY_DATE,
            description=NEW_DESCRIPTION,
            source_type='import',
            journal_type='general',
            currency_code_id='BWP',
            exchange_rate=Decimal('1'),
            is_related_party=False,
            company=adic,
            created_by=user,
            status='draft',
        )
        total_dr = total_cr = ZERO
        missing = []
        for code, dr, cr in LINES:
            acct = Account.objects.filter(code=code).first()
            if not acct:
                missing.append(code)
                continue
            d = Decimal(str(dr or 0))
            c = Decimal(str(cr or 0))
            JournalEntryLine.objects.create(
                journal_entry=je, account=acct,
                debit_amount=d, credit_amount=c,
                debit_bwp=d, credit_bwp=c,
            )
            total_dr += d
            total_cr += c
        if missing:
            raise SystemExit(
                f'Account codes missing from CoA — run seed_coa_v2.py first: {missing[:10]}'
            )
        print(f'  {len(LINES)} lines, Dr {total_dr:,.2f}, Cr {total_cr:,.2f}, '
              f'diff {(total_dr-total_cr):,.2f}')
        if total_dr != total_cr:
            raise SystemExit('  JE does not balance — aborting')
        je.post(user=user, _allow_direct=True)
        print(f'  Posted {je.entry_number} ({NEW_DESCRIPTION})')


main()
