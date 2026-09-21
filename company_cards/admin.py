from django.contrib import admin

from company_cards.models import (CardSpend, CardStatement, CardStatementLine,
                                  CompanyCard)


@admin.register(CompanyCard)
class CompanyCardAdmin(admin.ModelAdmin):
    list_display = ('label', 'last4', 'holder', 'company', 'is_active')
    list_filter = ('is_active', 'company')


@admin.register(CardSpend)
class CardSpendAdmin(admin.ModelAdmin):
    list_display = ('spent_on', 'card', 'amount', 'merchant', 'status', 'uploaded_by')
    list_filter = ('status', 'card')
    search_fields = ('merchant', 'what_for')


@admin.register(CardStatement)
class CardStatementAdmin(admin.ModelAdmin):
    list_display = ('card', 'period_year', 'period_month', 'uploaded_by')


@admin.register(CardStatementLine)
class CardStatementLineAdmin(admin.ModelAdmin):
    list_display = ('posted_on', 'statement', 'amount', 'description', 'waived')
    list_filter = ('waived',)
