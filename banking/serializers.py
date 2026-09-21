"""banking/serializers.py"""
from rest_framework import serializers

from .models import BankAccount, BankRecRule, BankStatement, BankStatementLine


class BankAccountSerializer(serializers.ModelSerializer):
    gl_account_code = serializers.CharField(source='gl_account.code', read_only=True)
    gl_account_name = serializers.CharField(source='gl_account.name', read_only=True)
    currency        = serializers.CharField(source='currency_code_id', read_only=True)

    # CFO directive 2026-05-25 (BANK-001): /banking must show the same
    # GL truth as /reports/cash-position. `book_balance` = JEL aggregate
    # via the linked GL account (canonical). `statement_balance` =
    # last uploaded statement closing balance or null if none. The
    # legacy `current_balance` field is preserved but no longer drives
    # the UI on its own.
    book_balance      = serializers.SerializerMethodField()
    statement_balance = serializers.SerializerMethodField()
    statement_as_of   = serializers.SerializerMethodField()
    # BANK-007 (2026-09-15): `book_balance` is as-of-today per BANK-001, so it
    # must NOT be subtracted from a statement closing balance at an older date.
    # These carry the timing-correct comparison from
    # services.get_reconciliation_report so the UI can explain the gap.
    statement_gl_balance = serializers.SerializerMethodField()
    statement_difference = serializers.SerializerMethodField()
    statement_reconciled = serializers.SerializerMethodField()
    statement_unmatched  = serializers.SerializerMethodField()
    statement_id         = serializers.SerializerMethodField()
    # Surface bank_name + account_number even when fall-through to
    # gl_account.name. Eliminates the "(unset — edit in /bank-accounts)"
    # placeholders on the /banking page (BANK-006).
    bank_name_display      = serializers.SerializerMethodField()
    account_number_display = serializers.SerializerMethodField()

    class Meta:
        model  = BankAccount
        fields = ['id', 'bank_name', 'account_name', 'account_number', 'branch_code',
                  'bank_name_display', 'account_number_display',
                  'currency', 'gl_account', 'gl_account_code', 'gl_account_name',
                  'is_active', 'hide_in_banking_ui', 'current_balance',
                  'book_balance', 'statement_balance', 'statement_as_of',
                  'statement_gl_balance', 'statement_difference',
                  'statement_reconciled', 'statement_unmatched', 'statement_id',
                  'last_reconciled_date',
                  'created_at', 'updated_at']
        read_only_fields = ['id', 'current_balance', 'last_reconciled_date',
                            'created_at', 'updated_at',
                            'book_balance', 'statement_balance', 'statement_as_of',
                            'statement_gl_balance', 'statement_difference',
                            'statement_reconciled', 'statement_unmatched',
                            'statement_id',
                            'bank_name_display', 'account_number_display']

    def get_book_balance(self, obj):
        """JE-side balance for the linked GL account (BWP)."""
        balances = self.context.get('book_balances', {})
        return balances.get(obj.gl_account_id, '0.00')

    def get_statement_balance(self, obj):
        """Closing balance of the most-recent uploaded statement.

        Returns None when no statement has been uploaded — UI renders "—".
        """
        latest = self.context.get('latest_statements', {}).get(obj.id)
        if latest is None or latest['closing_balance'] is None:
            # None also when the bank returned no balance block at all — the
            # UI renders "—" rather than a figure nobody can vouch for.
            return None
        return str(latest['closing_balance'])

    def get_statement_as_of(self, obj):
        latest = self.context.get('latest_statements', {}).get(obj.id)
        if latest is None:
            return None
        return latest['statement_date'].isoformat()

    def _latest(self, obj):
        return self.context.get('latest_statements', {}).get(obj.id)

    def get_statement_gl_balance(self, obj):
        """GL balance cut at the statement date — the like-for-like figure."""
        latest = self._latest(obj)
        return None if latest is None else str(latest['gl_at_statement_date'])

    def get_statement_difference(self, obj):
        """Bank closing minus GL AT THE SAME DATE (never minus book_balance)."""
        latest = self._latest(obj)
        if latest is None or latest['difference'] is None:
            return None
        return str(latest['difference'])

    def get_statement_reconciled(self, obj):
        latest = self._latest(obj)
        return None if latest is None else latest['is_reconciled']

    def get_statement_unmatched(self, obj):
        latest = self._latest(obj)
        return None if latest is None else latest['unmatched_lines']

    def get_statement_id(self, obj):
        latest = self._latest(obj)
        return None if latest is None else latest['statement_id']

    def get_bank_name_display(self, obj):
        # Fall back to GL name's first token if bank_name is blank OR
        # holds the legacy "(unset — edit in /bank-accounts)" placeholder.
        v = (obj.bank_name or '').strip()
        if v and not v.startswith('(unset'):
            return v
        gl = (obj.gl_account.name if obj.gl_account_id else '') or ''
        # Pluck the bank name from the GL name (drop trailing account number).
        first = gl.split('-')[0].strip()
        # GL names like "First Capital Bank-0002704018802 BWP" -> "First Capital Bank"
        # GL names like "FNBB 62403392335 CHEQ A/C" -> "FNBB"
        import re
        m = re.match(r'([A-Za-z][A-Za-z &\(\)]+?)(?=\s+\d|\s*$)', first)
        return (m.group(1).strip() if m else first) or 'Bank'

    def get_account_number_display(self, obj):
        v = (obj.account_number or '').strip()
        if v and not v.startswith('(unset'):
            return v
        gl_name = (obj.gl_account.name if obj.gl_account_id else '') or ''
        import re
        m = re.search(r'(\d{6,})', gl_name)
        return m.group(1) if m else ''


