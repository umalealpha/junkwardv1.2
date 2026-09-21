# Runbook — admit Goitsemang Ngwako to the Records Register

**Raised:** Tlotlo Maswabi, 25-Aug-2026, via Report a System Bug (feature request
`8e17a425-ae7f-4473-9ce0-b9a861ce50e4`, page: records register).
> "Please add Goitsemang Ngwako to the Records Register and provide her with the necessary
> access... Goitsemang also works as a Records Officer and will need access to familiarise
> herself with the system and its processes, particularly to ensure continuity and effective
> management of records in my absence."

**Status: code written and tested, NOT deployed** (CFO holding deploys).

## Why this needed a code change and not just a click

Goitsemang's account exists and is active (`goitsemang.ngwako` / gngwako@alphadirect.co.bw),
but her job title is `operations`, which is not one of the nine titles that may open the
register.

There was no way to admit her without doing something wrong:

| Option | Why it was rejected |
|---|---|
| Change her title to `accountant` (Tlotlo's title) | In Omni a job title also drives **approval rights on payments and purchase orders**. Promoting a Records Officer so she can read a register would hand her financial authority nobody granted her. |
| Add `operations` to the register titles | Opens the register to **every** operational staff member. |

The register already supported an explicit, named, admin-revocable grant for *restricted
records* — but not for opening the register at all. That asymmetry was the actual defect. It
is now closed: `records.view_records_register` admits one named person (or a group), exactly
the way `records.view_restricted_records` already did.

## After the deploy — run this

```bash
cd /opt/alpha-finance && sudo docker compose --env-file /etc/alpha-finance/.env exec -T backend \
  python manage.py shell -c "
from django.contrib.auth.models import User, Permission
u = User.objects.get(username='goitsemang.ngwako')
u.user_permissions.add(Permission.objects.get(codename='view_records_register'))
u.user_permissions.add(Permission.objects.get(codename='view_restricted_records'))
u = User.objects.get(pk=u.pk)                      # drop the permission cache
print('register  :', u.has_perm('records.view_records_register'))
print('restricted:', u.has_perm('records.view_restricted_records'))
"
```

Expect both to print `True`.

**Why `view_restricted_records` as well:** Tlotlo holds it (granted 11-Aug-2026 with CFO
approval), and the request is for continuity **in her absence**. A stand-in who cannot see the
same files cannot stand in. If you would rather Goitsemang start with the register only, drop
the second line — she will see everything except records flagged as personal data.

## Then verify as her, not as an admin

```bash
~/.claude/skills/prat-skill/e2e/qc.sh "/records"
```

and confirm the register loads for her account.

## Then close the loop

Mark feature request `8e17a425-ae7f-4473-9ce0-b9a861ce50e4` done on the bug board — that
auto-emails Tlotlo.

## To revoke later

Remove the permission in the Django admin (Users → goitsemang.ngwako → user permissions). No
code change, no deploy.
