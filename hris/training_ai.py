"""hris/training_ai.py — "Upload Here": turn a document into a course.

HR drops in a PDF or a Word file. This reads it, chunks it, and asks the model
cascade (`core.ai_assist.reasoning_complete` — local Ollama first, then DeepSeek,
then Gemini, then the paid tiers) to draft:

  * a 30-minute slide deck, and
  * a bank of multiple-choice questions large enough to cut 15 different papers.

Everything the model returns is treated as a DRAFT. A question is thrown away
unless it has a real stem, exactly four distinct options, a correct answer in
range, a section reference that appears in the source, and a `source_quote` that
appears VERBATIM in the extracted text.

🔴 Be honest about what that last check buys. It proves the quoted sentence is
really in the document; it does NOT prove the sentence supports the stem, the
options, or the answer the model marked correct. A model can pair a real
sentence from page 3 with an invented rule and pass. It is a hallucination
DAMPENER, not a correctness proof. **The real gate is a human: the course lands
in DRAFT and only becomes visible to staff when HR presses Publish.**

Only the company DOCUMENT is sent out — never a person's name, answers or
result (AD-POL-AI-GOV-001). The uploaded text still goes through
`is_safe_for_ai()` first, because HR will eventually upload something with a
staff name in it.
"""
from __future__ import annotations

import io
import json
import logging
import re

from django.db import transaction
from django.utils.text import slugify

from core.ai_assist import is_safe_for_ai, reasoning_complete

log = logging.getLogger(__name__)

# Aim for a bank several times the size of one paper so 15 papers genuinely
# differ. 30 per paper x 4 = 120 is the target; we accept anything above the
# floor and tell HR how many we got.
TARGET_BANK = 120
MIN_BANK = 40
MAX_CHARS_PER_CHUNK = 12000

# "Upload Here" runs inside one web request, and the backend runs behind
# gunicorn --timeout 120. Two model calls per chunk at 45s each means a handful
# of chunks is all that fits — a 42-page manual would otherwise make ~26 calls
# and the request would die after the spend was already burned. We read the
# FIRST MAX_CHUNKS and tell HR plainly how much of the document was used.
MAX_CHUNKS = 5
CALL_TIMEOUT = 45.0
# Each _ask walks a six-engine cascade, so one call can in the worst case run
# far longer than CALL_TIMEOUT. Stop starting new chunks once the whole upload
# has used this much, and tell HR where it stopped — better a short course than
# a dead request after the spend is burned.
UPLOAD_DEADLINE_SECONDS = 90.0


class ExtractionFailed(Exception):
    """The upload could not be read as text."""


class GenerationFailed(Exception):
    """The model gave us nothing usable."""


# ---------------------------------------------------------------------------
# Step 1 — get text out of the upload
# ---------------------------------------------------------------------------

def extract_text(data: bytes, filename: str) -> str:
    """Pull plain text out of a PDF or a Word file.

    Both libraries are already in the backend image (`pdfplumber`,
    `python-docx`); there is deliberately no new dependency here.
    """
    name = (filename or '').lower()
    if name.endswith('.pdf'):
        return _extract_pdf(data)
    if name.endswith('.docx'):
        return _extract_docx(data)
    if name.endswith('.txt') or name.endswith('.md'):
        return data.decode('utf-8', errors='replace')
    if name.endswith('.doc'):
        raise ExtractionFailed(
            'Old-style .doc files cannot be read. Please save it as .docx or PDF '
            'and upload again.')
    raise ExtractionFailed('Please upload a PDF, a Word (.docx) file, or plain text.')


def _extract_pdf(data: bytes) -> str:
    import pdfplumber
    pages = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            pages.append(f'\n<<<PAGE {i}>>>\n' + (page.extract_text() or ''))
    text = ''.join(pages).strip()
    if len(text) < 200:
        raise ExtractionFailed(
            'That PDF has almost no readable text — it looks like a scan. Please '
            'upload a text PDF or a Word file.')
    return text


def _extract_docx(data: bytes) -> str:
    import docx                                   # python-docx
    doc = docx.Document(io.BytesIO(data))
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            parts.append(' | '.join(c.text.strip() for c in row.cells))
    text = '\n'.join(t for t in parts if t.strip()).strip()
    if len(text) < 200:
        raise ExtractionFailed('That Word file has almost no readable text.')
    return text


