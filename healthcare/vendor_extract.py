"""healthcare/vendor_extract.py — DeepSeek field extraction for vendor intake.

Parses OCR'd / extracted text from CIPA documents (Certificate of Incorporation,
company extract) into structured form fields. Reuses omni's existing DeepSeek
client (core.ai_assist.deepseek_complete) — already prod-configured for the
health quick-quote.

PRIVACY (DPA No. 18 of 2024): only the OCR'd document TEXT is sent to DeepSeek
(cross-border sub-processor, recorded in the DPA register; CFO accepted the
transfer 2026-06-04). NEVER send banking details (those are manual-only and never
OCR'd). Every returned value is rendered for HUMAN CONFIRMATION in the UI — the
model never commits a field. Falls back to regex when DeepSeek is unavailable.
"""
from __future__ import annotations

import json
import logging
import re

from core.ai_assist import deepseek_complete, DeepSeekUnavailable

log = logging.getLogger(__name__)

# Fields DeepSeek may populate, by document type. Banking is deliberately absent.
_TARGETS = {
    "CoI": ["entity.companyName", "entity.registrationNumber", "entity.registrationDate"],
    "extract": ["entity.registeredAddress", "tax.tin", "tax.vatNumber",
                "directors"],
}

_SYSTEM = (
    "You are a field-extraction service for a Botswana insurance ERP onboarding a "
    "healthcare vendor. You are given OCR'd text from a CIPA document (Certificate "
    "of Incorporation or company extract). Extract ONLY the requested fields. "
    "NEVER fabricate a value: if a field is not clearly present, omit it. Botswana "
    "company registration numbers look like 'CO2023/45678'. "
    'Return STRICT JSON: {"fields":[{"path":string,"value":string,'
    '"confidence":number}]}. Allowed paths: entity.companyName, '
    "entity.registrationNumber, entity.registrationDate, entity.registeredAddress, "
    "tax.tin, tax.vatNumber. For directors, use path 'directors' with value as a "
    "JSON string array of full names. Never include banking, account, or payment "
    "data even if present in the text."
)


def extract_vendor_fields(text: str, doc_type: str = "CoI") -> dict:
    """Return {'fields': [{path, value, confidence}], 'deepseek_used': bool}."""
    text = (text or "").strip()
    if not text:
        return {"fields": [], "deepseek_used": False}
    if len(text) > 24000:
        text = text[:24000] + "\n…[truncated]"

    user_msg = (
        f"DOCUMENT TYPE: {doc_type}\n"
        f"REQUESTED PATHS: {', '.join(_TARGETS.get(doc_type, []))}\n"
        "DOCUMENT TEXT:\n---\n" + text + "\n---\n"
        "Return the JSON now. No prose."
    )
    try:
        raw = deepseek_complete(
            user_prompt=user_msg,
            system_prompt=_SYSTEM,
            response_format="json_object",
            timeout=45.0,
        )
        fields = _parse_fields_json(raw)
        return {"fields": fields, "deepseek_used": True}
    except DeepSeekUnavailable as e:
        log.warning("DeepSeek not configured, using regex fallback: %s", e)
        return {"fields": _regex_fallback(text, doc_type), "deepseek_used": False}
    except Exception as e:  # noqa: BLE001
        log.warning("DeepSeek extraction failed, regex fallback: %s", e)
        return {"fields": _regex_fallback(text, doc_type), "deepseek_used": False}


def _parse_fields_json(raw: str) -> list[dict]:
    if not raw:
        return []
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*", "", s).rstrip("`").strip()
    try:
        obj = json.loads(s)
    except Exception:  # noqa: BLE001
        m = re.search(r"\{.*\}", s, re.S)
        if not m:
            return []
        try:
            obj = json.loads(m.group(0))
        except Exception:  # noqa: BLE001
            return []
    fields = obj.get("fields") if isinstance(obj, dict) else None
    if not isinstance(fields, list):
        return []
    out = []
    for f in fields:
        if isinstance(f, dict) and f.get("path") and f.get("value") not in (None, ""):
            out.append({
                "path": str(f["path"]),
                "value": str(f["value"]),
                "confidence": float(f.get("confidence") or 0.0),
            })
    return out


# --- Regex fallback (DeepSeek down) ------------------------------------------

_RX_REG = re.compile(r"\b(CO\s?\d{4}\s?/?\s?\d{3,7})\b", re.I)
_RX_TIN = re.compile(r"\b(?:TIN|Tax(?:payer)?\s*(?:ID|No\.?|Number)?)\s*[:#]?\s*([A-Za-z]?\d{7,12})\b", re.I)
_RX_VAT = re.compile(r"\bVAT\s*(?:Reg(?:istration)?)?\s*(?:No\.?|Number)?\s*[:#]?\s*([Pp]?\d{8,12})\b", re.I)


def _after_label(text: str, labels: list[str]):
    for label in labels:
        m = re.search(rf"{label}\s*[:#-]?\s*(.+)", text, re.I)
        if m and m.group(1):
            return m.group(1).splitlines()[0].strip()
    return None


def _regex_fallback(text: str, doc_type: str) -> list[dict]:
    out = []

    def push(path, val):
        if val:
            out.append({"path": path, "value": str(val).strip(), "confidence": 0.5})

    if doc_type == "CoI":
        push("entity.companyName", _after_label(text, ["Company Name", "Name of Company", "Name"]))
        m = _RX_REG.search(text)
        push("entity.registrationNumber", m.group(1) if m else None)
        push("entity.registrationDate", _after_label(text, ["Date of Incorporation", "Incorporation Date"]))
    elif doc_type == "extract":
        push("entity.registeredAddress", _after_label(text, ["Registered Office", "Registered Address"]))
        mt = _RX_TIN.search(text)
        push("tax.tin", mt.group(1) if mt else None)
        mv = _RX_VAT.search(text)
        push("tax.vatNumber", mv.group(1) if mv else None)
    return out
