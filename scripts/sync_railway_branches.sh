#!/usr/bin/env bash
# Railway reads the repo-root railway.json for every service built from this repo, and
# that file belongs to the recorder. So the api and web services deploy from generated
# branches, railway/api and railway/web: the source branch's files with railway.json
# replaced by deploy/railway.<service>.json. The recorder (branch main) never sees them.
#
#   bash scripts/sync_railway_branches.sh [source-branch]    (default: phase2-app)
#
# The "railway branches" GitHub workflow runs this on every push to the source branch.
set -euo pipefail
SRC="${1:-phase2-app}"
git fetch -q origin "$SRC"
for svc in api web; do
  tmp="$(mktemp -d)"
  git worktree add -q --detach "$tmp" "origin/$SRC"
  cp "$tmp/deploy/railway.$svc.json" "$tmp/railway.json"
  git -C "$tmp" add railway.json
  git -C "$tmp" -c user.name="railway-sync" -c user.email="railway-sync@users.noreply.github.com" \
    commit -q -m "Railway $svc deploy config (generated from $SRC $(git rev-parse --short "origin/$SRC"))"
  git -C "$tmp" push -q -f origin "HEAD:refs/heads/railway/$svc"
  git worktree remove --force "$tmp"
  echo "railway/$svc <- $SRC"
done