def chunk(text: str, size: int = MAX_CHARS_PER_CHUNK) -> list[str]:
    """Split on page breaks where we can, on paragraphs where we cannot."""
    blocks = re.split(r'(?=<<<PAGE \d+>>>)', text) if '<<<PAGE' in text \
        else text.split('\n\n')
    out: list[str] = []
    buf = ''
    for b in blocks:
        if len(buf) + len(b) > size and buf:
            out.append(buf)
            buf = b
        else:
            buf += ('\n\n' if buf else '') + b
    if buf.strip():
        out.append(buf)
    return out


# ---------------------------------------------------------------------------
# Step 2 — ask the model
# ---------------------------------------------------------------------------

_SLIDES_SYSTEM = (
    "You write induction training for an insurance company in Botswana. You are "
    "given part of a real company document. Turn it into clear slides for a new "
    "employee who has never seen the document. Plain English, short sentences, "
    "no jargon. Every slide must cite the clause number it came from. Never "
    "invent a rule that is not in the text. Return JSON only."
)

_SLIDES_SHAPE = """
Return: {"slides": [
  {"title": "...", "bullets": ["...", "..."], "section_ref": "7.1", "seconds": 60}
]}
Rules: 3 to 6 slides for this extract. 2 to 5 bullets per slide. A bullet is one
sentence, under 22 words. section_ref must be a clause number that appears in
the extract. Do not include any person's name.
"""

_QUESTIONS_SYSTEM = (
    "You write multiple-choice exam questions that test whether an employee has "
    "actually read a company document. You are given part of that document. "
    "Every question must be answerable from the extract alone, and the correct "
    "answer must be the document's actual wording — never your own opinion. "
    "Wrong options must be plausible to someone who skim-read, not silly. "
    "Return JSON only."
)

_QUESTIONS_SHAPE = """
Return: {"questions": [
  {"stem": "...?", "options": ["A text", "B text", "C text", "D text"],
   "correct_index": 0, "explanation": "...", "section_ref": "7.1",
   "topic": "Leave",
   "source_quote": "the exact sentence from the extract that proves the answer"}
]}
Rules: exactly 4 options, all different, only ONE correct. correct_index is
0-3 and points at the correct option. source_quote MUST be copied WORD FOR WORD
from the extract above — do not paraphrase it, do not tidy it up. A question
whose source_quote is not found in the document is thrown away. section_ref must
be a clause number that appears in the extract. Do not use "All of the above" or
"None of the above". Do not include any person's name.
"""


def _ask(system: str, shape: str, extract: str, want: int, feature: str) -> dict:
    report = is_safe_for_ai(extract)
    prompt = (
        f'{shape}\n\nProduce about {want} items.\n\n'
        f'--- DOCUMENT EXTRACT ---\n{report.redacted_text}\n--- END EXTRACT ---'
    )
    raw = reasoning_complete(
        prompt, system_prompt=system, response_format='json_object',
        feature=feature, timeout=CALL_TIMEOUT, max_tokens=6000,
    )
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Step 3 — validate before anything is stored
# ---------------------------------------------------------------------------

_CLAUSE = re.compile(r'\b\d{1,2}(?:\.\d{1,2}){0,3}\b')


def _clauses_in(text: str) -> set[str]:
    return set(_CLAUSE.findall(text))


def _norm(s: str) -> str:
    """Collapse whitespace and case so a quote can be matched against the source
    without being defeated by a PDF's line breaks."""
    return re.sub(r'\s+', ' ', str(s or '')).strip().casefold()


