import re

from django.http import FileResponse, Http404
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .access import user_allowed
from .engine import ask
from .extract import extract_upload, MAX_FILES, MAX_BYTES
from .models import AwareQuery
from .reporting import KEY_LABELS, reports_dir

_REPORT_FNAME = re.compile(r'^[a-z0-9\-]+_\d{4}-\d{2}-\d{2}\.xlsx$')

ALLOWED_EXT = ('.pdf', '.xlsx', '.xls', '.docx', '.doc', '.csv',
               '.png', '.jpg', '.jpeg')


def _deny():
    return Response({'detail': 'Graphite Aware is restricted to the executive whitelist.'},
                    status=403)


def _read_uploads(request):
    """Extract text from every attached file (server-side, local OCR).
    Returns (texts, meta, error_response_or_None)."""
    files = request.FILES.getlist('files') or request.FILES.getlist('file')
    if len(files) > MAX_FILES:
        return None, None, Response({'detail': f'Up to {MAX_FILES} files at once.'}, status=400)
    texts, meta = [], []
    for f in files:
        name = f.name or 'file'
        if not name.lower().endswith(ALLOWED_EXT):
            return None, None, Response({'detail': f'Unsupported file type: {name}'}, status=400)
        if f.size and f.size > MAX_BYTES:
            return None, None, Response({'detail': f'{name} is too large (max 10 MB).'}, status=413)
        r = extract_upload(f.read(), name, getattr(f, 'content_type', '') or '')
        texts.append(r['text'])
        meta.append({'filename': name, 'chars': len(r['text']),
                     'confidence': r['confidence'], 'tier': r['tier'],
                     'escalate': r['escalate']})
    return texts, meta, None


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def aware_access(request):
    return Response({'allowed': user_allowed(request.user)})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser, JSONParser])
def aware_ask(request):
    if not user_allowed(request.user):
        return _deny()

    data = request.data
    mode = (data.get('mode') or 'chat').strip()
    question = (data.get('question') or '').strip()
    email_text = (data.get('email') or '').strip()[:8000]
    policy_no = (data.get('policy_no') or '').strip()

    texts, meta, err = _read_uploads(request)
    if err:
        return err
    texts = texts or []

    if mode == 'claim_payability':
        from .modes.payability import assess_payability
        out = assess_payability(texts, email_text, policy_no, request.user)
        _log(request.user, f'[payability] {policy_no or "auto"}', meta, out.get('easy_summary', ''))
        return Response({'mode': mode, 'result': out, 'files': meta})

    if mode == 'kyc':
        from .modes.kyc import check_kyc
        out = check_kyc(texts, email_text, policy_no, request.user)
        _log(request.user, f'[kyc] {policy_no or "auto"}', meta, out.get('easy_summary', ''))
        return Response({'mode': mode, 'result': out, 'files': meta})

    if mode == 'broker_analysis':
        from .modes.broker_analysis import broker_report
        out = broker_report(request.user)
        _log(request.user, '[broker_analysis]', meta,
             f"{len(out.get('brokers', []))} external brokers")
        return Response({'mode': mode, 'result': out, 'files': meta})

    if mode == 'broker_scorecard':
        from .modes.broker_scorecard import broker_scorecard_report
        out = broker_scorecard_report(request.user)
        _log(request.user, '[broker_scorecard]', meta,
             f"{len(out.get('brokers', []))} brokers · {out.get('loss_makers', 0)} loss-makers")
        return Response({'mode': mode, 'result': out, 'files': meta})

    if mode == 'top_dom':
        from .modes.top_dom import top_domestic_report
        out = top_domestic_report(request.user)
        _log(request.user, '[top_dom]', meta, f"{out.get('count', 0)} domestic policies")
        return Response({'mode': mode, 'result': out, 'files': meta})

    if mode == 'claims_registry':
        from .modes.claims_registry import claims_registry_report
        out = claims_registry_report(request.user)
        _log(request.user, '[claims_registry]', meta, f"{out.get('open_count', 0)} open claims")
        return Response({'mode': mode, 'result': out, 'files': meta})

    # default: chat (may include attachments / pasted email)
    if not question and not texts and not email_text:
        return Response({'detail': 'Ask a question or attach a document.'}, status=400)
    if len(question) > 2000:
        return Response({'detail': 'Question too long.'}, status=400)
    if not question:
        question = 'Review the attached document(s) and tell me what they mean for us, checking against the system.'
    result = ask(question, request.user, file_texts=texts, email_text=email_text)
    result['files'] = meta
    return Response(result)


def _log(user, q, meta, answer):
    try:
        AwareQuery.objects.create(user=user, question=q[:2000],
                                  rounds=[{'file': m['filename'], 'chars': m['chars']} for m in (meta or [])],
                                  answer=(answer or '')[:20000], ok=True)
    except Exception:
        pass


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def aware_reports_list(request):
    """Weekly Excel snapshots available for download (whitelist-gated)."""
    if not user_allowed(request.user):
        return _deny()
    items = []
    for p in sorted(reports_dir().glob('*.xlsx'), reverse=True):
        key, _, date = p.stem.rpartition('_')
        items.append({'key': key, 'label': KEY_LABELS.get(key, key),
                      'date': date, 'filename': p.name, 'size': p.stat().st_size})
    return Response({'reports': items})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def aware_report_download(request):
    """Stream one weekly Excel snapshot (whitelist-gated, name-sanitised)."""
    if not user_allowed(request.user):
        return _deny()
    fname = (request.GET.get('f') or '').strip()
    if not _REPORT_FNAME.match(fname):
        return Response({'detail': 'Bad file name.'}, status=400)
    base = reports_dir().resolve()
    path = (base / fname).resolve()
    if base != path.parent or not path.is_file():
        raise Http404()
    resp = FileResponse(
        open(path, 'rb'),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = f'attachment; filename="{fname}"'
    return resp


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def aware_history(request):
    if not user_allowed(request.user):
        return _deny()
    qs = AwareQuery.objects.filter(user=request.user)[:20]
    return Response([{
        'question': q.question, 'answer': q.answer,
        'created_at': q.created_at.isoformat(), 'duration_ms': q.duration_ms,
    } for q in qs])
