# Weekly 'CEO - YOU BETTER READ THIS' board read (PROD). Same live pipeline as the
# daily brief but a 7-day window, weekly layout, leads with what is still OPEN.
from builtins import Exception, float, int, str, len
import importlib.util, json, datetime, os, re
from django.utils import timezone
spec=importlib.util.spec_from_file_location("E","/tmp/ceo_engine.py")
E=importlib.util.module_from_spec(spec); spec.loader.exec_module(E)
from core.ceo_monitor_views import make_escalation_token
def _esc_row(item):
    chips=[]
    for label,handle in E.ESCALATION_TARGETS:
        tok=make_escalation_token(to=handle, party=item.party, ref=item.reference,
                                  matter=(item.polished_matter or item.summary), why=item.polished_why)
        href="https://omni.alphadirect.co.bw/api/ceo-monitor/escalate/?t="+tok
        chips.append('<a href="%s" style="display:inline-block;text-decoration:none;'
                     'background:#F1F4F8;color:#0D1B2A;font-family:Arial,Helvetica,sans-serif;'
                     'font-size:11px;font-weight:bold;padding:6px 12px;border:1px solid #D4DBE4;'
                     'border-radius:14px;margin:0 6px 6px 0;">%s</a>'%(href,label))
    return ('<div style="margin-top:14px;padding-top:12px;border-top:1px solid #EEF1F5;">'
            '<div style="font-family:Arial,Helvetica,sans-serif;font-size:11px;color:#98A2B3;'
            'text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px;">Assign &amp; escalate to</div>'
            +"".join(chips)+"</div>")
E._escalate_row=_esc_row
NAVY,ORANGE,SERIF,SANS=E.NAVY,E.ORANGE,E.SERIF,E.SANS

def gather():
    from aware.engine import run_select, mask_rows
    out=[]
    try:
        cols,rows=run_select("SELECT claim_number, policyNumber, reserve_amount, status, description_of_loss FROM new_claims WHERE status IN ('Pending','Reopen','Rejected') OR reserve_amount>=500000 ORDER BY reserve_amount DESC LIMIT 20")
        rows=mask_rows(cols,rows)
        for r in rows:
            hv=(r.get('reserve_amount') or 0)
            out.append({"party":str(r.get('policyNumber','')),"cat":"High-Value Claim (>BWP 500K)" if hv and float(hv)>=500000 else "Claim status","ref":str(r.get('claim_number','')),"text":f"Claim {r.get('claim_number','')} status {r.get('status','')}, reserve {r.get('reserve_amount','?')}, loss: {r.get('description_of_loss','') or 'n/a'}."})
    except Exception as e: print("claims ERR",repr(e)[:200])
    try:
        cols,rows=run_select("SELECT claim_id, title, note FROM claim_review_notes ORDER BY id DESC LIMIT 20")
        rows=mask_rows(cols,rows)
        for r in rows:
            out.append({"party":"","cat":"Customer Complaint","ref":f"note:{r.get('claim_id','')}","text":f"{r.get('title','')}: {r.get('note','')}"})
    except Exception as e: print("notes ERR",repr(e)[:200])
    return out

def classify(cands):
    from core.ai_assist import reasoning_complete, is_safe_for_ai
    SYS=('You triage internal insurance-ops items for a CEO WEEKLY board read at an NBFIRA insurer. '
     'Mark significant=TRUE ONLY for: a genuine customer COMPLAINT; a REGULATOR/NBFIRA/Ombudsman matter; '
     'a LEGAL threat/letter of demand; a HIGH-VALUE claim reserve >= BWP 500,000; or a DATA/SECURITY incident. '
     'Everything else is significant=FALSE. Be strict. Return STRICT JSON array, one per index i: '
     '{"i":int,"significant":bool,"category":"complaint|regulatory|legal|claim|incident|other",'
     '"severity":"Critical|High|Watch","matter":"<=1 sentence","why":"<=1 clause"}. Never invent facts.')
    cands=cands[:25]
    payload=[{"i":i,"text":c["text"][:400]} for i,c in enumerate(cands)]
    txt=json.dumps(payload,ensure_ascii=False)
    saf=is_safe_for_ai(txt)
    if not getattr(saf,"safe",False): return {},cands
    try:
        raw=reasoning_complete(getattr(saf,"redacted_text",txt),system_prompt=SYS,response_format="json",max_tokens=4096)
        raw=re.sub(r"^```(?:json)?\s*","",raw.strip()); raw=re.sub(r"\s*```$","",raw)
        data=json.loads(raw); return {int(v.get("i",k)):v for k,v in enumerate(data)},cands
    except Exception as e:
        print("AI ERR",repr(e)[:200]); return {},cands