def validate_question(q: dict, source_clauses: set[str],
                      source_norm: str = '') -> tuple[bool, str]:
    """Reject anything that would make a bad or unfair exam question.

    The strongest check here is the last one: the quoted sentence must actually
    appear in the document. That kills a wholly fabricated citation. It does NOT
    verify the quote supports the answer — see the module docstring. HR reading
    the draft before Publish is the real gate.

    Returns (ok, reason). The reason is shown to HR on the draft screen so they
    can see WHY the model's output shrank, rather than silently getting fewer
    questions than they expected.
    """
    stem = (q.get('stem') or '').strip()
    if len(stem) < 12:
        return False, 'stem too short'
    opts = q.get('options')
    if not isinstance(opts, list) or len(opts) != 4:
        return False, 'not exactly 4 options'
    cleaned = [str(o).strip() for o in opts]
    if any(len(o) < 1 for o in cleaned):
        return False, 'blank option'
    if len({o.lower() for o in cleaned}) != 4:
        return False, 'duplicate options'
    if any(o.lower().startswith(('all of the above', 'none of the above'))
           for o in cleaned):
        return False, 'all/none of the above'
    try:
        ci = int(q.get('correct_index'))
    except (TypeError, ValueError):
        return False, 'correct_index missing'
    if not 0 <= ci < 4:
        return False, 'correct_index out of range'
    ref = (q.get('section_ref') or '').strip()
    if ref and source_clauses and ref not in source_clauses:
        return False, f'clause {ref} is not in the document'
    # The anti-hallucination gate.
    if source_norm:
        quote = _norm(q.get('source_quote'))
        if len(quote) < 20:
            return False, 'no source quote'
        if quote not in source_norm:
            return False, 'source quote is not in the document'
    return True, ''


def validate_slide(s: dict, source_clauses: set[str]) -> tuple[bool, str]:
    title = (s.get('title') or '').strip()
    bullets = s.get('bullets')
    if len(title) < 3:
        return False, 'no title'
    if not isinstance(bullets, list) or not 1 <= len(bullets) <= 8:
        return False, 'bullet count out of range'
    ref = (s.get('section_ref') or '').strip()
    if ref and source_clauses and ref not in source_clauses:
        return False, f'clause {ref} is not in the document'
    # The model sometimes returns "sixty" or "1m30s" here. Coercing it at write
    # time raises ValueError AFTER the course row is saved, so check it now.
    if 'seconds' in s:
        try:
            secs = int(s['seconds'])
        except (TypeError, ValueError):
            return False, 'slide duration is not a number'
        if not 5 <= secs <= 600:
            return False, 'slide duration out of range'
    return True, ''


# ---------------------------------------------------------------------------
# Step 4 — the whole pipeline
# ---------------------------------------------------------------------------

