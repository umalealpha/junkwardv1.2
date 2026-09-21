"""budgets/spend_ai.py — DeepSeek reads the budget attached to a SpendRequest and
summarises it for the approver (CFO directive 2026-07-13).

Local doc-parse first (xlsx / pdf / image, all handled by core.doc_parse), then
DeepSeek turns the extracted text into a short factual summary + the grand total
+ flags. This is INTERNAL budget data (line items: venue, catering, sponsorship)
— non-PII commercial data the CFO has cleared for the DeepSeek helper. Never
raises: on any problem it just records an ai_status the dashboard can show.
"""
from __future__ import annotations

import json
import logging
from decimal import Decimal, InvalidOperation

log = logging.getLogger("spend-ai")

_SYSTEM = (
    "You are a finance analyst at an insurer. You are given the text of an "
    "event / spend budget a staff member attached to a pre-spend approval "
    "request. Summarise it factually for the approver — do NOT advise whether to "
    "approve. Return STRICT JSON with keys: "
    '"total" (number or null — the grand total cost in the document), '
    '"summary" (2-4 plain sentences: what the spend is for and the main line '
    'items), "flags" (array of short strings — anything the approver should '
    "note: no total shown, arithmetic that does not add up, unusually large "
    "items, bank charges, VAT, or stated assumptions)."
)


def analyze_spend_request(sr) -> None:
    """Fill sr.ai_* from the attached budget document. Never raises — always
    leaves sr.ai_status set. Caller saves the ai_* fields afterwards."""
    from core.ai_assist import deepseek_complete, DeepSeekUnavailable
    from core.doc_parse import parse as parse_doc

    if not sr.attachment:
        sr.ai_status = sr.AIStatus.SKIPPED
        sr.ai_summary = "No budget document attached."
        return

    try:
        sr.attachment.open("rb")
        data = sr.attachment.read()
        try:
            sr.attachment.close()
        except Exception:  # noqa: BLE001
            pass
        res = parse_doc(data, filename=(sr.attachment.name or ""))
        text = (getattr(res, "markdown", "") or getattr(res, "text", "") or "").strip()
        if not text:
            sr.ai_status = sr.AIStatus.SKIPPED
            sr.ai_summary = "Could not read the attached document."
            return

        prompt = (
            f"Spend request: {sr.get_request_type_display()} — {sr.title}. "
            f"Amount requested: BWP {sr.amount}. "
            f"Requester says this is within budget: {sr.within_budget}.\n\n"
            f"BUDGET DOCUMENT (extracted):\n{text[:12000]}"
        )
        raw = deepseek_complete(prompt, system_prompt=_SYSTEM,
                                response_format="json_object", max_tokens=700)
        obj = json.loads(raw)

        sr.ai_summary = str(obj.get("summary") or "")[:4000]
        flags = obj.get("flags") or []
        sr.ai_flags = (" • ".join(str(x) for x in flags) if isinstance(flags, list)
                       else str(flags))[:2000]
        tot = obj.get("total")
        try:
            sr.ai_extracted_total = (Decimal(str(tot))
                                     if tot not in (None, "", "null") else None)
        except (InvalidOperation, ValueError, TypeError):
            sr.ai_extracted_total = None
        sr.ai_status = sr.AIStatus.DONE

    except DeepSeekUnavailable:
        sr.ai_status = sr.AIStatus.SKIPPED
        sr.ai_summary = "AI analysis unavailable (DeepSeek not configured)."
    except Exception as e:  # noqa: BLE001
        log.warning("spend_ai failed for %s: %s", getattr(sr, "id", "?"), e)
        sr.ai_status = sr.AIStatus.ERROR
        sr.ai_summary = f"Could not analyse the budget: {e}"
