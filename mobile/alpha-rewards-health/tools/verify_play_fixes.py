#!/usr/bin/env python3
"""Independently verify the five Google Play rejection fixes for Alpha Nexus.

Written to be run by someone who does NOT trust the handover note - it reads the
actual files and reports PASS/FAIL per requirement. No dependencies beyond the
Python standard library, so it runs on macOS, Linux and Windows against a fresh
clone with no node_modules and no Android SDK.

    python3 tools/verify_play_fixes.py                     # source tree only
    python3 tools/verify_play_fixes.py --aab path/to.aab    # also check a build
    python3 tools/verify_play_fixes.py --live               # also HTTP-check the privacy URL

Exit status 0 only if every check passed.

The five fixes, and what actually proves each one:
  1 icon      - the launcher PNGs contain the orange heart and NOT the stock
                React Native robot. Existence alone proves nothing: the rejected
                build also had all ten files, they were just the wrong picture.
  2 name      - the compiled string, plus the regression that app.json "name"
                is unchanged (renaming it crashes AppRegistry at startup).
  3 16 KB     - React Native >= 0.81, because a prebuilt .so cannot be realigned
                by a packaging flag. Definitive proof needs --aab.
  4 API 36    - compileSdk/targetSdk in the ROOT build.gradle, not app/.
  5 privacy   - the new URL present, the dead one absent.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import struct
import sys
import zipfile

DENSITIES = ["mdpi", "hdpi", "xhdpi", "xxhdpi", "xxxhdpi"]
NEW_PRIVACY = "https://omni.alphadirect.co.bw/m/privacy"
OLD_PRIVACY = "alphadirect.co.bw/privacy/health-rewards"

results: list[tuple[str, bool, str]] = []

# Set by main(). This script is also shipped as a loose copy outside the repo, so the
# app directory is resolved rather than assumed - otherwise running the loose copy
# produced a bare FileNotFoundError traceback that looks like a broken tool.
HERE = ""


def looks_like_app(path: str) -> bool:
    return all(
        os.path.isfile(os.path.join(path, *p))
        for p in (("app.json",), ("android", "app", "build.gradle"))
    )


def resolve_repo(explicit: str | None) -> str:
    tried = []
    if explicit:
        cand = os.path.abspath(os.path.expanduser(explicit))
        if looks_like_app(cand):
            return cand
        tried.append(cand)
    # ../ from this file: correct when running as repo tools/verify_play_fixes.py
    cand = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if looks_like_app(cand):
        return cand
    tried.append(cand)
    # the working directory, and one level up from it
    for cand in (os.path.abspath("."), os.path.abspath("..")):
        if looks_like_app(cand):
            return cand
        tried.append(cand)

    print("ERROR: could not find the alpha-rewards-health app directory.\n")
    print("Looked in:")
    for t in dict.fromkeys(tried):
        print(f"  {t}")
    print(
        "\nPoint it at the app explicitly:\n"
        "  python3 verify_play_fixes.py --repo /path/to/alpha-finance/mobile/alpha-rewards-health\n\n"
        "Get the repo with:\n"
        "  git clone https://github.com/alphadirectinsurance/alpha-finance.git\n"
        "  cd alpha-finance/mobile/alpha-rewards-health\n"
        "  git checkout fix/nexus-play-rejection"
    )
    sys.exit(2)


def check(label: str, ok: bool, detail: str = "") -> bool:
    results.append((label, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    return ok


def read(*parts: str) -> str:
    with open(os.path.join(HERE, *parts), encoding="utf-8", errors="replace") as fh:
        return fh.read()


# --------------------------------------------------------------------------
# PNG decoding without Pillow: we only need to know which colours are present,
# so decode the IDAT stream and sample pixels.
# --------------------------------------------------------------------------
def png_colours(path: str, limit: int = 20000) -> list[tuple[int, int, int]]:
    import zlib

    with open(path, "rb") as fh:
        data = fh.read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return []

    pos, width, height, depth, ctype = 8, 0, 0, 0, 0
    idat = bytearray()
    while pos < len(data):
        (ln,) = struct.unpack(">I", data[pos : pos + 4])
        typ = data[pos + 4 : pos + 8]
        body = data[pos + 8 : pos + 8 + ln]
        if typ == b"IHDR":
            width, height, depth, ctype = struct.unpack(">IIBB", body[:10])
        elif typ == b"IDAT":
            idat += body
        elif typ == b"IEND":
            break
        pos += 12 + ln

    # Only handle the 8-bit truecolour forms that Android ships; anything else
    # is reported as "unknown" rather than guessed at.
    if depth != 8 or ctype not in (2, 6):
        return []
    channels = 3 if ctype == 2 else 4
    try:
        raw = zlib.decompress(bytes(idat))
    except zlib.error:
        return []

    stride = width * channels
    out: list[tuple[int, int, int]] = []
    prev = bytearray(stride)
    at = 0
    for _ in range(height):
        if at >= len(raw):
            break
        filt = raw[at]
        at += 1
        line = bytearray(raw[at : at + stride])
        at += stride
        if len(line) < stride:
            break
        # Undo the per-scanline PNG filter.
        for i in range(stride):
            a = line[i - channels] if i >= channels else 0
            b = prev[i]
            c = prev[i - channels] if i >= channels else 0
            if filt == 1:
                line[i] = (line[i] + a) & 0xFF
            elif filt == 2:
                line[i] = (line[i] + b) & 0xFF
            elif filt == 3:
                line[i] = (line[i] + ((a + b) >> 1)) & 0xFF
            elif filt == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        for x in range(0, stride, channels):
            if channels == 4 and line[x + 3] < 128:
                continue  # transparent
            out.append((line[x], line[x + 1], line[x + 2]))
            if len(out) >= limit:
                return out
        prev = line
    return out


def is_orange(p: tuple[int, int, int]) -> bool:
    r, g, b = p
    return r > 150 and r - b > 60 and r - g > 25


def is_rn_robot_teal(p: tuple[int, int, int]) -> bool:
    # The stock React Native launcher icon is a teal/green Android robot.
    r, g, b = p
    return g > 110 and g - r > 40 and abs(g - b) < 90 and b > 80


# --------------------------------------------------------------------------
def fix1_icon() -> None:
    print("\n[1] Launcher icon matches the store listing")
    res = os.path.join(HERE, "android", "app", "src", "main", "res")

    missing = [
        f"mipmap-{d}/{n}.png"
        for d in DENSITIES
        for n in ("ic_launcher", "ic_launcher_round")
        if not os.path.isfile(os.path.join(res, f"mipmap-{d}", f"{n}.png"))
    ]
    check("all 10 legacy launcher PNGs present", not missing, ", ".join(missing) or "5 densities x 2")

    adaptive = [
        os.path.join(res, "mipmap-anydpi-v26", "ic_launcher.xml"),
        os.path.join(res, "mipmap-anydpi-v26", "ic_launcher_round.xml"),
    ]
    check("adaptive icon declared (mipmap-anydpi-v26)", all(map(os.path.isfile, adaptive)))
    fg = [os.path.join(res, f"mipmap-{d}", "ic_launcher_foreground.png") for d in DENSITIES]
    check("adaptive foreground at all 5 densities", all(map(os.path.isfile, fg)))

    # The content check - this is the one that would have caught the rejection.
    big = os.path.join(res, "mipmap-xxxhdpi", "ic_launcher.png")
    if not os.path.isfile(big):
        check("icon artwork is the Alpha Nexus heart", False, "xxxhdpi icon missing")
        return
    px = png_colours(big)
    if not px:
        check("icon artwork readable", False, "could not decode PNG (unusual colour format)")
        return
    orange = sum(map(is_orange, px))
    teal = sum(map(is_rn_robot_teal, px))
    check(
        "icon contains the orange heart",
        orange > len(px) * 0.02,
        f"{orange}/{len(px)} sampled pixels orange",
    )
    check(
        "icon is NOT the stock React Native robot",
        teal < len(px) * 0.02,
        f"{teal}/{len(px)} sampled pixels robot-teal",
    )


def fix2_name() -> None:
    print("\n[2] App name is exactly 'Alpha Nexus'")
    strings = read("android", "app", "src", "main", "res", "values", "strings.xml")
    m = re.search(r'<string name="app_name">([^<]*)</string>', strings)
    check("strings.xml app_name", bool(m) and m.group(1) == "Alpha Nexus", m.group(1) if m else "not found")

    manifest = read("android", "app", "src", "main", "AndroidManifest.xml")
    check('manifest android:label -> @string/app_name', 'android:label="@string/app_name"' in manifest)
    check("old name absent from strings.xml", "Alpha Rewards Health" not in strings)

    app_json = json.loads(read("app.json"))
    check("app.json displayName", app_json.get("displayName") == "Alpha Nexus", str(app_json.get("displayName")))
    # Regression: this must NOT have been renamed or the app crashes on launch.
    check(
        "app.json name still 'AlphaRewardsHealth' (AppRegistry key)",
        app_json.get("name") == "AlphaRewardsHealth",
        str(app_json.get("name")),
    )
    main_activity = read(
        "android", "app", "src", "main", "java", "com", "alphadirect", "rewardshealth", "MainActivity.kt"
    )
    check(
        "MainActivity component name matches app.json name",
        f'"{app_json.get("name")}"' in main_activity,
    )


def fix3_sixteen_kb() -> None:
    print("\n[3] 16 KB memory page size")
    pkg = json.loads(read("package.json"))
    rn = pkg["dependencies"]["react-native"]
    nums = re.findall(r"\d+", rn)
    minor = int(nums[1]) if len(nums) > 1 else 0
    check(
        "react-native >= 0.81 in package.json",
        minor >= 81,
        f"declared {rn}  (0.76 prebuilt libraries are 4 KB aligned and cannot be fixed by a flag)",
    )

    app_gradle = read("android", "app", "build.gradle")
    check(
        "useLegacyPackaging = false for jniLibs",
        re.search(r"jniLibs\s*\{[^}]*useLegacyPackaging\s*=\s*false", app_gradle, re.S) is not None,
    )

    wrapper = read("android", "gradle", "wrapper", "gradle-wrapper.properties")
    gv = re.search(r"gradle-([\d.]+)-", wrapper)
    check("gradle wrapper >= 8.13 (needed by AGP 8.11)", bool(gv) and tuple(
        int(x) for x in gv.group(1).split(".")[:2]
    ) >= (8, 13), gv.group(1) if gv else "?")

    installed = os.path.join(HERE, "node_modules", "react-native", "package.json")
    if os.path.isfile(installed):
        with open(installed, encoding="utf-8") as fh:
            check("installed react-native matches", json.load(fh)["version"].startswith("0.81"))
    else:
        print("  note  node_modules absent - run `npm install`, then re-run, to check the installed version")
    print("  note  definitive proof requires a built bundle: --aab, or tools/check_16kb.py")


def fix4_api36() -> None:
    print("\n[4] Target API level 36")
    root = read("android", "build.gradle")

    def ext(key: str) -> str | None:
        m = re.search(rf"{key}\s*=\s*[\"']?(\d+(?:\.\d+)*)[\"']?", root)
        return m.group(1) if m else None

    check("compileSdkVersion = 36", ext("compileSdkVersion") == "36", str(ext("compileSdkVersion")))
    check("targetSdkVersion = 36", ext("targetSdkVersion") == "36", str(ext("targetSdkVersion")))
    check(
        "minSdkVersion = 26 (Health Connect needs 26, not the template's 24)",
        ext("minSdkVersion") == "26",
        str(ext("minSdkVersion")),
    )

    app_gradle = read("android", "app", "build.gradle")
    vc = re.search(r"versionCode\s+(\d+)", app_gradle)
    check(
        "versionCode > 3 (published is 2, a v3 was built)",
        bool(vc) and int(vc.group(1)) > 3,
        f"versionCode {vc.group(1)}" if vc else "not found",
    )


def fix5_privacy(live: bool) -> None:
    print("\n[5] Privacy policy URL")
    act = read(
        "android", "app", "src", "main", "java", "com", "alphadirect", "rewardshealth",
        "PermissionsRationaleActivity.kt",
    )
    check("new privacy URL present in the app", NEW_PRIVACY in act, NEW_PRIVACY)

    # Resolve what loadUrl actually opens. It takes a constant, not a literal, so
    # reading the loadUrl line alone would pass even with a dead URL behind the
    # constant. The old address may legitimately appear in a comment recording the
    # history - only the resolved value matters.
    loaded = re.search(r"loadUrl\(\s*([^)]*?)\s*\)", act)
    arg = loaded.group(1).strip() if loaded else ""
    effective = arg.strip('"')
    if loaded and not arg.startswith('"'):
        const = re.search(rf'val\s+{re.escape(arg)}\s*=\s*"([^"]+)"', act)
        effective = const.group(1) if const else f"<could not resolve {arg}>"
    check(
        "the URL the app actually opens is the live page",
        effective == NEW_PRIVACY,
        f"{arg} -> {effective}",
    )

    if live:
        import urllib.request
        try:
            req = urllib.request.Request(NEW_PRIVACY, headers={"User-Agent": "verify-play-fixes"})
            with urllib.request.urlopen(req, timeout=25) as r:
                check("privacy page responds 200", r.status == 200, f"HTTP {r.status}")
        except Exception as exc:  # noqa: BLE001
            check("privacy page responds 200", False, f"{type(exc).__name__}: {exc}")
    else:
        print("  note  pass --live to HTTP-check the page (a dead link repeats the rejection)")


def aab_checks(path: str) -> None:
    print(f"\n[bundle] {path}")
    if not os.path.isfile(path):
        check("bundle exists", False, path)
        return
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from check_16kb import check as align_check  # reuse the committed checker

    check("every 64-bit library is 16 KB aligned", align_check(path))

    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        pb = z.read("base/resources.pb")
        check("compiled resources contain 'Alpha Nexus'", b"Alpha Nexus" in pb)
        check("old app name absent from resources", b"Alpha Rewards Health" not in pb)
        check(
            "adaptive icon shipped in the bundle",
            sum(1 for n in names if "mipmap-anydpi-v26/ic_launcher" in n) == 2,
        )
        check(
            "launcher icons shipped at 5 densities",
            sum(1 for n in names if n.endswith("/ic_launcher.png")) == 5,
        )
        js = z.read("base/assets/index.android.bundle")
        check("privacy URL not the dead page in the JS bundle", OLD_PRIVACY.encode() not in js)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--aab", help="also verify a built .aab (the only definitive 16 KB proof)")
    ap.add_argument("--live", action="store_true", help="also HTTP-check the privacy URL")
    ap.add_argument(
        "--repo",
        help="path to mobile/alpha-rewards-health (only needed when running a copy of this "
        "script from outside the repo)",
    )
    args = ap.parse_args()

    global HERE
    HERE = resolve_repo(args.repo)

    print("Alpha Nexus - independent verification of the Play Store fixes")
    print(f"repo: {HERE}")

    fix1_icon()
    fix2_name()
    fix3_sixteen_kb()
    fix4_api36()
    fix5_privacy(args.live)
    if args.aab:
        aab_checks(args.aab)

    failed = [r for r in results if not r[1]]
    print("\n" + "=" * 64)
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("\nFAILED:")
        for label, _, detail in failed:
            print(f"  - {label}" + (f" ({detail})" if detail else ""))
        return 1
    print("All checks passed.")
    if not args.aab:
        print("NOTE: 16 KB alignment is only proven against a built bundle. Re-run with --aab.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