class BankStatementLineSerializer(serializers.ModelSerializer):
    matched_payment_number = serializers.CharField(
        source='matched_payment.payment_number', read_only=True, default=None
    )
    matched_je_number = serializers.CharField(
        source='matched_journal_entry.entry_number', read_only=True, default=None
    )

    class Meta:
        model  = BankStatementLine
        fields = ['id', 'line_number', 'transaction_date', 'description', 'reference',
                  'amount', 'running_balance', 'match_status',
                  'matched_payment', 'matched_payment_number',
                  'matched_journal_entry', 'matched_je_number',
                  'match_confidence', 'notes']
        read_only_fields = ['id', 'line_number', 'transaction_date', 'description',
                            'reference', 'amount', 'running_balance']


class BankStatementListSerializer(serializers.ModelSerializer):
    bank_account_name = serializers.CharField(source='bank_account.account_name', read_only=True)
    bank_name         = serializers.CharField(source='bank_account.bank_name', read_only=True)
    imported_by_name  = serializers.CharField(
        source='imported_by.get_full_name', read_only=True, default=None
    )

    class Meta:
        model  = BankStatement
        fields = ['id', 'statement_number', 'bank_account', 'bank_account_name',
                  'bank_name', 'statement_date', 'opening_balance', 'closing_balance',
                  'file_name', 'import_date', 'imported_by_name', 'status', 'line_count']
        read_only_fields = ['id', 'statement_number', 'import_date', 'line_count']


class BankRecRuleSerializer(serializers.ModelSerializer):
    company_code         = serializers.CharField(source='company.code', read_only=True)
    target_account_code  = serializers.CharField(source='target_account.code', read_only=True)
    target_account_name  = serializers.CharField(source='target_account.name', read_only=True)
    target_contact_name  = serializers.CharField(
        source='target_contact.name', read_only=True, default=None,
    )
    created_by_username  = serializers.CharField(
        source='created_by.username', read_only=True, default=None,
    )

    class Meta:
        model  = BankRecRule
        fields = ['id', 'company', 'company_code', 'priority',
                  'description_regex', 'amount_min', 'amount_max',
                  'target_account', 'target_account_code', 'target_account_name',
                  'target_contact', 'target_contact_name',
                  'action', 'is_active',
                  'created_by', 'created_by_username',
                  'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_description_regex(self, value):
        import re
        try:
            re.compile(value)
        except re.error as exc:
            raise serializers.ValidationError(f"Invalid regex: {exc}")
        return value

    def validate(self, attrs):
        lo = attrs.get('amount_min', getattr(self.instance, 'amount_min', None))
        hi = attrs.get('amount_max', getattr(self.instance, 'amount_max', None))
        if lo is not None and hi is not None and lo > hi:
            raise serializers.ValidationError(
                {'amount_max': 'amount_max must be >= amount_min.'}
            )
        return attrs


class BankStatementDetailSerializer(serializers.ModelSerializer):
    bank_account_name = serializers.CharField(source='bank_account.account_name', read_only=True)
    bank_name         = serializers.CharField(source='bank_account.bank_name', read_only=True)
    lines             = BankStatementLineSerializer(many=True, read_only=True)

    class Meta:
        model  = BankStatement
        fields = ['id', 'statement_number', 'bank_account', 'bank_account_name',
                  'bank_name', 'statement_date', 'opening_balance', 'closing_balance',
                  'file_name', 'import_date', 'status', 'line_count', 'lines']
        read_only_fields = ['id', 'statement_number', 'import_date', 'line_count']
