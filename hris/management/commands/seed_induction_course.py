"""Seed (or refresh) the Alpha Direct induction course.

    manage.py seed_induction_course              # dry run — says what it would do
    manage.py seed_induction_course --commit     # write it
    manage.py seed_induction_course --commit --republish   # rebuild the papers too

Content lives in hris/data/induction_slides.json and induction_questions_*.json,
authored against the Conditions of Service 2026 (ELRA 2025) in the HR Document
Vault. Editing the JSON and re-running is the supported way to change the course.

The seeder deliberately SHUFFLES each question's canonical option order with a
stable per-question seed. The source files are written with the right answer
first for readability; storing them that way would make the answer key guessable
to anyone who ever saw a raw dump.
"""
from __future__ import annotations

import hashlib
import json
import os
import random

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), 'data')


def _load(name: str) -> dict:
    with open(os.path.join(DATA_DIR, name), encoding='utf-8') as fh:
        return json.load(fh)


def _shuffled(options: list, correct_index: int, salt: str) -> tuple[list, int]:
    """Stable per-question shuffle of the canonical option order."""
    order = list(range(len(options)))
    random.Random(hashlib.sha256(salt.encode()).hexdigest()).shuffle(order)
    return [options[i] for i in order], order.index(correct_index)


class Command(BaseCommand):
    help = 'Seed the Conditions of Service induction course (slides + question bank).'

    def add_arguments(self, parser):
        parser.add_argument('--commit', action='store_true',
                            help='Actually write. Without it this is a dry run.')
        parser.add_argument('--republish', action='store_true',
                            help='Also rebuild the exam papers and publish.')

    def handle(self, *args, **opts):
        from hris.models import HRDocument
        from hris.training_models import (TrainingCourse, TrainingQuestion,
                                          TrainingSlide)
        from hris.training_service import build_versions

        deck = _load('induction_slides.json')
        meta = deck['course']
        slides = deck['slides']
        questions = (_load('induction_questions_1.json')['questions']
                     + _load('induction_questions_2.json')['questions'])

        self.stdout.write(f'Course : {meta["title"]}')
        self.stdout.write(f'Slides : {len(slides)} '
                          f'({sum(s.get("seconds", 60) for s in slides) // 60} minutes)')
        self.stdout.write(f'Bank   : {len(questions)} questions')
        self.stdout.write(f'Papers : {meta["version_count"]} of '
                          f'{meta["questions_per_paper"]}')

        if len(questions) < meta['questions_per_paper']:
            self.stderr.write('Bank is smaller than one paper — refusing.')
            return

        if not opts['commit']:
            self.stdout.write(self.style.WARNING('Dry run — nothing written. '
                                                 'Add --commit.'))
            return

        with transaction.atomic():
            course, created = TrainingCourse.objects.update_or_create(
                slug=meta['slug'],
                defaults={
                    'title': meta['title'],
                    'summary': meta['summary'],
                    'source': TrainingCourse.Source.SEEDED,
                    'is_induction': True,
                    'duration_minutes': meta['duration_minutes'],
                    'min_dwell_seconds': meta['min_dwell_seconds'],
                    'questions_per_paper': meta['questions_per_paper'],
                    'version_count': meta['version_count'],
                    'pass_mark': meta['pass_mark'],
                    'time_limit_minutes': meta['time_limit_minutes'],
                },
            )

            # Point the course at the real manual in the vault, so a reader can
            # always open the source. Matched on title — if HR re-uploads a new
            # version under the same title the link follows it.
            doc = HRDocument.objects.filter(
                title__icontains='Conditions of Service').order_by('-created_at').first()
            if doc and course.source_document_id != doc.id:
                course.source_document = doc
                course.source_filename = (doc.file.name.split('/')[-1]
                                          if doc.file else '')
                course.save(update_fields=['source_document', 'source_filename',
                                           'updated_at'])

            # The CFO's brief is a THIRTY-minute course, so the advertised
            # duration has to be the truth. The JSON carries a per-slide
            # estimate written for readability; scale it so the deck actually
            # sums to duration_minutes rather than quietly running long.
            raw_total = sum(s.get('seconds', 60) for s in slides) or 1
            scale = (course.duration_minutes * 60) / raw_total

            course.slides.all().delete()
            for n, s in enumerate(slides, start=1):
                TrainingSlide.objects.create(
                    course=course, order=n, title=s['title'],
                    body_md='\n'.join(f'- {b}' for b in s['bullets']),
                    section_ref=s.get('section_ref', ''),
                    est_seconds=max(15, round(s.get('seconds', 60) * scale)),
                )

            course.questions.all().delete()
            for q in questions:
                opts_, ci = _shuffled(q['options'], q['correct_index'], q['stem'])
                TrainingQuestion.objects.create(
                    course=course, stem=q['stem'], options=opts_, correct_index=ci,
                    explanation=q.get('explanation', ''),
                    section_ref=q.get('section_ref', ''),
                    topic=q.get('topic', ''), is_active=True, ai_generated=False,
                )

            if opts['republish'] or course.status != TrainingCourse.Status.PUBLISHED:
                built = build_versions(course)
                course.status = TrainingCourse.Status.PUBLISHED
                course.published_at = course.published_at or timezone.now()
                course.save(update_fields=['status', 'published_at', 'updated_at'])
                self.stdout.write(f'Built {len(built)} papers.')

        verb = 'Created' if created else 'Refreshed'
        self.stdout.write(self.style.SUCCESS(
            f'{verb} "{course.title}" — {course.slides.count()} slides, '
            f'{course.questions.count()} questions, {course.versions.count()} papers, '
            f'pass mark {course.pass_mark}%.'))
        if doc:
            self.stdout.write(f'Linked to vault document: {doc.title}')
        else:
            self.stdout.write(self.style.WARNING(
                'No "Conditions of Service" document found in the vault to link to.'))
