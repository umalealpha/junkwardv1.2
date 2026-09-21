"""Face compare for fake-ID detection — AWS Rekognition CompareFaces.

Capability-flagged and OFF by default. omni currently has no boto3/AWS creds,
and Rekognition has no af-south-1 endpoint, so this degrades gracefully to
{available: False} → the KYC verdict becomes 'manual_review', never a false
pass. Turn on later by installing boto3, setting REKOGNITION_ENABLED=1 and
REKOGNITION_REGION (e.g. eu-west-1 / us-east-1 — a region that has the service)
plus creds. Then wire the Graphite S3 fetch of the on-record reference image.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from django.conf import settings

log = logging.getLogger(__name__)


def is_enabled() -> bool:
    """Biometric face-match runs ONLY when it is switched on AND a DPIA has been
    approved for biometric processing (DPA s.65 / audit H-9, CFO 2026-07-19).
    Biometrics are special-category data: without the DPIA + consent sign-off this
    stays OFF even if REKOGNITION_ENABLED is set. Set BOTH REKOGNITION_ENABLED=1
    and FACE_MATCH_DPIA_APPROVED=1 (after the DPIA) to activate."""
    on = bool(getattr(settings, 'REKOGNITION_ENABLED', False))
    dpia_ok = bool(getattr(settings, 'FACE_MATCH_DPIA_APPROVED', False))
    if on and not dpia_ok:
        log.warning('Face-match is enabled but FACE_MATCH_DPIA_APPROVED is not set — '
                    'staying OFF until a DPIA is approved (DPA H-9).')
        return False
    return on and dpia_ok


def compare_faces(id_photo_bytes: bytes, reference_bytes: bytes,
                  threshold: float = 90.0) -> Dict[str, Any]:
    """Return {available, similarity, match, error}. available=False when the
    capability isn't wired — caller must treat that as 'needs manual review',
    NOT a pass."""
    if not is_enabled():
        return {'available': False, 'similarity': None, 'match': None,
                'error': 'face-check not enabled'}
    try:
        import boto3  # noqa: PLC0415
        region = getattr(settings, 'REKOGNITION_REGION', 'eu-west-1')
        client = boto3.client('rekognition', region_name=region)
        resp = client.compare_faces(
            SourceImage={'Bytes': id_photo_bytes},
            TargetImage={'Bytes': reference_bytes},
            SimilarityThreshold=float(threshold),
        )
        matches = resp.get('FaceMatches') or []
        sim = max((m['Similarity'] for m in matches), default=0.0)
        return {'available': True, 'similarity': round(sim, 1),
                'match': sim >= threshold, 'error': None}
    except Exception as e:  # noqa: BLE001
        log.warning('rekognition compare_faces failed: %s', e)
        return {'available': False, 'similarity': None, 'match': None, 'error': str(e)[:200]}
