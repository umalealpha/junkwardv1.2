# Deploy — how Omni changes go live safely

**Why this folder exists.** Many chats build features and fixes for Omni at the same time.
When several unfinished changes get pushed to the live Omni together, they fight each other,
the build breaks, and Omni stays offline during working hours — disturbing everyone.

This folder is the single, shared set of rules that every chat and every person MUST follow
so that stops happening. Read it before you deploy anything to Omni.

There are three files here:

| File | What it's for |
|---|---|
| `README.md` (this file) | The rules: the two lanes, and when to use each. |
| `CHANGE-LIST.md` | The live "what's being worked on right now" list. Fill it in **before** you touch the code, so two chats don't collide. |
| `RELEASE-CHECKLIST.md` | The exact step-by-step for making a change live safely (normal + emergency). |

---

## The core idea: two lanes

Every change to Omni goes down **one of two lanes**. You pick the lane per change.

### 🟢 Normal lane — for everything that isn't on fire
New features, tidy-ups, improvements, non-urgent fixes. **Most work is this lane.**

- The change waits until it is finished AND checked.
- It goes live **on its own or in a small planned batch**, at a quiet time (ideally after hours).
- It never rides live alongside another chat's half-finished work.

### 🔴 Emergency lane — for a live payment / payroll / money problem right now
A real problem hurting the business this minute (a payment won't process, payroll is wrong,
a screen is down for staff).

- It **jumps the queue** and goes live immediately.
- **Golden rule: the emergency fix goes out ALONE — nothing else rides with it.**
  This is exactly why it's safe to rush. One small change on its own almost never breaks Omni,
  and if it does, you undo just that one thing in seconds.

---

## The one rule that prevents the long outages

**Never push more than one unfinished change live at the same time.**

The long outages happen because many changes get shoved live *together* and clash.
- Normal lane → changes are combined and checked as one finished thing before going live.
- Emergency lane → one change, by itself, undo-able in seconds.

Either way, Omni is never asked to swallow a pile of untested, conflicting work at once.

---

## Two things that make this actually work

1. **Fill in `CHANGE-LIST.md` before you start editing.** One line: what you're changing, which
   area of Omni, which chat/who, and the date. If someone else is already listed as touching the
   same area — STOP and coordinate. This is how clashes are caught *before* they happen.

2. **Follow `RELEASE-CHECKLIST.md` for the go-live itself.** It uses Omni's built-in instant-swap
   (the new version is built quietly *beside* the running one, then flipped in), so a clean release
   is seconds of downtime, not hours.

---

## Plain-English summary (for the CFO)

- Building happens any time, in any chat — that never disturbs staff.
- **Normal changes wait, get checked together, and go live at a quiet time.**
- **Emergency money/payroll fixes go live now, but always alone and undo-able.**
- A shared list stops two chats quietly editing the same thing.
- Nobody ever pushes a pile of unfinished changes live at once again.

*This is the "free version" — process and discipline, no extra cost. The bigger fix (a separate
test copy of Omni, a "waiting room") can be added later; it costs a monthly server fee and needs
the CFO's OK on the spend.*
