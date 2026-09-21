"""Two models judge the CEO/CFO brief, then settle what they disagree on.

WHY THIS EXISTS (CFO, 16-Sep-2026). The briefs were triaged by `deepseek-chat`
through `reasoning_complete`, whose only quality test is `_answer_inadequate`:
blank, or unparseable JSON. A confidently WRONG answer is neither, so it sails
through untouched. That is how the CFO's own competitor analysis reached the CEO
filed under CUSTOMER COMPLAINTS with the claims manager told to "call the
customer today" — perfectly formed JSON, entirely wrong.

His instruction: "we have OpenAI api key and Gemini 2.5 flash include them both,
they can discuss among each other and finish the task, they make the decision."

So: both models read the same anonymised inbox independently. Where they agree,
that is the answer. Where they disagree, each is shown the other's verdict for
just those items and asked to settle. Anything still disputed is INCLUDED — for
a CEO brief, showing something that did not need showing costs a few seconds;
hiding a real matter costs a great deal more. Every disagreement is reported in
the note so the split is visible rather than averaged away.

Cost: 2 calls on a clean run, 4 when they disagree. Once a day, twice if you
count the CFO brief.

NEVER let the brief go dark: if both models fail, the caller falls back to the
old single-model path. A brief that does not arrive is worse than a cheap one.
"""
from __future__ import annotations

import json
import logging
import re

log = logging.getLogger(__name__)

#: Ordered worst-first, so `min` picks the more serious of two opinions.
_SEVERITY = ("Critical", "High", "Watch")

_CATEGORIES = ("complaint", "regulatory", "legal", "claim", "incident",
               "strategy", "other")

_SETTLE_PREFIX = (
    "You and another analyst triaged the same emails for a CEO's daily brief "
    "and disagreed on the items below. For each one you are given BOTH "
    "verdicts. Re-read the email text and decide. Change your mind if the "
    "other analyst is right; hold your position if you are. Do not split the "
    "difference. Reply with the SAME strict JSON array shape as before, one "
    "object per index i, covering ONLY these indexes."
)


def _strip_fence(raw: str) -> str:
    raw = re.sub(r"^```(?:json)?\s*", "", (raw or "").strip())
    return re.sub(r"\s*```$", "", raw)


def _parse(raw):
    """The models' JSON, keyed by index. None if it cannot be read."""
    try:
        d = json.loads(_strip_fence(raw))
    except (ValueError, TypeError):
        return None
    if isinstance(d, dict):
        for key in ("items", "result", "matters", "verdicts"):
            if isinstance(d.get(key), list):
                d = d[key]
                break
        else:
            # OpenAI JSON mode REQUIRES a top-level object, so a model asked
            # for an array has to invent a wrapper key - "results", "emails",
            # anything. Guessing names is a losing game: an unrecognised one
            # reads as "unreadable json" and quietly drops that engine from
            # the panel. If the object holds exactly ONE list, that is it.
            lists = [v for v in d.values() if isinstance(v, list)]
            if len(lists) == 1:
                d = lists[0]
            elif not lists and ("i" in d or "significant" in d):
                # A BARE verdict object. Asked to re-argue a handful of items,
                # gpt-5 answers with ONE object rather than an array of one -
                # measured on prod 16-Sep-2026, round 2, and thrown away as
                # "unreadable json" while being a perfectly good answer. Wrap it.
                d = [d]
    if not isinstance(d, list):
        return None
    out = {}
    for n, v in enumerate(d):
        if isinstance(v, dict):
            try:
                out[int(v.get("i", n))] = v
            except (TypeError, ValueError):
                out[n] = v
    return out


def _norm_sev(s):
    """Model output is not an enum. "critical"/"HIGH"/None all arrive."""
    s = str(s or "").strip().capitalize()
    return s if s in _SEVERITY else "Watch"


def _worse(a, b):
    """The more serious of two severities, normalised.

    Returning an off-enum string here is not cosmetic: ceo_engine looks
    severity up in a dict with NO default, so one stray "critical" meeting a
    same-customer group raises KeyError in a top-level script - and the CEO
    gets no brief at all (Fable, 16-Sep-2026).
    """
    order = {s: i for i, s in enumerate(_SEVERITY)}
    a, b = _norm_sev(a), _norm_sev(b)
    return min([a, b], key=lambda s: order.get(s, len(_SEVERITY)))


#: A named party, a policy or claim number, an amount, a date. The system prompt
#: already demands these and bans vague lines; this is how we tell which model
#: actually obeyed.
_ANCHOR = re.compile(r"\b(?:[A-Z][a-z]{2,}|\d[\d,.]*)\b")


