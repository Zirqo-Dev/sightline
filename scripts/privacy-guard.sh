#!/usr/bin/env bash
# Privacy guard: fail if owner wallet data appears in files to be published.
# Usage: scripts/privacy-guard.sh [--root DIR] [files...]   (no args = staged files, or all tracked files if none staged)
#   --root DIR: scan the git checkout at DIR (e.g. a desk-data worktree) with this script's rules.
# Extra private patterns (one ERE per line): env PRIVACY_GUARD_PATTERNS and/or gitignored .privacy-patterns
set -u
# Resolve this script's directory before any cd. In --root mode the workflow
# runs `bash main/scripts/privacy-guard.sh --root desk-data`; cd "$2" first
# makes that relative path miss, so privacy-public-keys.txt never loads
# (cd: main/scripts: No such file or directory).
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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
allow_file="$script_dir/privacy-public-keys.txt"
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
# Re-review M4: (1) every entry must be pinned to solada-prefilter/config.json whale.stake (the one public key it
# exists for); (2) the owner's stake key is derived from any owner pattern that contains a full base address
# (addr1q/r/s/t... with a stake part) and must not be allowlisted, so a secret listing only the owner's payment
# address still protects the owner's stake key. Nothing is printed except a generic failure line.
if [ ${#allow[@]} -gt 0 ]; then
  here="$script_dir"
  pycode=$(cat <<'PYEOF'
import json, os, re, sys
src, allow = sys.argv[1], sys.argv[2:]
sys.path.insert(0, src)
try:
    import flags as F
    pinned = (json.load(open(os.path.join(src, "config.json"))).get("whale") or {}).get("stake")
except Exception:
    sys.stderr.write("PRIVACY GUARD FAILED: cannot read solada-prefilter/config.json to pin the allowlist\n"); sys.exit(1)
if any(a != pinned for a in allow):
    sys.stderr.write("PRIVACY GUARD FAILED: a privacy-public-keys.txt entry is not config.json whale.stake\n"); sys.exit(1)
owner_stakes = set()
for line in sys.stdin:
    for m in re.findall(r"addr1[02-9ac-hj-np-z]{50,}", line):
        st = F.addr_info(m)[0]
        if st:
            owner_stakes.add(st)
if owner_stakes & set(allow):
    sys.stderr.write("PRIVACY GUARD FAILED: an allowlisted key is the stake key of an owner address pattern\n"); sys.exit(1)
PYEOF
)
  if ! printf '%s\n' "${patterns[@]:$n_generic}" | python3 -c "$pycode" "$here/../solada-prefilter" "${allow[@]}"; then exit 1; fi
fi
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

# desk-data publishes solada-prefilter/dev_checks.json, liquidity_moves.json and vetoes.json.
# In --root mode (desk-data-guard and the prefilter push) those files are always
# part of the scan, even if the caller passed a list that left them out. Pattern
# and allowlist handling below is unchanged; none of these files are in mask_files, so an
# allowlisted key here still fails.
if [ -n "${root_mode:-}" ]; then
  for need in solada-prefilter/dev_checks.json solada-prefilter/liquidity_moves.json solada-prefilter/vetoes.json solana-desk/rugger_would_veto.json solada-prefilter/whale_roster.json; do
    [ -f "$need" ] || continue
    have=0
    for f in "${files[@]}"; do
      [ "$f" = "$need" ] && have=1 && break
    done
    [ "$have" -eq 0 ] && files+=("$need")
  done
fi

if [ -n "${root_mode:-}" ] && [ ${#files[@]} -eq 0 ]; then
  echo "PRIVACY GUARD FAILED: no tracked files found under --root" >&2; exit 1
fi
self="scripts/privacy-guard.sh"
fail=0
for f in "${files[@]}"; do
  [ -f "$f" ] || continue
  case "$f" in "$self"|PRIVACY.md|.privacy-patterns|.github/workflows/privacy-guard.yml|.github/workflows/desk-data-guard.yml|scripts/privacy-public-keys.txt) continue;; esac
  case "$(basename "$f")" in
    rugger-blacklist.json|rugger-blacklist.json.enc|rugger-pulls.json)
      echo "PRIVACY GUARD: $f: private cache file must not be published" >&2
      fail=1
      continue
      ;;
    roster.json)
      echo "PRIVACY GUARD: $f: private roster must not be published" >&2
      fail=1
      continue
      ;;
  esac
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
  # dev_tag must be a w- hash or a role label from config. A watch-list prefix is not a tag.
  # Entry indexes only: the value itself is never printed. Allowlist masking is not applied.
  if [ "$f" = "solada-prefilter/dev_checks.json" ] || [ "$(basename "$f")" = "dev_checks.json" -a "$(basename "$(dirname "$f")")" = "solada-prefilter" ]; then
    if ! python3 -c '
import json, os, re, sys
path, cfg_path = sys.argv[1], sys.argv[2]
try:
    cfg = json.load(open(cfg_path))
    doc = json.load(open(path))
except Exception:
    sys.stderr.write("PRIVACY GUARD FAILED: cannot read dev_checks.json\n")
    sys.exit(1)
roles = set()
tags = ((cfg.get("flags") or {}).get("snek_prefix_tags") or {})
role_re = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
for k in tags:
    if isinstance(k, str) and not k.startswith("_") and role_re.fullmatch(k):
        roles.add(k)
if "lead_whale" in roles or "founder_whale" in roles:
    roles.update(("lead_whale", "founder_whale"))
ok = re.compile(r"^w-[0-9a-f]{8}$")
bad = []
for i, e in enumerate(doc.get("entries") or []):
    if not isinstance(e, dict) or "dev_tag" not in e:
        continue
    tag = e.get("dev_tag")
    if tag is None or (isinstance(tag, str) and (ok.fullmatch(tag) or tag in roles)):
        continue
    bad.append(str(i))
if bad:
    sys.stderr.write("PRIVACY GUARD: %s: dev_tag is not a role label or w- hash (entries %s)\n" % (path, ",".join(bad[:10])))
    sys.exit(1)
' "$f" "$script_dir/../solada-prefilter/config.json"; then
      fail=1
    fi
  fi
  # actor_tag must be a w- hash or a role label from config. A prefix, address, or stake key is not a tag.
  # Entry indexes only: the value itself is never printed. Allowlist masking is not applied.
  if [ "$f" = "solada-prefilter/liquidity_moves.json" ] || [ "$(basename "$f")" = "liquidity_moves.json" -a "$(basename "$(dirname "$f")")" = "solada-prefilter" ]; then
    if ! python3 -c '
import json, os, re, sys
path, cfg_path = sys.argv[1], sys.argv[2]
try:
    cfg = json.load(open(cfg_path))
    doc = json.load(open(path))
except Exception:
    sys.stderr.write("PRIVACY GUARD FAILED: cannot read liquidity_moves.json\n")
    sys.exit(1)
roles = set()
tags = ((cfg.get("flags") or {}).get("snek_prefix_tags") or {})
role_re = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
for k in tags:
    if isinstance(k, str) and not k.startswith("_") and role_re.fullmatch(k):
        roles.add(k)
if "lead_whale" in roles or "founder_whale" in roles:
    roles.update(("lead_whale", "founder_whale"))
ok = re.compile(r"^w-[0-9a-f]{8}$")
bad = []
rows = []
for key in ("moves", "pools"):
    block = doc.get(key)
    if isinstance(block, list):
        rows.extend(block)
for i, e in enumerate(rows):
    if not isinstance(e, dict) or "actor_tag" not in e:
        continue
    tag = e.get("actor_tag")
    if tag is None or (isinstance(tag, str) and (ok.fullmatch(tag) or tag in roles)):
        continue
    bad.append(str(i))
if bad:
    sys.stderr.write("PRIVACY GUARD: %s: actor_tag is not a role label or w- hash (entries %s)\n" % (path, ",".join(bad[:10])))
    sys.exit(1)
' "$f" "$script_dir/../solada-prefilter/config.json"; then
      fail=1
    fi
  fi
  # Veto evidence is tags only. Old codes: dev_tag and actor_tag, each a w- hash or a role label.
  # known_rugger / known_rugger_funded: rugger_id and wallet_id are w- hashes, evidence_tags are
  # the closed enum, hop is an integer. A raw wallet key fails. Indexes only: values are not printed.
  if [ "$f" = "solada-prefilter/vetoes.json" ] || [ "$(basename "$f")" = "vetoes.json" -a "$(basename "$(dirname "$f")")" = "solada-prefilter" ]; then
    if ! python3 -c '
import json, re, sys
path, cfg_path = sys.argv[1], sys.argv[2]
try:
    cfg = json.load(open(cfg_path))
    doc = json.load(open(path))
except Exception:
    sys.stderr.write("PRIVACY GUARD FAILED: cannot read vetoes.json\n")
    sys.exit(1)
roles = set()
tags = ((cfg.get("flags") or {}).get("snek_prefix_tags") or {})
role_re = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
for k in tags:
    if isinstance(k, str) and not k.startswith("_") and role_re.fullmatch(k):
        roles.add(k)
if "lead_whale" in roles or "founder_whale" in roles:
    roles.update(("lead_whale", "founder_whale"))
ok = re.compile(r"^w-[0-9a-f]{8}$")
ev_tags = {"ahead_of_dump", "late_rug", "lp_pulled", "rugcheck_rugged"}
banned = {"wallet", "stake", "address", "creator", "funder", "funded", "launcherAddress", "raw"}
leak = re.compile(r"stake1[02-9ac-hj-np-z]{20,}|addr1[02-9ac-hj-np-z]{20,}")
bad = []

def walk(node, key=""):
    if isinstance(node, dict):
        if banned & set(node):
            return False
        return all(walk(v, k) for k, v in node.items())
    if isinstance(node, list):
        return all(walk(v, key) for v in node)
    if key == "reason":
        return True
    if isinstance(node, str) and leak.search(node):
        return False
    return True

tokens = doc.get("tokens") if isinstance(doc, dict) else None
if not isinstance(tokens, list):
    sys.stderr.write("PRIVACY GUARD FAILED: vetoes.json has no tokens list\n")
    sys.exit(1)
if not walk(doc):
    sys.stderr.write("PRIVACY GUARD: %s: veto evidence is not a role label or w- hash (tokens 0)\n" % path)
    sys.exit(1)
for i, tok in enumerate(tokens):
    if not isinstance(tok, dict):
        bad.append(str(i))
        continue
    for v in tok.get("vetoes") or []:
        if not isinstance(v, dict) or not isinstance(v.get("evidence"), dict):
            bad.append(str(i))
            continue
        ev = v["evidence"]
        code = v.get("code")
        if code in ("known_rugger", "known_rugger_funded", "known_rugger_sibling"):
            if "rugger_id" in ev and not re.fullmatch(r"r-[0-9a-f]{12}", ev.get("rugger_id") or ""):
                bad.append(str(i))
                continue
            tags_v = ev.get("evidence_tags")
            if not isinstance(tags_v, list) or not tags_v or any(t not in ev_tags for t in tags_v):
                bad.append(str(i))
                continue
            if code == "known_rugger":
                if set(ev) - {"rugger_id", "evidence_tags"} or "evidence_tags" not in ev:
                    bad.append(str(i))
                continue
            hop = ev.get("hop")
            if (code not in ("known_rugger_funded", "known_rugger_sibling")
                    or set(ev) - {"rugger_id", "wallet_id", "evidence_tags", "hop"}
                    or "evidence_tags" not in ev or "hop" not in ev
                    or ("rugger_id" in ev) != ("wallet_id" in ev)
                    or ("wallet_id" in ev and not re.fullmatch(r"r-[0-9a-f]{12}", ev.get("wallet_id") or ""))
                    or isinstance(hop, bool) or not isinstance(hop, int) or not 1 <= hop <= 8):
                bad.append(str(i))
            continue
        if set(ev) - {"dev_tag", "actor_tag"}:
            bad.append(str(i))
            continue
        for tag in ev.values():
            if tag is None or (isinstance(tag, str) and (ok.fullmatch(tag) or tag in roles)):
                continue
            bad.append(str(i))
        reason = v.get("reason")
        if reason is None:
            continue
        # Amounts only. A stake key, address, or wallet tag in the reason is a leak.
        # The value is not printed.
        if (not isinstance(reason, str) or len(reason) > 240
                or not re.fullmatch(r"[A-Za-z0-9 .,;%:()=<>+-]+", reason)
                or re.search(r"stake|addr|w-", reason, re.I)):
            bad.append(str(i))
if bad:
    sys.stderr.write("PRIVACY GUARD: %s: veto evidence is not a role label or w- hash (tokens %s)\n" % (path, ",".join(bad[:10])))
    sys.exit(1)
' "$f" "$script_dir/../solada-prefilter/config.json"; then
      fail=1
    fi
  fi
  # Solana would-veto log. Wallet ids are w- hashes. A creator/funder/stake field is a leak.
  # Indexes only. The mint is the public token id already written on tasks.csv.
  if [ "$f" = "solana-desk/rugger_would_veto.json" ] || [ "$(basename "$f")" = "rugger_would_veto.json" -a "$(basename "$(dirname "$f")")" = "solana-desk" ]; then
    if ! python3 -c '
import json, re, sys
path = sys.argv[1]
try:
    doc = json.load(open(path))
except Exception:
    sys.stderr.write("PRIVACY GUARD FAILED: cannot read rugger_would_veto.json\n")
    sys.exit(1)
ok = re.compile(r"^w-[0-9a-f]{8}$")
b58 = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
ev_tags = {"ahead_of_dump", "late_rug", "lp_pulled", "rugcheck_rugged"}
banned = {"wallet", "stake", "address", "creator", "funder", "funded", "launcherAddress", "raw"}
leak = re.compile(r"stake1[02-9ac-hj-np-z]{20,}|addr1[02-9ac-hj-np-z]{20,}")
bad = []

def walk(node):
    if isinstance(node, dict):
        if banned & set(node):
            return False
        return all(walk(v) for v in node.values())
    if isinstance(node, list):
        return all(walk(v) for v in node)
    if isinstance(node, str) and leak.search(node):
        return False
    return True

rows = doc.get("rows") if isinstance(doc, dict) else None
if (not isinstance(doc, dict) or doc.get("buy_signal") is not False or doc.get("mode") != "log_only"
        or not isinstance(rows, list) or not walk(doc)):
    sys.stderr.write("PRIVACY GUARD FAILED: rugger_would_veto.json rejected\n")
    sys.exit(1)
for i, row in enumerate(rows):
    if not isinstance(row, dict):
        bad.append(str(i))
        continue
    code = row.get("code")
    if "rugger_id" in row and not re.fullmatch(r"r-[0-9a-f]{12}", row.get("rugger_id") or ""):
        bad.append(str(i))
        continue
    tags_v = row.get("evidence_tags")
    if not isinstance(tags_v, list) or not tags_v or any(t not in ev_tags for t in tags_v):
        bad.append(str(i))
        continue
    if code == "known_rugger":
        if set(row) - {"mint", "code", "rugger_id", "evidence_tags"} or "evidence_tags" not in row:
            bad.append(str(i))
        elif any(isinstance(v, str) and b58.fullmatch(v) for k, v in row.items() if k != "mint"):
            bad.append(str(i))
        continue
    if code in ("known_rugger_funded", "known_rugger_sibling"):
        hop = row.get("hop")
        if (set(row) - {"mint", "code", "rugger_id", "wallet_id", "evidence_tags", "hop"}
                or "hop" not in row or "evidence_tags" not in row
                or ("rugger_id" in row) != ("wallet_id" in row)):
            bad.append(str(i))
            continue
        if (("wallet_id" in row and not re.fullmatch(r"r-[0-9a-f]{12}", row.get("wallet_id") or ""))
                or isinstance(hop, bool) or not isinstance(hop, int) or not 1 <= hop <= 8):
            bad.append(str(i))
            continue
        if any(isinstance(v, str) and b58.fullmatch(v) for k, v in row.items() if k != "mint"):
            bad.append(str(i))
        continue
    bad.append(str(i))
if bad:
    sys.stderr.write("PRIVACY GUARD: %s: rugger log id is not a w- hash (rows %s)\n" % (path, ",".join(bad[:10])))
    sys.exit(1)
' "$f"; then
      fail=1
    fi
  fi
  # Private blacklist documents are detected by shape, not only by file name.
  case "$f" in
    *.json)
      if ! python3 -c '
