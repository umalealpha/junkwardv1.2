"""OD-4 split (Bokani 2026-06-24): Corporate (COM/COMG) vs Personal (DOM/DOMG),
INSTANT (MIS), OTHER. Verifies the classifier + the Collections Dashboard
grouping/total, engineered to Bokani's May-2026 anchor figures."""
from decimal import Decimal
from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from realpay.api_views import _classify_prefix, realpay_collections_dashboard
from realpay.models import RealPayTransaction


class OD4SplitTest(TestCase):
    def test_classify_prefix_split(self):
        self.assertEqual(_classify_prefix('MIS0001')[0], 'INSTANT')
        self.assertEqual(_classify_prefix('COM0001')[0], 'CORPORATE')
        self.assertEqual(_classify_prefix('COMG001')[0], 'CORPORATE')
        self.assertEqual(_classify_prefix('DOM0001')[0], 'PERSONAL')
        self.assertEqual(_classify_prefix('DOMG001')[0], 'PERSONAL')
        # Exact leading-letter run, NOT substring — these must fall to OTHER:
        self.assertEqual(_classify_prefix('MISC1')[0], 'OTHER')
        self.assertEqual(_classify_prefix('COMMERCIAL1')[0], 'OTHER')
        self.assertEqual(_classify_prefix('DOMESTIC1')[0], 'OTHER')
        self.assertEqual(_classify_prefix('XYZ1')[0], 'OTHER')
        self.assertEqual(_classify_prefix('')[0], 'OTHER')

    def test_dashboard_grouping_hits_may_anchor(self):
        U = get_user_model()
        u = U.objects.create_user('odtest', 'od@x.co', 'p')
        B = RealPayTransaction.Source.BILLING

        def row(cn, amt):
            RealPayTransaction.objects.create(
                source=B, client_number=cn, collected_amount=Decimal(amt),
                txn_date=date(2026, 5, 15), current_status='SUCCESSFUL')

        # Engineered to Bokani's May-2026 anchor: MIS 1,842,209.21;
        # Corporate+Personal combined 1,784,044.23; Total 3,626,253.44.
        row('MIS0001', '1842209.21')                                 # INSTANT
        row('COM0001', '1000000.00'); row('COMG001', '100000.00')    # CORPORATE 1,100,000.00
        row('DOM0001', '600000.00');  row('DOMG001', '84044.23')     # PERSONAL    684,044.23
        # A non-mapping row must NOT inflate any bucket (lands in OTHER):
        row('MISC999', '50.00')

        f = APIRequestFactory()
        req = f.get('/x', {'start': '2026-05-01', 'end': '2026-05-31'})
        force_authenticate(req, user=u)
        resp = realpay_collections_dashboard(req)
        self.assertEqual(resp.status_code, 200)
        g = {x['key']: x for x in resp.data['groups']}

        self.assertEqual(g['INSTANT']['collected'], '1842209.21')
        self.assertEqual(g['CORPORATE']['collected'], '1100000.00')
        self.assertEqual(g['PERSONAL']['collected'], '684044.23')
        # OD-4: the split still reconciles to Bokani's combined anchor.
        self.assertEqual(
            Decimal(g['CORPORATE']['collected']) + Decimal(g['PERSONAL']['collected']),
            Decimal('1784044.23'))
        # Grand total = MIS + Corp + Personal (OTHER's 50.00 also counts to total).
        self.assertEqual(Decimal(resp.data['total_collected']), Decimal('3626303.44'))
        self.assertEqual(g['OTHER']['collected'], '50.00')
