'use client'

/**
 * ChatWidget — small in-app team chatroom (CFO directive 2026-06-09).
 *
 * Floating launcher bottom-right. Shows who's online (presence endpoint), a
 * shared 'general' feed (polled 6s — omni is WSGI, no websockets), and a
 * composer with TWO easy modes (CFO 2026-06-09 "make it easy to use"):
 *   • Chat — plain message; type "@" for a name picker (no guessing usernames).
 *   • Task — pick a person from a dropdown + describe it → creates an OmniTask
 *            on their dashboard. No "/task" syntax to remember.
 * Power users can still type "/task @user do X" directly in Chat mode.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { MessageCircle, X, Send, Users, ClipboardList, Pencil, Trash2, Check, ArrowLeft, Lock, Sparkles } from 'lucide-react'
import {
  getMe, getOnlineUsers, listChatMessages, postChatMessage, getUserEmails,
  editChatMessage, deleteChatMessage, askOmni, type ChatMsg,
} from '@/lib/api'
import { PersonPicker } from '@/components/PersonPicker'

interface RosterUser { username: string; full_name: string }

// Which conversation is open: the shared team room, or a private 1:1 (CFO
// 2026-07-13 "the team should be able to communicate privately"). A DM reuses
// the same message plumbing — the server derives a per-pair room from the two
// user ids, so a person can only ever open a thread they are themselves in.
type Channel = { kind: 'team' } | { kind: 'dm'; username: string; full_name: string }

// One-tap common tasks (CFO 2026-06-09) — pick a person, tap one, Send.
const TASK_SUGGESTIONS = [
  'Approve this', 'Please review', 'Sign off needed', 'Meet me',
  'Call me', 'Send the report', 'Follow up', 'Urgent — see me',
]

export default function ChatWidget() {
  const [open, setOpen] = useState(false)
  const [me, setMe] = useState<string>('')
  const [msgs, setMsgs] = useState<ChatMsg[]>([])
  const [online, setOnline] = useState<RosterUser[]>([])
  const [roster, setRoster] = useState<RosterUser[]>([])
  const [text, setText] = useState('')
  // 'ask' = a private Q&A with Omni's assistant (CFO 2026-08-31), so area/how-to
  // questions stop landing in the team room. Its conversation is local to the
  // asker (askMsgs), never posted to the team or a DM.
  const [mode, setMode] = useState<'chat' | 'task' | 'ask'>('chat')
  const [askMsgs, setAskMsgs] = useState<{ role: 'user' | 'omni'; text: string }[]>([])
  const [askBusy, setAskBusy] = useState(false)
  const [channel, setChannel] = useState<Channel>({ kind: 'team' })
  const [showPeople, setShowPeople] = useState(false)
  // False until the first fetch for the CURRENT channel returns, so we show
  // "Loading…" (not a misleading "No messages yet") in the gap after a switch.
  const [loaded, setLoaded] = useState(false)
  const [assignee, setAssignee] = useState('')
  const [mentionOpen, setMentionOpen] = useState(false)
  const [sending, setSending] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  // Bug e79e4166: edit/delete your own messages.
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editText, setEditText] = useState('')
  const sinceRef = useRef<string>('')
  const feedRef = useRef<HTMLDivElement | null>(null)
  const seenIds = useRef<Set<string>>(new Set())

  useEffect(() => {
    getMe().then((m: unknown) => setMe(((m as { username?: string })?.username) || '')).catch(() => {})
    // full roster for the task picker + @mention (so you never guess a username);
    // falls back to online-only if the directory call isn't permitted.
    getUserEmails()
      .then((r: any) => setRoster(((r?.users || []) as any[])
        .map(u => ({ username: u.username, full_name: u.full_name || u.username }))
        .filter(u => u.username)))
      .catch(() => {})
    // CFO directive 2026-06-11: the chat box must ALWAYS start closed and only
    // open on an explicit click of the bubble — no auto-open. (Previously it
    // auto-opened once per session via sessionStorage 'omni_chat_autoopened',
    // which made it pop up uninvited on first load.)
  }, [])

  const pollMessages = useCallback(async () => {
    try {
      const dm = channel.kind === 'dm' ? channel.username : undefined
      const r = await listChatMessages(sinceRef.current || undefined, dm)
      sinceRef.current = r.server_time
      const fresh = r.messages.filter(m => !seenIds.current.has(m.id))
      fresh.forEach(m => seenIds.current.add(m.id))
      if (fresh.length) setMsgs(prev => [...prev, ...fresh].slice(-300))
      setLoaded(true)
    } catch { /* transient — keep showing Loading… and retry on next poll */ }
  }, [channel])

  // Switching channel (team <-> a person) wipes the feed and refetches that
  // room from scratch, so one thread's messages never bleed into another's.
  useEffect(() => {
    setMsgs([])
    setLoaded(false)
    seenIds.current = new Set()
    sinceRef.current = ''
  }, [channel])

  const pollOnline = useCallback(async () => {
    try {
      const r = await getOnlineUsers()
      setOnline(((r?.users as RosterUser[]) || []))
    } catch { /* transient */ }
  }, [])

  useEffect(() => {
    pollMessages(); pollOnline()
    const a = setInterval(pollMessages, 6000)
    const b = setInterval(pollOnline, 25000)
    return () => { clearInterval(a); clearInterval(b) }
  }, [pollMessages, pollOnline])

  useEffect(() => {
    if (open && feedRef.current) feedRef.current.scrollTop = feedRef.current.scrollHeight
  }, [msgs, open])

  // people list for picker/mention: roster if we have it, else whoever's online,
  // sorted seniors-first (CFO 2026-06-10) then the rest alphabetically.
  // Match on EXACT username — fuzzy first-name matching wrongly elevated
  // Paul Oloye (vs Paul Beka) + Ditso Pako Motlhabane (vs Pako Kago) and
  // missed Oprah (her record first-name is Goabaone). Order = CFO's list.
  const SENIORITY = [
    'arun.iyer', 'arjun.parameswaran', 'pganesharajah',
    'paul.beka', 'ubutale', 'pkago', 'omogomotsi',
  ]
  const rank = (u: RosterUser) => {
    const i = SENIORITY.indexOf((u.username || '').toLowerCase())
    return i < 0 ? 99 : i
  }
  const people = [...(roster.length ? roster : online)]
    .sort((a, b) => rank(a) - rank(b) || (a.full_name || '').localeCompare(b.full_name || ''))
  const mentionMatch = text.match(/@(\w*)$/)
  const mentionList = mentionOpen && mentionMatch
    ? people.filter(u => u.username.toLowerCase().includes(mentionMatch[1].toLowerCase())
        || u.full_name.toLowerCase().includes(mentionMatch[1].toLowerCase())).slice(0, 6)
    : []

  function pickMention(u: RosterUser) {
    setText(t => t.replace(/@(\w*)$/, `@${u.username} `))
    setMentionOpen(false)
  }

  function openDM(u: RosterUser) {
    setChannel({ kind: 'dm', username: u.username, full_name: u.full_name })
    setShowPeople(false); setMode('chat'); setText(''); setErr(null)
  }
  function backToTeam() {
    setChannel({ kind: 'team' }); setShowPeople(false); setMode('chat'); setText(''); setErr(null)
  }

  async function askSend() {
    const q = text.trim()
    if (!q || askBusy) return
    setErr(null)
    const hist = askMsgs.map(m => ({
      role: (m.role === 'user' ? 'user' : 'assistant') as 'user' | 'assistant',
      content: m.text,
    }))
    setAskMsgs(prev => [...prev, { role: 'user', text: q }])
    setText(''); setAskBusy(true)
    try {
      const r = await askOmni(q, hist)
      const reply = r.ok && r.reply
        ? r.reply
        : `I couldn't answer that one${r.reason ? ` (${r.reason})` : ''}. Try the “Report a bug” button, or ask a colleague.`
      setAskMsgs(prev => [...prev, { role: 'omni', text: reply }])
    } finally { setAskBusy(false) }
  }

  async function send() {
    if (mode === 'ask') { await askSend(); return }
    const t = text.trim()
    let body = t
    if (mode === 'task') {
      if (!assignee) { setErr('Pick who the task is for.'); return }
      if (!t) { setErr('Describe the task.'); return }
      body = `/task @${assignee} ${t}`
    }
    if (!body || sending) return
    setSending(true); setErr(null)
    try {
      const dm = channel.kind === 'dm' ? channel.username : undefined
      const m = await postChatMessage(body, dm)
      seenIds.current.add(m.id)
      setMsgs(prev => [...prev, m].slice(-300))
      setText(''); setMentionOpen(false)
      if (mode === 'task') setMode('chat')   // drop back to chat after assigning
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Send failed')
    } finally { setSending(false) }
  }

  // Bug e79e4166 — edit / delete your own messages.
  function startEdit(m: ChatMsg) { setEditingId(m.id); setEditText(m.body); setErr(null) }
  function cancelEdit() { setEditingId(null); setEditText('') }

  async function saveEdit(id: string) {
    const body = editText.trim()
    if (!body) return
    try {
      const updated = await editChatMessage(id, body)
      setMsgs(prev => prev.map(x => x.id === id ? updated : x))
      cancelEdit()
    } catch (e) { setErr(e instanceof Error ? e.message : 'Edit failed') }
  }

  async function removeMsg(id: string) {
    if (!confirm('Delete this message?')) return
    try {
      const updated = await deleteChatMessage(id)
      setMsgs(prev => prev.map(x => x.id === id ? updated : x))
    } catch (e) { setErr(e instanceof Error ? e.message : 'Delete failed') }
  }

  const onlineCount = online.length

  return (
    <>
      <button
        onClick={() => setOpen(o => !o)} title="Team chat"
        style={{ position: 'fixed', right: 96, bottom: 24, zIndex: 9997 }}
        className="w-12 h-12 rounded-full bg-[#0D1B2A] text-white shadow-lg flex items-center justify-center hover:bg-[#162a40] transition-colors"
      >
        {open ? <X className="w-5 h-5" /> : <MessageCircle className="w-5 h-5 text-[#F4A623]" />}
        {!open && onlineCount > 0 && (
          <span className="absolute -top-1 -right-1 bg-[#047857] text-white text-[10px] rounded-full w-5 h-5 flex items-center justify-center">{onlineCount}</span>
        )}
      </button>

      {open && (
        <div style={{ position: 'fixed', right: 24, bottom: 84, zIndex: 9997, width: 350, maxHeight: '72vh' }}
             className="flex flex-col rounded-xl overflow-hidden shadow-2xl border border-[#E5E7EB] bg-white">
          <div className="bg-[#0D1B2A] px-4 py-3">
            <div className="flex items-center gap-2">
              {channel.kind === 'dm' ? (
                <button onClick={backToTeam} title="Back to team chat" className="text-[#cbd5e1] hover:text-white shrink-0">
                  <ArrowLeft className="w-4 h-4" />
                </button>
              ) : (
                <MessageCircle className="w-4 h-4 text-[#F4A623] shrink-0" />
              )}
              <span className="text-white font-semibold text-sm truncate">
                {channel.kind === 'dm' ? channel.full_name : 'Team Chat'}
              </span>
              {channel.kind !== 'dm' && (
                <button onClick={() => setShowPeople(s => !s)} title="Start a private 1-to-1 chat"
                        className={`ml-1 shrink-0 inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium transition-colors ${showPeople ? 'bg-[#F4A623] text-[#0D1B2A]' : 'bg-white/10 text-[#cbd5e1] hover:bg-white/20'}`}>
                  <Lock className="w-3 h-3" /> Private
                </button>
              )}
              <button onClick={() => setOpen(false)} className="ml-auto text-[#cbd5e1] hover:text-white shrink-0"><X className="w-4 h-4" /></button>
            </div>
            <div className="mt-1.5 flex items-center gap-1 text-[11px] text-[#cbd5e1]">
              {channel.kind === 'dm' ? (
                <><Lock className="w-3 h-3" /> Private — only you and {channel.full_name.split(' ')[0]}</>
              ) : (
                <><Users className="w-3 h-3" />
                  {onlineCount === 0 ? 'no one else online' : `${onlineCount} online: ${online.map(u => u.full_name?.split(' ')[0] || u.username).slice(0, 5).join(', ')}`}</>
              )}
            </div>
          </div>

          {/* Private-message people picker (team view only) */}
          {showPeople && channel.kind !== 'dm' && (
            <div className="border-b border-[#E5E7EB] bg-white max-h-56 overflow-y-auto">
              <div className="px-3 py-2 text-[11px] font-semibold text-[#6B7280] uppercase tracking-wide">
                Message privately
              </div>
              {people.filter(u => u.username !== me).map(u => (
                <button key={u.username} onClick={() => openDM(u)}
                  className="w-full text-left px-3 py-2 text-sm hover:bg-[#F1F5F9] flex items-center gap-2">
                  <span className="w-6 h-6 rounded-full bg-[#0D1B2A] text-white text-[10px] flex items-center justify-center shrink-0">
                    {(u.full_name || u.username).slice(0, 1).toUpperCase()}
                  </span>
                  <span className="font-medium truncate">{u.full_name}</span>
                  <span className="text-xs text-[#9CA3AF]">@{u.username}</span>
                </button>
              ))}
              {people.filter(u => u.username !== me).length === 0 && (
                <div className="px-3 py-3 text-xs text-[#9CA3AF]">No one to message yet.</div>
              )}
            </div>
          )}

          <div ref={feedRef} className="flex-1 overflow-y-auto px-3 py-2 space-y-2 bg-[#F8F9FB]" style={{ minHeight: 220 }}>
            {mode === 'ask' && (
              <>
                {askMsgs.length === 0 && (
                  <div className="text-xs text-[#9CA3AF] text-center mt-6 px-3 leading-relaxed">
                    Ask Omni about your work — <span className="text-[#475569]">“How do I apply leave?”</span>, the claims process, a figure on your dashboard. Answered privately, just for you.
                  </div>
                )}
                {askMsgs.map((m, i) => (
                  <div key={i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                    <div className={`max-w-[85%] rounded-lg px-3 py-1.5 text-sm ${m.role === 'user' ? 'bg-[#0D1B2A] text-white' : 'bg-white border border-[#E5E7EB]'}`}>
                      {m.role === 'omni' && (
                        <div className="text-[10px] font-semibold text-[#B45309] mb-0.5 flex items-center gap-1">
                          <Sparkles className="w-3 h-3" /> Omni
                        </div>
                      )}
                      <span style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{m.text}</span>
                    </div>
                  </div>
                ))}
                {askBusy && (
                  <div className="flex justify-start">
                    <div className="max-w-[85%] rounded-lg px-3 py-1.5 text-sm bg-white border border-[#E5E7EB] text-[#9CA3AF] italic">
                      Omni is thinking…
                    </div>
                  </div>
                )}
              </>
            )}
            {mode !== 'ask' && (<>
            {!loaded && msgs.length === 0 && (
              <div className="text-xs text-[#9CA3AF] text-center mt-8">Loading…</div>
            )}
            {loaded && msgs.length === 0 && (
              <div className="text-xs text-[#9CA3AF] text-center mt-8">
                {channel.kind === 'dm'
                  ? `Private chat with ${channel.full_name.split(' ')[0]}. Say hello 👋`
                  : 'No messages yet. Say hello 👋'}
              </div>
            )}
            {msgs.map(m => {
              const mine = m.sender === me
              if (m.deleted) {
                return (
                  <div key={m.id} className={`flex ${mine ? 'justify-end' : 'justify-start'}`}>
                    <div className="max-w-[82%] rounded-lg px-3 py-1.5 text-xs italic text-[#9CA3AF] bg-[#F1F5F9] border border-[#E5E7EB]">
                      message deleted
                    </div>
                  </div>
                )
              }
              const editing = editingId === m.id
              return (
                <div key={m.id} className={`group flex ${mine ? 'justify-end' : 'justify-start'}`}>
                  {/* own-message actions (left of the bubble) */}
                  {mine && !editing && !m.is_task && (
                    <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity mr-1 self-center">
                      <button onClick={() => startEdit(m)} title="Edit" className="text-[#9CA3AF] hover:text-[#0D1B2A]"><Pencil className="w-3.5 h-3.5" /></button>
                      <button onClick={() => removeMsg(m.id)} title="Delete" className="text-[#9CA3AF] hover:text-[#DC2626]"><Trash2 className="w-3.5 h-3.5" /></button>
                    </div>
                  )}
                  <div className={`max-w-[82%] rounded-lg px-3 py-1.5 text-sm ${m.is_task ? 'bg-[#FFF7ED] border border-[#FED7AA]' : mine ? 'bg-[#0D1B2A] text-white' : 'bg-white border border-[#E5E7EB]'}`}>
                    {!mine && <div className="text-[10px] font-semibold text-[#6B7280] mb-0.5">{m.sender_name}</div>}
                    {editing ? (
                      <div className="flex items-center gap-1">
                        <input
                          value={editText}
                          onChange={e => setEditText(e.target.value)}
                          onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); saveEdit(m.id) } if (e.key === 'Escape') cancelEdit() }}
                          autoFocus
                          className="flex-1 rounded px-1.5 py-0.5 text-sm bg-white text-[#0D1B2A] border border-[#94A3B8] outline-none"
                        />
                        <button onClick={() => saveEdit(m.id)} title="Save" className="text-emerald-300 hover:text-emerald-200"><Check className="w-4 h-4" /></button>
                        <button onClick={cancelEdit} title="Cancel" className="text-[#cbd5e1] hover:text-white"><X className="w-4 h-4" /></button>
                      </div>
                    ) : (
                      <div className={`flex items-start gap-1 ${m.is_task ? 'text-[#92400E]' : ''}`}>
                        {m.is_task && <ClipboardList className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />}
                        <span style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                          {m.body}
                          {m.edited && <span className={`ml-1 text-[10px] ${mine ? 'text-white/50' : 'text-[#9CA3AF]'}`}>(edited)</span>}
                        </span>
                      </div>
                    )}
                  </div>
                </div>
              )
            })}
            </>)}
          </div>

          {/* composer */}
          <div className="border-t border-[#E5E7EB] p-2 bg-white relative">
            {/* mode toggle */}
            <div className="flex gap-1 mb-2">
              {(['chat', 'task', 'ask'] as const).map(mo => (
                <button key={mo} onClick={() => { setMode(mo); setErr(null) }}
                  className={`flex-1 h-7 rounded-md text-[11px] font-medium flex items-center justify-center gap-1 ${mode === mo ? (mo === 'ask' ? 'bg-[#B45309] text-white' : 'bg-[#0D1B2A] text-white') : 'bg-[#F1F5F9] text-[#475569]'}`}>
                  {mo === 'chat'
                    ? <><MessageCircle className="w-3.5 h-3.5" /> Message</>
                    : mo === 'task'
                      ? <><ClipboardList className="w-3.5 h-3.5" /> Assign task</>
                      : <><Sparkles className="w-3.5 h-3.5" /> Ask Omni</>}
                </button>
              ))}
            </div>

            {/* task assignee picker + one-tap common tasks */}
            {mode === 'task' && (
              <>
                <div className="mb-2">
                  <PersonPicker value={assignee} onChange={setAssignee} placeholder="Assign to…" />
                </div>
                <div className="flex flex-wrap gap-1.5 mb-2">
                  {TASK_SUGGESTIONS.map(s => (
                    <button key={s} type="button"
                      onClick={() => { setText(s); setErr(null) }}
                      className={`px-2.5 py-1 rounded-full text-[11px] border transition-colors ${text === s ? 'bg-[#0D1B2A] text-white border-[#0D1B2A]' : 'bg-[#F1F5F9] text-[#475569] border-[#E5E7EB] hover:bg-[#E2E8F0]'}`}>
                      {s}
                    </button>
                  ))}
                </div>
              </>
            )}

            {/* @mention autocomplete (chat mode) */}
            {mode === 'chat' && mentionList.length > 0 && (
              <div className="absolute bottom-[64px] left-2 right-2 bg-white border border-[#E5E7EB] rounded-lg shadow-lg max-h-44 overflow-y-auto z-10">
                {mentionList.map(u => (
                  <button key={u.username} onClick={() => pickMention(u)}
                    className="w-full text-left px-3 py-2 text-sm hover:bg-[#F1F5F9] flex items-center gap-2">
                    <span className="font-medium">{u.full_name}</span>
                    <span className="text-xs text-[#9CA3AF]">@{u.username}</span>
                  </button>
                ))}
              </div>
            )}

            {err && <div className="text-[11px] text-[#B91C1C] mb-1 px-1">{err}</div>}
            <div className="flex items-end gap-2">
              <textarea
                rows={1} value={text}
                onChange={e => { setText(e.target.value); setMentionOpen(/@\w*$/.test(e.target.value)) }}
                onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && !mentionList.length) { e.preventDefault(); send() } }}
                placeholder={mode === 'task'
                  ? 'What needs doing?'
                  : mode === 'ask'
                    ? 'Ask Omni a question…'
                    : channel.kind === 'dm'
                      ? `Message ${channel.full_name.split(' ')[0]} privately…`
                      : 'Message…  (type @ to mention)'}
                className="flex-1 resize-none border border-[#D1D5DB] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[#F4A623] max-h-24"
              />
              <button onClick={send} disabled={sending || askBusy || !text.trim() || (mode === 'task' && !assignee)}
                      className="w-9 h-9 rounded-lg bg-[#F4A623] text-[#0D1B2A] flex items-center justify-center disabled:opacity-50">
                <Send className="w-4 h-4" />
              </button>
            </div>
            <div className="text-[10px] text-[#9CA3AF] mt-1 px-1">
              {mode === 'task'
                ? 'Pick a person + describe it → lands on their dashboard.'
                : mode === 'ask'
                  ? 'Private answer from Omni, using our SOPs + your dashboard. Not sent to the team.'
                  : 'Enter to send · type @ to mention · switch to “Assign task” to delegate.'}
            </div>
          </div>
        </div>
      )}
    </>
  )
}
