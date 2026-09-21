"""Flag claims that need a claims manager's review before repudiation is drafted."""

import datetime as _dt
import re

_NEGATION_WORDS = frozenset({"no", "not", "never", "without", "nil"})
# Police forms read "Alcohol: No" / "alcohol test negative" — a negative that FOLLOWS.
_AFTER_NEGATION = frozenset({"no", "none", "nil", "negative", "not", "n"})

_INTOXICATION_PHRASES = (
    "drunk",
    "intoxicated",
    "intoxication",
    "blood alcohol",
    "alcohol",
    "under the influence",
    "driving under the influence",
    "breathalyser test positive",
    "breathalyzer test positive",
)

_UNLICENSED_PHRASES = (
    "no driver s licence",
    "no drivers licence",
    "no valid driver s licence",
    "no valid drivers licence",
    "not licensed",
    "without a driver s licence",
    "without a drivers licence",
    "without a valid driver s licence",
    "unlicensed",
    "driving without a licence",
    "driving without a valid licence",
    "no licence",
)


def _parse_date(value):
    """Return a date for an ISO date string/date/datetime, or None if unclear."""
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        try:
            return _dt.date.fromisoformat(value)
        except (ValueError, TypeError):
            return None
    return None


def _normalise_text(text):
    """Lowercase and reduce text to words separated by single spaces."""
    if not isinstance(text, str):
        return ""
    text = text.lower()
    text = text.replace("’", " ")
    text = text.replace("'", " ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _has_negation(prefix_tokens):
    """Return True when a negation word is within 3 words before a match."""
    return any(token in _NEGATION_WORDS for token in prefix_tokens[-3:])


def _match_positive_phrase(text, phrases):
    """Return first non-negated phrase found in text, else None."""
    normalised = _normalise_text(text)
    if not normalised:
        return None

    for phrase in phrases:
        pattern = r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])"
        for match in re.finditer(pattern, normalised):
            prefix_tokens = normalised[: match.start()].split()
            after_tokens = normalised[match.end():].split()[:3]
            if _has_negation(prefix_tokens) or any(
                t in _AFTER_NEGATION for t in after_tokens
            ):
                continue
            return match.group(0)
    return None


def _match_in_any(texts, phrases):
    """First non-negated phrase found in ANY one text, each screened on its own
    so the next answer's "no" is never read as negating this text's words."""
    for t in texts:
        hit = _match_positive_phrase(t, phrases)
        if hit:
            return hit
    return None



def _is_blank_licence(value):
    """Return True for missing/placeholder licence answers."""
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    normalised = value.strip().lower()
    return normalised in {
        "",
        "n/a",
        "na",
        "none",
        "nil",
        "null",
        "not applicable",
        "unknown",
        "missing",
        "no licence",
        "no license",
        "unlicensed",
    }


def _is_no(value):
    """Return True for common negative answer strings."""
    if value is None:
        return False
    if isinstance(value, bool):
        return value is False
    return str(value).strip().lower() in {"no", "n", "false", "0"}


def _append_flag(flags, code, label, detail):
    """Append a flag once only."""
    if any(flag.get("code") == code for flag in flags):
        return
    flags.append({"code": code, "label": label, "detail": detail})


def screen(facts):
    """Return deterministic policy-breach flags for a claim.

    The function never declines a claim; it only surfaces issues for a
    claims manager to review.
    """
    if not isinstance(facts, dict):
        facts = {}

    answers = facts.get("answers")
    if not isinstance(answers, dict):
        answers = {}

    date_of_loss = _parse_date(facts.get("date_of_loss"))
    reported_on = _parse_date(facts.get("reported_on"))
    policy_inception = _parse_date(facts.get("policy_inception"))
    policy_expiry = _parse_date(facts.get("policy_expiry"))

    documents_text = facts.get("documents_text") or ""
    texts = [documents_text] + [str(v) for v in answers.values() if v is not None]

    flags = []

    if date_of_loss and policy_inception and date_of_loss < policy_inception:
        _append_flag(
            flags,
            "loss_before_cover",
            "The loss happened before the policy started.",
            f"Date of loss {date_of_loss.isoformat()} is before policy inception {policy_inception.isoformat()}.",
        )

    if date_of_loss and policy_expiry and date_of_loss > policy_expiry:
        _append_flag(
            flags,
            "loss_after_expiry",
            "The loss happened after the policy expired.",
            f"Date of loss {date_of_loss.isoformat()} is after policy expiry {policy_expiry.isoformat()}.",
        )

    intoxication_phrase = _match_in_any(texts, _INTOXICATION_PHRASES)
    if intoxication_phrase:
        _append_flag(
            flags,
            "intoxication",
            "The claim documents or customer answers mention alcohol or intoxication.",
            f"Matched text: {intoxication_phrase!r}.",
        )

    licence_blank = "driver_licence" in answers and _is_blank_licence(
        answers.get("driver_licence")
    )
    unlicensed_phrase = _match_in_any(texts, _UNLICENSED_PHRASES)

    if licence_blank:
        _append_flag(
            flags,
            "unlicensed_driver",
            "The driver may not have held a valid licence.",
            f"Customer's driver licence answer was {answers.get('driver_licence')!r}.",
        )
    elif unlicensed_phrase:
        _append_flag(
            flags,
            "unlicensed_driver",
            "The driver may not have held a valid licence.",
            f"Matched text: {unlicensed_phrase!r}.",
        )

    if "driver_permission" in answers and _is_no(answers.get("driver_permission")):
        _append_flag(
            flags,
            "unauthorised_driver",
            "The driver may not have had permission to use the insured vehicle.",
            f"Customer's driver permission answer was {answers.get('driver_permission')!r}.",
        )

    if date_of_loss and reported_on and (reported_on - date_of_loss).days > 30:
        days_late = (reported_on - date_of_loss).days
        _append_flag(
            flags,
            "late_report",
            "The claim was reported more than 30 days after the loss.",
            f"Date of loss {date_of_loss.isoformat()} was reported on {reported_on.isoformat()} ({days_late} days later).",
        )

    return flags
