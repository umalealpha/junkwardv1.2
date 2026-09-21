"""The list Omni exports must be a list Omni can read back.

WHY THIS TEST EXISTS
--------------------
Reported 11-Sep-2026 by Keneilwe Jane: her sheet showed 212 providers accepted,
Omni showed 170, and nothing on screen explained the gap.

The cause was not the upload failing. It was the upload half-succeeding. The
export writes the acceptance column as "ADH Acceptance (manual)" and the
sticker column as "'Accepted Here' Sticker"; the importer's COLUMN_MAP only
knew the ADH working sheet's spellings, "ADH Acceptance (Ready)" and
"ADH 'Accepted Here' Sticker Displayed". Every other column matched, so the
file imported cleanly, reported no error — and quietly discarded exactly the
column the ADH team had spent their time filling in.

So the round trip is the thing under test, not either half on its own: export
the registry, change the acceptance value in the exported file, import it, and
the change must land. Remove either alias from COLUMN_MAP and
``test_acceptance_edited_in_an_exported_file_is_imported`` goes red.

The second test guards the reason it stayed invisible: a column the importer
does not understand must be NAMED in the warnings, never dropped in silence.
"""
import io

from django.test import TestCase
from openpyxl import load_workbook

from healthcare.models import ServiceProvider
from healthcare.provider_registry import build_export, parse_sheet, preview_import


def _edit_exported_cell(blob: bytes, header_label: str, new_value: str) -> io.BytesIO:
    """Open the exported workbook, overwrite one column for every data row, and
    hand it back as a file object — exactly what a person does in Excel."""
    wb = load_workbook(io.BytesIO(blob))
    ws = wb.active
    header = [c.value for c in ws[1]]
    col = header.index(header_label) + 1
    for row in range(2, ws.max_row + 1):
        ws.cell(row=row, column=col, value=new_value)
    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return out


class ProviderExportImportRoundTripTests(TestCase):
    def setUp(self):
        self.provider = ServiceProvider.objects.create(
            practice_number='60178',
            name='TEST PHARMACY',
            discipline='PHARMACY',
            contract_status='Signed',
            adh_acceptance='NO',
            sticker_displayed='',
        )

    def test_acceptance_edited_in_an_exported_file_is_imported(self):
        # The whole complaint in one assertion: mark a provider accepted in the
        # file Omni itself produced, upload it, and Omni must see the change.
        blob, _ = build_export('all')
        edited = _edit_exported_cell(blob, 'ADH Acceptance (manual)', 'YES')

        rows, _warnings = parse_sheet(edited)
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0].get('adh_acceptance'), 'YES',
            "an acceptance edited in Omni's own export must import, not be dropped")

        edited.seek(0)
        preview = preview_import(edited)
        changed = {r['practice_number']: r['changes'] for r in preview['changed']}
        self.assertIn('60178', changed,
                      'the preview must show the provider as changed, not unchanged')
        self.assertEqual(changed['60178']['adh_acceptance'],
                         {'old': 'NO', 'new': 'YES'})

    def test_sticker_edited_in_an_exported_file_is_imported(self):
        blob, _ = build_export('all')
        edited = _edit_exported_cell(blob, "'Accepted Here' Sticker", 'YES')

        rows, _warnings = parse_sheet(edited)
        self.assertEqual(rows[0].get('sticker_displayed'), 'YES')

    def test_an_exported_file_raises_no_unrecognised_column_warning(self):
        # Omni's own export must not look foreign to Omni's own importer.
        blob, _ = build_export('all')
        _rows, warnings = parse_sheet(io.BytesIO(blob))
        self.assertFalse(
            [w for w in warnings if 'not recognised' in w],
            f'Omni exported a file its own importer does not understand: {warnings}')

    def test_a_column_the_importer_ignores_is_named_in_the_warnings(self):
        # The silence is what hid the bug. An ignored column must be said out
        # loud, by name.
        blob, _ = build_export('all')
        wb = load_workbook(io.BytesIO(blob))
        ws = wb.active
        ws.cell(row=1, column=ws.max_column + 1, value='Some New Column')
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)

        _rows, warnings = parse_sheet(buf)
        joined = ' '.join(warnings)
        self.assertIn('not recognised', joined)
        self.assertIn('Some New Column', joined,
                      'the warning must name the column, not just count it')

    def test_derived_columns_are_not_reported_as_unrecognised(self):
        # AFA Registered / ADH Ready / Ready mismatch? / QC Confirmed are
        # deliberately not importable. Warning about them every single time
        # would train people to ignore the warnings.
        blob, _ = build_export('all')
        _rows, warnings = parse_sheet(io.BytesIO(blob))
        joined = ' '.join(warnings)
        for label in ('AFA Registered', 'ADH Ready', 'Ready mismatch', 'QC Confirmed'):
            self.assertNotIn(label, joined)
