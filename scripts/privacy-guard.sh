#!/usr/bin/env bash
# Privacy guard: fail if owner wallet data appears in files to be published.
# Usage: scripts/privacy-guard.sh [files...]   (no args = staged files, or all tracked files if none staged)
# Extra private patterns (one ERE per line): env PRIVACY_GUARD_PATTERNS and/or gitignored .privacy-patterns
set -u
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"

patterns=(
  '"wallet_snapshot"'
  '"held"[[:space:]]*:[[:space:]]*true'
)
if [ -n "${PRIVACY_GUARD_PATTERNS:-}" ]; then
  while IFS= read -r p; do [ -n "$p" ] && patterns+=("$p"); done <<< "$PRIVACY_GUARD_PATTERNS"
fi
if [ -f .privacy-patterns ]; then
  while IFS= read -r p; do case "$p" in ''|'#'*) ;; *) patterns+=("$p");; esac; done < .privacy-patterns
fi

if [ $# -gt 0 ]; then
  files=("$@")
else
  mapfile -t files < <(git diff --cached --name-only --diff-filter=ACMR 2>/dev/null)
  [ ${#files[@]} -eq 0 ] && mapfile -t files < <(git ls-files)
fi

self="scripts/privacy-guard.sh"
fail=0
for f in "${files[@]}"; do
  [ -f "$f" ] || continue
  case "$f" in "$self"|PRIVACY.md|.privacy-patterns|.github/workflows/privacy-guard.yml) continue;; esac
  for p in "${patterns[@]}"; do
    if hits=$(grep -nEI -- "$p" "$f" 2>/dev/null); then
      n=$(printf '%s\n' "$hits" | wc -l)
      echo "PRIVACY GUARD: $f: $n line(s) match a private pattern (#$(( $(printf '%s\n' "${patterns[@]}" | grep -nFx -- "$p" | head -1 | cut -d: -f1) )) ): lines $(printf '%s\n' "$hits" | cut -d: -f1 | head -10 | paste -sd, -)" >&2
      fail=1
    fi
  done
done
if [ $fail -ne 0 ]; then
  echo "PRIVACY GUARD FAILED: owner wallet data must not be committed. See PRIVACY.md." >&2
  exit 1
fi
echo "privacy-guard: OK (${#files[@]} file(s) checked)"
