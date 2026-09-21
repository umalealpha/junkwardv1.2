# Alpha Nexus — getting back onto Google Play

Everything in the app is fixed and built. Only access and paperwork remain.
Follow these in order. Do not skip ahead — step 4 depends on Google replying.

---

## Where we are

Google rejected the app on **25 July 2026** because the installed icon and the app
name did not match the store listing. That is fixed. So are two other Play
requirements that were about to bite (16 KB memory pages, and target API 36).

The app **cannot be uploaded** because the signing key password from 30 June is
lost. Google only accepts builds signed with that exact key, so the key has to be
replaced — which only the Play Console **account owner** can request.

---

## Step 1 — Arjun makes you the owner  *(waiting on him)*

Emailed 25 July, Arjun with Medu copied, with screenshots showing the reset
button greyed out and "You need permission".

He needs to do **one** of:
- make you account owner of the developer account (best — you stop needing him), or
- grant your account the "Manage Play App Signing" permission, or
- request the reset himself.

**Nothing below can start until this happens.**

---

## Step 2 — Create the new key  *(2 minutes, you)*

**Save the password in your password manager BEFORE you run this.** The script
will ask you to confirm you have, and stops if you haven't. That is deliberate:
losing the last one cost three weeks.

```powershell
powershell -ExecutionPolicy Bypass -File "C:\Users\PrathapAsus\work\af-nexus-play\mobile\alpha-rewards-health\create-upload-key.ps1"
```

It asks for the password twice, creates the key, and puts the file Google needs on
your Desktop:

> **Alpha-Nexus-upload_certificate.pem**

It also prints a fingerprint — keep it, you can compare it in Play Console later.

The script refuses to run twice, so it can never quietly destroy a key you already
made.

---

## Step 3 — Ask Google for the reset  *(you, in Play Console)*

1. Open Play Console → **Test and release** → **Setup** → **App integrity**
2. Under **Request upload key reset**, click it (it will be clickable once step 1 is done)
3. Attach **Alpha-Nexus-upload_certificate.pem** from your Desktop
4. Reason: the previous upload key password was lost
5. Submit

Google usually replies **within a few days**. They email you when it is active.

---

## Step 4 — Build and upload  *(only after Google confirms)*

```powershell
powershell -ExecutionPolicy Bypass -File "C:\Users\PrathapAsus\work\af-nexus-play\mobile\alpha-rewards-health\build-aab.ps1"
```

The file appears at:
`android\app\build\outputs\bundle\release\app-release.aab`

Copy it to your Desktop, then upload it in Play Console under
**Test and release → Testing → Internal testing → Create new release**.

Building before Google confirms the reset produces a file Play will reject, so wait
for their email.

---

## Step 5 — Fix the store listing  *(you, and this matters)*

The rejection was about the app not matching the listing, so the listing has to be
right too. In Play Console → **Grow users** → **Main store listing**:

- **App name** must read exactly **Alpha Nexus**
- **Screenshots** — replace any that show the old "Alpha Rewards" heading
- Check **translated or custom listings** carry the same name and icon (the rejection was tagged `en-US`)
- **Retire old releases on every track** — they still contain the old icon and name, and a reviewer can open them

No appeal is needed. A compliant new version replaces the rejection, and it is
faster than the 7-day appeal queue.

---

## Step 6 — Test it on a real phone  *(before telling anyone it works)*

The app has **never been run** since the upgrade — this machine has no phone or
emulator, so it is only proven to build, not to work. Install from the internal
testing track and confirm:

- it opens without crashing
- the launcher icon is the orange heart and the name reads Alpha Nexus
- Health Connect asks permission, and today's step count appears
- the top of the screen is not hidden under the clock

Do not release wider until someone has actually done this.

---

## Still open, not blocking

- **The privacy page** `https://omni.alphadirect.co.bw/m/privacy` is live and the app
  now points at it. Make sure the **same address** is in the Play Console listing's
  privacy-policy field.
- **Health apps declaration + Data safety form** — required because the app reads
  step data. Console-side, worth checking before resubmission.
- **Medu's Play Console invitation** from 22 July is still unaccepted.
- **React Native 0.82** removes the old architecture this app still uses. The next
  upgrade will need a build machine with an Android NDK.

---

## What is already done and verified

| Requirement | Evidence |
| --- | --- |
| Icon matches the store listing | The icon in the built app matches Google's own flagged screenshot to within 2.6/255 average difference |
| App name is "Alpha Nexus" | Compiled resources read "Alpha Nexus"; the old name is absent |
| 16 KB memory pages | 20 of 20 sixty-four-bit libraries aligned; the rejected build scored 0 of 18 |
| Target API 36 | Manifest shows `targetSdkVersion=36`, `minSdkVersion=26` |
| Version | `versionCode 4`, `versionName 1.0.1` (published is 2) |
| Signing wiring | Proven with a throwaway key, which was then deleted |
