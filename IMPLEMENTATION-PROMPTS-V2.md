# Alpha Direct ERP — Implementation Prompts (V2)

## How to Use This File

This file contains 4 paste-ready prompts for Claude Code. Execute them **in order** (Phase 1 → 2 → 3 → 4). Each prompt is self-contained and references actual files in the codebase.

**Before each phase:** Make sure the Django backend is running and the frontend compiles without errors. If a phase introduces errors, fix them before proceeding to the next phase.

---

## Phase 1: Foundation Fixes (Theme + Journal Entries + Dashboard)

```
You are working on the Alpha Direct Financial Management System in C:\Users\OTHER\alpha_finance. Read CLAUDE.md for full context. This phase has 3 objectives:

### OBJECTIVE 1: Fix Theme Consistency

The Quick Entry page (frontend/src/app/(dashboard)/quick-entry/page.tsx) has ~40 hard-coded hex colors that bypass the theme system. The same issue exists in a few other components.

Tasks:
1. Read frontend/src/contexts/ThemeContext.tsx and frontend/src/lib/themes.ts to understand the theme token structure.
2. In quick-entry/page.tsx, replace EVERY hard-coded color with the equivalent theme token from useTheme(). Map these:
   - #111827 → theme.text
   - #374151 → theme.g700
   - #6B7280 → theme.t2
   - #9CA3AF → theme.t3
   - #D1D5DB → theme.g200
   - #E5E7EB → theme.cardBdr
   - #F3F4F6 → theme.g100
   - #F9FAFB → theme.g50
   - #FFFFFF (bg-white) → theme.card
   - #FFF7ED → theme.oL
   - #FED7AA → use theme.orange + "40" opacity
   - #CC6C00 → theme.orange (darker variant, use as-is or create theme.orangeDark)
   - #F07F00 → theme.orange
   - #059669 → theme.ok
   - #ECFDF5 → theme.okB
   - #D97706 → theme.wr
   - #DC2626 → theme.er
   - #FEF2F2 → theme.erB
   - #FEE2E2 → use theme.er + "30" opacity
   - #D1FAE5 → theme.okB (close enough)
   - #A7F3D0 → use theme.ok + "40" opacity
   - #047857 → darker green, keep as-is for hover state
3. Convert from className-based colors to style-based colors using the theme object (same pattern as dashboard/page.tsx).
4. Verify the page renders correctly in BOTH light and fun themes.

### OBJECTIVE 2: Add Journal Entries Page

Create a new frontend page at frontend/src/app/(dashboard)/journal-entries/page.tsx.

Requirements:
1. The API endpoint already exists: GET /api/v1/journal-entries/ returns paginated results with fields: id, entry_number, entry_date, description, journal_type, status, currency, line_count, created_at.
2. GET /api/v1/journal-entries/{id}/ returns the detail with a "lines" array containing: account, account_name, debit_amount, credit_amount, description.
3. Build the page with:
   - TopBar with title "Journal Entries"
   - Filter bar: date range inputs, journal_type dropdown (Sales/Purchases/Cash Receipts/Cash Payments/Bank/General), status dropdown (Draft/Posted/Reversed), and a search input for entry_number or description
   - Table showing: entry_number (monospace, orange link), entry_date, description (truncated), journal_type (badge), status (colored badge), total amount, line_count
   - Clicking a row navigates to a detail page OR expands inline to show the debit/credit lines
   - Use the theme system consistently (style={{ color: theme.xxx }})
   - Follow the same component patterns as the invoices page

4. Add the page to the sidebar navigation in frontend/src/components/layout/Sidebar.tsx:
   - Add it under the "Reporting" group, after "General Ledger" and before "Exception Report"
   - Use the BookOpen icon (already imported) or FileSpreadsheet from lucide-react
   - href: '/journal-entries'

5. Also create a detail page at frontend/src/app/(dashboard)/journal-entries/[id]/page.tsx that shows:
   - Entry header (number, date, description, type, status)
   - Full debit/credit table with account codes, names, amounts
   - Totals row showing total debits and credits
   - If status is "draft", show a "Post" button that calls POST /api/v1/journal-entries/{id}/ (you may need to check if this endpoint exists)
   - Link back to the list page

### OBJECTIVE 3: Dashboard Improvements

Modify frontend/src/app/(dashboard)/dashboard/page.tsx:

1. Change the Balance Sheet and P&L summary cards from 2-column to 3-column layout (lg:grid-cols-3).

2. In both BS and P&L cards, replace formatCurrency() calls with formatMillions() for all values. The formatMillions() function already exists in frontend/src/lib/utils.ts.

3. Add a third card: Cash Flow Summary. Build it using data already available in the dashboard:
   - Calculate operating cash flow from payments data (sum of received payments minus sent payments in the period)
   - Show 3 lines: Operating Activities, Investing Activities (P0.00 for now), Net Cash Movement
   - Match the visual style of the BS and P&L cards exactly
   - Link to /reports/cash-position at the bottom

4. Make the KPI cards render as a 6-column single row on xl screens:
   - Change grid-cols from "xl:grid-cols-3" to "xl:grid-cols-6"
   - Adjust the card padding/sizing to fit 6 across at 1440px max-width

5. Merge the "Needs Attention" and "Recent Activity" cards into a single "Action Center" card with two internal tabs: "Attention" (current needs-attention items) and "Activity" (current recent activity items).

After completing all 3 objectives, run the frontend dev server and verify:
- Quick Entry page looks correct in both Light and Fun themes
- Journal Entries page loads, filters work, navigation from sidebar works
- Dashboard shows 3 financial statement columns with abbreviated values
- No TypeScript errors, no console errors

When all work is complete and verified, commit all changes to git with a descriptive commit message. Do not push to remote.
```

