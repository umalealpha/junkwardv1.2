"""
documents/services.py

Two-layer document processing pipeline for Alpha Direct.

Layer 1: Rule-based (regex, keyword matching, vendor mappings) - free, instant
Layer 2: Anthropic Claude API - only called when Layer 1 confidence < AI_CONFIDENCE_THRESHOLD

The system ALWAYS tries Layer 1 first. If confidence is high enough, Layer 2 is
skipped entirely. This keeps costs near zero for repeat vendors and standard formats.
"""

import json
import re
import time

from decimal import Decimal

from documents.models import AIActivityLog, DocumentUpload


class DocumentProcessor:
    """Main document processing pipeline."""

    def __init__(self):
        self.ai_client = None  # Lazy-loaded

    def process(self, upload_id):
        """Main entry point. Runs the full pipeline."""
        upload = DocumentUpload.objects.get(id=upload_id)
        try:
            upload.status = 'processing'
            upload.save()

            # Step 1: Extract raw text (always rule-based)
            raw_text = self._extract_text(upload)
            upload.extracted_text = raw_text

            # Step 2: LAYER 1 - Rule-based classification and extraction
            doc_type, confidence = self._classify_rules(raw_text, upload.original_filename)
            data = self._extract_data_rules(raw_text, doc_type)
            upload.document_type = doc_type
            upload.classification_confidence = Decimal(str(confidence))
            upload.extracted_data = data
            upload.extraction_method = 'rule_based'
            upload.status = 'extracted'
            upload.save()

            # Step 3: LAYER 2 - AI enhancement (only if confidence is low)
            from django.conf import settings
            threshold = getattr(settings, 'AI_CONFIDENCE_THRESHOLD', 0.75)

            if confidence < threshold and getattr(settings, 'ANTHROPIC_API_KEY', ''):
                ai_result = self._enhance_with_ai(
                    raw_text, doc_type, confidence, data, upload
                )
                if ai_result:
                    # Merge AI results
                    if (ai_result.get('document_type')
                            and ai_result.get('confidence', 0) > confidence):
                        upload.document_type = ai_result['document_type']
                        upload.classification_confidence = Decimal(
                            str(ai_result['confidence'])
                        )
                    if ai_result.get('extracted_data'):
                        merged = {
                            **ai_result['extracted_data'],
                            **{k: v for k, v in data.items() if v},
                        }
                        upload.extracted_data = merged
                    if ai_result.get('explanation'):
                        upload.ai_explanation = ai_result['explanation']
                    upload.extraction_method = (
                        'hybrid' if confidence > 0 else 'ai_assisted'
                    )
                    upload.save()

                    AIActivityLog.objects.create(
                        document_upload=upload,
                        action_type='ai_extraction',
                        input_data={
                            'text_length': len(raw_text),
                            'rule_confidence': float(confidence),
                        },
                        output_data=ai_result,
                        confidence=Decimal(str(ai_result.get('confidence', 0))),
                        processing_time_ms=ai_result.get('processing_time_ms'),
                    )
            else:
                upload.ai_explanation = self._generate_rule_explanation(upload)
                upload.save()

            # Step 4: Generate suggestion
            suggestion = self._suggest_action(upload.extracted_data, upload.document_type)
            upload.suggested_action = suggestion
            upload.status = 'suggested'
            upload.save()

        except Exception as e:
            upload.status = 'error'
            # Some exceptions have an empty str() — fall back to the type name
            # so the UI never shows a blank "Processing Error" panel.
            upload.error_message = str(e) or f"{type(e).__name__}"
            upload.save()
            raise

    # ------------------------------------------------------------------
    # TEXT EXTRACTION (always rule-based)
    # ------------------------------------------------------------------

    def _extract_text(self, upload):
        """Extract text from PDF, image, CSV, or XLSX."""
        ext = upload.original_filename.rsplit('.', 1)[-1].lower()
        if ext == 'pdf':
            return self._extract_pdf(upload.file.path)
        elif ext in ('png', 'jpg', 'jpeg', 'tiff', 'bmp'):
            return self._extract_image(upload.file.path)
        elif ext == 'csv':
            return self._extract_csv(upload.file.path)
        elif ext in ('xlsx', 'xls'):
            return self._extract_xlsx(upload.file.path)
        else:
            raise ValueError(f"Unsupported file type: {ext}")

    def _extract_xlsx(self, path):
        """Render every sheet as `Sheet: <name>` + pipe-separated rows.

        Mirrors the CSV extractor: returns a plain-text dump so the
        downstream AI extractor and rule-based parsers can scan it
        the same way they do for PDF/CSV uploads. Empty rows skipped.
        """
        import openpyxl
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        parts = []
        for sheet in wb.worksheets:
            parts.append(f'Sheet: {sheet.title}')
            for row in sheet.iter_rows(values_only=True):
                if row is None:
                    continue
                if all((c is None or c == '') for c in row):
                    continue
                parts.append(' | '.join('' if c is None else str(c) for c in row))
        return '\n'.join(parts)

    def _extract_pdf(self, path):
        """Use pdfplumber to extract text and tables."""
        import pdfplumber
        from pdfplumber.utils.exceptions import PdfminerException
        from pdfminer.pdfdocument import PDFPasswordIncorrect, PDFEncryptionError

        text_parts = []
        try:
            with pdfplumber.open(path) as pdf:
                for page in pdf.pages:
                    text_parts.append(page.extract_text() or '')
                    tables = page.extract_tables()
                    for table in tables:
                        for row in table:
                            text_parts.append(
                                ' | '.join(str(cell or '') for cell in row)
                            )
        except PdfminerException as exc:
            cause = exc.args[0] if exc.args else None
            if isinstance(cause, (PDFPasswordIncorrect, PDFEncryptionError)):
                raise ValueError(
                    "This PDF is password-protected and cannot be read. "
                    "Open the file, choose Print -> Save as PDF (without a "
                    "password), then upload the unlocked copy."
                )
            raise ValueError(f"PDF could not be read: {cause or exc}") from exc
        except Exception as exc:
            raise ValueError(f"PDF could not be read: {exc}") from exc
        return '\n'.join(text_parts)

    def _extract_image(self, path):
        """Use pytesseract for OCR if available."""
        try:
            import pytesseract
            from PIL import Image
            return pytesseract.image_to_string(Image.open(path))
        except ImportError:
            raise ValueError(
                "OCR not available. Install pytesseract and Tesseract."
            )

    def _extract_csv(self, path):
        """Read CSV as text."""
        with open(path, 'r', encoding='utf-8-sig') as f:
            return f.read()

    # ------------------------------------------------------------------
    # LAYER 1: RULE-BASED
    # ------------------------------------------------------------------

    def _classify_rules(self, text, filename):
        """Rule-based document classification using keywords and patterns."""
        text_lower = text.lower()
        fname_lower = filename.lower()

        # Bank statement patterns
        if any(kw in text_lower for kw in [
            'bank statement', 'account statement', 'statement period',
        ]):
            return 'bank_statement', 0.9
        if any(kw in fname_lower for kw in ['statement', 'bank']):
            return 'bank_statement', 0.7

        # Invoice patterns
        if any(kw in text_lower for kw in [
            'tax invoice', 'invoice number', 'invoice no', 'inv no',
        ]):
            return 'invoice', 0.9
        if 'invoice' in fname_lower:
            return 'invoice', 0.7

        # Receipt patterns
        if any(kw in text_lower for kw in [
            'receipt', 'proof of payment', 'payment received',
        ]):
            return 'receipt', 0.85

        # Credit note patterns
        if any(kw in text_lower for kw in ['credit note', 'credit memo']):
            return 'credit_note', 0.9

        # Expense patterns (catch-all for vendor documents)
        if any(kw in text_lower for kw in [
            'total due', 'amount due', 'balance due', 'please pay',
        ]):
            return 'expense', 0.6

        return 'unknown', 0.0

    def _extract_data_rules(self, text, doc_type):
        """Extract structured data using regex patterns."""
        from dateutil import parser as date_parser

        data = {}

        # Extract amounts
        amount_patterns = [
            r'(?:BWP|P|R|\$|USD|ZAR)\s*[\d,]+\.?\d*',
            r'(?:total|amount|balance|due|subtotal|net|gross)'
            r'\s*:?\s*[\d,]+\.?\d*',
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
        # Prefer the largest amount as the total (most reliable heuristic)
        data['total_amount'] = data['amounts'][0] if data['amounts'] else None

        # Extract dates
        date_patterns = [
            r'\d{1,2}[-/]\d{1,2}[-/]\d{2,4}',
            r'\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)'
            r'[a-z]*\s+\d{4}',
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
            r'(?:invoice|inv|ref|reference|receipt)'
            r'\s*(?:no|number|#|:)\s*[:\s]*([\w-]+)',
        ]
        refs = []
        for pattern in ref_patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                refs.append(match.group(1).strip())
        data['references'] = refs

        # Extract vendor/company name (first non-empty line)
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
        if conf is not None:
            conf_f = float(conf)
            if conf_f >= 0.85:
                parts.append("(high confidence)")
            elif conf_f >= 0.6:
                parts.append("(medium confidence - please verify)")
            else:
                parts.append("(low confidence - manual review recommended)")
        return ' '.join(parts) + '.'

    # ------------------------------------------------------------------
    # LAYER 2: ANTHROPIC CLAUDE API
    # ------------------------------------------------------------------

    def _get_ai_client(self):
        """Lazy-load the Anthropic client."""
        if self.ai_client is None:
            from django.conf import settings
            try:
                import anthropic
            except ImportError:
                return None
            api_key = getattr(settings, 'ANTHROPIC_API_KEY', '')
            if not api_key:
                return None
            self.ai_client = anthropic.Anthropic(api_key=api_key)
        return self.ai_client

    def _enhance_with_ai(self, raw_text, rule_doc_type, rule_confidence,
                         rule_data, upload):
        """
        Call Claude Haiku to classify and extract data from the document.
        Only called when rule-based confidence is below threshold.
        Uses claude-haiku for speed and cost (~$0.001 per document).
        """
        client = self._get_ai_client()
        if not client:
            return None

        # Truncate text to keep costs low
        truncated_text = raw_text[:3000]

        system_prompt = (
            "You are a financial document analyst for Alpha Direct Insurance, "
            "an insurance company in Botswana (currency: BWP, Botswana Pula).\n\n"
            "Your job is to analyze extracted text from a financial document "
            "and return structured data.\n\n"
            "You must respond with valid JSON only, no other text. "
            "The JSON must have these fields:\n"
            "{\n"
            '    "document_type": "invoice" | "receipt" | "bank_statement" '
            '| "expense" | "credit_note" | "unknown",\n'
            '    "confidence": 0.0 to 1.0,\n'
            '    "extracted_data": {\n'
            '        "total_amount": number or null,\n'
            '        "document_date": "YYYY-MM-DD" or null,\n'
            '        "due_date": "YYYY-MM-DD" or null,\n'
            '        "vendor_name": "string" or null,\n'
            '        "customer_name": "string" or null,\n'
            '        "reference_number": "string" or null,\n'
            '        "tax_amount": number or null,\n'
            '        "currency": "BWP" or other currency code,\n'
            '        "line_items": [{"description": "...", "amount": number}] '
            "or []\n"
            "    },\n"
            '    "explanation": "A 1-2 sentence plain English explanation of '
            "what this document is, who it's from, and what it's for. "
            'Written for a non-accountant to understand."\n'
            "}"
        )

        user_prompt = (
            f"Analyze this financial document text. Our rule-based system "
            f'classified it as "{rule_doc_type}" with '
            f"{rule_confidence:.0%} confidence, but we need verification.\n\n"
            f"Document text:\n---\n{truncated_text}\n---\n\n"
            "Respond with JSON only."
        )

        response_text = ''
        try:
            start = time.time()
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1024,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            elapsed_ms = int((time.time() - start) * 1000)

            response_text = response.content[0].text.strip()
            # Handle markdown code blocks
            if response_text.startswith('```'):
                response_text = (
                    response_text.split('\n', 1)[1].rsplit('```', 1)[0].strip()
                )

            result = json.loads(response_text)
            result['processing_time_ms'] = elapsed_ms
            return result

        except json.JSONDecodeError as e:
            AIActivityLog.objects.create(
                document_upload=upload,
                action_type='ai_error',
                input_data={'error': f'Invalid JSON from AI: {str(e)}'},
                output_data={'raw_response': response_text[:500]},
                confidence=0,
            )
            return None
        except Exception as e:
            AIActivityLog.objects.create(
                document_upload=upload,
                action_type='ai_error',
                input_data={'error': str(e)},
                output_data={},
                confidence=0,
            )
            return None

    # ------------------------------------------------------------------
    # SUGGESTION ENGINE
    # ------------------------------------------------------------------

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
                'due_date': (
                    data.get('due_date') or data.get('document_date')
                ),
                'vendor_name': (
                    data.get('vendor_name') or data.get('possible_vendor')
                ),
                'reference': (
                    data.get('reference_number')
                    or (data['references'][0] if data.get('references') else None)
                ),
                'tax_amount': data.get('tax_amount'),
                'line_items': data.get('line_items', []),
                'suggested_account': '5100',
            }
            self._apply_vendor_mapping(
                suggestion, suggestion['details'].get('vendor_name')
            )

        elif doc_type == 'receipt':
            suggestion['action_type'] = 'record_payment_received'
            suggestion['confidence'] = 0.6
            suggestion['details'] = {
                'amount': data.get('total_amount'),
                'payment_date': data.get('document_date'),
                'reference': (
                    data.get('reference_number')
                    or (data['references'][0] if data.get('references') else None)
                ),
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
                'vendor_name': (
                    data.get('vendor_name') or data.get('possible_vendor')
                ),
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
        mapping = VendorMapping.objects.filter(
            vendor_pattern__iexact=normalized
        ).first()
        if mapping:
            suggestion['details']['contact_id'] = str(mapping.contact_id)
            suggestion['details']['contact_name'] = mapping.contact.name
            if mapping.default_account:
                suggestion['details']['suggested_account'] = (
                    mapping.default_account.code
                )
            if mapping.auto_apply:
                suggestion['confidence'] = min(
                    suggestion['confidence'] + 0.2, 1.0
                )
