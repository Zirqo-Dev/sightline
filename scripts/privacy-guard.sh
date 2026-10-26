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
n_generic=${#patterns[@]}  # patterns above are generic; any added below are owner-specific
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

# Exact public third-party keys (scripts/privacy-public-keys.txt next to this script) are masked before the
# generic checks, so e.g. a founder whale's key in config can be published. Owner patterns still apply to them.
# Hardened (review M4): every entry must be one full stake key (^stake1 + 53 bech32 chars, nothing else); masking
# is OFF when no owner patterns are loaded (an allowlist must never be checked against an empty owner list); only
# whole tokens are masked (a longer id containing the key is not hidden); and only in the files listed in
# mask_files (the key lives in solada-prefilter/config.json; anywhere else it is still a failure).
allow_file="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/privacy-public-keys.txt"
mask_files=("solada-prefilter/config.json")
allow=()
if [ -f "$allow_file" ]; then
  while IFS= read -r a; do a="${a%$'\r'}"; case "$a" in ''|'#'*) ;; *) allow+=("$a");; esac; done < "$allow_file"
fi
for a in "${allow[@]}"; do
  if ! printf '%s\n' "$a" | grep -qxE -- 'stake1[02-9ac-hj-np-z]{53}'; then
    echo "PRIVACY GUARD FAILED: an entry in privacy-public-keys.txt is not exactly one full stake key" >&2; exit 1
  fi
  for p in "${patterns[@]:$n_generic}"; do
    if printf '%s\n' "$a" | grep -qE -- "$p"; then
      echo "PRIVACY GUARD FAILED: an entry in privacy-public-keys.txt matches an owner pattern" >&2; exit 1
    fi
  done
done
if [ ${#allow[@]} -gt 0 ] && [ ${#patterns[@]} -le "$n_generic" ]; then
  echo "::warning::privacy-guard: no owner patterns loaded; the public-key allowlist is NOT applied (fail closed)" >&2
  allow=()
fi
mask() {  # stdin -> stdout with allowlisted exact values (whole tokens only) replaced
  if [ ${#allow[@]} -eq 0 ]; then cat; return; fi
  python3 -c 'import re, sys
al=[re.compile(r"(?<![0-9a-z_])" + re.escape(a) + r"(?![0-9a-z_])") for a in sys.argv[1:]]
for line in sys.stdin:
    for r in al: line=r.sub("<public-key>", line)
    sys.stdout.write(line)' "${allow[@]}"
}
maskable() { local m; for m in "${mask_files[@]}"; do [ "$1" = "$m" ] && return 0; done; return 1; }

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
  case "$f" in "$self"|PRIVACY.md|.privacy-patterns|.github/workflows/privacy-guard.yml|.github/workflows/desk-data-guard.yml|scripts/privacy-public-keys.txt) continue;; esac
  grep -qI . "$f" 2>/dev/null || continue  # binary / empty
  for i in "${!patterns[@]}"; do
    p="${patterns[$i]}"
    if [ "$i" -lt "$n_generic" ] && maskable "$f"; then hits=$(mask < "$f" | grep -nEI -- "$p"); else hits=$(grep -nEI -- "$p" "$f" 2>/dev/null); fi
    if [ -n "$hits" ]; then
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
