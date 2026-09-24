"""Letters for Alpha Direct Insurance.

Provides HTML email bodies and PDF attachments for the Agreement of Loss and
repudiation letters. Pure Python 3.11, no Django imports.
"""

import io
import html
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle

from claims_lifecycle.amount_words import pula_in_words


_HOUSE_NAVY = "#0D1B2A"
_HOUSE_ORANGE = "#F4A623"


def _esc(value):
    """Escape a context value for HTML output."""
    return html.escape(str(value), quote=True)


def _xml_esc(value):
    """Escape text for reportlab Paragraph markup."""
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _format_amount(amount):
    """Return a decimal amount as a thousands-comma, 2dp string.

    Money is never converted through float. A negative amount keeps the sign
    before the formatted number, e.g. -5,000.00.
    """
    if amount is None:
        return "0.00"
    s = str(amount).strip().replace(",", "")
    if not s or s.lower() == "none":
        return "0.00"
    try:
        d = Decimal(s).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"not a money amount: {amount!r}") from exc
    sign = "-" if d < 0 else ""
    d_abs = abs(d)
    text = str(d_abs)
    if "." in text:
        whole, frac = text.split(".")
    else:
        whole, frac = text, "00"
    frac = frac[:2].ljust(2, "0")
    whole = f"{int(whole):,}"
    return f"{sign}{whole}.{frac}"


def _draft_banner_html(draft):
    if not draft:
        return ""
    return (
        '<div style="background:#F4A623;color:#0D1B2A;padding:10px;'
        'font-weight:bold;">DRAFT — wording awaiting Claims Manager sign-off</div>'
    )


def _style_block():
    return """
    <style>
      body { font-family: Arial, sans-serif; color: #0D1B2A; max-width: 720px; margin: 40px auto; padding: 20px; line-height: 1.5; }
      h1 { color: #0D1B2A; border-bottom: 3px solid #F4A623; padding-bottom: 6px; }
      table { border-collapse: collapse; width: 100%; margin: 16px 0; }
      th, td { border: 1px solid #0D1B2A; padding: 8px; text-align: left; }
      th { background: #0D1B2A; color: #ffffff; }
      td.amount, th.amount { text-align: right; }
      .draft { background: #F4A623; color: #0D1B2A; padding: 10px; font-weight: bold; margin: 16px 0; }
    </style>
    """


def _aol_money_block(ctx):
    """The three lines the CFO approved, and nothing else (B9).

    Total Claim (the sum insured), Less Excess worded exactly as the policy
    states it, Total Claim Payable. The excess is READ FROM THE POLICY and is
    never defaulted — a missing excess or a missing policy wording raises
    rather than quietly settling on zero, because that figure sets both the
    customer's payout and the 20% invoice to Veritas.

    Nothing else comes off until Finance confirms what else does.
    """
    figures = ctx.get("figures") or {}
    excess_wording = (ctx.get("excess_wording") or "").strip()

    for key in ("total_claim", "excess", "net"):
        if figures.get(key) in (None, ""):
            raise ValueError(
                f"Agreement of Loss: '{key}' is missing. The excess is read from "
                "the policy and is never defaulted."
            )
    if not excess_wording:
        raise ValueError(
            "Agreement of Loss: the excess must be worded exactly as the policy "
            "states it; no wording was supplied."
        )
    if Decimal(str(figures.get("other_deductions") or "0")) > 0:
        raise ValueError(
            "Agreement of Loss: something other than the excess has been deducted, "
            "so the three approved lines would not add up to the payable. What else "
            "comes off is for Finance to confirm."
        )

    net = Decimal(str(figures["net"]))
    return {
        "rows": [
            ("Total Claim", _format_amount(figures["total_claim"])),
            (excess_wording, "-" + _format_amount(figures["excess"]).lstrip("-")),
            ("Total Claim Payable", _format_amount(figures["net"])),
        ],
        "net_display": figures.get("net_display") or f"{net:,.2f}",
        "net_words": pula_in_words(net),
    }