---

## Phase 2: AI Document Processing Backend

```
You are working on the Alpha Direct Financial Management System in C:\Users\OTHER\alpha_finance. Read CLAUDE.md for full context. This phase builds the backend infrastructure for AI-powered document processing using a LAYERED approach: Layer 1 is rule-based (free, instant), Layer 2 is the Anthropic Claude API (costs ~$0.001/doc, 2-3 sec). Layer 2 only fires when Layer 1 has low confidence.

### OBJECTIVE 1: Create the "documents" Django App

1. Run: python manage.py startapp documents
2. Add 'documents' to INSTALLED_APPS in alpha_finance/settings.py

3. Create models in documents/models.py:

DocumentUpload:
- id: UUIDField (primary key, default=uuid4)
- file: FileField(upload_to='documents/%Y/%m/')
- original_filename: CharField(max_length=255)
- file_type: CharField choices=['pdf', 'image', 'csv', 'xlsx', 'unknown']
- file_size: IntegerField
- file_hash: CharField(max_length=64, unique=True) — SHA-256 hash for duplicate detection
- status: CharField choices=['uploading', 'processing', 'classified', 'extracted', 'suggested', 'confirmed', 'rejected', 'error']
- document_type: CharField choices=['invoice', 'receipt', 'bank_statement', 'expense', 'credit_note', 'unknown'], null=True
- classification_confidence: DecimalField(max_digits=3, decimal_places=2, null=True)
- extraction_method: CharField choices=['rule_based', 'ai_assisted', 'hybrid'], default='rule_based' — tracks which layer did the work
- extracted_text: TextField(null=True) — raw text from OCR/PDF extraction
- extracted_data: JSONField(null=True) — structured data (amounts, dates, vendor, line items)
- suggested_action: JSONField(null=True) — proposed invoice/payment/JE
- ai_explanation: TextField(null=True) — human-readable explanation from AI of what the document is and what it suggests (shown to user)
- user_decision: CharField choices=['pending', 'confirmed', 'rejected', 'edited'], default='pending'
- user_corrections: JSONField(null=True) — diff between suggestion and what user confirmed
- resulting_content_type: ForeignKey(ContentType, null=True) — for generic FK
- resulting_object_id: UUIDField(null=True)
- error_message: TextField(null=True)
- uploaded_by: ForeignKey(User)
- processed_by: ForeignKey(User, null=True)
- created_at: DateTimeField(auto_now_add=True)
- updated_at: DateTimeField(auto_now=True)

VendorMapping:
- id: UUIDField (primary key)
- vendor_pattern: CharField(max_length=255, unique=True) — normalized vendor name pattern
- contact: ForeignKey('billing.Contact') — matched contact
- default_account: ForeignKey('ledger.Account', null=True) — most frequently used expense account
- match_count: IntegerField(default=0) — how many times this mapping was used
- auto_apply: BooleanField(default=False) — True after 3+ consistent matches
- created_by: ForeignKey(User)
- created_at, updated_at

4. Add ANTHROPIC_API_KEY to .env file and settings.py:
   In .env add: ANTHROPIC_API_KEY=your-key-here
   In settings.py add: ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
   Also add: AI_CONFIDENCE_THRESHOLD = 0.75  # Below this, escalate to AI layer

5. Add to requirements.txt: pdfplumber, python-dateutil, Pillow, anthropic
   (pytesseract is optional — add it but handle ImportError gracefully)

6. Create documents/services.py with the TWO-LAYER processing pipeline:

class DocumentProcessor:
    """
    Two-layer document processing pipeline:
    Layer 1: Rule-based (regex, keyword matching, vendor mappings) — free, instant
    Layer 2: Anthropic Claude API — only called when Layer 1 confidence < AI_CONFIDENCE_THRESHOLD

    The system ALWAYS tries Layer 1 first. If confidence is high enough, Layer 2 is skipped entirely.
    This keeps costs near zero for repeat vendors and standard document formats.
    """

    def __init__(self):
        self.ai_client = None  # Lazy-loaded

    def process(self, upload_id):
        """Main entry point. Runs the full pipeline."""
        upload = DocumentUpload.objects.get(id=upload_id)
        try:
            upload.status = 'processing'
            upload.save()

            # Step 1: Extract raw text (always rule-based — pdfplumber/OCR)
            raw_text = self._extract_text(upload)
            upload.extracted_text = raw_text

            # Step 2: LAYER 1 — Rule-based classification and extraction
            doc_type, confidence = self._classify_rules(raw_text, upload.original_filename)
            data = self._extract_data_rules(raw_text, doc_type)
            upload.document_type = doc_type
            upload.classification_confidence = confidence
            upload.extracted_data = data
            upload.extraction_method = 'rule_based'
            upload.status = 'extracted'
            upload.save()

            # Step 3: LAYER 2 — AI enhancement (only if confidence is low)
            from django.conf import settings
            threshold = getattr(settings, 'AI_CONFIDENCE_THRESHOLD', 0.75)

            if confidence < threshold and getattr(settings, 'ANTHROPIC_API_KEY', ''):
                ai_result = self._enhance_with_ai(raw_text, doc_type, confidence, data, upload)
                if ai_result:
                    # Merge AI results — AI can upgrade classification and fill gaps
                    if ai_result.get('document_type') and ai_result.get('confidence', 0) > confidence:
                        upload.document_type = ai_result['document_type']
                        upload.classification_confidence = ai_result['confidence']
                    if ai_result.get('extracted_data'):
                        # Merge: AI fills in fields that rules left empty, but rules take priority for fields they found
                        merged = {**ai_result['extracted_data'], **{k: v for k, v in data.items() if v}}
                        upload.extracted_data = merged
                    if ai_result.get('explanation'):
                        upload.ai_explanation = ai_result['explanation']
                    upload.extraction_method = 'hybrid' if confidence > 0 else 'ai_assisted'
                    upload.save()

                    # Log the AI activity
                    AIActivityLog.objects.create(
                        document_upload=upload,
                        action_type='ai_extraction',
                        input_data={'text_length': len(raw_text), 'rule_confidence': float(confidence)},
                        output_data=ai_result,
                        confidence=ai_result.get('confidence', 0),
                    )
            else:
                # Generate a simple explanation from rules
                upload.ai_explanation = self._generate_rule_explanation(upload)
                upload.save()

            # Step 4: Generate suggestion (uses whatever extraction method produced the best data)
            suggestion = self._suggest_action(upload.extracted_data, upload.document_type)
            upload.suggested_action = suggestion
            upload.status = 'suggested'
            upload.save()

        except Exception as e:
            upload.status = 'error'
            upload.error_message = str(e)
            upload.save()
            raise

    # ─── TEXT EXTRACTION (always rule-based) ──────────────────────────────

    def _extract_text(self, upload):
        """Extract text from PDF or image. This is always rule-based."""
        ext = upload.original_filename.rsplit('.', 1)[-1].lower()
        if ext == 'pdf':
            return self._extract_pdf(upload.file.path)
        elif ext in ('png', 'jpg', 'jpeg', 'tiff', 'bmp'):
            return self._extract_image(upload.file.path)
        elif ext == 'csv':
            return self._extract_csv(upload.file.path)
        else:
            raise ValueError(f"Unsupported file type: {ext}")

    def _extract_pdf(self, path):
        """Use pdfplumber to extract text and tables."""
        import pdfplumber
        text_parts = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                text_parts.append(page.extract_text() or '')
                tables = page.extract_tables()
                for table in tables:
                    for row in table:
                        text_parts.append(' | '.join(str(cell or '') for cell in row))
        return '\n'.join(text_parts)

    def _extract_image(self, path):
        """Use pytesseract for OCR."""
        try:
            import pytesseract
            from PIL import Image
            return pytesseract.image_to_string(Image.open(path))
        except ImportError:
            raise ValueError("OCR not available. Install pytesseract and Tesseract.")

    def _extract_csv(self, path):
        """Read CSV as text."""
        with open(path, 'r', encoding='utf-8-sig') as f:
            return f.read()

    # ─── LAYER 1: RULE-BASED ─────────────────────────────────────────────

    def _classify_rules(self, text, filename):
        """Rule-based document classification using keywords and patterns."""
        text_lower = text.lower()
        fname_lower = filename.lower()

        # Bank statement patterns
        if any(kw in text_lower for kw in ['bank statement', 'account statement', 'statement period']):
            return 'bank_statement', 0.9
        if any(kw in fname_lower for kw in ['statement', 'bank']):
            return 'bank_statement', 0.7

        # Invoice patterns
        if any(kw in text_lower for kw in ['tax invoice', 'invoice number', 'invoice no', 'inv no']):
            return 'invoice', 0.9
        if 'invoice' in fname_lower:
            return 'invoice', 0.7

        # Receipt patterns
        if any(kw in text_lower for kw in ['receipt', 'proof of payment', 'payment received']):
            return 'receipt', 0.85

        # Credit note patterns
        if any(kw in text_lower for kw in ['credit note', 'credit memo']):
            return 'credit_note', 0.9

        # Expense patterns (catch-all for vendor documents)
        if any(kw in text_lower for kw in ['total due', 'amount due', 'balance due', 'please pay']):
            return 'expense', 0.6

        return 'unknown', 0.0

    def _extract_data_rules(self, text, doc_type):
        """Extract structured data using regex patterns."""
        import re
        from dateutil import parser as date_parser

        data = {}

        # Extract amounts (look for currency patterns)
        amount_patterns = [
            r'(?:BWP|P|R|\$|USD|ZAR)\s*[\d,]+\.?\d*',
            r'(?:total|amount|balance|due|subtotal|net|gross)\s*:?\s*[\d,]+\.?\d*',
        ]
        amounts = []
        for pattern in amount_patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                cleaned = re.sub(r'[^\d.]', '', match.group().split()[-1])
                try:
                    amounts.append(float(cleaned))
                except ValueError:
                    pass
        data['amounts'] = sorted(set(amounts), reverse=True)
        data['total_amount'] = amounts[0] if amounts else None

        # Extract dates
        date_patterns = [
            r'\d{1,2}[-/]\d{1,2}[-/]\d{2,4}',
            r'\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}',
            r'\d{4}-\d{2}-\d{2}',
        ]
        dates = []
        for pattern in date_patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                try:
                    parsed = date_parser.parse(match.group(), dayfirst=True)
                    dates.append(parsed.date().isoformat())
                except (ValueError, OverflowError):
                    pass
        data['dates'] = sorted(set(dates))
        data['document_date'] = dates[0] if dates else None

        # Extract reference numbers
        ref_patterns = [
            r'(?:invoice|inv|ref|reference|receipt)\s*(?:no|number|#|:)\s*[:\s]*([\w-]+)',
        ]
        refs = []
        for pattern in ref_patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                refs.append(match.group(1).strip())
        data['references'] = refs

        # Extract vendor/company name (first line or near "from" keyword)
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        data['possible_vendor'] = lines[0] if lines else None

        return data

    def _generate_rule_explanation(self, upload):
        """Generate a human-readable explanation from rule-based extraction."""
        doc_type = upload.document_type or 'document'
        data = upload.extracted_data or {}
        parts = [f"This appears to be a {doc_type.replace('_', ' ')}"]
        if data.get('possible_vendor'):
            parts.append(f"from {data['possible_vendor']}")
        if data.get('total_amount'):
            parts.append(f"for BWP {data['total_amount']:,.2f}")
        if data.get('document_date'):
            parts.append(f"dated {data['document_date']}")
        conf = upload.classification_confidence
        if conf and conf >= 0.85:
            parts.append("(high confidence)")
        elif conf and conf >= 0.6:
            parts.append("(medium confidence — please verify)")
        else:
            parts.append("(low confidence — manual review recommended)")
        return '. '.join([' '.join(parts) + '.'])

    # ─── LAYER 2: ANTHROPIC CLAUDE API ───────────────────────────────────

    def _get_ai_client(self):
        """Lazy-load the Anthropic client."""
        if self.ai_client is None:
            from django.conf import settings
            import anthropic
            api_key = getattr(settings, 'ANTHROPIC_API_KEY', '')
            if not api_key:
                return None
            self.ai_client = anthropic.Anthropic(api_key=api_key)
        return self.ai_client

    def _enhance_with_ai(self, raw_text, rule_doc_type, rule_confidence, rule_data, upload):
        """
        Call Claude Haiku to classify and extract data from the document.
        Only called when rule-based confidence is below threshold.
        Uses claude-haiku for speed and cost (~$0.001 per document).
        Returns a dict with: document_type, confidence, extracted_data, explanation
        """
        import json
        import time

        client = self._get_ai_client()
        if not client:
            return None

        # Truncate text to keep costs low (first 3000 chars is usually enough)
        truncated_text = raw_text[:3000]

        # Build the prompt — tell Claude exactly what we need back
        system_prompt = """You are a financial document analyst for Alpha Direct Insurance, an insurance company in Botswana (currency: BWP, Botswana Pula).

Your job is to analyze extracted text from a financial document and return structured data.

You must respond with valid JSON only, no other text. The JSON must have these fields:
{
    "document_type": "invoice" | "receipt" | "bank_statement" | "expense" | "credit_note" | "unknown",
    "confidence": 0.0 to 1.0,
    "extracted_data": {
        "total_amount": number or null,
        "document_date": "YYYY-MM-DD" or null,
        "due_date": "YYYY-MM-DD" or null,
        "vendor_name": "string" or null,
        "customer_name": "string" or null,
        "reference_number": "string" or null,
        "tax_amount": number or null,
        "currency": "BWP" or other currency code,
        "line_items": [{"description": "...", "amount": number}] or []
    },
    "explanation": "A 1-2 sentence plain English explanation of what this document is, who it's from, and what it's for. Written for a non-accountant to understand."
}"""

        user_prompt = f"""Analyze this financial document text. Our rule-based system classified it as "{rule_doc_type}" with {rule_confidence:.0%} confidence, but we need verification.

Document text:
---
{truncated_text}
---

Respond with JSON only."""

        try:
            start = time.time()
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1024,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            elapsed_ms = int((time.time() - start) * 1000)

            # Parse the JSON response
            response_text = response.content[0].text.strip()
            # Handle markdown code blocks if Claude wraps the JSON
            if response_text.startswith('```'):
                response_text = response_text.split('\n', 1)[1].rsplit('```', 1)[0].strip()

            result = json.loads(response_text)

            # Log processing time
            result['processing_time_ms'] = elapsed_ms

            return result

        except json.JSONDecodeError as e:
            # AI returned invalid JSON — log it but don't crash
            AIActivityLog.objects.create(
                document_upload=upload,
                action_type='ai_error',
                input_data={'error': f'Invalid JSON from AI: {str(e)}'},
                output_data={'raw_response': response_text[:500] if 'response_text' in dir() else ''},
                confidence=0,
            )
            return None
        except Exception as e:
            # API error (rate limit, auth, network) — log and continue without AI
            AIActivityLog.objects.create(
                document_upload=upload,
                action_type='ai_error',
                input_data={'error': str(e)},
                output_data={},
                confidence=0,
            )
            return None

    # ─── SUGGESTION ENGINE ────────────────────────────────────────────────

    def _suggest_action(self, data, doc_type):
        """Generate a suggested accounting action based on extracted data."""
        suggestion = {
            'action_type': None,
            'confidence': 0.0,
            'details': {},
        }

        if doc_type == 'invoice':
            suggestion['action_type'] = 'create_vendor_bill'
            suggestion['confidence'] = 0.7
            suggestion['details'] = {
                'invoice_type': 'vendor_bill',
                'total_amount': data.get('total_amount'),
                'issue_date': data.get('document_date'),
                'due_date': data.get('due_date') or data.get('document_date'),
                'vendor_name': data.get('vendor_name') or data.get('possible_vendor'),
                'reference': data.get('reference_number') or (data['references'][0] if data.get('references') else None),
                'tax_amount': data.get('tax_amount'),
                'line_items': data.get('line_items', []),
                'suggested_account': '5100',  # Default expense
            }
            self._apply_vendor_mapping(suggestion, suggestion['details'].get('vendor_name'))

        elif doc_type == 'receipt':
            suggestion['action_type'] = 'record_payment_received'
            suggestion['confidence'] = 0.6
            suggestion['details'] = {
                'amount': data.get('total_amount'),
                'payment_date': data.get('document_date'),
                'reference': data.get('reference_number') or (data['references'][0] if data.get('references') else None),
                'customer_name': data.get('customer_name'),
            }

        elif doc_type == 'bank_statement':
            suggestion['action_type'] = 'import_bank_statement'
            suggestion['confidence'] = 0.8
            suggestion['details'] = {
                'statement_date': data.get('document_date'),
                'line_count': len(data.get('amounts', [])),
            }

        elif doc_type == 'credit_note':
            suggestion['action_type'] = 'create_credit_note'
            suggestion['confidence'] = 0.7
            suggestion['details'] = {
                'total_amount': data.get('total_amount'),
                'issue_date': data.get('document_date'),
                'vendor_name': data.get('vendor_name') or data.get('possible_vendor'),
            }

        else:
            suggestion['action_type'] = 'manual_review'
            suggestion['confidence'] = 0.0
            suggestion['details'] = {
                'extracted_amounts': data.get('amounts', []),
                'extracted_dates': data.get('dates', []),
            }

        return suggestion

    def _apply_vendor_mapping(self, suggestion, vendor_name):
        """Check if we have a learned mapping for this vendor."""
        if not vendor_name:
            return
        from documents.models import VendorMapping
        normalized = vendor_name.strip().upper()
        mapping = VendorMapping.objects.filter(vendor_pattern__iexact=normalized).first()
        if mapping:
            suggestion['details']['contact_id'] = str(mapping.contact_id)
            suggestion['details']['contact_name'] = mapping.contact.name
            if mapping.default_account:
                suggestion['details']['suggested_account'] = mapping.default_account.code
            if mapping.auto_apply:
                suggestion['confidence'] = min(suggestion['confidence'] + 0.2, 1.0)

