# Omni — iPhone staff app

Wraps the live staff app at `https://omni.alphadirect.co.bw/app` in a native iOS shell, and
adds the things iPhone cannot do from the web:

- **Alerts** — Apple's own push service (APNs). Web push does **not** work inside an app's web
  view on iPhone; it only works in a site added to the Home Screen from Safari. Without this the
  morning summary and approval nudges would silently stop for every iPhone user.
- **Face ID unlock** — off by default. The page turns it on with
  `webkit.messageHandlers.omni.postMessage({action:'setLock', on:true})`. It is deliberately not
  forced: a forced biometric locks out anyone whose face fails, and blocks a store reviewer.
- **Camera and photo library** — with the usage descriptions Apple requires.
- **Pull to refresh**, an **offline screen**, and a **share sheet**.

Only `omni.alphadirect.co.bw` opens inside the app. `mailto:`, `tel:` and any other site are
handed to iOS, so the app never becomes a general web browser.

## The page-to-app bridge
The shell injects `window.OmniNative = { platform: 'ios', version: '1.0.0' }` before the page
loads, so the web app can tell it is running inside the native app (use this to hide the
"add to Home Screen" hint). Messages the page can send via
`window.webkit.messageHandlers.omni.postMessage({...})`:

| action | effect |
|---|---|
| `enableAlerts` | asks for notification permission, then registers with APNs |
| `setLock` `{on: bool}` | turns the Face ID lock on or off |
| `badge` `{count: n}` | sets the app icon badge |
| `share` `{text: "..."}` | opens the iOS share sheet |

When APNs returns a device token the shell fires a DOM event on `window`:
`omni-push-token` with `detail: { token, platform: 'ios' }`. **The backend still needs an
endpoint to store that token and a sender that talks to APNs** — until that exists, iPhone
alerts do not arrive.

## Build
```
xcodegen generate
# simulator
xcodebuild -project OmniStaff.xcodeproj -scheme OmniStaff -sdk iphonesimulator \
  -configuration Debug -derivedDataPath build CODE_SIGNING_ALLOWED=NO build
```

**Trap:** xcodegen rewrites `Resources/Info.plist` from `project.yml`'s `info.properties` on
every generate. Every key Apple needs — the launch storyboard, the camera / photo / Face ID
usage strings, `ITSAppUsesNonExemptEncryption` — must live there. `INFOPLIST_KEY_*` build
settings are ignored and get wiped. Missing `UILaunchStoryboardName` makes iOS letterbox the
whole app in black bands.

Bundle id `co.bw.alphadirect.omnistaff` — deliberately different from the desktop wrapper
`co.bw.alphadirect.omni`, so both can exist. Apple Team 3KXFUVSXG7.

Distribution is a **Custom App through Apple Business Manager**, not the public App Store —
Apple does not allow a staff-only app on the public store. See
`~/Desktop/OmniApp-Store-Pack/03-APPLE-APP-STORE.md`.
