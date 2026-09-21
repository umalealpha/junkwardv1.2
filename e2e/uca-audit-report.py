# UserCompanyAccess audit — READ ONLY. No writes, no deletes.
# Output is PII-safe: user IDs + company CODES + title/dept/flags only.
# No usernames, emails, full_names, national_id, bank.
from collections import defaultdict
from django.contrib.auth import get_user_model
from core.models import UserCompanyAccess, Company, UserProfile
from payroll.models import Employee

User = get_user_model()

companies = list(Company.objects.all().order_by('code'))
code_by_id = {str(c.id): c.code for c in companies}
all_codes = sorted(code_by_id.values())
N_CO = len(companies)

print("=== UCA AUDIT (read-only) ===")
print(f"COMPANIES n={N_CO}: {','.join(all_codes)}")

total_users  = User.objects.count()
active_users = User.objects.filter(is_active=True).count()
total_uca    = UserCompanyAccess.objects.count()
view_uca     = UserCompanyAccess.objects.filter(can_view=True).count()
write_uca    = UserCompanyAccess.objects.filter(can_write=True).count()
print(f"USERS total={total_users} active={active_users}")
print(f"UCA rows total={total_uca} can_view={view_uca} can_write={write_uca}")

# grants per user
view_grants  = defaultdict(set)
write_grants = defaultdict(set)
for r in UserCompanyAccess.objects.values('user_id', 'company_id', 'can_view', 'can_write'):
    code = code_by_id.get(str(r['company_id']), '?')
    if r['can_view']:
        view_grants[r['user_id']].add(code)
    if r['can_write']:
        write_grants[r['user_id']].add(code)

# home entity via Employee.user (OneToOne, related_name employee_record)
home_by_user = {}
for e in Employee.objects.filter(user__isnull=False).select_related('company'):
    home_by_user[e.user_id] = (e.company.code if e.company_id else None)

prof_by_user = {p.user_id: p for p in UserProfile.objects.select_related('user').all()}

buckets = defaultdict(list)
title_counts = defaultdict(int)
dept_counts = defaultdict(int)
overbroad_rows = []   # the prune candidates
write_beyond = []     # can_write beyond home — most sensitive

for u in User.objects.all().order_by('id'):
    prof = prof_by_user.get(u.id)
    title = (prof.title if prof else '') or ''
    dept = ((prof.department or '') if prof else '') or ''
    is_admin = bool(prof.is_administrator) if prof else False
    unrestricted = bool(u.is_superuser) or is_admin or (title == 'cfo')
    gv = view_grants.get(u.id, set())
    gw = write_grants.get(u.id, set())
    nview = len(gv)
    has_home = u.id in home_by_user
    home = home_by_user.get(u.id)

    if unrestricted:
        b = 'A_unrestricted'
    elif not has_home:
        b = 'D_no_home'
    elif nview == 0:
        b = 'E_no_grants'
    elif home and gv == {home}:
        b = 'C_already_scoped'
    else:
        b = 'B_overbroad'
    buckets[b].append(u.id)

    if b == 'B_overbroad':
        beyond = sorted(gv - ({home} if home else set()))
        title_counts[title or '(none)'] += 1
        dept_counts[dept or '(none)'] += 1
        overbroad_rows.append((u.id, title, dept, home, nview, len(gw)))
        if home and (gw - {home}):
            write_beyond.append((u.id, sorted(gw - {home})))

print("\n=== BUCKET COUNTS ===")
for b in ['A_unrestricted', 'B_overbroad', 'C_already_scoped', 'D_no_home', 'E_no_grants']:
    print(f"{b}: {len(buckets[b])}")

# headline: non-unrestricted users holding ALL companies
allN = [uid for uid, g in view_grants.items()
        if len(g) == N_CO and uid not in set(buckets['A_unrestricted'])]
print(f"\nNON-BYPASS USERS GRANTED ALL {N_CO} ENTITIES: {len(allN)}")

print("\n=== B_overbroad by TITLE ===")
for t, n in sorted(title_counts.items(), key=lambda x: -x[1]):
    print(f"  {t}: {n}")
print("=== B_overbroad by DEPARTMENT ===")
for d, n in sorted(dept_counts.items(), key=lambda x: -x[1]):
    print(f"  {d}: {n}")

print("\n=== B_overbroad ROWS (user_id | title | dept | home | n_view | n_write) ===")
for row in overbroad_rows:
    print("  {} | {} | {} | {} | {} | {}".format(*row))

print(f"\n=== can_write BEYOND home (most sensitive) n={len(write_beyond)} ===")
for uid, codes in write_beyond:
    print(f"  uid={uid} write_extra={','.join(codes)}")

print("\n=== D_no_home user_ids (no employee_record / null company — manual decision) ===")
print("  " + ",".join(str(x) for x in buckets['D_no_home']))
print("=== A_unrestricted user_ids (legit all-entity; UCA redundant) ===")
print("  " + ",".join(str(x) for x in buckets['A_unrestricted']))
print("\n=== END ===")