7. Create the AIActivityLog model (add to documents/models.py):
   - id: UUIDField (primary key)
   - document_upload: ForeignKey(DocumentUpload)
   - action_type: CharField choices=['ai_extraction', 'ai_error', 'rule_extraction', 'user_confirm', 'user_reject']
   - input_data: JSONField(null=True)
   - output_data: JSONField(null=True)
   - confidence: DecimalField(max_digits=3, decimal_places=2, default=0)
   - processing_time_ms: IntegerField(null=True)
   - created_at: DateTimeField(auto_now_add=True)

8. Create documents/views.py with DRF views:
   - DocumentUploadView: POST to upload a file (multipart/form-data), returns the created DocumentUpload. Calculates file hash (SHA-256), checks for duplicates (return existing if hash matches), then runs DocumentProcessor().process(upload.id) synchronously.
   - DocumentListView: GET to list all uploads for the current user, with filters (status, document_type, date range). Order by -created_at.
   - DocumentDetailView: GET to retrieve a single upload with all extracted data, suggestions, and ai_explanation.
   - DocumentConfirmView: POST to confirm a suggestion. Accepts the (possibly edited) form data. Compares to original suggestion and stores diff in user_corrections. Creates the actual Invoice/Payment/JE using existing billing/payments models. Updates VendorMapping (increment match_count, set auto_apply=True if match_count >= 3).
   - DocumentRejectView: POST to reject a suggestion. Sets user_decision='rejected'.

