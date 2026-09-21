# Per-company payroll setup

This is the prep work for the multi-entity payroll rollout. Each subsidiary
(VCM, QIH, UNI, ADRG, RSA, ADIIC, ADIH, ADIL, ADSA) keeps a separate payroll
register, gated by its own password so the HR Manager's master HRIS credential
doesn't accidentally expose senior-entity payroll.

## What's already in place

| Piece | Where |
|---|---|
| `Company` records for all 10 entities (ADIC + 9 group) | `ops/seeds/seed_group_companies.py` |
| `Company.payroll_password_hash` field (PBKDF2 hashed) | `core/models.py` |
| `Company.set_payroll_password(raw)` / `.check_payroll_password(raw)` | `core/models.py` |
| Migration `core/0005_company_payroll_password_hash` | applied |
| Management command `set_payroll_password <code>` | `core/management/commands/set_payroll_password.py` |
| `Employee.company` FK and `Payslip.company` FK | already in `payroll/models.py` from the original deploy — payroll data is multi-tenant by construction |

## Setting a password for each entity

```bash
# Interactive (recommended — password isn't echoed and stays out of shell history)
python manage.py set_payroll_password VCM
python manage.py set_payroll_password QIH
python manage.py set_payroll_password ADIH
python manage.py set_payroll_password UNI
python manage.py set_payroll_password ADRG
python manage.py set_payroll_password RSA
python manage.py set_payroll_password ADIIC
python manage.py set_payroll_password ADIL
python manage.py set_payroll_password ADSA
python manage.py set_payroll_password ADIC

# Or non-interactively (don't use this on a shared terminal)
python manage.py set_payroll_password VCM --password "ChooseSomethingStrong123"

# Remove the gate later
python manage.py set_payroll_password VCM --clear
```

Each password is stored as a PBKDF2 hash. Plaintext is not retained anywhere.
Each company can have a different password.

## Payroll data format the CFO will provide tomorrow

For each entity, an Excel sheet matching the existing `payroll.Employee` and
`payroll.Payslip` shapes. Expected columns at minimum:

**Employees sheet:**
- `Employee Number` (unique within company)
- `Full Name`
- `Department`
- `Job Title`
- `Email`
- `Hire Date`
- `National ID (Omang)` — stored locally, never returned by HR APIs
- `Bank Name`, `Bank Account No.`, `Bank Branch`
- `Status` (active / on_leave / suspended / terminated)
- `Company Code` (must match one of: ADIC, ADIIC, ADIH, ADIL, ADSA, ADRG, RSA, UNI, QIH, VCM)

**Payslips sheet (per period):**
- `Employee Number`
- `Period` (e.g. 2025-06)
- `Component` — earnings, deductions, contributions, tax (must match `PayslipComponent.name`)
- `Amount`

If the CFO supplies the data in a different shape (e.g. one row per employee
with columns for each pay component), the importer will adapt.

## Verifying the gate works in the API layer

Once a password is set, any payroll endpoint scoped to that company should
require the password to be passed in (header, body, or session) and call
`Company.check_payroll_password(raw)` before returning data. Pattern:

```python
# views.py
def payroll_for_company(request, code):
    company = get_object_or_404(Company, code=code)
    if company.has_payroll_password():
        provided = request.headers.get('X-Payroll-Password') or request.data.get('payroll_password')
        if not company.check_payroll_password(provided or ''):
            return Response({'detail': 'Payroll password required.'}, status=401)
    # ... return data for company.id ...
```

The Telegram bot and the HRIS web app should each prompt for the company's
payroll password before showing per-company payroll views, and remember it
for the session.
