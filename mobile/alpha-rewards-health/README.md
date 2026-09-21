# Alpha Rewards Health (Android)

React Native + TypeScript app for **Alpha Direct Insurance** (Botswana). It reads
your **daily steps** from **Health Connect** with **granular consent**, posts them
to the omni rewards backend, and shows points, tier, and a streak in the light
Alpha skin.

- Package: `com.alphadirect.rewardshealth`
- Store name: **Alpha Nexus**
- React Native 0.81.6 / TypeScript
- minSdk 26, compileSdk/target 36
- Android-only (iOS is stubbed — see `ios/STUB_iOS.md`)

## What it does (v1)

1. **Settings** — enter the backend Base URL, your Member ID, and a Bearer token.
   Stored locally with AsyncStorage. Nothing is hard-coded.
2. **Consent** — granular opt-in. Steps is live; Sleep and Workouts are shown as
   "Later". Nothing is read until you allow it. Granting requests Health Connect
   permission for the consented types and records consent at
   `POST /rewards/health-consent/`.
3. **Home** — read today's steps via Health Connect, `POST /rewards/health-metrics/`
   with `{ memberId, date, steps }`, and show `pointsAwarded` / `totalPoints` /
   `tier` / `streakDays` plus a streak counter.

Points and tier are computed on the **backend**, never on the device.

## Data protection (Botswana DPA)

- Only the data types you explicitly tick are ever requested or read.
- Only your **Member ID** is sent to the backend — never your name.
- No health data is stored on the device; it is read transiently and posted.
- Rewards-only. Health data never affects underwriting, pricing, or claims.

## Prerequisites

- Node 20+
- JDK 17
- Android SDK (platform 36, build-tools 36.0.0), `ANDROID_HOME` set
- **The Health Connect app must be installed on the device/emulator.** On
  Android 14+ it is part of the system; on Android 13 and below install it from
  the Play Store:
  https://play.google.com/store/apps/details?id=com.google.android.apps.healthdata
- A device/emulator with steps in Health Connect (the Health Connect app can seed
  sample data, or sync from a fitness app).

## First-time setup

```sh
npm install

# Gradle wrapper jar + the RN debug keystore are not bundled in this scaffold.
# Generate the wrapper jar (or copy from the RN template):
cd android && gradle wrapper --gradle-version 8.10.2 && cd ..
# or:
# cp node_modules/react-native/template/android/gradle/wrapper/gradle-wrapper.jar android/gradle/wrapper/gradle-wrapper.jar

# Debug keystore (see android/app/debug.keystore.README.txt):
keytool -genkeypair -v -storetype PKCS12 \
  -keystore android/app/debug.keystore \
  -storepass android -keypass android \
  -alias androiddebugkey -keyalg RSA -keysize 2048 -validity 10000 \
  -dname "CN=Android Debug,O=Android,C=US"
```

## Build (debug APK)

```sh
cd android
./gradlew assembleDebug
```

The APK is written to `android/app/build/outputs/apk/debug/app-debug.apk`.

## Run on a connected device/emulator

```sh
npx react-native run-android
```

## Build the Play Store bundle (release AAB)

On this Windows machine, use the wrapper script — plain `gradlew bundleRelease`
will not start, because AF_UNIX sockets are disabled here and JDK 17's NIO
selector cannot open a loopback pipe. The script applies the patched
`java.base` classes and retries Gradle's transform-cache race.

```powershell
# once, to supply the upload-signing password (stays on this machine)
powershell -ExecutionPolicy Bypass -File set-signing-key.ps1

# then, for every release
powershell -ExecutionPolicy Bypass -File build-aab.ps1
```

If the upload key itself has to be replaced (a lost password, or a compromised
key), `create-upload-key.ps1` generates a new one and exports the certificate
Google needs for an upload key reset. **See `PLAY-STORE-NEXT-STEPS.md` for the
full order of operations** — the reset must be approved by Google before a build
signed with the new key will be accepted.

Output: `android/app/build/outputs/bundle/release/app-release.aab`.

Without `android/keystore.properties` the build **refuses to run** rather than
produce a debug-signed bundle that Play would reject. To build for inspection
only (icons, alignment) without the upload key, use `verify-build.ps1`, which
passes `-PallowDebugSigning=true` — that output must never be uploaded.

### Play Store requirements this build satisfies

| Requirement | How |
| --- | --- |
| Launcher icon matches the store listing | `mipmap-*/ic_launcher*.png` + an adaptive icon (`mipmap-anydpi-v26`) generated from `APP ICON (512x512).png` |
| App name is "Alpha Nexus" | `android:label` → `@string/app_name` |
| 16 KB memory page size (enforced 1 Nov 2025) | RN 0.81 + Fresco 3.6 ship 16 KB-aligned 64-bit `.so`; `packaging.jniLibs.useLegacyPackaging = false` keeps them uncompressed and page-aligned in the bundle |
| Target API level 36 (due 31 Aug 2026) | `compileSdkVersion`/`targetSdkVersion` 36 in `android/build.gradle` |

Google's rule covers **64-bit only** ("must support 16 KB page sizes on 64-bit
devices" — the Play Console validates 64-bit libraries only), so `armeabi-v7a`
and `x86` are intentionally still shipped at 4 KB. Dropping them would only cut
off 32-bit phones for no compliance benefit.

### About the icon layers

The artwork's heart-and-wordmark content reaches a radius of 184 out of the
512px artwork's 256. On the adaptive icon's 108dp canvas the artwork is drawn at
**85%**, which puts the outermost content at radius 131.9dp against the
strictest 66dp safe circle's 132dp — so no launcher mask (circle, squircle or
rounded square) clips the wordmark. `drawable/ic_launcher_background.xml`
reuses the artwork's own corner colours so the flat background meets the
artwork edge without a seam. Legacy `ic_launcher.png` stays a full-bleed copy
of the artwork for pre-Android-8 launchers; `ic_launcher_round.png` is a
full-bleed circular crop, which is safe because 184 < 256.

Verify the alignment of any built bundle with `tools/check_16kb.py`:

```sh
python tools/check_16kb.py android/app/build/outputs/bundle/release/app-release.aab
```

## Health Connect permission grant flow

The first time you tap **Allow and continue** on the Consent screen, Health
Connect shows its own permission dialog for **Steps**. If Health Connect is not
installed or is out of date, the app surfaces a clear error. To fully revoke
access later, use **Revoke consent** in-app *and* remove the grant in the
Health Connect app (Settings → Apps → Health Connect → App permissions).

## Backend contract

```
POST {baseUrl}/rewards/health-consent/   { memberId, dataTypes: string[], grantedAt }
POST {baseUrl}/rewards/health-metrics/   { memberId, date, steps }
                                         -> { pointsAwarded, totalPoints, tier, streakDays }
```

Auth: `Authorization: Bearer <token>` from Settings.
