#!/usr/bin/env bash
# Scope scan before push. Usage: scripts/scope_check.sh frontend|backend|shared [second-area]
# Lists every path changed vs origin/main (committed + staged + unstaged + untracked) grouped by area,
# exits 1 if any changed path is outside the authorised area(s).
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
git fetch -q origin main 2>/dev/null || true
allowed=("$@"); [ ${#allowed[@]} -gt 0 ] || { echo "usage: $0 frontend|backend|shared [area2]"; exit 2; }
files=$( { git diff --name-only origin/main...HEAD 2>/dev/null; git diff --name-only; git diff --name-only --cached; git ls-files --others --exclude-standard; } | sort -u )
area() { case "$1" in
  frontend/*) echo frontend;;
  CLAUDE.md|README.md|.gitignore|scripts/*) echo shared;;
  *) echo backend;; esac; }
bad=0
for a in frontend backend shared; do
  list=$(for f in $files; do if [ "$(area "$f")" = "$a" ]; then echo "  $f"; fi; done; true)
  [ -n "$list" ] || continue
  ok=no; for x in "${allowed[@]}"; do [ "$x" = "$a" ] && ok=yes; done
  if [ $ok = yes ]; then echo "[$a] (authorised)"; else echo "[$a]  <-- NOT AUTHORISED"; bad=1; fi
  echo "$list"
done
[ -n "$files" ] || echo "no changes vs origin/main"
if [ $bad = 1 ]; then echo; echo "SCOPE CHECK FAILED — do not push. Ask before touching the unauthorised area."; exit 1; fi
echo; echo "scope check OK for: ${allowed[*]}"
