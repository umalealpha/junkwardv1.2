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


def _aol_html(ctx):
    draft = ctx.get("draft")
    insured = _esc(ctx.get("insured_name", ""))
    claim = _esc(ctx.get("claim_number", ""))
    policy = _esc(ctx.get("policy_number", ""))
    vehicle = _esc(ctx.get("vehicle", ""))
    date_loss = _esc(ctx.get("date_of_loss", ""))

    figures = ctx.get("figures") or {}
    lines = figures.get("lines") or []
    net_display = _esc(
        figures.get("net_display") or _format_amount(figures.get("net", "0"))
    )

    rows = ['<tr><th>Description</th><th class="amount">Amount</th></tr>']
    for line in lines:
        label = _esc(line.get("label", ""))
        amount = _esc(_format_amount(line.get("amount", "0")))
        rows.append(f'<tr><td>{label}</td><td class="amount">{amount}</td></tr>')
    table = f"<table>{''.join(rows)}</table>"

    return f"""<html>
<head><meta charset="utf-8"><title>Agreement of Loss</title>{_style_block()}</head>
<body>
  <h1>Agreement of Loss</h1>
  {_draft_banner_html(draft)}
  <p>Dear {insured},</p>
  <p>We are writing to you about claim {claim} under policy {policy} for {vehicle}. The date of loss was {date_loss}.</p>
  <p>The agreed figures are set out below.</p>
  {table}
  <p>The net settlement is <strong>{net_display}</strong>.</p>
  <p>If you accept this amount in full and final settlement, please sign below. Once we pay, ownership of the vehicle or salvage passes to Alpha Direct Insurance.</p>
  <p>Insured signature: ______________________</p>
  <p>Date: ______________________</p>
  <p>For Alpha Direct Insurance: ______________________</p>
</body>
</html>"""


def _repudiation_html(ctx):
    draft = ctx.get("draft")
    insured = _esc(ctx.get("insured_name", ""))
    claim = _esc(ctx.get("claim_number", ""))
    policy = _esc(ctx.get("policy_number", ""))

    reasons = ctx.get("reasons") or []
    list_items = "".join(f"<li>{_esc(reason)}</li>" for reason in reasons)

    return f"""<html>
<head><meta charset="utf-8"><title>Your claim {claim}</title>{_style_block()}</head>
<body>
  <h1>Your claim {claim}</h1>
  {_draft_banner_html(draft)}
  <p>Dear {insured},</p>
  <p>Thank you for giving us the information about your claim. We have looked at it carefully. We are unable to accept your claim {claim} under policy {policy}.</p>
  <p>Our reasons are:</p>
  <ul>{list_items}</ul>
  <p>This decision is under the terms and conditions of the policy.</p>
  <p>If you believe we have not considered something, please write to us within 30 days with that information.</p>
  <p>Yours sincerely,</p>
  <p>Alpha Direct Insurance</p>
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

    figures = ctx.get("figures") or {}
    lines = figures.get("lines") or []
    net_display = _xml_esc(
        figures.get("net_display") or _format_amount(figures.get("net", "0"))
    )

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

    story = [Paragraph("Agreement of Loss", title_style)]
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
    for line in lines:
        label = _xml_esc(line.get("label", ""))
        amount = _xml_esc(_format_amount(line.get("amount", "0")))
        data.append([Paragraph(label, body_style), Paragraph(amount, body_style)])
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

    story.append(Paragraph(f"The net settlement is <b>{net_display}</b>.", body_style))
    story.append(
        Paragraph(
            f"I, {insured}, accept the net amount of <b>{net_display}</b> in full and final settlement "
            f"of this claim. I understand that ownership of the vehicle or salvage passes to "
            f"Alpha Direct Insurance on payment.",
            body_style,
        )
    )
    story.append(Paragraph("Insured signature: ______________________", body_style))
    story.append(Paragraph("Date: ______________________", body_style))
    story.append(
        Paragraph("For Alpha Direct Insurance: ______________________", body_style)
    )
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

    story = [Paragraph(f"Your claim {claim}", title_style)]
    if draft:
        story.append(
            Paragraph("DRAFT — wording awaiting Claims Manager sign-off", draft_style)
        )

    story.append(Paragraph(f"Dear {insured},", body_style))
    story.append(
        Paragraph(
            f"Thank you for giving us the information about your claim. We have looked at it carefully. "
            f"We are unable to accept your claim {claim} under policy {policy}.",
            body_style,
        )
    )
    story.append(Paragraph("Our reasons are:", body_style))

    reasons = ctx.get("reasons") or []
    for reason in reasons:
        story.append(Paragraph(f"• {_xml_esc(reason)}", body_style))

    story.append(
        Paragraph(
            "This decision is under the terms and conditions of the policy.", body_style
        )
    )
    story.append(
        Paragraph(
            "If you believe we have not considered something, please write to us within 30 days "
            "with that information.",
            body_style,
        )
    )
    story.append(Paragraph("Yours sincerely,", body_style))
    story.append(Paragraph("Alpha Direct Insurance", body_style))
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
