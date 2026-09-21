# Morning Ops — Prevention Rules

Dated entries only. Each entry: the mistake, the plain-English rule to stop it happening again, and the one command that catches it. Written by the 4am cloud ops check, from commit messages and the MACHINE-TALK.md shared log.

---

## 2026-09-11

**Source window:** MACHINE-TALK.md lessons from the last two weeks, plus every commit on `main` dated 2026-09-10 (this clone's own git history only goes back to 2026-09-10, so commit-message mining could not cover a full 14 days — see the morning report for that day).

### 1. A button that looks fine but is quietly dead (hand-built login header)
**What keeps happening:** Someone writes a direct web request from scratch instead of using the shared "how do I talk to the server" helper, and hand-types the login header on it. The header is subtly wrong — it works for some logins but not others (for example, single-sign-on users). Nothing in the automated tests catches this, because the tests don't check real logins. The button then sits on the live site looking completely normal and does nothing when pressed.
**Seen again:** Yes — twice in two days. Once on 10 September (the "I'm running late" button), and again on 11 September (a new pop-up message feature).
**Prevention rule:** Never hand-type the login header on a request. Always use the shared helper that already knows how to log every kind of user in correctly.
**One command to catch it:** `grep -rn "Authorization.*Bearer\|Authorization.*Token" frontend/src --include="*.ts" --include="*.tsx" | grep -v "lib/api.ts"` — any hit outside the shared helper file is a hand-built header and needs fixing before it ships.

### 2. A save that doesn't tell the page what it just saved (missing id)
**What keeps happening:** A new record gets created, but the response sent back to the screen leaves out the record's own reference number. The screen doesn't know what it just made, sends the user to a broken page, and — because the flow depends on that reference number — the record never reaches the person who was supposed to approve it.
**Seen again:** Yes — on 10 September this broke Purchase Orders for everyone: a new purchase order saved, but never reached its approver. The same day it was found lurking in three more places (Petty Cash, Fixed Assets, Goods Receipts) before anyone pressed the button and noticed.
**Prevention rule:** Every "create a new record" screen must get the record's reference number back immediately, and that must be checked before the code ships, not discovered by a user hitting a dead page.
**One command to catch it:** `python manage.py test core.tests.test_create_serializers_return_id` — this test already exists in the repository and walks every "create" screen checking the reference number comes back; run it before every deploy.

### 3. A date read as UTC when it should be Botswana's date
**What keeps happening:** Code asks the computer's clock for "today's date" using the international standard clock (UTC) instead of Botswana's own clock. Botswana is two hours ahead, so for two hours every night (22:00 to midnight UTC), the computer's idea of "today" is wrong — it still thinks it's yesterday. Anything that checks "did this happen today" turns falsely red in that window.
**Seen again:** Yes — a large fix went out on 10 September (232 lines across 140 files), and the same mistake still caused fresh problems on 11 September in a different corner of the code, including a safety check that was silently looking at the wrong date and could never have caught the bug it was built to catch.
**Prevention rule:** Never ask the computer's own clock for "today" when the answer needs to be Botswana's today. Always go through the one shared "Botswana date" function.
**One command to catch it:** `python manage.py test core.tests.test_botswana_clock` — the repository's own clock guard test; run it, and also re-run the full test suite once between 22:00 and 23:59 UTC specifically, since that is the only window the bug shows itself in.

### 4. Treating "the code is on the server" as "the change is live"
**What keeps happening:** The updated code is pulled onto the live server, but the running program itself (the "image") is never rebuilt from that code. The old, unfixed version keeps running. Everyone believes the fix shipped because the file is visibly there — it just isn't being used.
**Seen again:** Yes — on 10 September, a reporting page was fixed in the code but the running program was never rebuilt, so the page kept failing in production until someone rebuilt it properly during a later deploy.
**Prevention rule:** "The file is on the server" is not proof of anything. A change only counts as live once you have checked the actual working page or a live web address and watched it behave correctly with your own eyes (or a screenshot).
**One command to catch it:** `curl -s -o /dev/null -w "%{http_code}\n" https://<live-site>/<the-changed-page-or-api>` immediately after any deploy — confirm it returns success (200), not a routing error (404), against the real live address.

---

## 2026-09-15

**Source window:** commit messages and MACHINE-TALK.md entries dated 2026-09-01 through 2026-09-15 (the last 14 days), with the closest attention on 2026-09-14, Botswana time.

### 1. A date read on the world clock (UTC) when it should be Botswana's own clock — STILL HAPPENING
**What keeps happening:** Somewhere in the code, "today" or "how old is this" is worked out from the computer's own UTC clock instead of Botswana's clock, which is two hours ahead. For those two hours each night, anything that checks "is this still within the same day" gets the wrong answer.
**Seen again:** Yes — again yesterday, and this is now at least the fourth time in two weeks. A payment-ageing check aged every open payment request one day too early during that nightly window, sending it to the CFO for escalation before it was actually due. It was caught by a test going red, not by a person, right around 22:20 UTC. The same wrong pattern was found and fixed in one file, then found again, separately, in a second file used for the CFO's own daily payment summary. Three more places with the exact same shape of bug have been found but not yet fixed.
**Prevention rule:** Every place that asks "what day is it" or "how many days old is this" must go through the one shared Botswana-clock helper — never the computer's own raw clock, and never a raw date taken straight off a stored timestamp without converting it to Botswana time first.
**One command to catch it:** `python manage.py test core.tests.test_botswana_clock` plus a full test-suite run scheduled between 22:00 and 23:59 UTC — that narrow window is the only time this bug shows itself.

### 2. Believing code is live because it was pulled onto the server — STILL HAPPENING
**What keeps happening:** The new code reaches the live server and the server's own "what am I running" check shows the new version — but the actual running program was never rebuilt and restarted properly, so part of it is still serving the old, broken behaviour underneath a status page that looks correct.
**Seen again:** Yes — again yesterday. The server's health check correctly named the new version, but a payment-details screen was still, for a short time, being served by the older, unfixed copy running alongside it. It was only caught because someone checked the actual answer the screen gave, not just the version number.
**Prevention rule:** A version number matching on a status page is not proof a change is live. Only trust a change is live once every copy of the program running behind it has finished switching over, and you have checked the actual screen or answer, not just the label.
**One command to catch it:** After every deploy, call the real page or API repeatedly for at least a minute and confirm every single response — not just the first one — reflects the fix, since old and new copies can serve requests side by side during a rollout.

### 3. Grading your own homework — a test written by the same person who wrote the feature, passing because it shares the same blind spot
**What keeps happening:** Someone writes both a feature and the automatic check that is supposed to prove the feature works. Because the same person wrote both, the check quietly inherits the same wrong assumption the feature has, so it happily passes even when the actual product is broken or lying.
**Seen again:** Yes — repeatedly. It was the single biggest lesson from an outside tester's review two weeks ago, and it showed up again yesterday and today: a new staff how-to-guide was built from one team member's guess at how the software's screens work, and a home-made checker was built to match that same guess — so the checker passed 13 out of 13 times while the guide itself was wrong in dozens of places, because neither the guide nor its checker ever actually looked at the real screens.
**Prevention rule:** A check that only compares a document against another document written by the same person proves nothing. At least one check on any new "does this describe/match the real system" feature must be built by reading the real, live thing directly — not by asking a second helper to agree with the first one's notes.
**One command to catch it:** There is no single shared command for this yet (each case has needed its own one-off script that reads the live screens or the live database directly). The practical check for a person: before accepting "13/13 passed" or similar, ask "did this check ever read the real system, or only my own notes about it?" — if the answer is only the notes, treat it as unproven.

### 4. Two of the earlier known repeat offenders — could not confirm either way for yesterday
The 11 September list also named (a) a button wired with a hand-built login header, and (b) a save that doesn't hand back the new record's own reference number. Neither showed up by name in yesterday's commit messages or MACHINE-TALK entries in the material available to this check — that is not the same as saying they didn't happen; it means this check found no evidence either way and is saying so rather than guessing.
## 2026-09-12
**Source window:** every commit on `main` dated 2026-09-11 Botswana time (64 commits, full history this time — last run's clone only went back one day), plus MACHINE-TALK.md entries from both machines covering the last two weeks.
### 1. A button that looks fine but is quietly dead (hand-built login header)
**Seen again:** Yes — a third time. A new pop-up messaging feature for the CFO and managers shipped yesterday sending its own hand-typed sign-in header instead of using the shared helper. It answered "not authorised" for every single person, in both ways people sign in, and nothing in the automated checks caught it — only a human clicking the button found it dead. The team itself now calls this "the third shipping of this class."
**Prevention rule:** unchanged — never hand-type the sign-in header on a request; always go through the shared helper.
**One command to catch it:** `grep -rn "Authorization.*Bearer\|Authorization.*Token" frontend/src --include="*.ts" --include="*.tsx" | grep -v "lib/api.ts"`
### 2. A save that doesn't tell the page what it just saved (missing id)
**Seen again:** No fresh incident found dated yesterday specifically. But because it has now shipped broken three separate times, the team wrote it into a new mandatory pre-push checklist yesterday, alongside five other repeat mistakes (this list, the login-header mistake, and four more).
**Prevention rule:** unchanged — every "create a new record" screen must get the record's reference number back and that must be checked before it ships.
**One command to catch it:** `python manage.py test core.tests.test_create_serializers_return_id`
### 3. A date read in Botswana's time zone as if it were the international standard clock (UTC)
**Seen again:** Yes — repeatedly. At least three separate date-related fixes landed yesterday alone, and one of them was the same mistake showing up a third time in a row: a safety test was quietly checking the wrong date and could never have caught the very bug it was built to catch. This is the single most repeated mistake found in the last two weeks of work — it has recurred on at least six different days.
**Prevention rule:** unchanged — never ask the computer's own clock for "today" when Botswana's today is meant; always go through the one shared Botswana-date helper.
**One command to catch it:** `python manage.py test core.tests.test_botswana_clock` — and re-run the full suite once between 22:00 and 23:59 UTC, the only window this bug hides in.
### 4. Treating "the code is on the server" as "the change is live"
**Seen again:** No fresh incident found dated yesterday specifically (the big example was two days ago, on 10 September, when a whole report page kept failing because the running program was never rebuilt from the fixed code). It stays in yesterday's new mandatory pre-push checklist as a standing risk.
**Prevention rule:** unchanged — a change only counts as live once someone has watched the actual page or address behave correctly, not just seen the file sitting on the server.
**One command to catch it:** `curl -s -o /dev/null -w "%{http_code}\n" https://<live-site>/<the-changed-page-or-api>` right after every deploy.
### 5. One person's shortcut command wipes another person's unsaved work (new this week)
**What happened:** Yesterday one machine ran a wholesale "throw away everything and reset to the shared copy" command in the same shared folder another person's unfinished work was sitting in, and it deleted 20 of their unsaved files. It was recovered from that person's own saved copy, so nothing was permanently lost, but it should never have been possible.
**Why it keeps nearly happening:** the same shared folder already had 11 sets of "unsaved work" tucked away for safekeeping from past near-misses, three of them labelled exactly this kind of close call — so this was not a one-off, it is a standing hazard of everyone working in the same shared folder.
**Prevention rule:** never run a "throw away everything" command (a hard reset, a forced clean, a forced checkout) in a shared working folder without first checking nobody else's unsaved work is sitting in it.
**One command to catch it:** a new guard for exactly this was built yesterday and is waiting to be turned on — it is not active yet (see the machine-log section of today's report). Once it is on, the check is automatic and needs no separate command.
## 2026-09-13
**Source window:** MACHINE-TALK.md entries for 11–12 September, plus every commit on `main` this clone can actually see (2026-09-11 13:48 to 2026-09-13 02:47, Botswana time — about 37 hours). This clone's history is even shorter than yesterday's report warned about — it does not reach back a full 14 days — so this section leans on the shared log, not on commit mining, and says so rather than guessing.
### Checking the four mistakes written down on 11 September, against yesterday's work
- **(a) Hand-built login header:** No new confirmed case found in yesterday's work or the shared log. Caveat: the catch-command for this one is blunt — run today, it still lists dozens of files that already carry the correct fix (they check for the sign-in-without-password case before picking the header), so on its own it cannot prove nothing new broke. Worth sharpening, not trusting blindly.
- **(b) A save that doesn't return its own reference number:** No new confirmed case found. Could not run the existing test myself — this check has no copy of the actual finance program installed, so "the test still passes" is taken on the log's word, not verified first-hand. The test file itself is still present in the repository.
- **(c) A Botswana date read as UTC:** Yes, again — see item 1 below. Third time in the log, and it bit twice in one day this time.
- **(d) Code on the server treated as a finished deploy:** Yes, in a new shape — see item 2 below.
### 1. The clock keeps reading the wrong country's "today" (again)
**What keeps happening:** The same mistake as 11 September's item 3, but this time it hid inside two different safety tests rather than the product itself: a test built "30 minutes ago" and landed on yesterday's date without meaning to, and a second test asked the computer's own clock instead of Botswana's, so it disagreed with the real system — which was right — for two hours every night.
**Seen again:** Yes — twice in one working day (12 September), described in the log as "the same old enemy" for the third time. One of the two tests was passing for the wrong reason: it was checking a date the underlying job never even writes, so it could never have caught a real fault.
**Prevention rule:** No test anywhere in the codebase may ask the computer's own clock for "today" or "now." Every test must go through the one shared Botswana-date helper, the same rule as the product code.
**One command to catch it:** `python manage.py test core.tests.test_botswana_clock` — this guard was itself widened yesterday to scan test files too, not just product code; run it, especially once between 22:00 and 23:59 UTC.
### 2. A safety check that can quietly not run at all, and nothing says so
**What keeps happening:** A step meant to protect a deploy — a post-deploy check, or a scheduled report being installed — can fail to run at all because of a small, boring reason (a file without permission to run, a step nobody wired in), and nothing prints a warning when it's skipped. A check that quietly never runs looks exactly like a check that ran and found nothing wrong, which is worse than having no check.
**Seen again:** Yes. Earlier in the week a post-deploy safety check was skipped because its file wasn't marked as runnable. This cloud check looked for the newer version of the same problem this morning and found it still live: the tool that installs new scheduled reports onto the production server is confirmed, right now, NOT wired into the main deploy script — so a new scheduled report can ship in the code, the deploy can say it succeeded, and the report will never actually fire until someone remembers to install it by hand.
**Prevention rule:** Anything that can be skipped must print that it was skipped, loudly, and that must count as a failure — never a silent pass.
**One command to catch it:** `grep -q "install-crons.sh" infra/skills/fabe/scripts/deploy_ssm.py && echo OK || echo "MISSING: deploy does not install scheduled jobs"` — this printed MISSING when run this morning. Recommend asking the Windows machine to wire this in before the next scheduled report ships.
### 3. A "no problem found" message built entirely on fake test data
**What keeps happening:** A safety guard gets added in one place but not in the other place that shows the same result to a person, and every automated test protecting it uses made-up stand-in data — so all the tests pass, but none of them is able to notice that the real guard is missing from the second place. A person looking at the screen then sees "all clear" at the exact moment the honest answer is "we don't know."
**Seen again:** Yes — on the weekly failed-payment-collection report. The "don't claim all-clear when we can't tell" guard was added to the job that emails the report, but not to the page a person can open directly — so the page kept showing a green tick and "nothing to report" on the same day the emailed version was correctly refusing to send anything.
**Prevention rule:** Any message that can say "all clear" must be exercised by at least one test using real, not fake, data — and the guard must be added to every place that shows the result to a person, not just the first one built.
**One command to catch it:** `grep -rn "book_is_reporting" realpay/*.py | grep -v test` — should show the guard in both the emailed report and the on-screen version; if it only appears once, the other one can still lie to whoever is looking at it.
### 4. Typing code straight into a Windows command window breaks it
**What keeps happening:** On the Windows machine, pasting a block of program code between quote marks in the command window silently turns the code's own "new line" markers into real line breaks, which corrupts the code before it can even run.
**Seen again:** Yes — at least four more times on 12 September alone, on top of earlier days; the log calls it a "trap" each time it recurs.
**Prevention rule:** On the Windows machine, never paste program code between quote marks in the command window. Save it to its own file first, then run that file.
**One command to catch it:** None yet — this is a typing habit in the moment, not something the finished code carries evidence of afterwards. Written down here so it stays visible rather than being solved by a check that doesn't exist.
## 2026-09-14
**Source window:** every commit on `main` dated 2026-09-13 (Botswana time), plus the last 14 days of commit messages (2026-08-31 to 2026-09-13) once the clone's history was widened past the earlier shallow-clone limit, plus the lessons written into MACHINE-TALK.md. Note: the shared MACHINE-TALK.md log itself has not been updated by either machine since 2026-09-12 — see the morning report for that day's full detail — so today's mining leans more on commit history than usual.
### 1. The computer's own clock is asked for "today" instead of Botswana's clock
**What keeps happening:** Code (or a script) calls the server's own date/time instead of going through the one shared "what is today in Botswana" helper. Botswana is two hours ahead of the world clock, so for part of every night the computer's own answer is wrong, and anything checking "did this happen today" can misfire.
**Seen again:** Yes — a 232-line, 140-file fix went out on 10 September, a dedicated guard test was added on 11 September, and on 13 September it turned up again in a different corner: a Sunday brief-writing script (`ceo_sunday_driver.py`) was still reading the world clock. That script lives on the host machine, not inside the delivered program, which is why the earlier fixes never reached it.
**Prevention rule:** Never let any script — inside the delivered program or sitting on the host machine — ask for "today" directly. Everything must go through the one shared Botswana-date helper, with no exceptions for "small" host-side scripts.
**One command to catch it:** `python manage.py test core.tests.test_botswana_clock` for code inside the program, plus, on the live machine, `grep -rn "datetime.date.today()\|datetime.now()" /opt/ceo-monitor` — that second check is necessary precisely because host-side scripts do not run through the normal test suite at all.
### 2. A file is fixed in the shared code, but the live machine keeps running the old copy
**What keeps happening:** A change is written, tested, reviewed and merged, and everyone treats that as "it's live now." But some files — scripts that sit directly on the host machine rather than inside the delivered program — are never touched by a normal rebuild. The old, unfixed copy keeps running with nobody noticing, because every check that matters (the pipeline, the tests) was looking at the repository, not the machine.
**Seen again:** Yes — on 13 September the CFO's own morning-brief script was rewritten to show people-decisions instead of payment-decisions, merged and deployed cleanly, and the live machine kept sending the old version anyway, because that particular file lives outside the delivered program.
**Prevention rule:** Any script that lives on the host machine rather than inside the delivered program must be actively copied across as its own deploy step — never assumed to "come along for the ride" with a normal build.
**One command to catch it:** `sudo bash /opt/alpha-finance/infra/install-crons.sh --check` — run this straight after any deploy; it compares every host-side script against the repository copy and reports any that still differ.
### 3. A one-person approval link is sent to more than that one person
**What keeps happening:** A screen sends someone a personal, single-tap "Approve" link meant for them alone, but the underlying email helper defaults to also copying a shared mailbox unless the sender explicitly switches that off. Anyone who reads the shared mailbox can then use another person's personal approval link.
**Seen again:** Yes — on 13 September a commission-approval email, including the CFO's own final-approval link, was going out copied to the shared board mailbox by default; a similar staff-loan email built in the same week got this right by explicitly switching the copy off.
**Prevention rule:** Any email carrying a personal, one-tap approval link must have the "also copy the shared mailbox" option explicitly and visibly switched off in the code — never rely on the default.
**One command to catch it:** `grep -rn "send_html_with_cfo_cc(" --include="*.py" | grep -v "cc_cfo=False"` — any hit is a personal-approval email still relying on the (shared-mailbox-on) default.
### 4. The same charge, import row, or payment risks being processed twice
**What keeps happening:** A record — a bank statement line, a refund, an imported charge — gets applied a second time because nothing at the database level stops it, only a check in the screen that can be bypassed or missed. This pattern turned up eleven separate times across refunds, bank statement imports, broker records and payment screens in the last two weeks.
**Seen again:** Yes, repeatedly — most recently 11 September (a refund that could still be paid twice, and a statement import guard to stop the same bank charge loading twice).
**Prevention rule:** Every place that records money moving in or out needs its own database-level "you already have this one" guard, not just a check the screen happens to run — and a test that proves submitting the same thing twice is refused the second time.
**One command to catch it:** `python manage.py test --pattern="test_*duplicate*"` — runs the repository's existing scattered duplicate-guard tests as one group; run it before shipping anything that touches money.

---

*This file is appended to by the automated 4am ops check. Each new day's findings go in a new dated section above this line.*