9. Register the endpoints in alpha_finance/api_router.py or urls.py:
   - POST /api/v1/documents/upload/
   - GET /api/v1/documents/
   - GET /api/v1/documents/{id}/
   - POST /api/v1/documents/{id}/confirm/
   - POST /api/v1/documents/{id}/reject/

10. Run makemigrations and migrate.

11. Create a management command: python manage.py test_document_processing <filepath>
    That processes a local file through the pipeline and prints:
    - File type and text extraction result (first 500 chars)
    - Layer 1 classification and confidence
    - Whether Layer 2 (AI) was triggered and what it returned
    - Final suggested action
    - Include a --no-ai flag to test rule-based only

IMPORTANT NOTES:
- The system must work WITHOUT an Anthropic API key (rule-based only). The AI layer is an enhancement, not a dependency. If ANTHROPIC_API_KEY is empty, skip Layer 2 silently.
- Use claude-haiku-4-5-20251001 (cheapest and fastest model). Do NOT use Sonnet or Opus for document processing — it's wasteful for this use case.
- Truncate document text to 3000 characters before sending to the API to keep costs minimal.
- Always wrap API calls in try/except — network errors, rate limits, and auth failures must not crash the upload flow.
- The AI response must be logged in AIActivityLog regardless of success or failure.

