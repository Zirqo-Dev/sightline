#!/usr/bin/env bash
# Privacy guard: fail if owner wallet data appears in files to be published.
# Usage: scripts/privacy-guard.sh [--root DIR] [files...]   (no args = staged files, or all tracked files if none staged)
#   --root DIR: scan the git checkout at DIR (e.g. a desk-data worktree) with this script's rules.
# Extra private patterns (one ERE per line): env PRIVACY_GUARD_PATTERNS and/or gitignored .privacy-patterns
set -u
if [ "${1:-}" = "--root" ]; then
  [ -d "${2:-}" ] || { echo "privacy-guard: --root needs a directory" >&2; exit 2; }
  cd "$2" || exit 2; shift 2; root_mode=1
else
  cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
fi

patterns=(
  '"wallet_snapshot"'
  '"held"[[:space:]]*:[[:space:]]*true'
  # Generic full-length Cardano wallet ids (no owner-specific values live in this file).
  # Full stake keys and full base (addr1q) addresses are never published; short tags only.
  # Enterprise/script addresses (addr1v/addr1w) are not matched generically: public platform
  # addresses (e.g. the Solada curve address in solada/log.json) use them.
  'stake1[02-9ac-hj-np-z]{50,}'
  'addr1q[02-9ac-hj-np-z]{90,}'
  "Bousky's"
  'Bousky holds'
  'Bousky wallet'
)
# Owner-specific wallet patterns come ONLY from the PRIVACY_GUARD_PATTERNS secret (one ERE per line)
# or a gitignored .privacy-patterns file. They are never printed: matches report a pattern number only.
if [ -n "${PRIVACY_GUARD_PATTERNS:-}" ]; then
  while IFS= read -r p; do p="${p%$'\r'}"; case "$p" in ''|'#'*) ;; *) patterns+=("$p");; esac; done <<< "$PRIVACY_GUARD_PATTERNS"
else
  echo "::warning::privacy-guard: PRIVACY_GUARD_PATTERNS is not set; owner-specific wallet patterns are NOT checked (generic checks still run)" >&2
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

if [ -n "${root_mode:-}" ] && [ ${#files[@]} -eq 0 ]; then
  echo "PRIVACY GUARD FAILED: no tracked files found under --root" >&2; exit 1
fi
self="scripts/privacy-guard.sh"
fail=0
for f in "${files[@]}"; do
  [ -f "$f" ] || continue
  case "$f" in "$self"|PRIVACY.md|.privacy-patterns|.github/workflows/privacy-guard.yml|.github/workflows/desk-data-guard.yml) continue;; esac
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
