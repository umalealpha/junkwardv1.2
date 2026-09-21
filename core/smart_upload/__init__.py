"""
core.smart_upload — generic "drop any file, get clean rows" pipeline.

CFO directive 2026-05-18:
  Stop routing imports through Claude Code. Every data-import page on
  omni gets a Smart Upload widget. CFO drops xlsx / csv / pdf / docx,
  DeepSeek figures out the column mapping, omni previews the canonical
  rows, CFO commits.

Pipeline:
  1. extractors.py  file -> (headers, sample_rows, all_rows)
  2. mapper.py      DeepSeek maps raw headers -> canonical fields
                    (only headers + 3 redacted sample rows are sent)
  3. sections.py    canonical schema per section (CoA, TB, GL, Vendors,
                    Customers, PP&E, Bank Accounts, Employees, Payroll)
  4. committers.py  per-section idempotent DB writers
  5. api_views.py   POST /preview/ + POST /commit/
"""