When all work is complete, verified (migrations run, endpoints return JSON, management command works), commit all changes to git with a descriptive commit message. Do not push to remote.
```

---

## Phase 3: Smart Entry Frontend

```
You are working on the Alpha Direct Financial Management System in C:\Users\OTHER\alpha_finance. Read CLAUDE.md for full context. This phase builds the Smart Entry frontend page that consumes the document processing API built in Phase 2.

### OBJECTIVE: Redesign Quick Entry → Smart Entry

1. Rename the route: Keep the file at frontend/src/app/(dashboard)/quick-entry/page.tsx but completely rewrite it. Also update the sidebar label from "Quick Entry" to "Smart Entry" in Sidebar.tsx.

2. The page should have TWO modes, toggled by a segmented control at the top:
   - "Upload Document" (AI-assisted) — default mode
   - "Manual Entry" (existing payment/expense tabs, already built, just move them here)

3. UPLOAD DOCUMENT MODE — Layout:

   Left panel (40% width on desktop, full width on mobile):
   - Upload Zone: A large dashed-border drop area. It should:
     * Accept drag-and-drop files
     * Accept click-to-browse (hidden file input)
     * Accept paste from clipboard (Ctrl+V event listener)
     * Show accepted file types: PDF, PNG, JPG, CSV, XLSX
     * Show max file size: 10MB
     * Use theme colors (theme.cardBdr for border, theme.orange for hover/active state)
   - Recent Uploads List: Below the upload zone, show a scrollable list of recent document uploads from GET /api/v1/documents/. Each item shows:
     * File name (truncated)
     * Status badge (processing/ready/confirmed/rejected)
     * Document type badge if classified
     * Upload time (relative: "2 min ago")
     * Click to select → shows details in right panel

   Right panel (60% width):
   - When no document selected: Show a placeholder "Select a document or upload a new one"
   - When a document is selected and status is "processing": Show a spinner with "Analyzing document..."
   - When status is "suggested" or "extracted": Show the Review Interface:

     TOP SECTION — Document Summary:
     * Document type badge with confidence (e.g., "Invoice — 90% confidence")
     * AI explanation: "This appears to be a vendor invoice from [vendor name] for BWP [amount], dated [date]."
     * Original file name and upload time

     MIDDLE SECTION — Extracted Data (editable form):
     * Show all extracted fields as form inputs the user can edit
     * For invoices: vendor (contact search), amount, date, due date, reference, expense account (dropdown)
     * For receipts: from whom, amount, date, reference
     * For bank statements: bank account, statement date, line count
     * Highlight any field that has low confidence with an orange border
     * Pre-fill from vendor mapping if available

     BOTTOM SECTION — Suggested Action:
     * Show what the system proposes: "Create Vendor Bill" / "Record Payment" / etc.
     * Show the journal entry that would be created (debit/credit preview table)
     * Three action buttons:
       - "Confirm" (green, primary) — POST /api/v1/documents/{id}/confirm/ with current form data
       - "Edit & Confirm" (orange) — same as confirm but sends user_corrections
       - "Reject" (gray/red outline) — POST /api/v1/documents/{id}/reject/
     * After confirm: Show success state with link to created invoice/payment/JE

   - When status is "confirmed": Show a green "Confirmed" state with link to the created record
   - When status is "rejected": Show a muted "Rejected" state with option to reprocess
   - When status is "error": Show error message with retry button

