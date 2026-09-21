# Alpha Direct Finance — Docker Setup

Containerized local stack with PostgreSQL 16, Django 5.2, and Next.js 15. Same image shape ships to AWS (ECS/Fargate + RDS Postgres).

---

## Why this shape (security & cost rationale)

| Concern | Choice | Why |
|---|---|---|
| Database | **PostgreSQL 16** | ACID, mature security (RLS, SSL, encryption-at-rest on RDS), free, AWS RDS free tier db.t4g.micro 750 hrs/mo for 12 months. Industry standard for finance. |
| Containers | **Multi-stage Dockerfiles, non-root users** | Smaller attack surface; same image runs dev → prod. |
| Secrets | `.env` (gitignored) + `${VAR:?}` guards in compose | Compose refuses to start if a required secret is missing. Move to AWS Secrets Manager / SSM Parameter Store on deploy. |
| Network | Postgres on internal Docker network only | DB is unreachable from host by default. Mirrors `RDS in private subnet` topology. |
| HTTPS | Hardening flags env-driven (off in dev, on in prod) | `SECURE_SSL_REDIRECT`, `HSTS`, `Secure` cookies all flip via `.env` when fronted by ALB/CloudFront. |
| Static files | Whitenoise + `CompressedManifestStaticFilesStorage` | No nginx required for MVP; cache-busted hashed filenames. |
| Auth bootstrap | `DJANGO_CREATE_SUPERUSER=1` first boot | Idempotent — skips if user exists. Set the password in `.env`, never hard-code. |

---

## Prerequisites — install once

You need **Docker Desktop**. Everything else (Python, Postgres, Node) lives inside containers.

**Windows 11:**

1. Install WSL2 if not already (admin PowerShell):
   ```
   wsl --install
   ```
   Reboot when prompted.

2. Install Docker Desktop: https://www.docker.com/products/docker-desktop/  
   During install, leave "Use WSL 2 instead of Hyper-V" checked.

3. Launch Docker Desktop and wait for the whale icon to say "Engine running".

4. Verify:
   ```
   docker --version
   docker compose version
   ```

---

## First run — production-shaped (gunicorn, hardened)

From the project root (`d:\ADRiskProjects\AlphaFinance`):

```bash
# 1. .env was already generated for local dev. Review it once:
type .env

# 2. Build images and start the stack
docker compose up -d --build

# 3. Watch the backend bring itself up (migrate + seed + collectstatic)
docker compose logs -f backend
```

When you see `Listening at: http://0.0.0.0:8000` in the backend logs, open:

| URL | What |
|---|---|
| http://localhost:3000 | Next.js UI (login page) |
| http://localhost:8000/admin/ | Django admin |
| http://localhost:8000/api/v1/ | DRF browsable API |

Login with the superuser from `.env` (`DJANGO_SUPERUSER_USERNAME` / `DJANGO_SUPERUSER_PASSWORD`).

---

## Dev mode — hot reload

Bind-mounts your source so edits are picked up without rebuilding:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

This swaps gunicorn for `runserver` and Next's prod server for `next dev`.

---

## Common operations

```bash
# Stop
docker compose down

# Stop AND wipe DB volume (destructive — local dev only)
docker compose down -v

# One-off Django shell
docker compose exec backend python manage.py shell

# Run a management command (e.g. seed test invoices)
docker compose exec backend python manage.py create_test_invoices

# Tail just the backend
docker compose logs -f backend

# psql into the database (DB is not exposed to host in prod compose; use exec)
docker compose exec db psql -U alpha_admin -d alpha_finance
```

---

## Keeping the stack updated (your "stay in sync" requirement)

Three things to keep current:

1. **Code** — manual `git pull` (we agreed no auto-sync).
2. **Python deps** — pinned in `requirements.txt`. To upgrade:
   ```bash
   docker compose exec backend pip list --outdated
   # edit requirements.txt, then:
   docker compose build backend && docker compose up -d backend
   ```
3. **Base images** — Postgres / Python / Node minor versions:
   ```bash
   docker compose pull           # refresh base images
   docker compose up -d --build  # rebuild with new bases
   ```

For production: add this as a scheduled GitHub Action / CodeBuild step on a weekly cadence.

---

## AWS deployment shape (when you're ready)

This stack maps 1:1 to AWS:

| Local service | AWS equivalent |
|---|---|
| `db` (Postgres container) | **RDS PostgreSQL** (db.t4g.micro on free tier; enable encryption-at-rest, automated backups, multi-AZ later) |
| `backend` (Django container) | **ECS Fargate** task running the same `Dockerfile` image, pushed to ECR |
| `frontend` (Next container) | **ECS Fargate** OR static export to **S3 + CloudFront** |
| `.env` | **AWS Secrets Manager** + ECS task `secrets:` block |
| Network | Postgres in **private subnet**, ECS tasks in private subnet, **ALB** in public subnet |
| HTTPS | **ACM cert** on ALB → flip the prod env flags in `.env` (`SECURE_SSL_REDIRECT=True`, etc.) |

The Dockerfiles already produce images that ECS Fargate can run unchanged.

---

## Security checklist before going live

- [ ] Rotate `SECRET_KEY`, `DB_PASSWORD`, `DJANGO_SUPERUSER_PASSWORD` from the dev values in `.env`
- [ ] Set `DEBUG=False`
- [ ] Set `ALLOWED_HOSTS` to your real domain(s) only
- [ ] Set `CORS_ALLOWED_ORIGINS` and `CSRF_TRUSTED_ORIGINS` to the real frontend domain
- [ ] Flip the production HTTPS flags (uncomment block at bottom of `.env.example`)
- [ ] Move secrets out of `.env` into AWS Secrets Manager
- [ ] Enable RDS encryption-at-rest, automated backups (PITR), and force SSL connections (`rds.force_ssl=1` parameter group)
- [ ] Enable CloudWatch logs for ECS tasks
- [ ] Add a WAF rule on the ALB (rate limit + AWS managed rule sets)
- [ ] Configure an S3 lifecycle policy for `media/` if you move uploads off EFS
- [ ] Set up CloudWatch alarms on RDS CPU / connections / free storage
- [ ] Schedule monthly `docker compose pull` to pick up base-image security patches
