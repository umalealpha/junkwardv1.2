#!/usr/bin/env bash
# Build the Alpha Nexus release bundle on macOS or Linux.
#
# The Windows build machine needs two ugly workarounds (a JDK loopback patch and a
# Gradle cache retry) which is why build-aab.ps1 exists. macOS needs neither, so
# this stays deliberately plain.
#
# You need, before this will work:
#   - JDK 17 (Gradle 8.14 / AGP 8.11 will not run on 11, and 21+ is untested here)
#   - Android SDK with platform 36 and build-tools 36.0.0, ANDROID_HOME set
#   - Node 20+
#   - android/keystore.properties AND the keystore it names
#
# Without keystore.properties the build REFUSES to run rather than quietly produce
# a debug-signed bundle that Google Play would reject. That is intentional.

set -euo pipefail
cd "$(dirname "$0")"

echo "== environment =="
if [[ -z "${ANDROID_HOME:-}" && -z "${ANDROID_SDK_ROOT:-}" ]]; then
  if [[ -d "$HOME/Library/Android/sdk" ]]; then
    export ANDROID_HOME="$HOME/Library/Android/sdk"
    echo "ANDROID_HOME defaulted to $ANDROID_HOME"
  else
    echo "ERROR: set ANDROID_HOME to your Android SDK." >&2
    exit 1
  fi
fi
export ANDROID_SDK_ROOT="${ANDROID_SDK_ROOT:-$ANDROID_HOME}"

java_major="$(java -version 2>&1 | head -1 | sed -E 's/.*"([0-9]+).*/\1/')"
echo "java: $java_major   node: $(node --version 2>/dev/null || echo MISSING)"
if [[ "$java_major" -lt 17 ]]; then
  echo "ERROR: Java $java_major found. Gradle 8.14 / AGP 8.11 need 17 or newer." >&2
  echo "       On macOS: brew install --cask temurin@17, then set JAVA_HOME." >&2
  exit 1
fi

if [[ ! -f android/keystore.properties ]]; then
  cat >&2 <<'MSG'
ERROR: android/keystore.properties is missing.

It is deliberately not in git - it holds the upload-key password. Without it this
build would be signed with the debug key and Google Play would reject the upload,
so it stops here instead.

It needs four lines, and the keystore file it names must sit in android/app/:

  storeFile=release.keystore
  storePassword=<password>
  keyAlias=<alias>
  keyPassword=<password>

Write it WITHOUT a byte-order mark - Java's Properties.load reads a BOM into the
first key name and storeFile then resolves to null.
MSG
  exit 1
fi

echo
echo "== install js dependencies =="
npm install --no-audit --no-fund

echo
echo "== build the bundle =="
cd android
./gradlew :app:bundleRelease --console=plain

aab="app/build/outputs/bundle/release/app-release.aab"
cd ..
if [[ ! -f "android/$aab" ]]; then
  echo "ERROR: no bundle was produced." >&2
  exit 1
fi

echo
echo "== verify before you upload =="
python3 tools/check_16kb.py "android/$aab"
python3 tools/verify_play_fixes.py --aab "android/$aab" --live

echo
echo "Signed by:"
keytool -printcert -jarfile "android/$aab" 2>/dev/null | grep -i '^Owner' || true
echo
echo "Bundle: $(cd android && pwd)/$aab"
echo "Do not upload it if the owner above says 'CN=Android Debug'."