4. MANUAL ENTRY MODE:
   - Move the existing PaymentTab and QuickExpenseTab components into this mode
   - Add a third tab: "Journal Entry" that allows creating a manual journal entry with:
     * Date, description, journal type
     * Dynamic rows for debit/credit lines (account dropdown, amount, description)
     * Running balance indicator (must equal zero to submit)
     * POST to /api/v1/journal-entries/

5. IMPORTANT STYLING RULES:
   - Use the theme system for ALL colors (no hard-coded hex values)
   - Follow the Alpha Direct Design System (navy + orange, 8px grid)
   - File upload zone should have a subtle animation on drag-over (border color change + slight scale)
   - Status badges should use the getStatusBgColor() utility
   - The page should feel fast — show optimistic UI updates where possible

6. Add to frontend/src/lib/api.ts:
   - uploadDocument(file: File): POST multipart to /api/v1/documents/upload/
   - getDocuments(params): GET /api/v1/documents/
   - getDocument(id): GET /api/v1/documents/{id}/
   - confirmDocument(id, data): POST /api/v1/documents/{id}/confirm/
   - rejectDocument(id): POST /api/v1/documents/{id}/reject/

7. Add TypeScript types in frontend/src/types/index.ts (or in the api.ts file):
   - DocumentUpload interface matching the backend model
   - DocumentSuggestion interface for the suggested_action JSON

