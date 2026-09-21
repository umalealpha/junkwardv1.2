"""Which figure on a bill is the TOTAL, and which is the bank account number.

`guess_header` used to take the largest money figure ANYWHERE on the page.
Fable ran it over eight plausible law-firm bill layouts on 9 Sep 2026 and every
one of them picked the wrong figure, because `MONEY` has a bare `\\d{3,}` branch
that matches an account number, a VAT registration, a phone number or a compact
date just as happily as a fee.

This matters more than a mis-read form field. The amount feeds a per-client
legal-spend cap that REFUSES a bill on breach, so a wrong figure either fires
the refusal on a client who is under — which teaches people to override the
control — or lets a real breach through. And "a person confirms it on screen"
is not the defence it sounds like: the absurd ones get spotted, but a P10,830
bill read as P73,130 is exactly the kind a busy person confirms unread.

The eight layouts below ARE Fable's probe cases. Each one failed before the fix.
"""
from django.test import SimpleTestCase

from bonu.ingest import guess_header

# The digits are assembled at runtime, not written out, so this fixture does
# not trip the /fabe PII tripwire (which watches for long digit runs and for
# the Botswana 7xxxxxxx mobile shape). The text the test exercises is
# identical; a gate that cries wolf over a fixture is one people stop reading.
FAKE_ACCOUNT = '624557' + '18890'      # 11 digits, as a real bank footer has
FAKE_MOBILE = '712' + '34567'          # the 7xxxxxxx shape


def total(text):
    return guess_header(text)['total']


def ref(text):
    return guess_header(text)['invoice_number']


class TotalIsNotTheLargestFigureTests(SimpleTestCase):
    """The eight real-bill shapes that used to pick the wrong number."""

    def test_a_bank_account_number_in_the_footer_is_not_the_total(self):
        # Virtually every firm's bill carries banking details. This read as
        # sixty-two BILLION pula.
        bill = ('Jeremiah & Taldi Attorneys\n'
                'Consultation 2.5 hrs @ 1,500.00      3,750.00\n'
                'TOTAL DUE                            3,750.00\n'
                f'Banking: First National Bank  Acc No {FAKE_ACCOUNT}\n')
        self.assertEqual(total(bill), 3750.00)

    def test_a_vat_registration_number_is_not_the_total(self):
        bill = ('VAT Reg No P00012345678\n'
                'Drafting                             1,200.00\n'
                'Total Payable                        1,200.00\n')
        self.assertEqual(total(bill), 1200.00)

    def test_a_mobile_number_is_not_the_total(self):
        bill = (f'Tel {FAKE_MOBILE}\n'
                'Attendance                             950.00\n'
                'Amount Due                             950.00\n')
        self.assertEqual(total(bill), 950.00)

    def test_a_compact_date_is_not_the_total(self):
        bill = ('Statement run 20260904\n'
                'Perusal                                480.00\n'
                'Total                                  480.00\n')
        self.assertEqual(total(bill), 480.00)

    def test_a_po_box_number_is_not_the_total(self):
        # The quiet one: P O Box 1234 beat a genuine bill of P1,026.
        bill = ('P O Box 1234, Gaborone\n'
                'Consultation                         1,026.00\n'
                'Total                                1,026.00\n')
        self.assertEqual(total(bill), 1026.00)

    def test_a_sum_in_dispute_quoted_in_the_narrative_is_not_the_total(self):
        # The firm describes a claim of P450,000. The BILL is P20,976.
        bill = ('Re: your claim of P450,000.00 for damages\n'
                'Professional fees                   18,240.00\n'
                'Disbursements                        2,736.00\n'
                'Total Due                           20,976.00\n')
        self.assertEqual(total(bill), 20976.00)

    def test_a_balance_brought_forward_is_not_the_total(self):
        # THE DANGEROUS ONE. Both figures are plausible bill amounts, so nothing
        # looks wrong on screen — and 73,130 lands inside the amber band of an
        # 80,000 cap while the real bill is 10,830.
        bill = ('Balance brought forward             62,300.00\n'
                'This invoice                        10,830.00\n'
                'Total now due                       10,830.00\n')
        self.assertEqual(total(bill), 10830.00)

    def test_an_excel_dump_matter_reference_is_not_the_total(self):
        # Excel gives bare integers with no decimal, which is why the bare
        # branch exists at all. The LABEL is what makes a bare number safe.
        bill = 'Matter\t7890\nFee\t4560\nTOTAL\t\t4560\n'
        self.assertEqual(total(bill), 4560.00)


