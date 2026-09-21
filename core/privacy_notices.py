"""
core/privacy_notices.py — standalone privacy notices, DPA Part VIII (audit S-3, H-2).

Separate, plain-language notices for STAFF and POLICYHOLDERS — distinct from the
HR access undertaking (core.privacy_notice), which was the audit's "bundled/mislabelled
as consent" problem. Full Part VIII content: controller, data, purpose + lawful basis,
recipients, retention, cross-border transfers, rights, and the right to complain.
CFO directive 2026-07-19. Confirm final wording with the DPO/legal before external use.
"""
CONTROLLER = "Alpha Direct Insurance Company (Pty) Ltd"
DPO_NAME = "Oratile Tlhomelang"
DPO_EMAIL = "otlhomelang@alphadirect.co.bw"
REGULATOR = "Information and Data Protection Commission (IDPC), Botswana"
NOTICES_VERSION = "2026-07-19"

_RIGHTS = f"""
<p><b>Your rights.</b> You may ask us to: see the personal data we hold about you;
correct it if wrong; delete it; restrict or object to how we use it; and receive a
copy in a portable format. To exercise any of these, contact our Data Protection
Officer, {DPO_NAME}, at <a href="mailto:{DPO_EMAIL}">{DPO_EMAIL}</a>. We respond within
the time the law allows.</p>
<p><b>Complaints.</b> If you are unhappy with how we handle your data, you may complain
to the {REGULATOR}.</p>
"""

STAFF_PRIVACY_NOTICE = {
    "audience": "staff",
    "title": "Staff Privacy Notice",
    "version": NOTICES_VERSION,
    "html": f"""
<p>This notice explains how {CONTROLLER} (the data controller) handles your personal
data as an employee. It is separate from the system access undertaking you accept when
you log in.</p>
<p><b>What we hold.</b> Your name, contact details, national ID, date of birth, bank
account (for pay), salary and tax details, leave and attendance, performance records,
and role/access data.</p>
<p><b>Why, and our lawful basis.</b> To run payroll and pay you, meet tax and labour-law
duties, manage leave/performance, and secure our systems — on the basis of your
<b>employment contract</b> and our <b>legal obligations</b> (tax, BURS, labour law).</p>
<p><b>Who sees it.</b> Authorised HR, Finance and your line management; our regulators,
BURS, and payroll/banking partners where required; and IT service providers under
contract.</p>
<p><b>Cross-border.</b> Some IT services process data outside Botswana (South Africa,
EU, USA). Where we use AI to help, personal details are tokenised (masked) before they
leave our systems.</p>
<p><b>How long.</b> We keep employee records only as long as needed for the purpose and
the law (generally up to 6 years after you leave, longer where a law requires it).</p>
{_RIGHTS}
""",
}

POLICYHOLDER_PRIVACY_NOTICE = {
    "audience": "policyholder",
    "title": "Policyholder Privacy Notice",
    "version": NOTICES_VERSION,
    "html": f"""
<p>This notice explains how {CONTROLLER} (the data controller) handles your personal
data as a customer/policyholder.</p>
<p><b>What we hold.</b> Your name and contact details, national ID/passport (KYC),
date of birth, address, policy and claims information, payment/bank details, and — for
motor cover — vehicle and, where you use our app, driving/telematics data.</p>
<p><b>Why, and our lawful basis.</b> To give you a quote, issue and administer your
policy, handle claims, verify identity (KYC) and prevent fraud, and meet our legal and
regulatory duties — on the basis of your <b>insurance contract</b> and our <b>legal
obligations</b>.</p>
<p><b>Who sees it.</b> Authorised staff; reinsurers, assessors, panel repairers and
medical providers for claims; payment partners; regulators; and IT service providers
under contract.</p>
<p><b>Cross-border.</b> Some processing happens outside Botswana (South Africa, EU,
USA). Where we use AI to help assess documents or claims, your personal details are
tokenised (masked) before anything leaves our systems.</p>
<p><b>How long.</b> We keep your data only as long as needed for your policy, claims,
and the law.</p>
{_RIGHTS}
""",
}

ALL_NOTICES = {"staff": STAFF_PRIVACY_NOTICE, "policyholder": POLICYHOLDER_PRIVACY_NOTICE}
