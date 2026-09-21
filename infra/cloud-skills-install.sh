#!/usr/bin/env bash
# cloud-skills-install.sh — make the repo's skills visible to Claude Code on the
# web. Registered as a SessionStart hook in .claude/settings.json.
#
# WHY THIS EXISTS
# ---------------
# Every skill the CFO has written lives in infra/skills/ (put under version
# control on 2026-09-11 so the Windows PC and the Mac Mini could finally share
# them — see infra/skills-sync.sh). A cloud session clones this repo, so the
# files are present, but Claude Code only auto-registers skills found in
# ~/.claude/skills. Result: /PratThis, /CFO, /omni and the rest sat on disk
# unusable in every web session. This hook copies them into place at startup.
#
# CLOUD ONLY — AND THAT IS THE POINT
# ----------------------------------
# On the PC and the Mac, ~/.claude/skills is the LIVE, hand-edited copy. Writing
# over it from the repo is precisely the accident infra/skills-sync.sh was built
# to prevent (the 10-Sep-2026 /PratThis divergence, where each machine held work
# the other lacked and a naive copy either way would have destroyed it).
# skills-sync.sh stays the only thing that moves skills between those two
# machines. This hook runs in a throwaway cloud container and nowhere else.
set -euo pipefail

REPO="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SRC="$REPO/infra/skills"
DEST="$HOME/.claude/skills"

# Guard: run in a Claude Code web container, never on the PC (MINGW/MSYS) or the
# Mac (Darwin). CLAUDE_CODE_REMOTE is the documented signal; the uname test is a
# belt-and-braces fallback in case a future runtime stops setting it.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ] && [ "$(uname -s)" != "Linux" ]; then
  exit 0
fi

[ -d "$SRC" ] || exit 0
mkdir -p "$DEST"

installed=0
for skill in "$SRC"/*/; do
  name="$(basename "$skill")"
  # A directory is a skill only if it carries SKILL.md; anything else is skipped.
  [ -f "$skill/SKILL.md" ] || continue
  [ -n "$name" ] || continue

  rm -rf "${DEST:?}/$name"
  mkdir -p "$DEST/$name"
  # Text and assets the skill needs to run; not nested checkouts or build junk.
  tar -C "$skill" \
      --exclude=.git --exclude=node_modules --exclude=venv --exclude=.venv \
      --exclude=__pycache__ --exclude=.next --exclude=dist \
      -cf - . 2>/dev/null | tar -C "$DEST/$name" -xf - 2>/dev/null || true
  installed=$((installed + 1))
done

echo "Installed $installed repo skills from infra/skills into ~/.claude/skills."
