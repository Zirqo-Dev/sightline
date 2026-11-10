#!/usr/bin/env bash
# Publish nft-pilot to the nft-data branch with the job's GITHUB_TOKEN.
# Git smart HTTP does not accept the REST Authorization scheme. actions/checkout
# sends Basic with username x-access-token. This script does the same, and only
# on the command, so the token is not written into the URL, the git config, or the log.
set -euo pipefail

# A DRY_RUN dispatch must never publish. The workflow also skips this step.
if [ "${NFT_DRY_RUN:-false}" != "false" ]; then
  echo "dry run: refusing to publish" >&2
  exit 1
fi

dest=nft-data
if [ "$dest" != "nft-data" ]; then
  echo "refusing to push; only nft-data is allowed" >&2
  exit 1
fi
# symbolic-ref works before the first commit. rev-parse does not.
if [ "$(git symbolic-ref --short HEAD)" = "desk-data" ]; then
  echo "refusing to push the Solana branch" >&2
  exit 1
fi
if [ -z "${TOKEN:-}" ]; then
  echo "missing token" >&2
  exit 1
fi

git config user.name "nft-snapshot"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add -A nft-pilot
if git diff --cached --quiet; then
  echo "nothing to commit"
  exit 0
fi
git commit -q -m "nft snapshot $(date -u +%Y-%m-%dT%H:%MZ)"

# A header left by checkout plus this one is a duplicate Authorization.
git config --local --unset-all http.extraheader 2>/dev/null || true
git config --local --unset-all "http.https://github.com/.extraheader" 2>/dev/null || true
if [ -n "${GITHUB_REPOSITORY:-}" ]; then
  git remote set-url origin "https://github.com/${GITHUB_REPOSITORY}.git"
fi

b64=$(printf '%s' "x-access-token:${TOKEN}" | base64 -w 0)
echo "::add-mask::${b64}"
git_auth() {
  git -c "http.https://github.com/.extraheader=AUTHORIZATION: basic ${b64}" "$@"
}

export GIT_TERMINAL_PROMPT=0
pause="${NFT_PUSH_RETRY_SLEEP:-5}"

is_rejected() {
  grep -Eq 'non-fast-forward|\(fetch first\)|cannot lock ref'
}

for i in 1 2 3 4 5; do
  err=$(mktemp)
  if git_auth push origin "HEAD:${dest}" 2>"$err"; then
    rm -f "$err"
    exit 0
  fi
  cat "$err" >&2
  if ! is_rejected <"$err" || [ "$i" -eq 5 ]; then
    rm -f "$err"
    echo "push failed" >&2
    exit 1
  fi
  rm -f "$err"
  echo "push rejected, retry ${i}" >&2
  sleep $((i * pause))
  if ! git_auth pull --rebase origin "$dest"; then
    merge=$(git rev-parse --git-path rebase-merge)
    apply=$(git rev-parse --git-path rebase-apply)
    if [ -d "$merge" ] || [ -d "$apply" ]; then
      git rebase --abort || true
    fi
    echo "could not rebase onto ${dest}" >&2
    exit 1
  fi
done
echo "push failed" >&2
exit 1
