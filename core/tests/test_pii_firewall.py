"""
Tests for core.pii_firewall — the reversible DeepSeek/LLM PII gateway.

Focus: (1) round-trip fidelity (rehydrate restores exactly what was tokenised),
(2) mode behaviour (off/monitor identical to today; tokenize hides PII; block
fails closed), (3) feature preservation (ERP codes, amounts and dates are NOT
touched, and the caller sees the same final answer).
"""
import os
import unittest

from core import pii_firewall as pf


def _isolate_names(fn):
    """Run with the DB name-dictionary disabled so unit tests are deterministic
    and DB-free."""
    def wrapper(*a, **k):
        prev = os.environ.get('PII_FIREWALL_NAMES')
        os.environ['PII_FIREWALL_NAMES'] = '0'
        pf._NAME_CACHE.update(regex=None, at=0.0)
        try:
            return fn(*a, **k)
        finally:
            if prev is None:
                os.environ.pop('PII_FIREWALL_NAMES', None)
            else:
                os.environ['PII_FIREWALL_NAMES'] = prev
    return wrapper


class TokeniserTests(unittest.TestCase):

    @_isolate_names
    def test_omang_bank_email_phone_roundtrip(self):
        tk = pf.Tokeniser()
        original = ('Kabo, Omang 123456789, account 6201234567890, '
                    'email kabo@x.co.bw, cell +267 71 234 567')
        scrubbed = tk.scrub(original)
        # No raw PII survives outbound.
        self.assertNotIn('123456789', scrubbed)
        self.assertNotIn('6201234567890', scrubbed)
        self.assertNotIn('kabo@x.co.bw', scrubbed)
        self.assertNotIn('+267 71 234 567', scrubbed)
        # Tokens present.
        self.assertIn('ID_1', scrubbed)
        self.assertIn('ACCT_1', scrubbed)
        self.assertIn('EMAIL_1', scrubbed)
        self.assertIn('PHONE_1', scrubbed)
        # Rehydrating the model's answer (which echoes tokens) restores exactly.
        model_answer = 'Summary for ID_1 / ACCT_1 / EMAIL_1 / PHONE_1.'
        restored = tk.rehydrate(model_answer)
        self.assertIn('123456789', restored)
        self.assertIn('6201234567890', restored)
        self.assertIn('kabo@x.co.bw', restored)
        self.assertIn('+267 71 234 567', restored)

    @_isolate_names
    def test_erp_codes_and_amounts_preserved(self):
        """Feature preservation: JE/PO codes, money and ISO dates must NOT be tokenised."""
        tk = pf.Tokeniser()
        text = 'Post JE-2026-000123 for PO-ADIC-2026-000045 on 2026-07-18 of BWP 1,250,000.00'
        scrubbed = tk.scrub(text)
        self.assertIn('JE-2026-000123', scrubbed)
        self.assertIn('PO-ADIC-2026-000045', scrubbed)
        self.assertIn('2026-07-18', scrubbed)
        self.assertIn('1,250,000.00', scrubbed)
        self.assertEqual(tk.total, 0)   # nothing tokenised

    @_isolate_names
    def test_gl_account_codes_not_touched(self):
        """6-digit account codes / short numbers are safe (only 9=Omang, 10+=acct)."""
        tk = pf.Tokeniser()
        scrubbed = tk.scrub('Debit 280001, credit 116002, entry 000123')
        self.assertEqual(scrubbed, 'Debit 280001, credit 116002, entry 000123')
        self.assertEqual(tk.total, 0)

    @_isolate_names
    def test_money_amounts_preserved_not_tokenised(self):
        """H1 (Fable): big BWP amounts must reach the model intact — the 9-digit
        (Omang) and 10+-digit (bank) rules must never eat a figure."""
        tk = pf.Tokeniser()
        text = ('Collected BWP 34,567,890.50; net 125150000.0; requested BWP 34500000; '
                'ratio 79.99')
        scrubbed = tk.scrub(text)
        self.assertIn('34,567,890.50', scrubbed)
        self.assertIn('125150000.0', scrubbed)
        self.assertIn('34500000', scrubbed)
        self.assertIn('79.99', scrubbed)
        self.assertEqual(tk.total, 0)   # nothing tokenised

    @_isolate_names
    def test_phone_only_matches_7_prefix_mobile(self):
        """H1 (Fable): bare 8-digit run starting 3 (collides with amounts/dates)
        is NOT a phone; a 7-prefixed BW mobile IS."""
        tk = pf.Tokeniser()
        self.assertIn('PHONE_1', tk.scrub('call 72123456'))
        tk2 = pf.Tokeniser()
        self.assertEqual(tk2.scrub('code 32123456 here'), 'code 32123456 here')
        self.assertEqual(tk2.total, 0)

    @_isolate_names
    def test_json_safe_rehydrate_keeps_json_valid(self):
        """M2 (Fable): a rehydrated value with a quote/backslash must not break a
        JSON-mode caller's json.loads."""
        import json as _json
        tk = pf.Tokeniser()
        tk.map['PERSON_1'] = 'O"Brien \\ & Co'          # JSON-breaking chars
        out = tk.rehydrate('{"vendor": "PERSON_1"}', json_safe=True)
        parsed = _json.loads(out)                        # must not raise
        self.assertEqual(parsed['vendor'], 'O"Brien \\ & Co')
        # Plain (non-JSON) rehydrate substitutes the raw value.
        tk2 = pf.Tokeniser()
        tk2.map['PERSON_1'] = 'O"Brien'
        self.assertEqual(tk2.rehydrate('name PERSON_1'), 'name O"Brien')

    @_isolate_names
    def test_rehydrate_exact_and_markdown_escape(self):
        """M1: match the exact token or its markdown-escaped form (ID\\_1). The
        spaced form 'ID 1' is deliberately NOT matched (too collision-prone)."""
        tk = pf.Tokeniser()
        tk.scrub('Omang 123456789')            # -> ID_1
        self.assertIn('123456789', tk.rehydrate('The id is ID_1.'))      # exact
        self.assertIn('123456789', tk.rehydrate(r'The id is ID\_1.'))    # markdown escape
        self.assertNotIn('123456789', tk.rehydrate('The id is ID 1.'))   # spaced -> left as-is (safe)

    @_isolate_names
    def test_rehydrate_no_midword_or_header_overmatch(self):
        """M1 confirm (Fable): a live token must NOT splice PII into a word ending
        in the label (invalid_1 / PAID_1 / salesperson_1) or a space-separated CSV
        header ('Beneficiary ID 1' / 'Contact Person 1') — the smart-upload regression."""
        tk = pf.Tokeniser()
        tk.map['ID_1'] = 'OMANG-999999999'
        self.assertEqual(tk.rehydrate('flagged invalid_1 here'), 'flagged invalid_1 here')
        self.assertEqual(tk.rehydrate('marked PAID_1 today'), 'marked PAID_1 today')
        self.assertEqual(tk.rehydrate('{"Beneficiary ID 1": "x"}'), '{"Beneficiary ID 1": "x"}')
        tk2 = pf.Tokeniser()
        tk2.map['PERSON_1'] = 'Kabo Modise'
        self.assertEqual(tk2.rehydrate('salesperson_1 closed it'), 'salesperson_1 closed it')
        self.assertEqual(tk2.rehydrate('{"Contact Person 1": "y"}'), '{"Contact Person 1": "y"}')
        # standalone token still resolves, and ID_1 never clips ID_11
        tk3 = pf.Tokeniser()
        tk3.map.update({'ID_1': 'AAA', 'ID_11': 'BBB'})
        self.assertEqual(tk3.rehydrate('see ID_11 and ID_1 now'), 'see BBB and AAA now')

    @_isolate_names
    def test_stable_token_for_repeated_value(self):
        tk = pf.Tokeniser()
        scrubbed = tk.scrub('Omang 123456789 again 123456789')
        self.assertEqual(scrubbed.count('ID_1'), 2)   # same value -> same token
        self.assertNotIn('ID_2', scrubbed)

    def test_names_via_injected_dictionary(self):
        """When a name dictionary is present, names are tokenised + restored."""
        import re
        import time
        prev = os.environ.get('PII_FIREWALL_NAMES')
        os.environ['PII_FIREWALL_NAMES'] = '1'            # explicitly enable names
        pf._NAME_CACHE.update(
            regex=re.compile(r'(?<![A-Za-z])(?:Kabo|Modise)(?![A-Za-z])'),
            at=time.time())                                # fresh cache entry
        try:
            tk = pf.Tokeniser()
            scrubbed = tk.scrub('Kabo Modise requested leave')
            self.assertNotIn('Kabo', scrubbed)
            self.assertNotIn('Modise', scrubbed)
            self.assertIn('PERSON_1', scrubbed)
            self.assertIn('PERSON_2', scrubbed)
            self.assertEqual(tk.rehydrate('PERSON_1 PERSON_2'), 'Kabo Modise')
        finally:
            pf._NAME_CACHE.update(regex=None, at=0.0)
            if prev is None:
                os.environ.pop('PII_FIREWALL_NAMES', None)
            else:
                os.environ['PII_FIREWALL_NAMES'] = prev


