"""banking/balance_views.py — the Morning Bank Balances endpoint.

``GET /api/v1/banking/balances/`` and nothing else, for ever. Omni never moves
money; this reads what the bank told us and says how old it is. The shape is
fixed by BALANCES_CONTRACT.md.
"""
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import CanViewFinancials

from .balances import build_balances_payload


class BankBalancesView(APIView):
    """What is actually in the bank this morning, and how sure we are of it.

    GET only — ``http_method_names`` is pinned rather than left to DRF's
    default so that adding a ``post()`` to this class would not silently open
    a write path onto the bank screen. There is nothing here to write: a
    balance is the bank's fact, and Omni never moves money.
    """

    permission_classes = [IsAuthenticated, CanViewFinancials]
    http_method_names  = ['get', 'head', 'options']

    def get(self, request):
        # ``request.query_params`` is deliberately not read, and in particular
        # ``?company=<uuid>`` is IGNORED. The frontend's apiFetch appends that
        # to every GET from the sidebar company switcher, so it arrives here
        # whether anyone meant it or not — and this screen is group-wide by
        # design: six accounts across Alpha Direct, Veritas, Risk Software and
        # Unicoin, with a headline that is the cash across all four. Filtering
        # on company would quietly turn that headline into one company's cash
        # while still labelling it the total — a wrong number presented as
        # right, on the screen the CFO uses to decide what he can pay today.
        # Do not add a company filter here.
        return Response(build_balances_payload())
