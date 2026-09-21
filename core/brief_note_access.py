"""Who may put a note into whose morning brief (CFO 2026-09-10).

Two spaces, two different rules:

* **CEO space** — the seven executives the CFO named. Nobody else, and
  deliberately **no superuser arm**: the QC robots run as superusers, and this
  space is confidential to those seven (same reasoning as
  `core.permissions.is_the_cfo` and `internal_audit.access.is_editor`).

* **CFO space** — the staff the CFO named, PLUS anyone employed by Alpha Direct
  Insurance, Unicoin, Veritas or Risksoftware Africa.

ENTITY CODES, NOT NAMES. The CFO wrote "ADIC, Unicoin, Veritas, Risk Software";
the database keys on `Company.code`, and the names vary between seeds
("Veritas Capital" vs "Veritas Capital Management", "Risk Software Africa" vs
"Risksoftware Africa"). The codes are ADIC / UNI / VCM / RSA.
⚠️ `RSA` here is **Risksoftware Africa**, NOT South Africa — Alpha Direct South
Africa is `ADSA` and is NOT in this list.

🔴 A user with NO company must never be treated as ADIC. M365 roster imports
land with a null company (see hris/api_views.py), so defaulting null to ADIC
would silently hand ~everyone with a login the right to write into the CFO's
morning brief. Null fails closed.
"""
from django.conf import settings

CEO_AUDIENCE = "ceo"
CFO_AUDIENCE = "cfo"
AUDIENCES = (CEO_AUDIENCE, CFO_AUDIENCE)

#: The CFO's seven, verbatim from his instruction: "visible to me, unami,
#: arjun, and Paul, kakale, bharath, wangu". Keys are email local-parts.
CEO_SPACE_LOCAL_PARTS = frozenset({
    "pganesharajah",     # Prathap Ganesharajah - CFO
    "ubutale",           # Unami Butale
    "arjuniyer",         # Arjun Parameswaran - COO
    "pbeka",             # Paul Beka
    "kbotana",           # Kakale Botana
    "bbalasubramanian",  # Bharath Balasubramanian
    "wmoses",            # Wangu Moses
})

#: Named by the CFO for his own space. The entity rule below already covers
#: most of them; they are listed so a change of entity never silently removes
#: someone he asked for by name.
CFO_SPACE_LOCAL_PARTS = frozenset({
    "ktshutlhedi",   # Kago Tshutlhedi
    "pkago",         # Pako Kago
    "tchimidza",     # Tlamelo Chimidza
    "kmokhendo",     # Keetile Mokhendo
    "btendani",      # Bontle Tendani
    "bbalasubramanian",
    "omogomotsi",    # Oprah Mogomotsi
    "wmoses",
    "pbeka",
    "ubutale",
    "kbotana",       # Kakale Botana
    "ceooffice",     # Modiri Katai - CFO 2026-09-10: BOTH of his accounts, so
    "ceooffice2",    #   he cannot lock himself out. The duplicate account is a
                     #   separate data problem, not fixed here.
    "arjuniyer",
    "pphesodi",      # Patience Phesodi
    "mmolefe",       # Motlatsi Molefe (@insurance.co.bw)
    "dikgopoleng",   # Dorothy Ikgopoleng   - CFO confirmed "Dorothy Gaolabale"
    "gmachobane",    # Gaolebale Machobane  -   meant BOTH of these people
    "pganesharajah",
})

#: Alpha Direct Insurance, Unicoin, Veritas Capital, Risksoftware Africa.
CFO_SPACE_ENTITY_CODES = frozenset({"ADIC", "UNI", "VCM", "RSA"})


def _identifiers(user) -> set:
    """Every identifier a user might present, so a roster entry matches whether
    SSO populated .email or .username, and whether it carried the full address
    or just the local-part."""
    ids = set()
    for raw in (getattr(user, "email", ""), getattr(user, "username", "")):
        v = (raw or "").strip().lower()
        if not v:
            continue
        ids.add(v)
        ids.add(v.split("@", 1)[0])
    return ids


def _roster(setting_name: str, default: frozenset) -> set:
    """The roster, overridable from settings so membership is a config change
    and never a redeploy."""
    extra = getattr(settings, setting_name, None) or ()
    keys = set(default)
    for e in extra:
        v = (e or "").strip().lower()
        if v:
            keys.add(v)
            keys.add(v.split("@", 1)[0])
    return keys


def _entity_code(user) -> str:
    """The entity this person actually belongs to, via their payroll record.
    Empty string when unknown - the caller must fail closed on that."""
    emp = getattr(user, "employee_record", None)
    company = getattr(emp, "company", None)
    return ((getattr(company, "code", "") or "").strip().upper())


def can_post_to_ceo(user) -> bool:
    """The seven named executives. No superuser arm - this space is theirs."""
    if not (user and getattr(user, "is_authenticated", False)):
        return False
    if not getattr(user, "is_active", False):
        return False
    roster = _roster("BRIEF_NOTE_CEO_EXTRA", CEO_SPACE_LOCAL_PARTS)
    return bool(_identifiers(user) & roster)


def can_post_to_cfo(user) -> bool:
    """The CFO's named list, or any employee of ADIC / UNI / VCM / RSA."""
    if not (user and getattr(user, "is_authenticated", False)):
        return False
    if not getattr(user, "is_active", False):
        return False
    if _identifiers(user) & _roster("BRIEF_NOTE_CFO_EXTRA", CFO_SPACE_LOCAL_PARTS):
        return True
    code = _entity_code(user)
    if not code:            # unknown entity -> closed, never assumed to be ADIC
        return False
    return code in CFO_SPACE_ENTITY_CODES


def can_post(user, audience: str) -> bool:
    if audience == CEO_AUDIENCE:
        return can_post_to_ceo(user)
    if audience == CFO_AUDIENCE:
        return can_post_to_cfo(user)
    return False


def can_read_space(user, audience: str) -> bool:
    """Who may READ a space.

    The CEO space is shared on purpose - the CFO asked for one space "visible
    to" all seven, so they see each other's notes and two people do not raise
    the same thing twice. Its readers are exactly its posters, plus the CEO
    whose brief it feeds.

    The CFO space is NOT shared: a junior's note to the CFO is between them, so
    a poster sees only their own note. Only the CFO (and the CEO) read the whole
    space.
    """
    if audience == CEO_AUDIENCE:
        return can_post_to_ceo(user) or bool(_identifiers(user) & {"aiyer"})
    if audience == CFO_AUDIENCE:
        return bool(_identifiers(user) & {"pganesharajah", "aiyer"})
    return False


def audiences_for(user) -> list:
    """The spaces this person may write to - drives what the dashboard shows."""
    return [a for a in AUDIENCES if can_post(user, a)]