def _aol_html(ctx):
    draft = ctx.get("draft")
    insured = _esc(ctx.get("insured_name", ""))
    claim = _esc(ctx.get("claim_number", ""))
    policy = _esc(ctx.get("policy_number", ""))
    vehicle = _esc(ctx.get("vehicle", ""))
    date_loss = _esc(ctx.get("date_of_loss", ""))

    block = _aol_money_block(ctx)
    net_display = _esc(block["net_display"])
    net_words = _esc(block["net_words"])

    rows = ['<tr><th>Description</th><th class="amount">Amount</th></tr>']
    for label, amount in block["rows"]:
        rows.append(
            f'<tr><td>{_esc(label)}</td><td class="amount">{_esc(amount)}</td></tr>'
        )
    table = f"<table>{''.join(rows)}</table>"

    return f"""<html>
<head><meta charset="utf-8"><title>Agreement of Loss / Tax Invoice</title>{_style_block()}</head>
<body>
  <h1>AGREEMENT OF LOSS / TAX INVOICE</h1>
  <p><strong>WITHOUT PREJUDICE</strong></p>
  <p><strong>PAYMENT IS SUBJECT TO SENIOR MANAGEMENT'S APPROVAL</strong></p>
  {_draft_banner_html(draft)}
  <p>Dear {insured},</p>
  <p>We are writing to you about claim {claim} under policy {policy} for {vehicle}. The date of loss was {date_loss}.</p>
  <p>The agreed figures are set out below.</p>
  {table}
  <p>The amount payable is <strong>{net_display}</strong> ({net_words}).</p>
  <p>If you accept this amount in full and final settlement, please sign below. Once we pay, ownership of the vehicle or salvage passes to Alpha Direct Insurance.</p>
  <p>Claimant signature: ______________________</p>
  <p>Date: ______________________</p>
  <p>Witness signature: ______________________</p>
  <p>Witness name: ______________________</p>
</body>
</html>"""


def _require_repudiation_fields(ctx):
    """A decline letter is a legal document. It must not render half-empty.

    The CFO's wording is fixed: the policy section named, THE CLAUSE QUOTED
    WORD FOR WORD WITH ITS ORIGINAL NUMBERING, and the Claims Manager signing
    on behalf of the company. A blank in any of those produces a letter that
    declines a claim while quoting nothing — so it refuses, the same way the
    Agreement of Loss refuses a defaulted excess.
    """
    required = {
        "policy_section": "the policy section this decision is taken under",
        "clause_number": "the clause number, as it is numbered in the policy",
        "clause_text": "the clause quoted word for word",
        "claims_manager_name": "the Claims Manager who signs it",
        "insured_name": "the claimant's name",
        "claim_number": "the claim number",
    }
    missing = [why for key, why in required.items() if not str(ctx.get(key) or "").strip()]
    if missing:
        raise ValueError(
            "Repudiation letter: cannot be produced without " + "; ".join(missing) + "."
        )
    if not (ctx.get("reasons") or []):
        raise ValueError("Repudiation letter: cannot be produced without a reason.")


