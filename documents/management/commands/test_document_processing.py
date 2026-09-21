"""
Management command to test the document processing pipeline on a local file.

Usage:
    python manage.py test_document_processing path/to/file.pdf
    python manage.py test_document_processing path/to/file.pdf --no-ai
"""

import hashlib
import os

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand

from documents.models import DocumentUpload
from documents.services import DocumentProcessor


class Command(BaseCommand):
    help = 'Process a local file through the document processing pipeline'

    def add_arguments(self, parser):
        parser.add_argument(
            'filepath',
            type=str,
            help='Path to the file to process',
        )
        parser.add_argument(
            '--no-ai',
            action='store_true',
            default=False,
            help='Skip AI layer (rule-based only)',
        )

    def handle(self, *args, **options):
        filepath = options['filepath']
        no_ai = options['no_ai']

        if not os.path.isfile(filepath):
            self.stderr.write(self.style.ERROR(f"File not found: {filepath}"))
            return

        filename = os.path.basename(filepath)
        file_size = os.path.getsize(filepath)

        self.stdout.write(self.style.MIGRATE_HEADING(
            '\n=== Alpha Direct Document Processing Test ===\n'
        ))
        self.stdout.write(f"  File: {filename}")
        self.stdout.write(f"  Size: {file_size:,} bytes")
        self.stdout.write(f"  AI Layer: {'DISABLED' if no_ai else 'ENABLED (if needed)'}")
        self.stdout.write('')

        # Read file and compute hash
        with open(filepath, 'rb') as f:
            file_data = f.read()
        file_hash = hashlib.sha256(file_data).hexdigest()

        # Get or create a system user
        sys_user = User.objects.filter(is_superuser=True).first()
        if not sys_user:
            self.stderr.write(self.style.ERROR(
                "No superuser found. Run setup_initial_data first."
            ))
            return

        # Determine file type
        ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
        file_type_map = {
            'pdf': 'pdf', 'png': 'image', 'jpg': 'image', 'jpeg': 'image',
            'csv': 'csv', 'xlsx': 'xlsx',
        }
        file_type = file_type_map.get(ext, 'unknown')

        # Delete any existing record with the same hash (test mode)
        DocumentUpload.objects.filter(file_hash=file_hash).delete()

        # Create upload record
        upload = DocumentUpload(
            original_filename=filename,
            file_type=file_type,
            file_size=file_size,
            file_hash=file_hash,
            status='uploading',
            uploaded_by=sys_user,
        )
        upload.file.save(filename, ContentFile(file_data), save=False)
        upload.save()

        self.stdout.write(f"  Upload ID: {upload.id}")
        self.stdout.write(f"  File Hash: {file_hash[:16]}...")
        self.stdout.write('')

        # Override AI setting if --no-ai
        if no_ai:
            from django.conf import settings
            original_key = getattr(settings, 'ANTHROPIC_API_KEY', '')
            settings.ANTHROPIC_API_KEY = ''

        # Run the pipeline
        self.stdout.write(self.style.MIGRATE_HEADING('--- Processing ---'))
        try:
            processor = DocumentProcessor()
            processor.process(upload.id)
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"\nProcessing error: {e}"))

        # Restore AI key
        if no_ai:
            from django.conf import settings
            settings.ANTHROPIC_API_KEY = original_key

        # Reload from DB
        upload.refresh_from_db()

        # Display results
        self.stdout.write('')
        self.stdout.write(self.style.MIGRATE_HEADING('--- Results ---'))
        self.stdout.write('')

        # Text extraction
        text = upload.extracted_text or ''
        self.stdout.write(self.style.SUCCESS('1. Text Extraction:'))
        if text:
            preview = text[:500].replace('\n', '\n    ')
            self.stdout.write(f"    {preview}")
            if len(text) > 500:
                self.stdout.write(f"    ... ({len(text)} chars total)")
        else:
            self.stdout.write('    (no text extracted)')
        self.stdout.write('')

        # Classification
        self.stdout.write(self.style.SUCCESS('2. Layer 1 Classification:'))
        self.stdout.write(f"    Document Type: {upload.document_type or 'unknown'}")
        self.stdout.write(f"    Confidence:    {upload.classification_confidence or 0}")
        self.stdout.write(f"    Method:        {upload.extraction_method}")
        self.stdout.write('')

        # AI layer
        self.stdout.write(self.style.SUCCESS('3. Layer 2 (AI Enhancement):'))
        logs = upload.activity_logs.filter(
            action_type__in=['ai_extraction', 'ai_error']
        )
        if no_ai:
            self.stdout.write('    SKIPPED (--no-ai flag)')
        elif not logs.exists():
            conf = float(upload.classification_confidence or 0)
            from django.conf import settings
            threshold = getattr(settings, 'AI_CONFIDENCE_THRESHOLD', 0.75)
            if conf >= threshold:
                self.stdout.write(
                    f'    NOT NEEDED (confidence {conf:.0%} >= '
                    f'threshold {threshold:.0%})'
                )
            else:
                api_key = getattr(settings, 'ANTHROPIC_API_KEY', '')
                if not api_key:
                    self.stdout.write(
                        '    SKIPPED (no ANTHROPIC_API_KEY configured)'
                    )
                else:
                    self.stdout.write('    No AI activity recorded')
        else:
            for log in logs:
                self.stdout.write(f"    Action: {log.action_type}")
                self.stdout.write(f"    Confidence: {log.confidence}")
                if log.processing_time_ms:
                    self.stdout.write(
                        f"    Processing Time: {log.processing_time_ms}ms"
                    )
                if log.output_data:
                    import json
                    preview = json.dumps(log.output_data, indent=2)[:400]
                    self.stdout.write(f"    Output: {preview}")
        self.stdout.write('')

        # Explanation
        self.stdout.write(self.style.SUCCESS('4. Explanation:'))
        self.stdout.write(f"    {upload.ai_explanation or '(none)'}")
        self.stdout.write('')

        # Extracted data
        self.stdout.write(self.style.SUCCESS('5. Extracted Data:'))
        if upload.extracted_data:
            import json
            self.stdout.write(
                '    ' + json.dumps(upload.extracted_data, indent=2)
                .replace('\n', '\n    ')
            )
        else:
            self.stdout.write('    (none)')
        self.stdout.write('')

        # Suggested action
        self.stdout.write(self.style.SUCCESS('6. Suggested Action:'))
        if upload.suggested_action:
            import json
            self.stdout.write(
                '    ' + json.dumps(upload.suggested_action, indent=2)
                .replace('\n', '\n    ')
            )
        else:
            self.stdout.write('    (none)')
        self.stdout.write('')

        # Final status
        self.stdout.write(self.style.MIGRATE_HEADING('--- Summary ---'))
        status_style = (
            self.style.SUCCESS if upload.status == 'suggested'
            else self.style.ERROR if upload.status == 'error'
            else self.style.WARNING
        )
        self.stdout.write(f"  Status: {status_style(upload.status)}")
        if upload.error_message:
            self.stdout.write(
                f"  Error: {self.style.ERROR(upload.error_message)}"
            )
        self.stdout.write('')
