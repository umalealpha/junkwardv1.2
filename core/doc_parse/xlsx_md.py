"""Excel → Markdown via python-calamine (fast, deterministic, fully local).

calamine reads xlsx / xlsb / xls / ods through ONE Rust path — it supersedes
both openpyxl (xlsx-only, slow) and pyxlsb (xlsb-only). No LLM, no network.

KNOWN GOTCHA (verified): calamine returns '' for a formula cell that has NO
cached result. The MA / TB workbooks are formula-heavy, so a naive read can
silently drop computed P&L / GWP / balance cells. When `recalc=True` and
LibreOffice (`soffice`) is available, we recalc the workbook ONCE via headless
LibreOffice and re-read — mandatory for the TB/GL path, optional elsewhere.

Django-free + import-guarded.
"""
from __future__ import annotations

import io
import logging
import os
import shutil
import subprocess
import tempfile
import uuid

log = logging.getLogger(__name__)

_SPREADSHEET_EXT = ('.xlsx', '.xlsm', '.xlsb', '.xls', '.ods')


def is_available() -> bool:
    try:
        import python_calamine  # noqa: F401
        return True
    except Exception:    # noqa: BLE001
        return False


def _soffice_bin() -> str | None:
    """Locate the LibreOffice headless binary (Mac cask path or PATH)."""
    cand = shutil.which('soffice') or shutil.which('libreoffice')
    if cand:
        return cand
    mac = '/Applications/LibreOffice.app/Contents/MacOS/soffice'
    return mac if os.path.exists(mac) else None


def _recalc_to_xlsx(raw: bytes, ext: str) -> bytes | None:
    """Open `raw` in headless LibreOffice and re-export to xlsx so formula
    cells carry cached values. Each call uses a throwaway user profile to dodge
    the LO concurrency deadlock (bugs 82775/106134). Returns bytes or None."""
    soffice = _soffice_bin()
    if not soffice:
        return None
    tmp = tempfile.mkdtemp(prefix='omni_xlsx_')
    try:
        # Separate in/out dirs so the converted file never collides with the
        # source basename (soffice keeps the basename on --convert-to).
        outdir = os.path.join(tmp, 'out')
        os.makedirs(outdir, exist_ok=True)
        src = os.path.join(tmp, f'source{ext or ".xlsx"}')
        with open(src, 'wb') as fh:
            fh.write(raw)
        profile = os.path.join(tmp, f'profile_{uuid.uuid4().hex[:8]}')
        # Force "always recalculate on load" so uncached formula cells (common
        # in library-written xlsx) actually compute — without this, --convert-to
        # only preserves existing cached values. 0 = Always.
        user_dir = os.path.join(profile, 'user')
        os.makedirs(user_dir, exist_ok=True)
        with open(os.path.join(user_dir, 'registrymodifications.xcu'), 'w') as fh:
            fh.write(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<oor:items xmlns:oor="http://openoffice.org/2001/registry" '
                'xmlns:xs="http://www.w3.org/2001/XMLSchema" '
                'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n'
                '<item oor:path="/org.openoffice.Office.Calc/Formula/Load">'
                '<prop oor:name="OOXMLRecalcMode" oor:op="fuse"><value>0</value></prop></item>\n'
                '<item oor:path="/org.openoffice.Office.Calc/Formula/Load">'
                '<prop oor:name="ODFRecalcMode" oor:op="fuse"><value>0</value></prop></item>\n'
                '</oor:items>\n'
            )
        subprocess.run(
            [soffice, '--headless', '--norestore',
             f'-env:UserInstallation=file://{profile}',
             '--convert-to', 'xlsx:Calc MS Excel 2007 XML', '--outdir', outdir, src],
            check=True, capture_output=True, timeout=120,
        )
        for f in os.listdir(outdir):
            if f.lower().endswith('.xlsx'):
                with open(os.path.join(outdir, f), 'rb') as fh:
                    return fh.read()
        return None
    except Exception as e:    # noqa: BLE001
        log.warning('LibreOffice recalc failed: %s', e)
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _read_sheets(raw: bytes):
    from python_calamine import CalamineWorkbook
    wb = CalamineWorkbook.from_filelike(io.BytesIO(raw))
    out = []
    for name in wb.sheet_names:
        try:
            rows = wb.get_sheet_by_name(name).to_python()
        except Exception:    # noqa: BLE001
            rows = []
        out.append((name, rows))
    return out