import json, os, sys
try:
    doc = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
if (isinstance(doc, dict) and doc.get("schema") == 1
        and isinstance(doc.get("entries"), dict) and isinstance(doc.get("edges"), list)
        and "exclusions" in doc):
    sys.exit(1)
if isinstance(doc, dict) and doc.get("kind") == "whale_roster" and doc.get("private") is True:
    sys.exit(2)
whales = doc.get("whales") if isinstance(doc, dict) else None
if isinstance(whales, list) and any(isinstance(w, dict) and "wallets" in w for w in whales):
    sys.exit(2)
if isinstance(doc, dict) and (doc.get("kind") == "whale_roster_public" or os.path.basename(sys.argv[1]) == "whale_roster.json"):
    import pathlib
    sys.path.insert(0, str(pathlib.Path(sys.argv[2])))
    import whale_roster as W
    if not W.public_text_ok(doc):
        sys.exit(3)
sys.exit(0)
' "$f" "$script_dir/.."; then
        echo "PRIVACY GUARD: $f: private roster or rugger document must not be published" >&2
        fail=1
      fi
      ;;
  esac
done
owner_hits=0
if [ -n "${PRIVACY_GUARD_PATTERNS:-}" ]; then
  if ! owner_id_blob=$(python3 -c '
import hashlib, hmac, os, re, sys
src = sys.argv[1] if len(sys.argv) > 1 else ""
if src:
    sys.path.insert(0, src)
try:
    import flags as F
except Exception:
    F = None
text = os.environ.get("PRIVACY_GUARD_PATTERNS", "")
if F is None and re.search(r"addr1[02-9ac-hj-np-z]{20,}", text):
    sys.stderr.write("PRIVACY GUARD FAILED: cannot derive stake keys from owner addresses\n")
    sys.exit(1)
ids = []
seen = set()
pepper = None
raw = os.environ.get("RUGGER_ID_PEPPER", "").strip()
if len(raw) >= 32:
    prk = hmac.new(b"\x00" * 32, raw.encode("utf-8"), hashlib.sha256).digest()
    pepper = hmac.new(prk, b"sightline-rugger-id-v1\x01", hashlib.sha256).digest()

def add(token):
    if not token or token in seen:
        return
    seen.add(token)
    ids.append("w-" + hashlib.sha256(token.encode()).hexdigest()[:8])
    if pepper is not None:
        ids.append("r-" + hmac.new(pepper, token.encode(), hashlib.sha256).hexdigest()[:12])

cardano = list(re.finditer(r"stake1[02-9ac-hj-np-z]{20,}|addr1[02-9ac-hj-np-z]{20,}", text))
spans = [m.span() for m in cardano]
for m in cardano:
    token = m.group(0)
    add(token)
    if token.startswith("addr1") and F is not None:
        try:
            st = F.addr_info(token)[0]
        except Exception:
            st = None
        if st:
            add(st)
sol = re.compile(r"(?<![1-9A-HJ-NP-Za-km-z])[1-9A-HJ-NP-Za-km-z]{32,44}(?![1-9A-HJ-NP-Za-km-z])")
for m in sol.finditer(text):
    if any(m.start() < end and m.end() > start for start, end in spans):
        continue
    add(m.group(0))
sys.stdout.write("\n".join(ids))
' "$script_dir/../solada-prefilter"); then
    exit 1
  fi
  if [ -n "$owner_id_blob" ]; then
    for f in "${files[@]}"; do
      [ -f "$f" ] || continue
      case "$f" in "$self"|PRIVACY.md|.privacy-patterns|.github/workflows/privacy-guard.yml|.github/workflows/desk-data-guard.yml|scripts/privacy-public-keys.txt) continue;; esac
      n=$(python3 -c '
import sys
ids = [ln.strip() for ln in sys.stdin.read().splitlines() if ln.strip()]
data = open(sys.argv[1], errors="replace").read()
sys.stdout.write(str(sum(data.count(i) for i in ids)))
' "$f" <<< "$owner_id_blob")
      owner_hits=$((owner_hits + n))
    done
  fi
fi
if [ "$owner_hits" -gt 0 ]; then
  echo "PRIVACY GUARD: owner id matched ${owner_hits} time(s)" >&2
  fail=1
fi
if [ $fail -ne 0 ]; then
  echo "PRIVACY GUARD FAILED: owner wallet data must not be committed. See PRIVACY.md." >&2
  exit 1
fi
echo "privacy-guard: OK (${#files[@]} file(s) checked)"
