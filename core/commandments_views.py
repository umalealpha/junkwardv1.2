"""
core/commandments_views.py — endpoints for the monthly /commandments page.

  GET /api/v1/commandments/             → list of all 9 categories
  GET /api/v1/commandments/<key>/       → 10 commandments for current month
                                          (?year=YYYY&month=MM optional)
"""
from __future__ import annotations

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView


class CommandmentCategoryListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from core.commandments import list_categories
        return Response({'categories': list_categories()})


class MonthlyCommandmentsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, category: str):
        from core.commandments import CATEGORIES, get_monthly_commandments

        if category not in CATEGORIES:
            return Response(
                {'detail': f'Unknown category "{category}".',
                 'allowed': list(CATEGORIES.keys())},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            year  = request.query_params.get('year')
            month = request.query_params.get('month')
            year  = int(year)  if year  else None
            month = int(month) if month else None
        except ValueError:
            return Response({'detail': 'year and month must be integers.'},
                            status=status.HTTP_400_BAD_REQUEST)

        return Response(get_monthly_commandments(category, year=year, month=month))
