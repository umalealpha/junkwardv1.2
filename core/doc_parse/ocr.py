"""Local OCR via RapidOCR (ONNX, Apache-2.0, fully offline).

Runs identically on the M4 (CoreMLExecutionProvider → Neural Engine/GPU) and
the x86_64 EC2 (CPU). No torch, no NVIDIA, no network. Exposes per-line
confidence scores so the gate can decide whether to escalate. Lazy singleton
(model load is ~0.3s, kept warm). Django-free + import-guarded.
"""
from __future__ import annotations

import io
import logging

log = logging.getLogger(__name__)

_engine = None
_engine_failed = False


def is_available() -> bool:
    try:
        import rapidocr  # noqa: F401
        return True
    except Exception:    # noqa: BLE001
        return False


def _get_engine():
    global _engine, _engine_failed
    if _engine is not None or _engine_failed:
        return _engine
    try:
        from rapidocr import RapidOCR
        _engine = RapidOCR()
    except Exception as e:    # noqa: BLE001
        log.warning('RapidOCR init failed: %s', e)
        _engine_failed = True
    return _engine


def _preprocess(img):
    """Grayscale + autocontrast + light upscale, in PIL (no OpenCV → no X11/libGL
    on the server). Improves OCR on phone photos. Falls back to the raw RGB array
    on any failure."""
    try:
        import numpy as np
        from PIL import ImageOps
        g = ImageOps.grayscale(img.convert('RGB'))
        w, h = g.size
        if max(w, h) < 1600:                 # upscale small captures toward ~300 DPI
            scale = 1600 / max(w, h)
            from PIL import Image as _I
            g = g.resize((int(w * scale), int(h * scale)), _I.LANCZOS)
        g = ImageOps.autocontrast(g)
        return np.array(g.convert('RGB'))
    except Exception:    # noqa: BLE001
        import numpy as np
        return np.array(img.convert('RGB'))


def ocr_image(file_bytes: bytes, *, preprocess: bool = True) -> dict:
    """OCR an image. Returns {text, mean_conf, min_conf, n_lines, lines, ok}.

    mean_conf / min_conf are 0-1 RapidOCR per-line scores for the gate.
    Returns ok=False (and empty text) if RapidOCR is unavailable.
    """
    eng = _get_engine()
    if eng is None:
        return {'text': '', 'mean_conf': 0.0, 'min_conf': 0.0, 'n_lines': 0, 'lines': [], 'ok': False}
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(file_bytes))
        arr = _preprocess(img) if preprocess else None
        res = eng(arr if arr is not None else img)
    except Exception as e:    # noqa: BLE001
        log.warning('RapidOCR run failed: %s', e)
        return {'text': '', 'mean_conf': 0.0, 'min_conf': 0.0, 'n_lines': 0, 'lines': [], 'ok': False}

    txts = list(getattr(res, 'txts', None) or [])
    scores = [float(s) for s in (getattr(res, 'scores', None) or [])]
    text = '\n'.join(txts)
    mean_conf = (sum(scores) / len(scores)) if scores else 0.0
    min_conf = min(scores) if scores else 0.0
    lines = [{'text': t, 'score': round(s, 4)} for t, s in zip(txts, scores)]
    return {
        'text': text,
        'mean_conf': round(mean_conf, 4),
        'min_conf': round(min_conf, 4),
        'n_lines': len(txts),
        'lines': lines,
        'ok': True,
    }