def _to_markdown(name: str, rows: list) -> str:
    if not rows:
        return f'### {name}\n\n_(empty)_'
    try:
        from tabulate import tabulate
        body = tabulate(rows[1:], headers=[str(c) for c in rows[0]], tablefmt='github')
    except Exception:    # noqa: BLE001 — tabulate optional; manual pipe render
        lines = ['| ' + ' | '.join(str(c) for c in rows[0]) + ' |',
                 '| ' + ' | '.join('---' for _ in rows[0]) + ' |']
        for r in rows[1:]:
            lines.append('| ' + ' | '.join('' if c is None else str(c) for c in r) + ' |')
        body = '\n'.join(lines)
    return f'### {name}\n\n{body}'


def _numeric_density(rows: list) -> float:
    """Fraction of non-header cells that parse to a number — a cheap signal
    that formula values made it through (low density on a numeric sheet ⇒
    dropped/uncached formulas ⇒ recalc needed)."""
    total = filled = 0
    for r in rows[1:]:
        for c in r:
            total += 1
            if isinstance(c, (int, float)):
                filled += 1
            elif isinstance(c, str) and c.strip():
                s = c.strip().replace(',', '').replace('%', '').lstrip('(').rstrip(')')
                try:
                    float(s); filled += 1
                except ValueError:
                    pass
    return (filled / total) if total else 0.0


def xlsx_to_markdown(file_bytes: bytes, filename: str = '', *,
                     recalc: bool = False, max_rows_per_sheet: int = 5000) -> dict:
    """Return {markdown, sheets:[{name,n_rows,n_cols,numeric_density}], signals}.

    `recalc=True` forces a LibreOffice recalc-then-reread (use for TB/GL/MA
    workbooks where uncached formulas would silently drop P&L/GWP cells).
    """
    ext = os.path.splitext((filename or '').lower())[1]
    raw = file_bytes
    recalced = False

    if recalc:
        fixed = _recalc_to_xlsx(raw, ext)
        if fixed:
            raw, recalced = fixed, True

    sheets_raw = _read_sheets(raw)

    # Auto-recalc: if NOT already recalced, a numeric-looking sheet reads with
    # very low numeric density AND there are empty cells, suspect dropped
    # formulas and try one recalc pass.
    if not recalced and recalc is not False:
        worst = min((_numeric_density(r) for _, r in sheets_raw if len(r) > 1), default=1.0)
        any_empty = any(
            any(c == '' or c is None for r in rows[1:] for c in r)
            for _, rows in sheets_raw if len(rows) > 1
        )
        if worst < 0.15 and any_empty:
            fixed = _recalc_to_xlsx(raw, ext)
            if fixed:
                raw, recalced = fixed, True
                sheets_raw = _read_sheets(raw)

    md_parts, sheet_meta = [], []
    for name, rows in sheets_raw:
        rows = rows[:max_rows_per_sheet]
        md_parts.append(_to_markdown(name, rows))
        sheet_meta.append({
            'name': name,
            'n_rows': max(0, len(rows) - 1),
            'n_cols': len(rows[0]) if rows else 0,
            'numeric_density': round(_numeric_density(rows), 3),
        })

    return {
        'markdown': '\n\n'.join(md_parts),
        'sheets': sheet_meta,
        'signals': {
            'engine': 'python-calamine',
            'recalced': recalced,
            'n_sheets': len(sheet_meta),
            'min_numeric_density': round(min((s['numeric_density'] for s in sheet_meta), default=0.0), 3),
        },
    }