def _repudiation_html(ctx):
    _require_repudiation_fields(ctx)
    draft = ctx.get("draft")
    insured = _esc(ctx.get("insured_name", ""))
    address = _esc(ctx.get("postal_address", ""))
    claim = _esc(ctx.get("claim_number", ""))
    policy = _esc(ctx.get("policy_number", ""))
    vehicle = _esc(ctx.get("vehicle", ""))
    date_loss = _esc(ctx.get("date_of_loss", ""))
    manager = _esc(ctx.get("claims_manager_name", ""))
    section = _esc(ctx.get("policy_section", ""))
    clause_no = _esc(ctx.get("clause_number", ""))
    clause_text = _esc(ctx.get("clause_text", ""))

    reasons = ctx.get("reasons") or []
    list_items = "".join(f"<li>{_esc(reason)}</li>" for reason in reasons)

    return f"""<html>
<head><meta charset="utf-8"><title>Repudiation — claim {claim}</title>{_style_block()}</head>
<body>
  <h1>WITHOUT PREJUDICE</h1>
  {_draft_banner_html(draft)}
  <p>{insured}<br>{address}</p>
  <p>Dear Sir or Madam,</p>
  <p><strong>RE: REPUDIATION — policy {policy}, {insured}, claim {claim}</strong></p>
  <p>We are writing about the loss reported under {vehicle} on {date_loss}.</p>
  <p>Having considered the claim documents and the report of the assessor we appointed, we are unable to accept this claim. Our reasons are:</p>
  <ul>{list_items}</ul>
  <p>This decision is taken under {section} of your policy, which reads:</p>
  <blockquote><p>{clause_no} {clause_text}</p></blockquote>
  <p>If you believe we have not considered something, you may appeal in writing to the Principal Officer or the Chief Executive Officer of Alpha Direct Insurance Company within six months of the date of this letter. If we hear nothing within six months, the file is closed.</p>
  <p>This decision is taken in good faith and on the information before us, and we will gladly look again at anything new you send us.</p>
  <p>Yours faithfully,</p>
  <p>{manager}<br>Claims Manager<br>for and on behalf of Alpha Direct Insurance Company</p>
</body>
</html>"""


def render_html(kind, ctx):
    """Return an HTML string for the given letter kind and context."""
    kind = kind.lower()
    if kind == "aol":
        return _aol_html(ctx)
    if kind == "repudiation":
        return _repudiation_html(ctx)
    raise ValueError(f"Unknown letter kind: {kind}")


def _aol_pdf_story(ctx, styles):
    draft = ctx.get("draft")
    insured = _xml_esc(ctx.get("insured_name", ""))
    claim = _xml_esc(ctx.get("claim_number", ""))
    policy = _xml_esc(ctx.get("policy_number", ""))
    vehicle = _xml_esc(ctx.get("vehicle", ""))
    date_loss = _xml_esc(ctx.get("date_of_loss", ""))

    block = _aol_money_block(ctx)
    net_display = _xml_esc(block["net_display"])
    net_words = _xml_esc(block["net_words"])

    title_style = styles["Title"]
    body_style = styles["BodyText"]
    header_style = ParagraphStyle(
        "header",
        parent=body_style,
        textColor=colors.white,
        backColor=colors.HexColor(_HOUSE_NAVY),
        borderPadding=4,
    )
    draft_style = ParagraphStyle(
        "draft",
        parent=body_style,
        backColor=colors.HexColor(_HOUSE_ORANGE),
        borderPadding=6,
    )

    story = [Paragraph("AGREEMENT OF LOSS / TAX INVOICE", title_style)]
    story.append(Paragraph("<b>WITHOUT PREJUDICE</b>", body_style))
    story.append(
        Paragraph("<b>PAYMENT IS SUBJECT TO SENIOR MANAGEMENT'S APPROVAL</b>", body_style)
    )
    if draft:
        story.append(
            Paragraph("DRAFT — wording awaiting Claims Manager sign-off", draft_style)
        )

    story.append(Paragraph(f"Dear {insured},", body_style))
    story.append(
        Paragraph(
            f"We are writing to you about claim {claim} under policy {policy} for {vehicle}. "
            f"The date of loss was {date_loss}.",
            body_style,
        )
    )
    story.append(Paragraph("The agreed figures are set out below.", body_style))

    data = [[Paragraph("Description", header_style), Paragraph("Amount", header_style)]]
    for label, amount in block["rows"]:
        data.append(
            [
                Paragraph(_xml_esc(label), body_style),
                Paragraph(_xml_esc(amount), body_style),
            ]
        )
    table = Table(data, colWidths=[300, 110])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(_HOUSE_NAVY)),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor(_HOUSE_NAVY)),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ]
        )
    )
    story.append(table)

    story.append(
        Paragraph(
            f"The amount payable is <b>{net_display}</b> ({net_words}).", body_style
        )
    )
    story.append(
        Paragraph(
            f"I, {insured}, accept the amount of <b>{net_display}</b> ({net_words}) in full and "
            f"final settlement of this claim. I understand that ownership of the vehicle or "
            f"salvage passes to Alpha Direct Insurance on payment.",
            body_style,
        )
    )
    story.append(Paragraph("Claimant signature: ______________________", body_style))
    story.append(Paragraph("Date: ______________________", body_style))
    story.append(Paragraph("Witness signature: ______________________", body_style))
    story.append(Paragraph("Witness name: ______________________", body_style))
    return story