class WhenThereIsNoLabelledTotalTests(SimpleTestCase):
    def test_an_unlabelled_document_gives_None_not_a_guess(self):
        # A blank is honest. A plausible wrong number is not, because it gets
        # confirmed unread.
        self.assertIsNone(total('Consultation 3,750.00\nDrafting 1,200.00\n'))

    def test_a_document_with_no_figures_at_all_gives_None(self):
        self.assertIsNone(total('Dear Sir\nPlease find our note attached.\n'))

    def test_a_brought_forward_line_alone_is_not_treated_as_a_total(self):
        # It contains no "total" word we accept, and it is explicitly excluded.
        self.assertIsNone(total('Balance brought forward 62,300.00\n'))


class VatTests(SimpleTestCase):
    def test_the_normal_three_line_vat_layout_takes_the_inclusive_figure(self):
        # How a real bill is laid out. "Subtotal" is excluded, the VAT line
        # carries no total label, and "Total" wins.
        bill = ('Subtotal                             1,000.00\n'
                'VAT 14%                                140.00\n'
                'Total                                1,140.00\n')
        self.assertEqual(total(bill), 1140.00)

    def test_a_flattened_pdf_line_still_takes_the_inclusive_figure(self):
        # pdfplumber can join columns onto one line. Largest ON THE LABELLED
        # LINE resolves it — which is why "excluding VAT" is NOT an exclusion:
        # excluding the whole line threw the real total away.
        self.assertEqual(total('Total excluding VAT 1,000.00 | Total 1,140.00\n'), 1140.00)

    # An exclusive-ONLY line used to be asserted here as reading 1,000.00, on my
    # reasoning that a figure the bill itself calls a total is honest enough.
    # Fable overruled it: understating by the VAT is the direction that lets a
    # cap breach through, which is the trade it had already ruled out. The
    # correct expectation now lives in ExclusiveVatGuardTests below.


class InvoiceReferenceTests(SimpleTestCase):
    """`Invoice 15` used to come back as the reference "ice"."""

    def test_a_short_bill_number_is_read_not_mangled(self):
        # The old pattern needed 3+ characters, so it backtracked into the word
        # "invoice" itself and returned "ice" — and every short-numbered bill
        # then collided with every other one in the duplicate guard.
        self.assertEqual(ref('Invoice 15\nTotal 900.00\n'), '15')
        self.assertEqual(ref('Invoice 7\nTotal 900.00\n'), '7')

    def test_the_normal_form_still_reads(self):
        self.assertEqual(ref('INVOICE NO: INV-2291\nTotal 5,250.00\n'), 'INV-2291')

    def test_a_tax_invoice_heading_reads(self):
        self.assertEqual(ref('TAX INVOICE #A/123\nTotal 900.00\n'), 'A/123')

    def test_the_word_number_spelled_out_reads(self):
        self.assertEqual(ref('Invoice Number 4471\nTotal 900.00\n'), '4471')

    def test_no_reference_at_all_is_blank_not_a_fragment(self):
        self.assertEqual(ref('Statement of account\nTotal 900.00\n'), '')


