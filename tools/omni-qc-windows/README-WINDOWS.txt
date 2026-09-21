====================================================================
  OMNI QC  —  the /qctest checker, for the Windows PC
====================================================================

WHAT THIS THING IS (in one paragraph)
-------------------------------------
It is a robot that opens Omni in a real browser, as a staff member who is
only allowed to LOOK, and walks through the screens one by one. On every
screen it asks six questions: did the page actually load, or is it blank?
Did any button that should be there go missing? Did the page crash behind
the scenes? Is it showing junk a human should never see (raw web links,
"undefined", long code blobs)? Is the data stale — an "as of" date weeks
old, or a total that has not moved for days? And is the page suddenly much
slower than it usually is? It saves a photo of every screen, then a cheap
AI writes one plain-English paragraph saying what is broken and what to
check first. It can also click a named button and tell you what happened.

It can never move money, approve anything, or change data. The login it
uses is refused by the server for anything except reading.

TWO NAMES, TWO DIFFERENT THINGS
-------------------------------
  /qc      = run the robot.  "Check the dashboard." Gives PASS/FAIL + photos.
  /qctest  = the bigger routine. In a chat where Claude just built things,
             /qctest lists everything that was built, runs the robot on each
             one, looks up the best fix for whatever failed, asks Fable for
             a second opinion, and hands you a table:
             what you asked for | does it work | what it means for you |
             recommended fix | how long | how risky.
             Then it asks you yes/no which fixes to do.

INSTALL — THREE STEPS
---------------------
  1. Install Node.js if the PC does not have it:  https://nodejs.org
     (the big green LTS button, click Next through everything)

  2. Copy this whole folder onto the Windows PC. Anywhere is fine.
     Suggested:  C:\Users\<you>\OmniQC

  3. Double-click  SETUP.bat  and wait about three minutes.

SETUP.bat finishes by PROVING it works, in front of you:
  - it runs a fake broken page. That MUST fail. If it passes, the robot is
    blind and you must not trust it.
  - it runs the real Omni dashboard. That MUST pass.
Both lines are printed at the end in plain words.

USING IT AFTERWARDS
-------------------
  qc.bat                     check every screen it knows
  qc.bat dashboard           check one screen
  qc.bat uc_all              check the UniCoin portal only
  qc.bat all --report        also send the verdict to your phone (Telegram)
  click.bat "/dashboard" "Refresh"     make it click a button and report back

  Photos of every screen land in:  %TEMP%\omni-qc

THE CLEVER ONE  (explore.bat)
-----------------------------
qc.bat looks at a screen. click.bat clicks a button you name. explore.bat takes
a GOAL IN PLAIN WORDS and works out the clicks itself:

  explore.bat "/dashboard" "go to the Commissions screen"
  explore.bat "/commissions/brokers" "find the broker with the worst loss ratio"
  explore.bat --selftest      checks its own safety guard, no browser needed

It needs an AI to think with. By default it uses one running ON THE PC (Ollama),
so nothing about the screen leaves the machine. If that PC has no Ollama it says
so plainly and stops - it will never quietly send anything to an outside model.
Add  --brain gateway  to use the cheap DeepSeek/Gemini gateway instead.

Three things keep it safe:
  1. Same read-only login - the server refuses it any change.
  2. Its own blocklist - it refuses to even TRY approve, pay, post, delete,
     save, export, run and 20 more. Looking, filtering and navigating are fine.
  3. It never sees the page's data. It sees the address, the headings and the
     button labels, with emails and long numbers masked out.

One honest limit: ask it the same thing twice and it may take two different
routes. So it is for EXPLORING and finding things - never treat its answer as
proof. Whatever it finds, confirm with qc.bat, which is repeatable.

In Claude Code on the Windows PC you can now type  /qc dashboard  or  /qctest
— SETUP.bat installed both.

WHAT IS IN THE BOX
------------------
  SETUP.bat          the one-click installer + the proof run
  qc.bat             run a check
  click.bat          click a named button on a screen
  explore.bat        give it a goal in words and let it find its own way
  engine\qc.mjs      the robot itself (the checks live here)
  engine\act.mjs     the button-clicker
  engine\explore.mjs the clever layer + its safety guard
  engine\qa-token.ps1 keeps the read-only login fresh (it expires every 15 hours)
  skills\qc          the /qc instructions for Claude Code
  skills\qctest      the /qctest instructions for Claude Code
  secrets\           your READ-ONLY Omni key, IF this copy of the folder came
                     across by USB. The copy that lives in git does NOT carry
                     it - there, SETUP.bat asks you to paste the key once.
                     Get it on the Mac with:   cat ~/.omni-qa-key | pbcopy
                     Keep the key off email and off shared drives. It cannot
                     change anything, but it is still a key.

TWO OPTIONAL EXTRAS
-------------------
  Phone alerts:  put your Telegram bot token in  %USERPROFILE%\.omni-qc-tg-bot
                 and your chat id in  %USERPROFILE%\.omni-qc-tg-target
                 then add --report to any run. Without them nothing breaks —
                 the message is just printed on screen instead.

  The plain-English paragraph: it comes from DeepSeek/Gemini through the local
  gateway if the PC has one (key in %USERPROFILE%\.omni-gateway-key), or from a
  local Ollama, or not at all. Without any of them the PASS/FAIL list and the
  photos still work exactly the same. No paid Anthropic key is ever used.

ADDING A NEW SCREEN TO WATCH
----------------------------
Open engine\qc.mjs, find the TARGETS list near the top, copy a line:

    my_screen:  { route: '/some/screen', expect: ['Refresh'] },

Rule that keeps it honest: only add a screen you have SEEN load with your own
eyes. A guessed address shows up blank and the robot starts crying wolf about
a screen that was never there.

WHEN SOMETHING GOES WRONG
-------------------------
  "Node.js is not installed"      -> step 1 above.
  "NO_KEY"                        -> secrets\omni-qa-key.txt did not copy across.
  Everything fails at once        -> the read-only login expired and could not
                                     refresh. Check the PC is online, then run
                                     SETUP.bat again.
  One screen says BLANK           -> open its photo in %TEMP%\omni-qc first.
                                     Nine times out of ten the address in
                                     TARGETS is wrong, not the screen.
  The fake broken page PASSES     -> stop. The robot is blind. Nothing else in
                                     that run means anything.
