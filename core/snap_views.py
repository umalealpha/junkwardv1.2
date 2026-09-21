"""core/snap_views.py — "Snap anything" (CFO 2026-09-03, Omni staff app).

One camera button on the phone. Photo an invoice / receipt / supplier quote /
anything → Omni says what it is and which flow to open already filled:

  POST /api/v1/snap/classify/   multipart `image` (jpeg/png/webp/heic, ≤ 5 MB)
    → { kind, confidence, hint, suggested: { route, fields } }

The photo is looked at ONCE by core.ai_assist.vision_complete (the Gemini vision
failover chain — the only vision engine Omni uses; DeepSeek cannot see images)
and is never stored. Nothing the model read is logged.

DPA rule: an identity document (passport, Omang, driver licence) is refused —
kind "id_document" comes back with a fixed sentence and NO extracted text, so a
person's ID number never enters Omni through this door.
"""
from __future__ import annotations

import base64
import json
import logging
from django.core.files.uploadhandler import MemoryFileUploadHandler
import re
from decimal import Decimal, InvalidOperation

from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

log = logging.getLogger(__name__)

MAX_BYTES = 5 * 1024 * 1024   # = FILE_UPLOAD_MAX_MEMORY_SIZE: the photo must stay in memory, never on disk
KINDS = ("invoice", "receipt", "quote", "id_document", "other")
ROUTE_FOR = {"invoice": "raise-payment", "receipt": "receipt", "quote": "raise-po"}
FIELDS = ("vendor", "total", "currency", "date", "reference")
_FIELD_MAX = 120

ID_HINT = ("That looks like an identity document. "
           "Omni does not accept these through Snap.")
OTHER_HINT = "Omni couldn't tell what this is. Pick the flow yourself."
UNAVAILABLE_HINT = "Omni couldn't look at that photo right now. Pick the flow yourself."

PROMPT = (
    "You are a document classifier for an insurance company's finance app. Look at "
    "the photo and answer with ONE compact JSON object and nothing else — no prose, "
    "no markdown fences. Schema: {\"kind\": one of \"invoice\" (a supplier's bill "
    "asking to be paid), \"receipt\" (proof something was ALREADY paid — till slip, "
    "card slip, 'paid' stamp), \"quote\" (a quotation, proforma or estimate offering "
    "a price before work is done), \"id_document\" (ANY identity document: passport, "
    "Omang, national ID, driver licence, permit, or any card carrying a person's "
    "photo and an ID number), \"other\" (anything else); \"confidence\": number 0-1; "
    "\"vendor\": the business name on the document or null; \"total\": the grand "
    "total as printed, digits and one decimal point only, or null; \"currency\": ISO "
    "code such as \"BWP\", \"ZAR\", \"USD\" or null; \"date\": the document date as "
    "YYYY-MM-DD or null; \"reference\": the invoice / receipt / quote number or "
    "null}. If kind is \"id_document\" set vendor, total, currency, date and "
    "reference to null — read NOTHING off an identity document. If torn between "
    "invoice and quote, choose \"quote\" when the words quotation, quote, proforma "
    "or estimate appear. Never invent a value you cannot see."
)

_JSON_BLOCK = re.compile(r"\{.*\}", re.S)