class _Boom(Exception):
    pass


def _make_echo():
    calls = {}

    @pf.firewall('Test', _Boom)
    def echo(user_prompt, *, system_prompt=None, max_tokens=800):
        calls['user'] = user_prompt
        calls['system'] = system_prompt
        return f'Model saw: {user_prompt}'
    return echo, calls


class FirewallDecoratorTests(unittest.TestCase):

    def setUp(self):
        self._prev = os.environ.get('PII_FIREWALL_MODE')
        os.environ['PII_FIREWALL_NAMES'] = '0'
        pf._NAME_CACHE.update(regex=None, at=0.0)

    def tearDown(self):
        if self._prev is None:
            os.environ.pop('PII_FIREWALL_MODE', None)
        else:
            os.environ['PII_FIREWALL_MODE'] = self._prev
        os.environ.pop('PII_FIREWALL_NAMES', None)

    def test_off_mode_passthrough(self):
        os.environ['PII_FIREWALL_MODE'] = 'off'
        echo, calls = _make_echo()
        out = echo('Omang 123456789')
        self.assertEqual(calls['user'], 'Omang 123456789')   # untouched
        self.assertEqual(out, 'Model saw: Omang 123456789')

    def test_monitor_mode_sends_original(self):
        os.environ['PII_FIREWALL_MODE'] = 'monitor'
        echo, calls = _make_echo()
        out = echo('Omang 123456789')
        # monitor = behaviour-identical to off (detect+log only, send original)
        self.assertEqual(calls['user'], 'Omang 123456789')
        self.assertEqual(out, 'Model saw: Omang 123456789')

    def test_tokenize_mode_hides_pii_and_rehydrates(self):
        os.environ['PII_FIREWALL_MODE'] = 'tokenize'
        echo, calls = _make_echo()
        out = echo('Omang 123456789')
        # The model must NOT have seen the real Omang…
        self.assertNotIn('123456789', calls['user'])
        self.assertIn('ID_1', calls['user'])
        # …but the caller gets the real value back in the answer.
        self.assertIn('123456789', out)

    def test_block_mode_raises_on_pii(self):
        os.environ['PII_FIREWALL_MODE'] = 'block'
        echo, _ = _make_echo()
        with self.assertRaises(_Boom):
            echo('Omang 123456789')

    def test_block_mode_allows_clean_text(self):
        os.environ['PII_FIREWALL_MODE'] = 'block'
        echo, calls = _make_echo()
        out = echo('Summarise FY26 GWP vs budget')   # no PII
        self.assertEqual(calls['user'], 'Summarise FY26 GWP vs budget')
        self.assertIn('Model saw', out)

    def test_system_prompt_also_scrubbed(self):
        os.environ['PII_FIREWALL_MODE'] = 'tokenize'
        echo, calls = _make_echo()
        echo('hello', system_prompt='Client Omang 123456789 context')
        self.assertNotIn('123456789', calls['system'])
        self.assertIn('ID_1', calls['system'])


if __name__ == '__main__':
    unittest.main()