cands=gather(); verds,cands=classify(cands)
_SEV={"Critical":0,"High":1,"Watch":2}
items=[]
for i,c in enumerate(cands):
    v=verds.get(i) or {}
    if not v.get("significant"): continue
    it=E.QueueItem(id=i,title=c["ref"],from_addr="",category=c["cat"],summary=c["text"],confirmed=True)
    it.severity=v.get("severity","High"); it.party=c["party"] or c["ref"]
    it.polished_matter=v.get("matter","") or c["text"][:240]; it.polished_why=v.get("why","")
    it.reference=c["ref"]; items.append(it)
merged=E.merge_related(items); merged.sort(key=lambda i:(_SEV.get(i.severity,9),(i.party or '').lower()))
open_items=merged[:8]
print("significant open:",len(open_items))
cards="".join(E.render_card(it) for it in open_items) or (
  f'<div style="padding:30px 26px;text-align:center;font-family:{SANS};color:#6B7280;">Nothing outstanding this week.</div>')
today=timezone.localdate(); wk=today-datetime.timedelta(days=6)
header=(f'<div style="background:{NAVY};padding:30px 26px 26px 26px;">'
 f'<div style="font-family:{SERIF};font-size:29px;font-weight:bold;color:#FFFFFF;line-height:1.15;">CEO &mdash; YOU BETTER READ THIS</div>'
 f'<div style="font-family:{SANS};font-size:13px;color:{ORANGE};margin-top:8px;letter-spacing:1px;text-transform:uppercase;">Weekly Board Read</div>'
 f'<div style="font-family:{SANS};font-size:13px;color:#A9B4C2;margin-top:4px;">Week of {wk.strftime("%d")}&ndash;{today.strftime("%d %B %Y")}</div></div>')
summary=(f'<div style="background:#F7F9FB;border-bottom:1px solid #E6EBF1;padding:16px 26px;">'
 f'<div style="font-family:{SERIF};font-size:15px;font-weight:bold;color:{NAVY};">{len(open_items)} matter(s) still open &mdash; act this week</div></div>')
bar=(f'<div style="padding:22px 26px 4px 26px;"><div style="background:#7A1F1F;color:#FFFFFF;font-family:{SANS};font-size:12px;font-weight:bold;letter-spacing:1.5px;text-transform:uppercase;padding:8px 14px;border-radius:5px;margin:0 0 14px 0;">Still open &mdash; not resolved</div>'+cards+'</div>')
footer=(f'<div style="background:{NAVY};padding:20px 26px;font-family:{SANS};font-size:11px;color:#A0AEC0;text-align:center;line-height:1.7;border-top:3px solid {ORANGE};">Weekly Board Read &mdash; CEO Monitor &mdash; Alpha Direct Insurance Company (Pty) Ltd<br>CONFIDENTIAL &mdash; Internal use only &mdash; DPA Act 18 of 2024 controlled &nbsp;|&nbsp; &copy; 2026</div>')
html=(f'<div style="background:#EEF1F5;padding:22px 10px;font-family:{SERIF};"><div style="max-width:660px;margin:0 auto;background:#FFFFFF;border:1px solid #E0E5EC;border-radius:9px;overflow:hidden;">{header}{summary}{bar}{footer}</div></div>')
subject="CEO - YOU BETTER READ THIS - Weekly Board Read "+today.strftime("%d %b %Y")
print("SUBJECT:",subject,"BYTES:",len(html))
if os.environ.get("CEO_SEND")=="1":
    from django.core.mail import EmailMultiAlternatives
    from django.conf import settings
    to=[x for x in os.environ.get("CEO_TO","aiyer@alphadirect.co.bw").split(",") if x]
    cc=[x for x in os.environ.get("CEO_CC","pganesharajah@alphadirect.co.bw").split(",") if x]
    frm=getattr(settings,"DEFAULT_FROM_EMAIL","omni@alphadirect.co.bw")
    m=EmailMultiAlternatives(subject=subject,body="(See the HTML version.)",from_email=frm,to=to,cc=cc)
    m.attach_alternative(html,"text/html"); m.send(); print("SENT to",to,"cc",cc)