After completing, verify:
- File drag-and-drop works (drops a file, shows processing, then shows suggestion)
- Manual entry tabs still work for payments and expenses
- Theme works in both Light and Fun modes
- No TypeScript errors

When all work is complete and verified, commit all changes to git with a descriptive commit message. Do not push to remote.
```

---

## Phase 4: UX Polish + Fun Mode + Quick Actions

```
You are working on the Alpha Direct Financial Management System in C:\Users\OTHER\alpha_finance. Read CLAUDE.md for full context. This phase focuses on visual polish, Fun Mode improvements, and UX refinements across the system.

### OBJECTIVE 1: Fun Mode Visual Refinement

Edit frontend/src/lib/themes.ts — modify the "fun" theme object:

1. Change these values:
   - bg: '#0A0A30' → '#121235' (lighter, less eye strain)
   - card: '#151555' → '#1A1A5E' (slightly lighter cards for better contrast)
   - t2: '#C4B5FD' → '#D4CCFF' (more readable secondary text)
   - t3: '#8B7FD4' → '#9B91E0' (more readable muted text)
   - cardSh: '0 2px 12px rgba(240,127,0,0.12)' → '0 2px 8px rgba(240,127,0,0.06)' (softer glow)

2. Edit frontend/src/components/FunBackground.tsx:
   - Reduce mascot opacity from 0.05 to 0.025
   - Remove the text phrases entirely (the FUN_PHRASES array and the isText branch). Keep only the mascot images.
   - Change the radial gradient to use the new bg color: '#121235' instead of '#0A0A30'
   - Reduce ELEMENT_COUNT from 16 to 10

