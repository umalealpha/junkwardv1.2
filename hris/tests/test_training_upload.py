"""The "Upload Here" pipeline, driven end to end with the model stubbed.

This file exists because of a bug it would have caught. `generate_course` and
`_write_course` had ZERO coverage — every test stopped at the validators — so a
NameError in the course-writing path sat green through a full suite run and
would have 500'd on Dorothy's first upload, after burning the whole model spend.

The model is stubbed, so this costs nothing and never leaves the box. What is
under test is OUR code: extraction, chunking, validation, the deadline, and the
database write.
"""
from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase

from hris import training_ai
from hris.training_ai import (ExtractionFailed, GenerationFailed, chunk,
                              extract_text, generate_course)
from hris.training_models import TrainingCourse

SOURCE = (
    '7.6.3 Employees are entitled to 20 days sick leave per annum. '
    'It is important to note that sick leave cannot be accrued.\n\n'
    '7.1 Staff members are entitled to annual leave. '
    'Manager and above accrue 25 days a year.\n\n'
    '8.10.1 Notice of termination shall be three months for C-suite and '
    'Controller positions, and one month for all other employees.\n\n'
) * 6


def _slide(n):
    return {'title': f'Sick leave {n}', 'bullets': ['You get 20 days a year.'],
            'section_ref': '7.6.3', 'seconds': 60}


def _question(n):
    return {
        'stem': f'How many days of sick leave per year? (variant {n})',
        'options': ['20', '15', '30', '10'],
        'correct_index': 0,
        'explanation': 'Clause 7.6.3.',
        'section_ref': '7.6.3',
        'topic': 'Leave',
        'source_quote': 'Employees are entitled to 20 days sick leave per annum.',
    }


def fake_ask(system, shape, extract, want, feature):
    if 'slides' in feature:
        return {'slides': [_slide(i) for i in range(4)]}
    return {'questions': [_question(f'{feature}-{i}') for i in range(60)]}


class ExtractionTests(TestCase):
    def test_a_plain_text_upload_is_read(self):
        self.assertIn('sick leave', extract_text(SOURCE.encode(), 'cos.txt'))

    def test_an_unsupported_file_type_is_refused_in_plain_english(self):
        with self.assertRaises(ExtractionFailed) as cm:
            extract_text(b'x', 'scan.tiff')
        self.assertIn('PDF', str(cm.exception))

    def test_an_old_doc_file_tells_the_user_what_to_do(self):
        with self.assertRaises(ExtractionFailed) as cm:
            extract_text(b'x', 'policy.doc')
        self.assertIn('.docx', str(cm.exception))

    def test_chunking_never_loses_the_text(self):
        parts = chunk(SOURCE, size=200)
        self.assertGreater(len(parts), 1)
        self.assertIn('20 days sick leave', ''.join(parts))


class GenerateCourseTests(TestCase):
    """The path that had no coverage at all."""

    def test_an_upload_becomes_a_draft_course(self):
        with patch.object(training_ai, '_ask', side_effect=fake_ask):
            course, report = generate_course(
                data=SOURCE.encode(), filename='Conditions.txt',
                title='Conditions test')

        self.assertEqual(course.status, TrainingCourse.Status.DRAFT,
                         'an AI course must never publish itself')
        self.assertTrue(course.ai_generated)
        self.assertGreaterEqual(course.questions.count(), 40)
        self.assertGreater(course.slides.count(), 0)
        self.assertEqual(report['questions_kept'], course.questions.count())
        self.assertEqual(report['parts_read'], report['parts_total'])
        self.assertIn('was read', course.ai_notes)

    def test_a_document_the_model_cannot_use_is_refused_cleanly(self):
        """No half-built course may survive a failed generation."""
        before = TrainingCourse.objects.count()
        with patch.object(training_ai, '_ask',
                          side_effect=lambda *a, **k: {'slides': [], 'questions': []}):
            with self.assertRaises(GenerationFailed):
                generate_course(data=SOURCE.encode(), filename='Empty.txt')
        self.assertEqual(TrainingCourse.objects.count(), before)

    def test_an_invented_rule_never_reaches_the_bank(self):
        """The quote must be in the document. A fabricated citation is dropped."""
        def liar(system, shape, extract, want, feature):
            if 'slides' in feature:
                return {'slides': [_slide(0)]}
            good = [_question(f'v{i}') for i in range(50)]
            bad = dict(_question('bad'),
                       stem='How many days of sick leave per year?',
                       options=['40', '15', '30', '10'],
                       source_quote='Employees are entitled to 40 days sick leave.')
            return {'questions': good + [bad]}

        with patch.object(training_ai, '_ask', side_effect=liar):
            course, report = generate_course(data=SOURCE.encode(),
                                             filename='Liar.txt')
        # The fabricated question is the only one offering "40" as an option.
        banks = [q.options for q in course.questions.all()]
        self.assertTrue(banks, 'no questions were kept at all')
        self.assertFalse(any('40' in opts for opts in banks),
                         'a question whose citation is not in the document '
                         'reached the live bank')
        self.assertEqual(report['reject_reasons']
                         .get('source quote is not in the document'), 1)

    def test_a_long_document_says_how_much_it_actually_read(self):
        long_source = SOURCE * 400          # forces more chunks than MAX_CHUNKS
        with patch.object(training_ai, '_ask', side_effect=fake_ask):
            course, report = generate_course(data=long_source.encode(),
                                             filename='Long.txt')
        self.assertTrue(report['truncated'])
        self.assertGreater(report['parts_total'], report['parts_read'])
        self.assertIn('Split the file', course.ai_notes.replace('split', 'Split'))

    def test_the_deadline_stops_the_read_and_says_so(self):
        """A slow model must not run past the web request. parts_read has to
        report what was really read, not the slice we hoped to read."""
        calls = {'n': 0}

        def slow(system, shape, extract, want, feature):
            calls['n'] += 1
            if calls['n'] > 2:
                training_ai.UPLOAD_DEADLINE_SECONDS = -1     # deadline passed
            return fake_ask(system, shape, extract, want, feature)

        original = training_ai.UPLOAD_DEADLINE_SECONDS
        try:
            with patch.object(training_ai, '_ask', side_effect=slow):
                course, report = generate_course(
                    data=(SOURCE * 400).encode(), filename='Slow.txt')
        finally:
            training_ai.UPLOAD_DEADLINE_SECONDS = original

        self.assertLess(report['parts_read'], training_ai.MAX_CHUNKS)
        self.assertIn('Stopped', course.ai_notes)
        self.assertNotIn('\n\n', course.ai_notes, 'blank line in the HR note')
