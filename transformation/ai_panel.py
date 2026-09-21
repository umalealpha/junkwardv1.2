"""The daily read on the board — two drafters and a judge.

CFO, 2026-09-20: "linking to our everyday activities and software development
like gemini and deepseek, and open ai judge for this".

How it works, and the two rules that matter:

  1. The AI writes WORDS ONLY. Every number on the board is computed by rule in
     pulse.py before this module is called, and no model output is ever parsed
     back into a figure. If all three models are down the board is unchanged —
     it simply has no paragraph that morning. (Omni must keep working when the
     AI box dies.)

  2. Nothing personal leaves. The panel is given department names, percentages,
     counts and Pula totals. It is never given a person's name, e-mail, salary,
     a customer, a policy or a claim. The "who is not pushing automation" list
     is computed locally and stays local — naming a colleague to an outside
     model is exactly what policy AD-POL-AI-GOV-001 forbids.

Drafters: DeepSeek and Gemini, through the house `reasoning_complete` cascade
(local Ollama first, then DeepSeek, then Gemini). Judge: OpenAI when a key is
configured, otherwise the house cascade acts as the judge and says so.

Note on the cascade: `core.ai_assist.reasoning_complete` has further fallbacks
beyond Gemini. This module does not add or configure any provider of its own —
whatever the house cascade is set to is what runs, and the facts pack is built
to be safe for ALL of them.
"""
from __future__ import annotations

import json
import logging
import os

log = logging.getLogger(__name__)

# Long enough for a board paragraph, short enough that the reasoning tokens do
# not eat the whole budget (Gemini spends thinking tokens from the same pot).
MAX_TOKENS = 900

DRAFT_PROMPT = """You are writing the morning note on a company transformation board
for the CEO, CFO, COO and Chief Human Capital Officer of an insurance company in Botswana.

The programme goal: become the first AI insurance company in Botswana, with everything
delivered inside four months.

Here are today's figures. They are final — do not recalculate them, do not invent any
number that is not here, and do not name any person.

{facts}

Write at most 150 words, plain English, for readers who are not engineers. Cover:
what moved, what is stuck and who needs to unstick it (by role, not by name),
and the single most valuable thing to do today. No preamble, no bullet list, no headings.
"""

JUDGE_PROMPT = """You are the independent judge on a company transformation board.

Two drafts of this morning's note are below, written from the same figures.
The figures themselves are:

{facts}

Draft A:
{draft_a}

Draft B:
{draft_b}

Reply with STRICT JSON and nothing else:
{{"pick": "A" or "B", "why": "<one sentence>", "verdict": "ON TRACK" or "AT RISK" or "OFF TRACK",
"challenge": "<the one thing the drafts are glossing over, one sentence>"}}

Judge on: does it match the figures, does it name the real blocker, is it honest about
being behind? Prefer the draft that is uncomfortable and correct over the one that is
comfortable. Never invent a figure.
"""


def _safe_facts(board: dict) -> str:
    """Aggregates only. No names, no e-mails, no customers, no salaries."""
    clock = board.get('clock', {})
    cost = board.get('staff_cost', {})
    delay = board.get('cost_of_delay', {})
    workforce = board.get('workforce', {})

    # A per-department money figure in a department of one or two people IS
    # that person's salary. Those rows carry the name of the department only —
    # never the amount — so no individual's pay can be inferred outside.
    SMALL_DEPARTMENT = 3
    departments = [{
        'department': d['department'],
        'automation_score': d['automation_score'],
        'adoption_percent': d['adoption_percent'],
        'attendance_percent': d.get('attendance_percent'),
        'unexplained_days': d.get('unexplained_days'),
        'monthly_cost_of_not_using': (d['monthly_cost_of_not_using']
                                      if d.get('headcount_now', 0) >= SMALL_DEPARTMENT
                                      else None),
        'salary_at_risk': (d.get('salary_at_risk')
                           if d.get('headcount_now', 0) >= SMALL_DEPARTMENT
                           else None),
        'too_small_to_report': d.get('headcount_now', 0) < SMALL_DEPARTMENT,
    } for d in board.get('departments', [])]

    blocked = [{
        # The code, never the title: the title is free text the CFO edits and
        # could name a person or a vendor.
        'step': b.get('code'),
        'waiting_on_role': 'external vendor' if b['is_vendor'] else 'internal owner',
        'days': b['days'],
        'annual_value_bwp': b['annual_saving_bwp'],
    } for b in board.get('blocked', [])]

    return json.dumps({
        'days_elapsed': clock.get('days_elapsed'),
        'days_remaining': clock.get('days_remaining'),
        'time_used_percent': board.get('time_percent'),
        'work_done_percent': board.get('overall_percent'),
        # Enumerated, not passed through. A key added to the month roll-up
        # later would otherwise ride out to three external providers unread.
        'months': [{'month': m.get('month'), 'count': m.get('count'),
                    'done': m.get('done'), 'percent': m.get('percent')}
                   for m in (board.get('months') or [])],
        'staff_cost_now_bwp': cost.get('now'),
        'staff_cost_target_bwp': cost.get('target'),
        'headcount_now': cost.get('headcount_now'),
        'headcount_target': cost.get('headcount_target'),
        'unused_automation_cost_per_month_bwp': delay.get('monthly'),
        'salary_at_risk_per_month_bwp': workforce.get('salary_at_risk_month'),
        'steps_behind': len(board.get('behind', [])),
        'departments': departments,
        'blocked': blocked,
    }, indent=1, default=str)