### OBJECTIVE 2: Navigation Cleanup

Edit frontend/src/components/layout/Sidebar.tsx:

1. The sidebar currently shows: Home, Dashboard, Quick Entry, then 3 collapsible groups (Debtors, Payables, Reporting), then Bank Reconciliation and Tax Calendar standalone.

2. Remove the "Home" item (it just redirects to /dashboard anyway — redundant).

3. Rename "Quick Entry" to "Smart Entry" and update the href to match.

4. Add a new group "Accounting" between the top items and "Debtors":
   - Accounting (icon: BookOpen)
     - Chart of Accounts → /accounts
     - Journal Entries → /journal-entries
     - Fiscal Periods → /fiscal-periods (placeholder for now)

5. Move "Bank Reconciliation" into its own group or under a "Banking" group:
   - Banking (icon: Landmark)
     - Bank Reconciliation → /banking
     - Bank Accounts → /bank-accounts (placeholder)

6. Move "Tax Calendar" under a "Compliance" section or keep standalone — your judgment. If standalone, that's fine.

### OBJECTIVE 3: Dashboard Quick Actions Bar

Edit frontend/src/app/(dashboard)/dashboard/page.tsx:

1. Add a "Quick Actions" row at the bottom of the dashboard (after the Action Center):
   - 4 buttons in a horizontal row, each as a card-like element:
     * "Record Payment" → /quick-entry (manual mode, customer tab)
     * "Create Invoice" → /invoices/new
     * "Upload Document" → /quick-entry (upload mode)
     * "Reconcile Bank" → /banking
   - Each button has an icon, label, and subtle description
   - Use theme colors, orange accent for the primary action (Upload Document)

### OBJECTIVE 4: Component-Level UX Improvements

1. Contact Search (in quick-entry/page.tsx or wherever the ContactSearch component lives):
   - Show the 3 most recently used contacts as clickable chips above the search input
   - Store recent contacts in localStorage (key: 'alpha_recent_contacts_{type}')
   - When a chip is clicked, select that contact immediately

2. Date inputs across the app:
   - Find all <input type="date"> elements
   - Ensure they all use the theme system for colors
   - Add a "Today" quick button next to each date input (small text link that sets value to today)

3. Success states:
   - In the Quick Entry payment success card, add an expandable "View Journal Entry" section
   - When expanded, show the debit/credit lines of the JE that was created
   - Fetch from GET /api/v1/journal-entries/{je_id}/ using the je_number from the payment response

### OBJECTIVE 5: Loading and Transitions

1. In frontend/src/app/(dashboard)/layout.tsx:
   - Change the navigation loading state from a full LoadingScreen to a subtle fade transition
   - Use CSS opacity transition: content fades out slightly (opacity 0.6) for 150ms, then fades back in
   - This is faster and less jarring than the current approach

2. Add a subtle slide-up animation for cards on the dashboard when they first load:
   - Use CSS @keyframes with translateY(8px) → translateY(0) and opacity 0 → 1
   - Stagger the animation for each card row (50ms delay between rows)
   - Keep it fast (200ms duration) and subtle

After completing all objectives, do a full visual review:
- Navigate through every page in Light theme
- Switch to Fun theme and navigate through every page
- Verify no hard-coded colors remain visible (everything should adapt to theme)
- Check that the sidebar navigation makes sense and is not overcrowded
- Verify dashboard loads cleanly with all new elements

When all work is complete and verified, commit all changes to git with a descriptive commit message. Do not push to remote.
```

---

## Execution Order Summary

| Phase | Focus | Depends On | Key Deliverables |
|-------|-------|------------|-----------------|
| 1 | Foundation Fixes | Nothing | Theme fix, Journal Entries page, Dashboard redesign |
| 2 | AI Backend | Phase 1 (stable codebase) | documents app, processing pipeline, API endpoints |
| 3 | Smart Entry UI | Phase 2 (API endpoints) | Upload interface, AI review UI, suggestion forms |
| 4 | UX Polish | Phases 1-3 (all pages exist) | Fun Mode fixes, nav cleanup, animations, micro-UX |

**Total estimated effort:** 3-5 Claude Code sessions depending on complexity of each phase.

**After all 4 phases**, you will have:
- A fully themed system that works in both Light and Fun modes
- A dedicated Journal Entries page for the finance team
- A dashboard with all 3 financial statements and abbreviated values
- An AI-powered document upload system with rule-based extraction
- A polished, consistent UX following Apple-like design principles