def _anchors(text):
    """How much of this sentence a CEO could act on."""
    return len(set(_ANCHOR.findall(text or "")))


def _richer(a, b):
    """The answer that actually says something.

    CONCRETENESS first, length only as a tie-break. Length alone rewarded the
    wordier model, and the wordier one is often the one hedging - "this appears
    to be a routine matter that may require attention in due course" is long and
    says nothing, while "Commercial property flood claim for Nexovant (Pty) Ltd"
    is shorter and tells the CEO who, what and which policy (CFO, 16-Sep-2026,
    on seeing both models' wording side by side).
    """
    a, b = (a or "").strip(), (b or "").strip()
    na, nb = _anchors(a), _anchors(b)
    if na != nb:
        return a if na > nb else b
    return a if len(a) >= len(b) else b


def _merge_one(a, b):
    """Two verdicts that agree on significance, merged into one."""
    out = dict(a)
    out["significant"] = bool(a.get("significant"))
    out["severity"] = _worse(a.get("severity", "Watch"), b.get("severity", "Watch"))
    out["matter"] = _richer(a.get("matter"), b.get("matter"))
    out["why"] = _richer(a.get("why"), b.get("why"))
    ca, cb = (a.get("category") or "other"), (b.get("category") or "other")
    # A named category beats "other": one model recognising the shape of the
    # matter is more informative than the other shrugging.
    out["category"] = ca if ca == cb else (ca if cb == "other" else
                                           (cb if ca == "other" else ca))
    return out


def _is_vacuous(verdicts):
    """A model that answered without doing the work.

    gemini-2.5-flash returned 30 structurally perfect verdicts on the real
    inbox with EVERY matter and why empty and nothing significant. Valid JSON,
    right shape, right count - and completely hollow. "Nothing is significant"
    reads exactly like a clean result, so a hollow voter would sit in the panel
    looking alive while contributing nothing (16-Sep-2026, measured on prod).
    """
    if not verdicts:
        return True
    return not any((v.get("matter") or "").strip() or
                   (v.get("why") or "").strip() or v.get("significant")
                   for v in verdicts.values())


def _call(fn, payload_text, system_prompt, max_tokens):
    try:
        raw = fn(payload_text, system_prompt=system_prompt,
                 response_format="json_object", max_tokens=max_tokens)
        parsed = _parse(raw)
        if parsed is None:
            # Say WHAT came back. "unreadable json" on its own cost three
            # round-trips to prod today to learn that gpt-5 had simply replied
            # with one object instead of an array. A failure that does not
            # describe itself is a failure somebody has to reproduce.
            head = " ".join((raw or "").split())[:90]
            return None, "unreadable json (%d chars, starts %r)" % (
                len(raw or ""), head)
        if _is_vacuous(parsed):
            return None, "answered but said nothing (%d blank verdicts)" % len(parsed)
        return parsed, "ok"
    except Exception as exc:  # noqa: BLE001 — any engine failure is just a miss
        return None, repr(exc)[:160]


#: Both models returned 12-14k characters on the real 30-email inbox, which is
#: ~3.5k tokens of ANSWER before any reasoning tokens. 4096 left no headroom.
_ANSWER_BUDGET = 8000