def _draft(facts: str, flavour: str) -> tuple[str, str]:
    """One draft through the house cascade. Returns (text, which model)."""
    from core.ai_assist import reasoning_complete

    prompt = DRAFT_PROMPT.format(facts=facts)
    if flavour == 'b':
        prompt += ('\nWrite as a sceptical operator: lead with what is going wrong '
                   'and what it is costing.')
    else:
        prompt += ('\nWrite as a steady programme director: lead with what moved '
                   'and what happens next.')
    try:
        text = reasoning_complete(prompt, max_tokens=MAX_TOKENS)
    except Exception as exc:
        log.warning('transformation: draft %s failed: %s', flavour, exc)
        return '', ''
    return (text or '').strip(), 'reasoning_complete (Ollama → DeepSeek → Gemini)'


def _judge(facts: str, draft_a: str, draft_b: str) -> tuple[dict, str]:
    """OpenAI judges when a key is set; otherwise the house cascade judges."""
    prompt = JUDGE_PROMPT.format(facts=facts, draft_a=draft_a or '(none)',
                                 draft_b=draft_b or '(none)')

    raw, source = '', ''
    # Resolve the key the way the rest of Omni does — the Secrets Vault first,
    # then settings. Reading os.environ directly reported "no OpenAI key
    # configured" on a box where the key was present the whole time.
    try:
        from core.ai_assist import get_llm_key
        openai_key = get_llm_key('OPENAI_API_KEY')
    except Exception:
        openai_key = os.environ.get('OPENAI_API_KEY', '')

    if openai_key:
        try:
            from core.ai_assist import openai_complete  # optional, may not exist
            raw = openai_complete(prompt, max_tokens=400)
            source = 'OpenAI'
        except Exception as exc:
            log.info('transformation: OpenAI judge unavailable (%s)', exc)

    if not raw:
        try:
            from core.ai_assist import reasoning_complete
            raw = reasoning_complete(prompt, max_tokens=400)
            source = 'house cascade (no OpenAI key configured)'
        except Exception as exc:
            log.warning('transformation: judge failed: %s', exc)
            return {}, ''

    text = (raw or '').strip()
    # Models like to wrap JSON in a code fence. Take the first object.
    start, end = text.find('{'), text.rfind('}')
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1]), source
        except Exception:
            pass
    return {'verdict': '', 'why': text[:400], 'pick': '', 'challenge': ''}, source


def daily_read(board: dict) -> dict:
    """Two drafts, one judge, one paragraph. Never raises."""
    facts = _safe_facts(board)

    draft_a, source_a = _draft(facts, 'a')
    draft_b, source_b = _draft(facts, 'b')

    if not draft_a and not draft_b:
        return {
            'narrative': '',
            'narrative_source': '',
            'judge_verdict': '',
            'judge_source': '',
            'note': 'The AI panel was unreachable this morning. Every figure on this '
                    'board is computed by rule and is unaffected.',
        }

    verdict, judge_source = _judge(facts, draft_a, draft_b)
    picked = draft_b if (verdict.get('pick') or '').upper() == 'B' and draft_b else draft_a
    picked = picked or draft_b or draft_a
    source = source_b if picked is draft_b else source_a

    return {
        'narrative': picked,
        'narrative_source': source,
        'judge_verdict': json.dumps(verdict),
        'judge_source': judge_source,
        'drafts': {'a': draft_a, 'b': draft_b},
        'note': '',
    }