def _repudiation_pdf_story(ctx, styles):
    draft = ctx.get("draft")
    insured = _xml_esc(ctx.get("insured_name", ""))
    claim = _xml_esc(ctx.get("claim_number", ""))
    policy = _xml_esc(ctx.get("policy_number", ""))

    title_style = styles["Title"]
    body_style = styles["BodyText"]
    draft_style = ParagraphStyle(
        "draft",
        parent=body_style,
        backColor=colors.HexColor(_HOUSE_ORANGE),
        borderPadding=6,
    )

    _require_repudiation_fields(ctx)
    address = _xml_esc(ctx.get("postal_address", ""))
    vehicle = _xml_esc(ctx.get("vehicle", ""))
    date_loss = _xml_esc(ctx.get("date_of_loss", ""))
    manager = _xml_esc(ctx.get("claims_manager_name", ""))
    section = _xml_esc(ctx.get("policy_section", ""))
    clause_no = _xml_esc(ctx.get("clause_number", ""))
    clause_text = _xml_esc(ctx.get("clause_text", ""))

    story = [Paragraph("WITHOUT PREJUDICE", title_style)]
    if draft:
        story.append(
            Paragraph("DRAFT — wording awaiting Claims Manager sign-off", draft_style)
        )

    story.append(Paragraph(f"{insured}<br/>{address}", body_style))
    story.append(Paragraph("Dear Sir or Madam,", body_style))
    story.append(
        Paragraph(
            f"<b>RE: REPUDIATION — policy {policy}, {insured}, claim {claim}</b>",
            body_style,
        )
    )
    story.append(
        Paragraph(
            f"We are writing about the loss reported under {vehicle} on {date_loss}.",
            body_style,
        )
    )
    story.append(
        Paragraph(
            "Having considered the claim documents and the report of the assessor we "
            "appointed, we are unable to accept this claim. Our reasons are:",
            body_style,
        )
    )

    reasons = ctx.get("reasons") or []
    for reason in reasons:
        story.append(Paragraph(f"• {_xml_esc(reason)}", body_style))

    story.append(
        Paragraph(
            f"This decision is taken under {section} of your policy, which reads:",
            body_style,
        )
    )
    story.append(Paragraph(f"<i>{clause_no} {clause_text}</i>", body_style))
    story.append(
        Paragraph(
            "If you believe we have not considered something, you may appeal in writing "
            "to the Principal Officer or the Chief Executive Officer of Alpha Direct "
            "Insurance Company within six months of the date of this letter. If we hear "
            "nothing within six months, the file is closed.",
            body_style,
        )
    )
    story.append(
        Paragraph(
            "This decision is taken in good faith and on the information before us, and "
            "we will gladly look again at anything new you send us.",
            body_style,
        )
    )
    story.append(Paragraph("Yours faithfully,", body_style))
    story.append(
        Paragraph(
            f"{manager}<br/>Claims Manager<br/>for and on behalf of "
            "Alpha Direct Insurance Company",
            body_style,
        )
    )
    return story


def render_pdf(kind, ctx):
    """Return a PDF (bytes) for the given letter kind and context."""
    kind = kind.lower()
    if kind not in ("aol", "repudiation"):
        raise ValueError(f"Unknown letter kind: {kind}")

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4)
    styles = getSampleStyleSheet()

    if kind == "aol":
        story = _aol_pdf_story(ctx, styles)
    else:
        story = _repudiation_pdf_story(ctx, styles)

    doc.build(story)
    return buf.getvalue()