def panel_classify(payload_text, system_prompt, *, max_tokens=_ANSWER_BUDGET,
                   engines=None):
    """Returns (verdicts_by_index, note).

    `verdicts_by_index` is the same shape the single-model path produced, so the
    drivers need no other change. `note` is a one-line human summary of who
    answered and what they argued about — it goes in the run log so a split is
    visible rather than silently averaged.

    Returns (None, reason) if neither model could answer; the caller then falls
    back to the old path rather than sending no brief at all.
    """
    from core.ai_assist import is_safe_for_ai

    safe = is_safe_for_ai(payload_text)
    if not getattr(safe, "safe", False):
        return None, "pii-block"
    text = getattr(safe, "redacted_text", payload_text)

    if engines is None:
        from functools import partial

        from django.conf import settings

        from core.ai_assist import gemini_complete, openai_complete

        # Name the model HERE rather than through settings.OPENAI_MODEL. That
        # setting is unset on prod, so openai_complete falls back to its own
        # cheapest default (gpt-4o-mini) and the panel would be two cheap models
        # arguing. Setting it globally would also re-point every other OpenAI
        # caller in Omni, which nobody asked for. A new env var is worse again:
        # one set only in .env and not in docker-compose arrives EMPTY.
        model = getattr(settings, "BRIEF_OPENAI_MODEL", "") or "gpt-5"
        # gemini-2.5-FLASH returned 30 structurally perfect verdicts with every
        # matter and why blank and nothing significant, on the real 25k-char
        # inbox - it did not crash, it just did not do the work, and "nothing
        # is significant" is indistinguishable from a clean result. PRO did the
        # job on the same payload (12,085 chars of real content) and caught an
        # NBFIRA escalation gpt-5 read as an ordinary flood claim. Measured
        # 16-Sep-2026, not assumed. settings.GEMINI_MODEL stays on flash for
        # every other caller in Omni.
        gmodel = getattr(settings, "BRIEF_GEMINI_MODEL", "") or "gemini-2.5-pro"
        # TIMEOUTS, not the 30s default. gpt-5 reasons before it answers and
        # routinely takes 60-120s over ~30 emails. Every call would time out,
        # the note would read "Gemini alone", and the CEO would be triaged by
        # ONE cheap model again - the exact thing this change exists to end -
        # visible only in a cron log nobody reads (Fable, 16-Sep-2026).
        engines = (("OpenAI", partial(openai_complete, model=model, timeout=180)),
                   ("Gemini", partial(gemini_complete, model=gmodel, timeout=180)))

    # --- round 1: independent -------------------------------------------
    first = {}
    notes = []
    for name, fn in engines:
        verdicts, status = _call(fn, text, system_prompt, max_tokens)
        if verdicts is None:
            notes.append(f"{name} failed ({status})")
        else:
            first[name] = verdicts

    if not first:
        return None, "; ".join(notes) or "no engine answered"
    if len(first) == 1:
        only = next(iter(first))
        return first[only], f"{only} alone ({'; '.join(notes)})"

    (name_a, a), (name_b, b) = list(first.items())

    agreed, disputed = {}, []
    for i in sorted(set(a) | set(b)):
        va, vb = a.get(i), b.get(i)
        if va is None and vb is None:
            continue
        if va is None or vb is None:
            agreed[i] = va or vb
            continue
        if bool(va.get("significant")) == bool(vb.get("significant")):
            agreed[i] = _merge_one(va, vb)
        else:
            disputed.append(i)

    if not disputed:
        return agreed, f"{name_a} + {name_b} agreed on all {len(agreed)}"

    # --- round 2: each sees the other's verdict on the disputed items ----
    argument = json.dumps(
        [{"i": i,
          f"{name_a}_says": a.get(i),
          f"{name_b}_says": b.get(i)} for i in disputed],
        ensure_ascii=False)
    settle_prompt = _SETTLE_PREFIX + "\n\nORIGINAL INSTRUCTIONS:\n" + system_prompt

    second = {}
    for name, fn in engines:
        verdicts, status = _call(fn, argument, settle_prompt, max_tokens)
        if verdicts is not None:
            second[name] = verdicts
        else:
            notes.append(f"{name} could not settle ({status})")

    settled, still_split = 0, 0
    for i in disputed:
        va = (second.get(name_a) or {}).get(i, a.get(i))
        vb = (second.get(name_b) or {}).get(i, b.get(i))
        if va is None or vb is None:
            agreed[i] = va or vb
            continue
        if bool(va.get("significant")) == bool(vb.get("significant")):
            agreed[i] = _merge_one(va, vb)
            settled += 1
            continue
        # Still split. Show it. Missing a real matter is the expensive mistake;
        # an extra card is not. Take the serious reading, and say so.
        still_split += 1
        keep = va if va.get("significant") else vb
        other = vb if keep is va else va
        merged = dict(keep)
        merged["significant"] = True
        merged["severity"] = _worse(keep.get("severity", "High"),
                                    other.get("severity", "Watch"))
        # The SIGNIFICANT side supplies the words, never the longer text. The
        # dissenter writes "routine newsletter, no action needed" - longer than
        # "NBFIRA demands P3.2m by Friday" - so _richer would headline a card
        # marked Critical with the opposite conclusion (Fable, 16-Sep-2026).
        merged["matter"] = keep.get("matter") or other.get("matter")
        merged["why"] = keep.get("why") or other.get("why")
        merged["panel_split"] = True
        agreed[i] = merged

    note = (f"{name_a} + {name_b}: {len(agreed) - len(disputed)} agreed, "
            f"{len(disputed)} disputed -> {settled} settled, "
            f"{still_split} still split (shown anyway)")
    if notes:
        note += " | " + "; ".join(notes)
    return agreed, note
