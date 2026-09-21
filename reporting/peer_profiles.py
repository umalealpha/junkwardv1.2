"""peer_profiles.py — line-level transcription of the eight FY2025 statements.

Supplied 10 September 2026 from the Registrar. See peer_benchmark.py for the
derived ratios and peer_benchmark_detail.py for the cost-base analysis.
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# 5. PEER PROFILES — the full transcription of each signed statement
# ---------------------------------------------------------------------------
# Every expense line, class split and reinsurance component each insurer
# actually discloses, at the lowest level published. This is the record the
# narrative rests on: if a figure in the report is challenged, it is checked
# here first and then against the statement itself.
#
# Units differ by insurer and are stated per profile — Old Mutual and Bryte
# report in thousands, the rest in Pula. Nothing here is scaled; the screen
# converts for comparison and says so.
#
# Where a statement does not disclose something it is absent, not zero. Where
# a statement disagrees with itself (Bryte's expense sub-notes, Old Mutual's
# reinsurance gross versus net) the note field says so rather than picking a
# side.

PROFILES = {
 'BIC': dict(
   full='Botswana Insurance Company Limited', ye='31 Dec 2025', units='Pula',
   revenue=718_048_108, ise=475_016_375, net_ri=159_884_769, isr=83_146_964,
   pbt=64_611_598, tax=14_664_224, pat=49_947_374,
   ta=634_880_462, te=154_761_504, cash=194_606_564,
   ise_components=[('Incurred claims incl. risk adjustment',308_187_900),
                   ('Amortisation of acquisition cash flows',106_813_238),
                   ('Other directly attributable expenses',60_015_236)],
   staff=[('Salaries and wages',52_322_591),('Pension - defined contribution',4_169_182)],
   staff_total=56_491_773, staff_ise=41_023_851, staff_below=18_753_761,
   opex_below=[('Employee benefit expense',18_753_761),('Management fees',12_973_970),
               ('Computer expenses',2_228_369),('Sundry expenses',1_896_427),
               ('Advertising and promotions',1_266_598),('Depreciation - PPE',977_473),
               ('Auditors remuneration',732_608),('Travel and accommodation',652_204),
               ('Education and training',610_299),('Consulting fees',554_548),
               ('Insurance',539_776),('Directors remuneration',381_302),
               ('Utilities',125_373),('Operating lease rentals',118_099),
               ('Repairs and maintenance',106_071),('Telephone',46_426)],
   opex_below_total=41_963_305,
   opex_ise=[('Employee benefit expense',41_023_851),('Computer expenses',4_874_556),
             ('Advertising and promotions',2_770_684),('Depreciation - PPE',2_138_223),
             ('Auditors remuneration',1_602_581),('Sundry expenses',1_583_598),
             ('Travel and accommodation',1_426_697),('Education and training',1_335_030),
             ('Consulting fees',1_213_075),('Insurance',1_180_759),
             ('Utilities',274_254),('Operating lease rentals',258_343),
             ('Repairs and maintenance',232_030),('Telephone',101_556)],
   opex_ise_total=60_015_236,
   classes=[('Specialised lines',231_517_994,93_590_991),('Motor',196_151_942,178_068_974),
            ('Property',174_968_966,135_256_509),('Liability',43_071_277,23_656_857),
            ('Engineering',36_326_928,11_218_402),('Accident',22_651_822,11_829_423),
            ('Others',13_359_178,21_395_220)],
   cession=[('Specialised lines',231_517_994,177_431_312,32_227_770),
            ('Property',174_968_966,138_638_831,32_713_688),
            ('Engineering',36_326_928,22_985_982,2_678_098),
            ('Liability',43_071_277,16_007_700,2_545_255),
            ('Motor',196_151_942,3_929_469,499_627),
            ('Accident',22_651_822,2_718_967,699_646),
            ('Others',13_359_178,3_321_933,354_878)],
   ri_commission=71_718_961, ceded=365_034_194, recovered=138_637_005,
   ri_other=[('Non-performance risk adjustment', 5_206_541)],
   acq=106_813_238, pct_note='Prescribed Capital Target cover 1.53x (2024 1.26x).',
   headline='Four times the size of the next insurer. Cedes 79% of property and '
            '2% of motor. Earns P71.7m of ceding commission — more than our entire '
            'net earned premium.'),

 'Hollard': dict(
   full='The Hollard Insurance Company of Botswana', ye='30 Jun 2025', units='Pula',
   revenue=444_463_146, ise=338_150_301, net_ri=58_094_408, isr=48_218_437,
   pbt=44_894_953, tax=10_513_147, pat=34_381_806,
   ta=None, te=149_886_651, cash=None,
   ise_components=[('Incurred claims and directly attributable expenses',274_580_704),
                   ('Amortisation of acquisition cash flows',68_915_523),
                   ('Changes to liabilities for incurred claims',-5_345_928)],
   staff=[('Salaries, wages, bonuses and other benefits',45_630_873),('Pension costs',4_257_743)],
   staff_total=49_888_616, staff_ise=48_690_294, staff_below=1_198_322,
   opex_below=[('Auditors remuneration - external',4_761_582),('Consulting fees',5_632_371),
               ('Management fees',3_989_574),('Computer expenses',2_934_075),
               ('Employee benefit expense',1_198_322),('License fees',1_040_552),
               ('Other overhead costs',854_636),('Directors fees',660_600),
               ('Advertising and promotions',373_863),('Legal and professional fees',202_421),
               ('Administrative expenses',133_940)],
   opex_below_total=21_781_936,
   opex_ise=[('Employee benefit expense',48_690_294),('Other overhead costs',8_634_260),
             ('Computer expenses',8_251_974),('Administrative expenses',2_625_338),
             ('Advertising and promotions',2_108_015)],
   opex_ise_total=70_309_881,
   classes=[('Motor',192_745_130,164_765_278),('Other (liability)',150_514_367,116_942_595),
            ('Property',101_203_649,56_442_427)],
   cession=None,
   treaty_cession=[('Facultative',77_191_327,20_179_944),('Excess of loss',19_654_533,0),
                   ('Treaty',9_565_426,27_941_214)],
   ri_commission=None, ceded=106_411_285, recovered=48_121_158,
   acq=68_915_523,
   pct_note='Dividends of P20.0m paid in FY25 (FY24 P29.0m).',
   headline='Same 30 June year-end as us — the cleanest like-for-like in the market. '
            'Cedes only 23.9%, almost all facultative on individual large risks '
            'rather than a whole class.'),

 'Old Mutual': dict(
   full='Old Mutual Short-Term Insurance (Botswana) Limited', ye='31 Dec 2025', units="P'000",
   revenue=304_906, ise=298_409, net_ri=-4_948, isr=11_445,
   pbt=22_213, tax=2_670, pat=19_543,
   ta=286_313, te=138_308, cash=148_587,
   gwp=284_737,
   ise_components=[('Incurred claims',124_410),('Adjustments to liabilities for incurred claims',97_562),
                   ('Amortisation of acquisition cash flows',71_934),
                   ('Losses on onerous contracts',4_503)],
   staff=[('Employee costs (single line, not itemised)',16_683)],
   staff_total=16_683, staff_ise=16_683, staff_below=0,
   opex_below=[('Other expenses (fx, head office, travel)',2_756),('Depreciation',919),
               ('Audit fees',799)],
   opex_below_total=4_474,
   opex_ise=[('Management fees to group companies',33_943),('Employee costs',16_683)],
   opex_ise_total=50_626,
   mgmt_fee_split=[('Old Mutual Financial Services Botswana',23_576),
                   ('Old Mutual Insure Limited',8_284),
                   ('Old Mutual (Africa) Holdings',2_083)],
   classes=None, cession=None,
   ri_commission=None, ceded=140_640, recovered=69_587,
   ri_note='The net figure of 4,948 INCOME comes from note 18.3, which is all '
           'the P&L discloses. The 140,640 ceded and 69,587 recovered come '
           'from the note 9 reinsurance-asset roll-forward, a different basis. '
           'They do not reconcile to the net and are shown for scale only.',
   acq=71_934,
   pct_note='Met minimum capital requirements throughout the year; no ratio disclosed. '
            'Motor loss ratio 63%, non-motor 32% (stated qualitatively).',
   headline='Thinnest disclosure in the set — four lines in the whole expense note. '
            'A P33.9m group management fee is its single largest cost and sits '
            'inside insurance service expenses.'),

 'Phoenix': dict(
   full='Phoenix of Botswana Assurance Company (Pty) Ltd', ye='31 Dec 2025', units='Pula',
   revenue=125_555_169, ise=50_054_391, net_ri=46_236_120, isr=29_264_657,
   pbt=9_556_160, tax=1_113_174, pat=8_442_986,
   ta=101_337_543, te=32_399_201, cash=52_726_161,
   gwp=133_062_993,
   ise_components=[('Insurance claims service expense',38_573_660),
                   ('Movement in risk adjustment',1_151_676),
                   ('Amortisation of acquisition expenses',10_329_055)],
   staff=[('Salaries, wages, bonuses and other benefits',7_975_394),('Termination benefits',86_814)],
   staff_total=8_062_208, staff_ise=0, staff_below=8_062_208,
   opex_below=[('Employee costs',8_425_173),('Administration and management fees',4_407_817),
               ('Depreciation',2_233_323),('Staff welfare',986_098),
               ('Business development expenses',718_597),('IT expenses',812_474),
               ('Auditor - external',350_000),('Printing and stationery',305_405),
               ('Levies',283_156),('Telephone and fax',253_204),
               ('Subscriptions',224_416),('Advertising',213_167),
               ('Bank charges',187_732),('Motor vehicle expenses',180_115),
               ('Leases of low value assets',162_315),('Board sitting fees',147_191),
               ('Auditor - internal',113_830),('Travel - local',57_197),
               ('Insurance',58_209),('Recruitment expenses',50_941),
               ('Secretarial fees',33_200),('Office upkeep and maintenance',388_785),
               ('Amortization',12_745),('Postage',4_815),
               ('Work permit expenses',4_500),('Donations',2_092)],
   opex_below_total=20_616_498,
   opex_ise=[], opex_ise_total=0,
   classes=None, cession=None,
   ri_commission=None, ceded=87_976_705, recovered=41_740_585,
   acq=10_329_055,
   pct_note='No numeric solvency ratio disclosed. Standard going-concern statement.',
   headline='THE SIZE TWIN. Gross written premium P133.1m against our P133.4m, same '
            'market, same year — and profit after tax of P8.4m against our P2.6m. '
            'Cedes 70% yet still earns a 23.3% insurance service margin.'),

 'Bryte': dict(
   full='BICB Limited trading as Bryte Risk Services Botswana', ye='31 Dec 2025', units="P'000",
   revenue=165_637, ise=159_639, net_ri=-21_605, isr=27_603,
   pbt=18_648, tax=-2_297, pat=20_945,
   ta=335_374, te=119_752, cash=65_691,
   ise_components=[('Losses on claims, undiscounted',124_595),
                   ('Amortisation of acquisition cash flows',35_317),
                   ('Losses on claims, discounted',-229)],
   ise_note='These three tie to the 159,639 total within 44 of OCR drift. The '
            '8,386 of other directly attributable expenses disclosed in note '
            '17.2 sits OUTSIDE this total, and note 8.4 gives a different '
            'figure for the same item — the source disagrees with itself.',
   staff=[('Salaries and wages (opex)',10_785),('Salaries and wages (ISE)',5_428),
          ('Share-based remuneration',967),('Defined contribution plans',565),
          ('Employee benefit expenses',427)],
   staff_total=18_171, staff_ise=6_009, staff_below=12_162,
   opex_below=[('Remuneration',12_162),('Administration and other expenses',4_672),
               ('Audit, legal and tax fees',3_673),('Consulting fees',1_346),
               ('Marketing expenses',1_146),('Depreciation',971),
               ('IT expenses',883),('Office expenses',245)],
   opex_below_total=25_098,
   opex_ise=[('Remuneration',6_009),('Administration and other expenses',998),
             ('Consulting fees',942),('Travel and entertainment',332),
             ('Depreciation',296),('IT expenses',147),('Audit, legal and tax fees',32)],
   opex_ise_total=8_656,
   classes=[('Property',66_711,91_589),('Motor',49_327,20_433),('Other',49_599,12_101)],
   class_note='The expense column is the incurred-claims component only and sums '
              'to 124,123. The remaining 35,516 of acquisition amortisation is '
              'not allocated to class in the source.',
   cession=None,
   ri_commission=None, ceded=50_618, recovered=76_732,
   acq=35_317,
   pct_note='Prescribed Capital Target requirement P47.1m. FY25 carried a tax CREDIT '
            'of P2.3m, which is why profit exceeds pre-tax profit.',
   headline='Fairfax-owned. Net reinsurance was a NET INCOME of P21.6m in FY25 — '
            'recoveries exceeded the premium ceded, because property claims were heavy. '
            'Scanned source with OCR damage; treat the small lines with care.'),

 'Insure Guard': dict(
   full='BIHL Insurance Company Limited trading as Insure Guard', ye='31 Dec 2025', units='Pula',
   revenue=76_350_145, ise=44_769_497, net_ri=3_364_064, isr=28_216_584,
   pbt=15_688_056, tax=4_664_837, pat=11_023_219,
   ta=125_602_714, te=79_507_563, cash=50_977_310,
   nwp=72_687_493,
   ise_components=[('Amortisation of acquisition cash flows',18_592_986),
                   ('Incurred claims incl. risk adjustment',18_110_151),
                   ('Other directly attributable expenses',8_066_360)],
   staff=[('Employee benefit expense (ISE)',8_904_126),('Employee benefit expense (below line)',6_042_085)],
   staff_total=14_946_211, staff_ise=8_904_126, staff_below=6_042_085,
   opex_below=[('Employee benefit expense',6_042_085),('Management fees',2_805_750),
               ('Computer expenses',1_110_165),('Other operating costs - admin',1_062_363),
               ('Directors fees - sitting allowances',974_893),('Advertising and promotions',803_781),
               ('Depreciation on right-of-use asset',567_637),('Auditors remuneration',313_774),
               ('Depreciation of property and equipment',305_499),
               ('Amortisation of intangible assets',228_142),('Telephone',121_523),
               ('Postages, courier, printing',98_531),('Insurance',91_608),
               ('Travel and accommodation',86_754),('Repairs and maintenance',47_667)],
   opex_below_total=14_660_173,
   opex_ise=[('Employee benefit expense',8_904_126),('Bank charges and premium collection fees',2_133_802),
             ('Computer expenses',1_636_032),('Cashback bonus expense',1_583_382),
             ('Other operating costs',1_399_249),('Advertising and promotions',1_184_520),
             ('Depreciation on right-of-use asset',836_518),('Auditors remuneration',462_404),
             ('Depreciation of property and equipment',450_209),
             ('Amortisation of intangible assets',336_209),('Telephone',179_087),
             ('Postages, courier, printing',145_203),('Insurance',135_001),
             ('Travel and accommodation',127_848),('Repairs and maintenance',70_246)],
   opex_ise_total=19_583_837,
   classes=[('Legal cover',56_019_094,None),('Motor',15_367_774,None),
            ('Property',2_383_304,None),('Liability',1_735_787,None),
            ('Accident',639_901,None),('Engineering',204_285,None)],
   cession=None,
   ri_commission=2_056_339, ceded=8_021_042, recovered=2_600_639,
   acq=18_592_986,
   pct_note='Capital adequacy 2.67x the Prescribed Capital Target (2024 2.40x) — '
            'the strongest in the set.',
   headline='Cedes almost nothing — 10.5% — and keeps the margin: a 37.0% insurance '
            'service margin, the highest in the market. Legal cover is 73% of its book. '
            'Motor grew from P4.1m to P15.4m in one year.'),

 'WestSure': dict(
   full='WestSure Insurance Botswana (Pty) Ltd', ye='28 Feb 2025', units='Pula',
   revenue=75_663_359, ise=53_120_556, net_ri=19_425_886, isr=3_116_917,
   pbt=-2_847_863, tax=-503_522, pat=-2_344_341,
   ta=87_623_330, te=-670_058, cash=14_161_624,
   ise_components=[('Incurred claims and other expenses',37_456_840),
                   ('Movement in expected cost of outstanding claims',12_650_394),
                   ('Change in IBNR reserve',2_987_827),
                   ('Loss adjustment on adoption of IFRS 17',1_642_700),
                   ('Change in unearned premium - gross',603_080),
                   ('Risk adjustment on adoption of IFRS 17',263_426),
                   ('Salvages',-2_483_711)],
   staff=[('Salaries and wages',6_657_280),('Staff incentive',499_904),
          ('Directors remuneration',195_000)],
   staff_total=7_352_184, staff_ise=0, staff_below=7_352_184,
   opex_below=[('Employee costs',7_352_184),('Consulting and professional fees',4_180_886),
               ('Other expenses',3_751_152),('Computer expenses',630_785),
               ('Auditor - external audit',501_695)],
   opex_below_total=16_416_702,
   opex_ise=[], opex_ise_total=0,
   commission_income=15_506_405, commission_expense=11_021_144,
   classes=[('Motor',37_581_878,None),('Property',23_998_343,None),
            ('Liability',9_067_972,None),('Transport',2_309_535,None),
            ('Engineering Other',1_998_254,None),('Accident and Health',375_589,None),
            ('Fidelity Guarantee',331_788,None)],
   cession=None,
   ri_commission=15_506_405, ceded=52_579_716, recovered=22_362_890,
   ri_components=[('Allocation of reinsurance premiums',52_579_716),
                  ('Amounts recoverable for incurred claims',-22_362_890),
                  ('Movement in expected cost of outstanding RI claims',-12_571_194),
                  ('Change in unearned premium reserve',-3_754_120),
                  ('Change in IBNR reserve',3_672_587),
                  ('Salvages',1_240_719),
                  ('Non-performance risk',733_810),
                  ('Risk adjustment on adoption of IFRS 17',-112_742)],
   acq=None,
   pct_note='PCT ratio 0.88 against a statutory minimum of 1.0. Liabilities exceed '
            'assets by P670,058. Material going-concern uncertainty flagged by the '
            'auditors. P10m capital injected 20 March 2025.',
   headline='THE WARNING CASE. Negative equity, failed its capital test, going concern '
            'flagged. Cedes 69.5% and still lost money. Excluded from every median in '
            'this document but kept visible.'),

 'Sunshine': dict(
   full='Sunshine Insurance Company of Botswana (Pty) Ltd', ye='30 Jun 2025', units='Pula',
   revenue=26_190_928, ise=19_315_055, net_ri=5_273_713, isr=1_602_160,
   pbt=582_240, tax=103_273, pat=478_967,
   ta=73_748_715, te=26_232_033, cash=46_010_714,
   ise_components=[('Other incurred directly attributable expenses',13_969_504),
                   ('Insurance acquisition cash flows amortisation',2_826_247),
                   ('Incurred claims and other directly attributable expenses',2_945_151),
                   ('Losses on onerous contracts',317_816),
                   ('Changes relating to past service',-743_664)],
   staff=[], staff_total=None, staff_ise=None, staff_below=None,
   opex_below=[('Right-of-use asset depreciation',573_572),('PPE depreciation',483_475),
               ('Defined contribution pension',37_810)],
   opex_below_total=3_147_054,
   opex_ise=[], opex_ise_total=13_969_504,
   classes=None, cession=None,
   ri_commission=None, ceded=7_200_937, recovered=None,
   acq=2_826_247,
   pct_note='No numeric solvency ratio. Standard going-concern statement. '
            'Combined ratios quoted by class in the onerous-contract test: '
            'property 128%.',
   headline='Shrinking hard — insurance revenue fell from P42.2m to P26.2m, down 38% '
            'in a single year. Publishes no staff-cost note at all. Profit of P0.5m '
            'came from P2.2m of finance income, not from underwriting.'),
}
