"""
fnb_pop_rerender — rebuild stored proof PDFs from the email text already on file.

Needed once on 26-Aug-2026: the first 29 proofs captured on prod were rendered
before two document defects were fixed (the Exchange caution banner was only
half-stripped, and the provenance footer ran off the page). The email text itself
was stored correctly, so the documents can simply be re-rendered — no mailbox
read, no re-matching, nothing refiled.

  python manage.py fnb_pop_rerender --dry-run
  python manage.py fnb_pop_rerender
"""
from __future__ import annotations

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand

from fnb.models import FNBProofOfPayment as POP
from fnb.pop_capture import build_proof_pdf, clean_body


class Command(BaseCommand):
    help = 'Re-render stored proof PDFs from the email text already on file.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **o):
        qs = POP.objects.all().order_by('received_at')
        self.stdout.write(f'{qs.count()} proof(s) on file.')
        done = 0
        for p in qs:
            body = clean_body(p.body_text)
            if o['dry_run']:
                stale = ('ransomware' in (p.body_text or '')
                         or 'Do not process any payments' in (p.body_text or ''))
                self.stdout.write(f'  {p.reference[:44]:46s} '
                                  f'{"banner still in stored text" if stale else "text clean"}')
                continue
            # The stored text is re-cleaned too: rows captured before the fix kept
            # the second banner sentence in body_text as well as in the PDF.
            if body != p.body_text:
                p.body_text = body
                p.save(update_fields=['body_text', 'updated_at'])
            pdf = build_proof_pdf(reference=p.reference, amount=p.amount,
                                  bank_status=p.bank_status, received_at=p.received_at,
                                  body=p.body_text, mailbox=p.mailbox)
            name = (p.proof_pdf.name or '').split('/')[-1] or f'FNB-POP-{p.pk}.pdf'
            p.proof_pdf.save(name, ContentFile(pdf), save=True)
            done += 1
        if o['dry_run']:
            self.stdout.write(self.style.WARNING('DRY RUN — nothing written.'))
        else:
            self.stdout.write(self.style.SUCCESS(f're-rendered {done} proof PDF(s)'))
