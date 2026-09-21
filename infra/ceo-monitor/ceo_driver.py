# Daily "CEO Omni Brief" (PROD) — reads ARUN'S REAL INBOX (aiyer@) via Graph,
# classifies with DeepSeek (Gemini fallback), renders, and (CEO_SEND=1) emails.
# NAMES: pulled from the un-redacted email itself (subject/body) locally, so the
# real customer/party name shows WITHOUT sending PII to any external model
# (the models still only ever see is_safe_for_ai()-redacted text = DPA-safe).
from builtins import Exception, float, int, str, len, list, dict, sorted, set, range, enumerate
import importlib.util, json, datetime, os, re, urllib.request, urllib.parse
from django.utils import timezone
spec=importlib.util.spec_from_file_location("E","/tmp/ceo_engine.py")
E=importlib.util.module_from_spec(spec); spec.loader.exec_module(E)

from core.ceo_monitor_views import make_escalation_token
def _esc_row(item):
    cells=[]
    for label,handle in E.ESCALATION_TARGETS:
        tok=make_escalation_token(to=handle, party=item.party, ref=item.reference,
                                  matter=(item.polished_matter or item.summary), why=item.polished_why)
        href="https://omni.alphadirect.co.bw/api/ceo-monitor/escalate/?t="+tok
        cells.append('<td style="padding:0 8px 8px 0;"><a href="%s" style="display:block;'
                     'text-align:center;text-decoration:none;background:#F1F4F8;color:#0D1B2A;'
                     'font-family:Arial,Helvetica,sans-serif;font-size:12px;font-weight:bold;'
                     'padding:9px 4px;border:1px solid #D4DBE4;border-radius:8px;">%s</a></td>'%(href,label))
    rows=""
    for i in range(0,len(cells),4):
        row=cells[i:i+4]
        while len(row)<4: row.append('<td style="padding:0 8px 8px 0;"></td>')
        rows+="<tr>"+"".join(row)+"</tr>"
    return ('<div style="margin-top:16px;padding-top:12px;border-top:1px solid #EEF1F5;">'
            '<div style="font-family:Arial,Helvetica,sans-serif;font-size:11px;color:#98A2B3;'
            'text-transform:uppercase;letter-spacing:0.5px;margin-bottom:10px;">Assign &amp; escalate to</div>'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="table-layout:fixed;">'+rows+'</table></div>')
E._escalate_row=_esc_row

# --- sources: drop automated/system/marketing (but NEVER external complaint bodies) ---
_SKIP_ADDR=("no-reply","noreply","donotreply","do-not-reply","mailer-daemon","postmaster",
            "notifications@","newsletter","marketing@","@pinterest","@linkedin","@facebook",
            "@blinq","svc-","omni@alphadirect")
_SKIP_NAME=("Omni ERP","SVC CEO Monitor","Pinterest","Blinq","TransUnion","LinkedIn")
_SKIP_SUBJ=("unsubscribe","% off","gift card","sign-in","sign in","newsletter","webinar",
            "invitation:","accepted:","declined:","development dialogue","did not answer on team downtime")
def _skip_sender(addr,name,subj):
    s=(subj or "").lower()
    if any(a in addr for a in _SKIP_ADDR): return True
    if name in _SKIP_NAME: return True
    if any(x in s for x in _SKIP_SUBJ): return True
    return False

def _html_to_text(h):
    t=re.sub(r"(?is)<(script|style)[^>]*>.*?</(script|style)>"," ",h or "")
    t=re.sub(r"(?s)<[^>]+>"," ",t); t=t.replace("&nbsp;"," ").replace("&amp;","&")
    return re.sub(r"\s+"," ",t).strip()

def gather():
    cid=os.environ.get("CEO_GRAPH_CLIENT_ID"); ten=os.environ.get("CEO_GRAPH_TENANT"); sec=os.environ.get("CEO_GRAPH_CLIENT_SECRET")
    if not (cid and ten and sec): print("gather ERR: CEO_GRAPH_* missing"); return []
    data=urllib.parse.urlencode({"client_id":cid,"client_secret":sec,"scope":"https://graph.microsoft.com/.default","grant_type":"client_credentials"}).encode()
    try:
        tok=json.load(urllib.request.urlopen("https://login.microsoftonline.com/%s/oauth2/v2.0/token"%ten,data=data))["access_token"]
    except Exception as ex: print("gather ERR token:",repr(ex)[:200]); return []
    sel="subject,from,receivedDateTime,bodyPreview,body"
    base="https://graph.microsoft.com/v1.0/users/aiyer@alphadirect.co.bw/mailFolders/inbox/messages"
    msgs={}
    # (1) the recent window
    try:
        r=json.load(urllib.request.urlopen(urllib.request.Request(
            base+"?$top=50&$select="+sel+"&$orderby=receivedDateTime%20desc",
            headers={"Authorization":"Bearer "+tok})))
        for m in r.get("value",[]): msgs[m.get("id")]=m
    except Exception as ex: print("gather ERR recent:",repr(ex)[:200])
    # (2) targeted sweep for priority senders/subjects, regardless of age, so a
    #     Consumer Watchdog / Ombudsman / NBFIRA / complaint / demand email is NEVER missed.
    for term in ('"consumer watchdog"','ombudsman','nbfira','complaint','"letter of demand"'):
        try:
            q=base+"?$search="+urllib.parse.quote('"'+term.strip('"')+'"')+"&$top=10&$select="+sel
            rr=json.load(urllib.request.urlopen(urllib.request.Request(
                q, headers={"Authorization":"Bearer "+tok,"ConsistencyLevel":"eventual"})))
            for m in rr.get("value",[]): msgs.setdefault(m.get("id"),m)
        except Exception as ex: print("gather sweep '%s' err:"%term, repr(ex)[:120])
    res={"value":list(msgs.values())}
    out=[]; dropped=0
    for m in res.get("value",[]):
        frm=((m.get("from") or {}).get("emailAddress") or {}); addr=(frm.get("address","") or "").lower(); name=frm.get("name","") or ""
        subj=m.get("subject","") or ""
        body=m.get("body") or {}; btxt=_html_to_text(body.get("content","")) if body.get("contentType")=="html" else (body.get("content","") or "")
        if not btxt: btxt=(m.get("bodyPreview","") or "")
        if _skip_sender(addr,name,subj): dropped+=1; continue
        out.append({"subject":subj,"from_name":name,"from_addr":addr,"body":btxt[:1500],
                    "ref":subj[:120],"text":(subj+" -- "+E.new_text(btxt))[:900]})
    print("gathered",len(out),"inbox messages (",dropped,"automated/junk dropped)"); globals()["_GATHER_DROPPED"]=dropped
    return out

# --- NAMES from source (no PII to any model) ---
# Issue 1 fix — never let a generic administrative subject prefix become the party
# name. "FORMAL COMPLAINT: ..." used to yield party="FORMAL COMPLAINT", so every
# complaint card headed with the same label and looked like a duplicate.
_GENERIC_PARTY = {
    "formal complaint", "complaint", "urgent", "reminder", "final reminder",
    "follow up", "followup", "follow-up", "notice", "final notice",
    "letter of demand", "demand", "request", "query", "enquiry", "inquiry",
    "escalation", "important", "action required", "fyi",
}
_GENERIC_PREFIXES = sorted(_GENERIC_PARTY, key=len, reverse=True)  # longest first
_SEP = " :-–—"  # space, colon, hyphen, en/em dash


def _strip_generic_prefix(s):
    low = s.lower()
    for g in _GENERIC_PREFIXES:
        if low.startswith(g):
            rest = s[len(g):]
            if rest == "" or rest[0] in _SEP:
                return rest.lstrip(_SEP).strip()
    return s


def _extract_party(subject, from_name, from_addr):
    s = (subject or "").strip()
    # peel ALL stacked reply/forward markers ("RE: FW: FWD: X" -> "X"), then any
    # stacked leading generic admin phrase(s) ("FORMAL COMPLAINT: X" -> "X").
    prev = None
    while s and s != prev:
        prev = s
        s = re.sub(r"^(re|fw|fwd)\s*:\s*", "", s, flags=re.I).strip()
        s = _strip_generic_prefix(s)
    ref = ""
    m = re.search(r"[-–:]\s*([A-Za-z]{0,3}\d[\d\-/]{3,})\s*$", s)  # trailing ref
    if m:
        ref = m.group(1).strip()
        s = s[:m.start()].strip()
    name = ""
    m2 = re.match(r"^([A-Z][A-Za-z'\.\-]+(?:\s+[A-Z][A-Za-z'\.\-]+){1,3})\b", s)  # Proper Name
    if m2 and 5 <= len(m2.group(1)) <= 48:
        name = m2.group(1).strip()
    if name and name.lower() in _GENERIC_PARTY:   # never keep a generic word as the name
        name = ""
    party = name or from_name or (from_addr.split("@")[0] if from_addr else "")
    if ref and party:
        party = f"{party} ({ref})"
    return party, ref

# Watchdog/Ombudsman/NBFIRA detection is SUBJECT+SENDER only (the word "nbfira"
# in an ordinary body/signature must NOT trigger it). Always surface, reputational.
def _watchdog_which(subject, from_name, from_addr):
    hay=((subject or "")+" "+(from_name or "")+" "+(from_addr or "")).lower()
    if "watchdog" in hay or "consumerwatchdog" in hay: return "Consumer Watchdog"
    if "ombudsman" in hay: return "the Ombudsman"
    if "nbfira" in hay: return "NBFIRA"
    return None
# High-precision text signals (specific enough to match anywhere in the email).
_STRONG=("not happy","dissatisf","letter of demand","summons","court order","litigation",
         "data breach","class action","authorisation request","authorization request","repudiat")
def _strong(text): t=(text or "").lower(); return any(s in t for s in _STRONG)
def _cat_from(text):
    t=(text or "").lower()
    if "ombudsman" in t or "nbfira" in t or "watchdog" in t: return "regulatory"
    if "attorney" in t or "legal" in t or "demand" in t or "summons" in t or "court" in t or "litigat" in t: return "legal"
    if "complaint" in t or "not happy" in t or "dissatisf" in t or "dispute" in t: return "complaint"
    if "breach" in t or "fraud" in t: return "incident"
    return "claim"

_SYS = """You triage a CEO's incoming email for his daily brief at an NBFIRA insurer. Mark significant=TRUE ONLY for email the CEO MUST know: a customer COMPLAINT/dissatisfaction; a CONSUMER WATCHDOG or OMBUDSMAN or NBFIRA/regulator contact; a LEGAL threat/letter of demand; a HIGH-VALUE claim or payment (>=BWP 500k); a DATA/SECURITY incident; or a major financial escalation. A Consumer Watchdog / Ombudsman / regulator email is ALWAYS significant and at least High (reputational). Newsletters, marketing, receipts, routine internal admin = FALSE. Be strict otherwise. WRITE WITH DEPTH from the email body: 'matter' = 1-2 full sentences stating WHAT happened, the AMOUNT (in BWP), the PARTY, and how long it has run; 'why' = one sentence giving the CONSEQUENCE/risk AND exactly WHAT THEY ARE ASKING FOR, anchored to a number or a named party. Vague lines such as 'may be significant', 'claim-related discussion' or 'high-value claim mentioned' are BANNED - state the actual facts. Return STRICT JSON array, one per index i: {"i":int,"significant":bool,"category":"complaint|regulatory|legal|claim|incident|other","severity":"Critical|High|Watch","matter":"1-2 detailed sentences","why":"1 detailed clause with a number or party"}. The text is anonymised; do NOT invent names. TONE: BLUNT. The CEO is a straight-talking man who wants it raw, not politically correct. Say plainly who is dragging their feet, who is being unreasonable, who has ignored us and for how long, and who is not doing what they agreed. Do NOT soften it: no 'it appears that', no 'may require attention', no 'the team is reviewing'. Write the sentence a person would actually say out loud. Every blunt judgement MUST be anchored to a fact from the email - an amount, a date, a count of days, a broken promise - never an opinion on its own. Stick to conduct and money; never comment on anyone's health, personal circumstances or anything protected. The text you are reading is anonymised, so describe the party by ROLE (the broker, the lawyer, the client) and NEVER invent a name - the real name is put back afterwards from our own records."""

def _run_model(fn, payload_text):
    from core.ai_assist import is_safe_for_ai
    saf=is_safe_for_ai(payload_text)
    if not getattr(saf,"safe",False): return None,"pii-block"
    try:
        raw=fn(getattr(saf,"redacted_text",payload_text),system_prompt=_SYS,response_format="json_object",max_tokens=4096)
        raw=re.sub(r"^```(?:json)?\s*","",raw.strip()); raw=re.sub(r"\s*```$","",raw)
        d=json.loads(raw)
        if isinstance(d,dict):
            for k in ("items","result","matters"):
                if isinstance(d.get(k),list): d=d[k]; break
        return ({int(v.get("i",k)):v for k,v in enumerate(d)} if isinstance(d,list) else {}),"ok"
    except Exception as e:
        return None,repr(e)[:160]

def classify(cands):
    # TWO models, not one (CFO, 16-Sep-2026). This used to run on deepseek-chat
    # through reasoning_complete, whose only quality test is blank-or-broken
    # JSON - so a confidently WRONG verdict passed untouched, which is how a
    # competitor analysis reached the CEO as a CUSTOMER COMPLAINT telling the
    # claims manager to ring a customer. OpenAI and Gemini now judge the same
    # inbox independently and settle what they disagree on; an unresolved split
    # is SHOWN, never averaged away.
    # Guarded like is_routine_signoff_task above: the drivers are host-copied
    # files and the module lives in the backend IMAGE, so a driver copied
    # before a rebuild - or an image rolled back - would raise ImportError
    # here and the CEO would get NO brief.
    try:
        from core.brief_panel import panel_classify
    except ImportError:
        panel_classify=None
    from core.ai_assist import reasoning_complete
    cands=cands[:30]
    payload=json.dumps([{"i":i,"text":c["text"][:850]} for i,c in enumerate(cands)],ensure_ascii=False)
    verds,note=(panel_classify(payload,_SYS) if panel_classify else (None,"panel module not in this image"))
    print("panel:",note)
    n_sig=sum(1 for v in (verds or {}).values() if v.get("significant"))
    n_strong=sum(1 for c in cands if _strong(c["text"]))
    if verds is None or (n_sig==0 and n_strong>0):
        # Both models down, or they agreed on nothing while the deterministic
        # safety-net can see an obvious matter. Never send no brief: fall back
        # to the old single-model path rather than go dark.
        print("panel weak (%s, sig=%s, strong=%s) -> single-model fallback"%(note,n_sig,n_strong))
        g,gst=_run_model(reasoning_complete,payload)
        if g is not None: verds=g; print("fallback used:",gst)
    return (verds or {}),cands

cands=gather(); print("candidates:",len(cands))
verds,cands=classify(cands)
_SEV={"Critical":0,"High":1,"Watch":2}
groups={}; order=[]; forced=0
for i,c in enumerate(cands):
    v=dict(verds.get(i) or {})
    wd=_watchdog_which(c["subject"], c.get("from_name",""), c.get("from_addr",""))
    strong=_strong(c["text"]) or bool(wd)
    if not v.get("significant") and not strong: continue
    if not v.get("significant") and strong:
        forced+=1; v["significant"]=True
        v.setdefault("category",_cat_from(c["text"]))
        v.setdefault("severity","High")
        if not v.get("matter"): v["matter"]=c["ref"]
        if not v.get("why"): v["why"]="Flagged for review (keyword safety-net)."
    # A Consumer Watchdog / Ombudsman / NBFIRA email (by subject or sender) is a
    # reputational/regulatory matter — frame it as such (the firewalled model can't
    # see the sender, so it may call it "routine").
    if wd:
        v["category"]="regulatory"
        if v.get("severity","Watch")=="Watch": v["severity"]="High"
        v["why"]="%s is following up — reputational/regulatory risk; respond promptly."%wd
        base=(v.get("matter") or c["ref"]).strip().rstrip(".")
        if wd.split()[-1].lower() not in base.lower():
            v["matter"]="%s: %s"%(wd, base)
    key=re.sub(r"^(re|fw|fwd)\s*:\s*","",(c["ref"] or "").strip(),flags=re.I).lower() or ("solo-%d"%i)
    if key not in groups: groups[key]=[]; order.append(key)
    groups[key].append((i,c,v))
print("forced by safety-net:",forced,"| threads:",len(order))

items=[]
for key in order:
    grp=sorted(groups[key],key=lambda x:_SEV.get((x[2] or {}).get("severity","Watch"),9))
    i,c,v=grp[0]
    party,ref=_extract_party(c["subject"],c["from_name"],c["from_addr"])
    matter=v.get("matter","") or c["ref"]
    # re-hydrate: if the model said generic "customer/client/complainant", use the real name
    if party:
        matter=re.sub(r"\b([Tt]he |[Aa] )?(customer|client|complainant|policyholder|insured)\b", party, matter, count=1)
    if len(grp)>1:
        who=", ".join(sorted({(cc.get("from_name") or "").split()[0] for _,cc,_ in grp if cc.get("from_name")}))[:80]
        matter += " — %d emails in this thread%s." % (len(grp),(" (from "+who+")") if who else "")
    it=E.QueueItem(id=i,title=c["ref"],from_addr=c.get("from_addr",""),category=v.get("category",""),summary=c["text"],confirmed=True)
    it.severity=v.get("severity","High"); it.party=party or c["from_name"] or c["ref"][:60]
    it.polished_matter=matter; it.polished_why=v.get("why",""); it.reference=(ref or c["ref"])
    items.append(it)
print("significant threads:",len(items))
import html as _eschtml
def render_escalate_row(item, targets, make_token):
    rec = getattr(item, "rec_owner_label", "")
    cells = []
    for label, handle in targets:
        tok = make_token(to=handle, party=item.party, ref=item.reference,
                         matter=(item.polished_matter or item.summary), why=item.polished_why)
        href = "https://omni.alphadirect.co.bw/api/ceo-monitor/escalate/?t=" + tok
        lbl = _eschtml.escape(label)
        if label and label == rec:
            a = ('<a href="%s" style="display:block; text-align:center; text-decoration:none; '
                 'background:#0D1B2A; color:#F4A623; font-family:Arial,Helvetica,sans-serif; '
                 'font-size:12px; font-weight:bold; padding:9px 4px; border:1px solid #0D1B2A; '
                 'border-radius:8px;">%s&nbsp;&middot;&nbsp;rec</a>' % (href, lbl))
        else:
            a = ('<a href="%s" style="display:block; text-align:center; text-decoration:none; '
                 'background:#F1F4F8; color:#0D1B2A; font-family:Arial,Helvetica,sans-serif; '
                 'font-size:12px; font-weight:bold; padding:9px 4px; border:1px solid #D4DBE4; '
                 'border-radius:8px;">%s</a>' % (href, lbl))
        cells.append('<td style="padding:0 8px 8px 0;">%s</td>' % a)
    rows = ""
    for i in range(0, len(cells), 4):
        row = cells[i:i + 4]
        while len(row) < 4:
            row.append('<td style="padding:0 8px 8px 0;"></td>')  # pad the last row to 4 cols
        rows += "<tr>" + "".join(row) + "</tr>"
    return ('<tr><td style="background-color:#F7F8FA; border-top:1px solid #E3E7EC; '
            'padding:14px 24px 16px 24px;">'
            '<div style="font-family:Arial,Helvetica,sans-serif; color:#6C757D; font-size:10px; '
            'letter-spacing:2px; text-transform:uppercase; line-height:14px; padding-bottom:10px;">'
            'Assign &amp; escalate to</div>'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            'style="table-layout:fixed;">' + rows + '</table></td></tr>')

def _esc_row_v6(item):
    return render_escalate_row(item, E.ESCALATION_TARGETS, make_escalation_token)

E.SUBJECT_PREFIX = "CEO Omni Brief"
E._escalate_row = _esc_row_v6

# --- v6: pull today's + tomorrow's real meetings (Calendars.Read, scoped group) ---
def fetch_meetings():
    cid=os.environ.get("CEO_GRAPH_CLIENT_ID");ten=os.environ.get("CEO_GRAPH_TENANT");sec=os.environ.get("CEO_GRAPH_CLIENT_SECRET")
    if not (cid and ten and sec): return None
    try:
        data=urllib.parse.urlencode({"client_id":cid,"client_secret":sec,"scope":"https://graph.microsoft.com/.default","grant_type":"client_credentials"}).encode()
        tok=json.load(urllib.request.urlopen("https://login.microsoftonline.com/%s/oauth2/v2.0/token"%ten,data=data))["access_token"]
    except Exception as ex:
        print("cal token err",repr(ex)[:120]); return None
    today=timezone.localdate(); start=today.isoformat()+"T00:00:00"; end=(today+datetime.timedelta(days=2)).isoformat()+"T00:00:00"
    people=[("You","aiyer@alphadirect.co.bw"),("Arjun","arjuniyer@alphadirect.co.bw"),("Prathap","pganesharajah@alphadirect.co.bw")]
    def _events(who):
        url=("https://graph.microsoft.com/v1.0/users/%s/calendarView?startDateTime=%s&endDateTime=%s"
             "&$select=subject,start,isAllDay,organizer&$orderby=start/dateTime&$top=40")%(who,start,end)
        req=urllib.request.Request(url,headers={"Authorization":"Bearer "+tok,"Prefer":'outlook.timezone="Africa/Gaborone"'})
        try: return json.load(urllib.request.urlopen(req)).get("value",[]),None
        # The reason must be truthy for EVERY exception this can catch. A
        # URLError, a timeout or bad JSON carries no HTTP status, so an empty
        # string default reads back as "no error" and the column falls through
        # to "No meetings". Fall back to the exception class name instead.
        except Exception as ex: return [],(getattr(ex,"code",None) or type(ex).__name__)
    def _ext(e):
        org=((e.get("organizer") or {}).get("emailAddress") or {}).get("address","") or ""
        return bool(org) and "@alphadirect.co.bw" not in org.lower()
    def _fmt(e):
        st=(e.get("start") or {}).get("dateTime","") or ""
        tm="All day" if e.get("isAllDay") else (st[11:16] if len(st)>=16 else "")
        return {"time":tm,"title":(e.get("subject","") or "(no subject)").strip()[:48],"ext":_ext(e),"date":st[:10]}
    today_iso=today.isoformat(); tom_iso=(today+datetime.timedelta(days=1)).isoformat()
    tday={}; pending={}; tcounts={}; tcounts_today={}; tomo_ext=[]
    for label,who in people:
        evs,err=_events(who)
        # ANY failed read, not just a 403. Falling through on a timeout or a
        # 5xx left evs empty and printed "No meetings" against a real name.
        _why=E.unavailable_reason(err)
        if _why:
            tday[label]=[]; pending[label]=_why; tcounts[label]=None; tcounts_today[label]=None; continue
        rows=[_fmt(e) for e in evs]
        # Count today BEFORE the display cap. The column shows six rows at most;
        # counting the shown rows printed "6 today" on a nine-meeting day, which
        # is the cap presented as a fact about the person's diary.
        drows=[{"time":r["time"],"title":r["title"],"ext":r["ext"]} for r in rows if r["date"]==today_iso]
        tday[label]=drows[:6]
        tcounts_today[label]=len(drows)
        trows=[r for r in rows if r["date"]==tom_iso]
        tcounts[label]=len(trows)
        for r in trows:
            if r["ext"]: tomo_ext.append((label,r["title"]))
    headline=""
    if tomo_ext:
        who,tt=tomo_ext[0]
        headline=("%s &mdash; %s <span style=\"background:#fdeccb;color:#8a6b1e;font-size:8px;font-weight:bold;letter-spacing:1px;border-radius:3px;padding:2px 6px;\">EXT</span>"%(who,tt))
    if pending.get("Arjun"):
        headline=(headline+"  &middot;  " if headline else "")+"Arjun's calendar still enabling"
    # small witty Setswana sign-off keyed to today's meetings (local only, no PII sent anywhere)
    titles=" ".join(m["title"].lower() for m in (tday.get("You",[])+tday.get("Prathap",[])))
    QUIPS=[("yoga","PS: yoga maitseboa ano — gagamatsa mmele, mme o se ka wa goga maoto mo ditirong!"),
           ("tea","PS: tee le ba lelapa — se lebale go itapolosa, mothusi wa rona!"),
           ("gathering","PS: phuthego ya losika — iketle, tiro e tla nna teng ka moso!"),
           ("refund","PS: letsatsi la madi a a boelang — a re a tsamaise ka kelotlhoko!"),
           ("payment","PS: letsatsi la dituelo — madi a tsamaye, mme a boele ka nako!"),
           ("claim","PS: di-claim di a re disa — a re di rarabolole pele di re gogela kwa Ombudsman!"),
           ("okr","PS: letsatsi la di-OKR — nna pele ga dipotso!"),
           ("board","PS: kopano tse dikgolo gompieno — nna phatlha!")]
    quip=next((q for kw,q in QUIPS if kw in titles),"PS: letsatsi le tletse dikopano — nna phatlha, o se lape!")
    return {"today":tday,"pending":pending,"today_counts":tcounts_today,"tomorrow_counts":tcounts,"tomorrow_headline":headline,"quip":quip}

def fetch_waiting_on_ceo():
    try:
        from django.contrib.auth import get_user_model
        from core.models import OmniTask
        U=get_user_model()
        ceo=U.objects.filter(profile__title="ceo", is_active=True).first()
        if not ceo: return None
        # Routine staff sign-offs (leave / staff loan / incentive) never belong
        # in a CEO brief - CFO 21-Aug-2026. The rule lives in the repo, tested
        # (hris.exec_signoff_service.is_routine_signoff_task); the fallback is
        # here only so an older backend image cannot kill the whole 04:30 brief
        # on an import error.
        try:
            from hris.exec_signoff_service import is_routine_signoff_task
        except ImportError:   # older backend image: fall back, never lose the brief
            def is_routine_signoff_task(t):
                return ((getattr(t,"source","") or "").startswith("exec_signoff")
                        or (getattr(t,"title","") or "").startswith("Sign off: "))
        today=timezone.localdate(); out=[]
        for t in OmniTask.objects.filter(assignee=ceo, status__in=["pending","in_progress"]).order_by("created_at"):
            if is_routine_signoff_task(t):
                continue
            cd=getattr(t,"created_at",None); age=(today-cd.date()).days if cd else 0
            due=getattr(t,"due_at",None); overdue=bool(due and due < today)
            amt=None
            try:
                pr=t.payment_request
                if pr and getattr(pr,"total",None): amt="P{:,.0f}".format(float(pr.total))
            except Exception: amt=None
            # One-tap decisions (CFO 2026-09-10). The gate and the tokens live in
            # core.ceo_monitor_views where they are tested; a payment task is
            # never decidable from the email.
            try:
                from core.ceo_monitor_views import (DECISION_LABELS,
                                                    is_decidable_task,
                                                    make_decision_token)
                decidable = is_decidable_task(t)
                acts = [(lbl, "https://omni.alphadirect.co.bw/api/ceo-monitor/decide/?t="
                         + make_decision_token(task_id=t.pk, action=act))
                        for act, lbl in DECISION_LABELS] if decidable else []
            except ImportError:   # older backend image: lose the buttons, keep the brief
                decidable, acts = False, []
            out.append({"title":(getattr(t,"title","") or "").strip()[:80],"age_days":age,
                        "overdue":overdue,"amount":amt,"decidable":decidable,"actions":acts})
        return out or None
    except Exception as ex:
        print("waiting probe err:", repr(ex)[:160]); return None


def fetch_notes(audience):
    """This morning's 25-word notes. Rules live in core.brief_note_delivery."""
    try:
        from core.brief_note_delivery import notes_for_brief
        return notes_for_brief(audience)
    except Exception as ex:
        print("notes err:", repr(ex)[:160]); return [], 0


def fetch_money_lens():
    """The four business numbers. Business rules live in core.ceo_money_lens
    (tested); this only calls them and must never break the brief."""
    try:
        from core.ceo_money_lens import money_lens
        return money_lens()
    except Exception as ex:
        print("money lens err:", repr(ex)[:160]); return None


merged=E.merge_related(items)
for it in merged:
    E.extract_facts(it); E.score_item(it); E.recommend(it)
merged.sort(key=lambda i:(-getattr(i,"score",0), _SEV.get(i.severity,9), (i.party or "").lower()))
# AI-quality gate: a matter becomes a CARD only with a real signal (score >= 40).
kept=[it for it in merged if getattr(it,"score",0) >= 40]
capped=kept[:E.MAX_ITEMS]
_scanned=len(cands); _dropped=int(globals().get("_GATHER_DROPPED",0))
d=E.Digest(date=timezone.localdate().strftime("%A, %d %B %Y"),
           raw_count=_scanned+_dropped, distinct_count=len(kept), items=capped,
           overflow=max(0,len(kept)-E.MAX_ITEMS),
           filtered_noise=_dropped, filtered_immaterial=max(0,_scanned-len(kept)))
_notes, _overflow = fetch_notes("ceo")
_blurb = ("%d more note(s) did not fit - they are first in tomorrow's brief."
          % _overflow) if _overflow else None
html=E.render_html(d, meetings=fetch_meetings(), waiting=fetch_waiting_on_ceo(),
                   lens=fetch_money_lens(), notes=_notes,
                   notes_heading="From your executives", notes_blurb=_blurb)
print("SUBJECT:",E.digest_subject(d),"| BYTES:",len(html))
if os.environ.get("CEO_SEND")=="1":
    from django.core.mail import EmailMultiAlternatives
    from django.conf import settings
    to=[x for x in os.environ.get("CEO_TO","aiyer@alphadirect.co.bw").split(",") if x]
    cc=[x for x in os.environ.get("CEO_CC","pganesharajah@alphadirect.co.bw").split(",") if x]
    frm=getattr(settings,"DEFAULT_FROM_EMAIL","omni@alphadirect.co.bw")
    m=EmailMultiAlternatives(subject=E.digest_subject(d),body="(See the HTML version.)",from_email=frm,to=to,cc=cc)
    m.attach_alternative(html,"text/html"); m.send(); print("SENT to",to,"cc",cc)
    # Freeze the notes ONLY now the send has succeeded. Marking them before
    # would tell an author "delivered" for a note the CEO never received.
    if _notes:
        from core.brief_note_delivery import mark_delivered
        print("notes marked delivered:",
              mark_delivered("ceo", [n["id"] for n in _notes]))