# ---------------------------------------------------------------------------
# Upload checks — magic bytes, not the filename the phone chose.
# ---------------------------------------------------------------------------
def sniff_image_mime(head: bytes) -> str | None:
    """jpeg / png / webp / heic from the first bytes, else None."""
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[4:8] == b"ftyp" and head[8:12] in (
            b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1", b"heif"):
        return "image/heic"
    return None


# ---------------------------------------------------------------------------
# Model output → a safe, small dict
# ---------------------------------------------------------------------------
# DPA guard #2 — content-based. The model's own "kind" label is not trusted to
# be the only thing standing between an Omang/passport number and the caller:
# a misframed or adversarial photo can be labelled "other" or "invoice" and its
# fields would then flow straight back. So every extracted value is checked
# for identity-document patterns and dropped on match; two or more such hits
# (or any Omang-shaped number) reclassify the whole result as id_document.
_ID_PATTERNS = (
    re.compile(r"\b\d{9}\b"),                        # Botswana Omang: 9 digits
    re.compile(r"\b[A-Z]{1,2}\d{6,9}\b"),             # passport-style
    re.compile(r"\b(omang|passport|identity|national id|driver'?s? licen[cs]e|permit no)\b", re.I),
)


def _looks_like_identity(value: str) -> bool:
    return any(p.search(value or "") for p in _ID_PATTERNS)


def parse_classification(text: str) -> dict:
    """Defensive parse of the model's reply: first {...} block, kind validated,
    confidence clamped, fields shortened. Garbage → kind 'other', confidence 0."""
    fallback = {"kind": "other", "confidence": 0.0}
    m = _JSON_BLOCK.search(text or "")
    if not m:
        return fallback
    try:
        data = json.loads(m.group(0))
    except ValueError:  # falls back to kind "other" — logged, text never logged
        log.info("snap classify: model reply was not valid JSON — treating as other")
        return fallback
    if not isinstance(data, dict):
        return fallback
    kind = str(data.get("kind") or "").strip().lower()
    if kind not in KINDS:
        kind = "other"
    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence") or 0)))
    except (TypeError, ValueError):  # a non-numeric confidence → 0, logged
        log.info("snap classify: confidence was not a number — treating as 0")
        confidence = 0.0
    out = {"kind": kind, "confidence": round(confidence, 2)}
    if kind == "id_document":
        return out          # the DPA rule: nothing read off an ID leaves this function
    scrubbed = 0
    omang_hit = False
    for f in FIELDS:
        v = data.get(f)
        if v in (None, "", "null"):
            continue
        sv = str(v).strip()[:_FIELD_MAX]
        if _looks_like_identity(sv):
            scrubbed += 1
            omang_hit = omang_hit or bool(_ID_PATTERNS[0].search(sv))
            log.info("snap classify: dropped an identity-looking %s field", f)
            continue
        out[f] = sv
    if omang_hit or scrubbed >= 2:
        # Whatever the model called it, this is an identity document. Nothing leaves.
        return {"kind": "id_document", "confidence": out["confidence"]}
    return out


def _fmt_total(total: str | None, currency: str | None) -> str:
    if not total:
        return ""
    try:
        amount = Decimal(str(total).replace(",", "").replace(" ", ""))
        text = f"{amount:,.2f}"
    except (InvalidOperation, ValueError):  # shown as printed, logged
        log.debug("snap classify: total was not a plain number — shown as read")
        text = str(total)
    return f"{text} {currency}".strip() if currency else text


def build_hint(c: dict) -> str:
    kind = c["kind"]
    if kind == "id_document":
        return ID_HINT
    if kind == "other":
        return OTHER_HINT
    noun = {"invoice": "a supplier invoice", "receipt": "a receipt",
            "quote": "a supplier quote"}[kind]
    bits = [f"Looks like {noun}"]
    if c.get("vendor"):
        bits.append(f"from {c['vendor']}")
    money = _fmt_total(c.get("total"), c.get("currency"))
    if money:
        bits.append(f"for {money}")
    return " ".join(bits) + "."


def build_response(c: dict) -> dict:
    kind = c["kind"]
    body = {"kind": kind, "confidence": c.get("confidence", 0.0), "hint": build_hint(c)}
    if kind == "id_document":
        body["suggested"] = {"route": None}
        return body
    fields = {f: c[f] for f in FIELDS if c.get(f)}
    body["suggested"] = {"route": ROUTE_FOR.get(kind), "fields": fields}
    return body


class SnapClassifyView(APIView):
    """POST multipart `image` → what it is + where to take it."""
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "snap"

    def post(self, request):
        # Refuse oversized bodies BEFORE the multipart parser buffers anything, and
        # force in-memory handling so the photo is never spooled to disk (DPA).
        try:
            declared = int(request.META.get("CONTENT_LENGTH") or 0)
        except (TypeError, ValueError):
            declared = 0
        if declared > MAX_BYTES + 64 * 1024:
            return Response({"detail": "That photo is too big (over 5 MB). Take it again at normal size."}, status=400)
        request._request.upload_handlers = [MemoryFileUploadHandler(request._request)]
        f = request.FILES.get("image")
        if f is None:
            return Response({"detail": 'Attach the photo as "image".'}, status=400)
        if f.size > MAX_BYTES:
            return Response({"detail": "That photo is over 5 MB. Take it again or "
                                       "pick a smaller one."}, status=400)
        head = f.read(16)
        mime = sniff_image_mime(head)
        if mime is None:
            return Response({"detail": "Only a JPEG, PNG, WebP or HEIC photo can be "
                                       "snapped."}, status=400)
        raw = head + f.read()
        if len(raw) > MAX_BYTES:
            return Response({"detail": "That photo is over 5 MB."}, status=400)
        data_url = f"data:{mime};base64," + base64.b64encode(raw).decode()
        del raw   # the photo lives only for this request

        from core.ai_assist import VisionUnavailable, vision_complete
        try:
            text = vision_complete(PROMPT, data_url, timeout=30)
        except VisionUnavailable as exc:  # logged + 503 below, never swallowed
            log.warning("snap classify: vision unavailable (%s)", exc)
            return Response({"kind": "other", "confidence": 0.0, "hint": UNAVAILABLE_HINT,
                             "suggested": {"route": None, "fields": {}},
                             "detail": UNAVAILABLE_HINT}, status=503)
        c = parse_classification(text)
        # Kind + confidence only — never the vendor / reference / amounts read.
        log.info("snap classify: user=%s kind=%s confidence=%s",
                 request.user.pk, c["kind"], c["confidence"])
        return Response(build_response(c))