def generate_course(*, data: bytes, filename: str, title: str = '',
                    created_by=None, questions_target: int = TARGET_BANK):
    """Read the upload and draft a whole course from it.

    Returns the created `TrainingCourse` (status DRAFT) plus a plain-English
    report of what the AI managed and what was thrown away. Raises
    `ExtractionFailed` or `GenerationFailed` with a message HR can act on.
    """
    from hris.training_models import TrainingCourse, TrainingQuestion, TrainingSlide

    text = extract_text(data, filename)
    parts = chunk(text)
    total_parts = len(parts)
    truncated = total_parts > MAX_CHUNKS
    parts = parts[:MAX_CHUNKS]
    source_clauses = _clauses_in(text)
    source_norm = _norm(text)

    slides_raw: list[dict] = []
    questions_raw: list[dict] = []
    engine_errors: list[str] = []

    per_chunk_q = max(4, -(-questions_target // max(1, len(parts))))

    import time
    started = time.monotonic()
    stopped_early = 0
    for i, part in enumerate(parts, start=1):
        if time.monotonic() - started > UPLOAD_DEADLINE_SECONDS:
            stopped_early = len(parts) - i + 1
            log.warning('training: upload of %s stopped after %s of %s sections '
                        '— deadline reached', filename, i - 1, len(parts))
            break
        try:
            got = _ask(_SLIDES_SYSTEM, _SLIDES_SHAPE, part, 4, 'training_slides')
            slides_raw.extend(got.get('slides') or [])
        except Exception as exc:                        # noqa: BLE001 - report, keep going
            engine_errors.append(f'slides chunk {i}: {exc}')
        try:
            got = _ask(_QUESTIONS_SYSTEM, _QUESTIONS_SHAPE, part, per_chunk_q,
                       'training_questions')
            questions_raw.extend(got.get('questions') or [])
        except Exception as exc:                        # noqa: BLE001
            engine_errors.append(f'questions chunk {i}: {exc}')

    good_slides, slide_rejects = [], []
    for s in slides_raw:
        ok, why = validate_slide(s, source_clauses)
        (good_slides if ok else slide_rejects).append(s if ok else (s, why))

    good_questions, q_rejects = [], []
    seen_stems: set[str] = set()
    for q in questions_raw:
        ok, why = validate_question(q, source_clauses, source_norm)
        if not ok:
            q_rejects.append(why)
            continue
        key = re.sub(r'\W+', ' ', (q.get('stem') or '')).strip().lower()
        if key in seen_stems:
            q_rejects.append('duplicate question')
            continue
        seen_stems.add(key)
        good_questions.append(q)

    if len(good_questions) < MIN_BANK:
        raise GenerationFailed(
            f'The AI only produced {len(good_questions)} usable questions from '
            f'"{filename}" (a course needs at least {MIN_BANK}). The document may '
            f'be too short, or mostly tables and pictures. '
            + ('First error: ' + engine_errors[0] if engine_errors else '')
        )

    course_title = title.strip() or filename.rsplit('.', 1)[0].replace('_', ' ').strip()
    slug = _unique_slug(course_title)

    with transaction.atomic():
        course = _write_course(course_title, slug, filename, text, good_slides,
                               good_questions, slide_rejects, q_rejects,
                               engine_errors, created_by, parts, total_parts,
                               truncated or bool(stopped_early), stopped_early)

    return course, {
        'slides_kept': len(good_slides),
        'slides_rejected': len(slide_rejects),
        'questions_kept': len(good_questions),
        'questions_rejected': len(q_rejects),
        'reject_reasons': _tally(q_rejects),
        'engine_errors': engine_errors[:5],
        'source_chars': len(text),
        'parts_total': total_parts,
        'parts_read': len(parts) - stopped_early,
        'truncated': truncated,
    }


def _write_course(course_title, slug, filename, text, good_slides, good_questions,
                  slide_rejects, q_rejects, engine_errors, created_by,
                  parts, total_parts, truncated, stopped_early=0):
    """All the DB writes for a generated course, in one transaction."""
    from hris.training_models import TrainingCourse, TrainingQuestion, TrainingSlide
    course = TrainingCourse.objects.create(
        title=course_title, slug=slug,
        summary=f'Generated from {filename}.',
        source=TrainingCourse.Source.UPLOADED,
        source_filename=filename, source_chars=len(text),
        ai_generated=True,
        ai_notes='\n'.join(
            [f'{len(good_slides)} slides and {len(good_questions)} questions kept.',
             f'{len(slide_rejects)} slides and {len(q_rejects)} questions rejected.',
             (f'⚠ Only the first {len(parts)} of {total_parts} sections of the '
              f'document were read — the rest was too long for one pass. Split '
              f'the file and upload the remainder as a second course.'
              if truncated else
              f'The whole document ({total_parts} section(s)) was read.')]
            + ([f'⚠ Stopped {stopped_early} section(s) early because the document '
                f'was taking too long to read.'] if stopped_early else [])
            + engine_errors[:5]),
        created_by=created_by,
        status=TrainingCourse.Status.DRAFT,
    )

    for n, s in enumerate(good_slides, start=1):
        TrainingSlide.objects.create(
            course=course, order=n, title=str(s.get('title'))[:200],
            body_md='\n'.join(f'- {b}' for b in (s.get('bullets') or [])),
            section_ref=str(s.get('section_ref') or '')[:40],
            est_seconds=min(300, max(20, int(s.get('seconds') or 60))),
        )

    for q in good_questions:
        TrainingQuestion.objects.create(
            course=course, stem=str(q['stem']).strip(),
            options=[str(o).strip() for o in q['options']],
            correct_index=int(q['correct_index']),
            explanation=(str(q.get('explanation') or '')[:1600]
                         + ('\n\nManual says: \u201c'
                            + str(q.get('source_quote'))[:300] + '\u201d'
                            if q.get('source_quote') else '')),
            section_ref=str(q.get('section_ref') or '')[:40],
            topic=str(q.get('topic') or '')[:60],
            ai_generated=True, is_active=True,
        )
    return course


def _tally(reasons: list[str]) -> dict:
    out: dict[str, int] = {}
    for r in reasons:
        out[r] = out.get(r, 0) + 1
    return out


def _unique_slug(title: str) -> str:
    from hris.training_models import TrainingCourse
    base = slugify(title)[:70] or 'course'
    slug, n = base, 1
    while TrainingCourse.objects.filter(slug=slug).exists():
        n += 1
        slug = f'{base}-{n}'[:80]
    return slug