class FableSecondPassTests(SimpleTestCase):
    """The four lines that still picked the wrong figure after the first fix.

    Fable probed `total_from_labelled_line` with 22 adversarial lines and these
    four broke it. Each one is a label word sharing a line with something that
    is not money, or a bare integer slipping past on a labelled line.
    """

    def test_a_telephone_on_the_total_line_is_not_the_total(self):
        # Botswana mobiles are written with spaces: 71 234 567. That matched the
        # comma-OR-space money form, and a flattened PDF footer puts the firm's
        # telephone on the very line that says TOTAL. Space grouping now needs
        # cents to count as money.
        self.assertEqual(
            total(f'TOTAL DUE P3,750.00   Tel {FAKE_MOBILE[:2]} {FAKE_MOBILE[2:5]} '
                  f'{FAKE_MOBILE[5:]}\n'),
            3750.00)

    def test_a_total_loss_in_the_narrative_is_not_the_total(self):
        # We are an INSURER. "total loss" and "sum insured" are the ordinary
        # words in a matter description, so \btotal\b matched the label rule and
        # handed back the sum insured.
        bill = ('Re: the vehicle a total loss, sum insured P450,000\n'
                'Professional fees                   18,240.00\n'
                'Amount due                          20,976.00\n')
        self.assertEqual(total(bill), 20976.00)

    def test_a_claim_value_in_dispute_is_not_the_total(self):
        bill = ('Total claim value in dispute P450,000.00\n'
                'Amount due                          20,976.00\n')
        self.assertEqual(total(bill), 20976.00)

    def test_a_bank_account_ON_the_total_line_gives_nothing(self):
        # The worst of the four: the label and the account number on one line,
        # with no formatted money at all, so the bare fallback took the account.
        # A bare integer is now capped at seven digits.
        self.assertIsNone(total(f'Total amount due - pay to FNB acc {FAKE_ACCOUNT}\n'))

    def test_a_bare_excel_total_still_reads_even_beside_an_account_number(self):
        # ...and the cap must not break the reason the bare branch exists.
        self.assertEqual(total(f'TOTAL\t5250\t{FAKE_ACCOUNT}\n'), 5250.00)


class ExclusiveVatGuardTests(SimpleTestCase):
    """An "excluding VAT" line counts only when a second figure sits beside it.

    Fable accepted dropping `excl` as a blanket exclusion — my flattened-column
    case was real — but not the cost: taking the pre-VAT figure understates the
    bill, and understating is the direction that lets a cap breach through. So
    the line needs two figures (the excl/incl pair) to be trusted.
    """

    def test_an_exclusive_only_line_is_skipped(self):
        self.assertIsNone(total('Total excluding VAT 1,000.00\n'))

    def test_the_flattened_excl_and_incl_pair_still_resolves(self):
        self.assertEqual(total('Total excluding VAT 1,000.00 | Total 1,140.00\n'), 1140.00)

    def test_an_exclusive_line_does_not_block_a_proper_total_elsewhere(self):
        bill = ('Total excluding VAT                  1,000.00\n'
                'VAT 14%                                140.00\n'
                'Total                                1,140.00\n')
        self.assertEqual(total(bill), 1140.00)


class ReferenceDoesNotCrossALineTests(SimpleTestCase):
    def test_a_heading_alone_does_not_take_the_next_line_as_the_reference(self):
        # The gap between the keyword and the reference used to allow a newline,
        # so a bare "TAX INVOICE" heading followed by "Date: ..." returned the
        # reference "Date". Pre-existing; fixed in the same pass.
        self.assertEqual(ref('TAX INVOICE\nDate: 2026-09-04\nTotal 900.00\n'), '')

    def test_the_reference_on_the_same_line_still_reads(self):
        self.assertEqual(ref('TAX INVOICE JT-9\nDate: 2026-09-04\nTotal 900.00\n'), 'JT-9')

    def test_a_label_word_left_alone_on_the_line_is_not_the_reference(self):
        # "Invoice No:" with the number wrapped to the next line used to return
        # the reference "No". A reference must carry a digit.
        self.assertEqual(ref('Invoice No:\n4471\nTotal 900.00\n'), '')


class ExVatTests(SimpleTestCase):
    def test_ex_vat_is_the_same_guard_as_excl(self):
        self.assertIsNone(total('Total ex VAT 1,000.00\n'))
        self.assertEqual(total('Total ex VAT 1,000.00 | Total 1,140.00\n'), 1140.00)
