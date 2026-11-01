#!/usr/bin/env bash
# Auto-bump pinned upstream versions across the catalog.
#
# For each apps/<name>/app.json that declares an "upstream" block, this script
#   1. resolves the current upstream version (GitHub release tag or branch HEAD),
#   2. reads the pinned VERSION="..." line from apps/<name>/get-<name>.sh,
#   3. if they differ AND no open PR already proposes the same bump, opens a PR.
#
# Designed to run from a GitHub Actions workflow with GH_TOKEN set. Safe to run
# from a laptop too: pass --dry-run to skip branch/commit/push/PR.
#
# Requires: bash, jq, gh, curl, sed, grep, git.

set -euo pipefail

DRY_RUN=0
if [ "${1:-}" = "--dry-run" ]; then
  DRY_RUN=1
fi

log()  { printf '[bump] %s\n' "$*" >&2; }
note() { printf '       %s\n' "$*" >&2; }

# resolve_upstream <type> <repo> [strip_prefix] [branch]
# Echoes the resolved version string (or empty on failure).
resolve_upstream() {
  local type=$1 repo=$2 strip=${3:-} branch=${4:-master} v=""
  case "$type" in
    github-release)
      v=$(gh api "repos/$repo/releases/latest" --jq '.tag_name' 2>/dev/null || true)
      if [ -n "$strip" ] && [ -n "$v" ]; then
        v=${v#"$strip"}
      fi
      ;;
    github-branch-head)
      v=$(gh api "repos/$repo/commits/$branch" --jq '.sha[0:8]' 2>/dev/null || true)
      ;;
    *)
      note "unknown upstream type: $type"
      return 0
      ;;
  esac
  printf '%s' "$v"
}

# read_pinned <app_dir>
# Echoes the pinned version from the get script's `[A-Z_]*VERSION="..."` line,
# or empty if no such line.
read_pinned() {
  local dir=$1 line=""
  # Match e.g. TAILSCALE_VERSION="1.84.0" or OCTOAPP_VERSION=2.1.10
  line=$(grep -hE '^[[:space:]]*[A-Z_]+VERSION=' "$dir"/get-*.sh 2>/dev/null | head -1 || true)
  printf '%s' "$line" | sed -E 's/^[[:space:]]*[A-Z_]+VERSION=//; s/^"//; s/"$//'
}

# Images that build the app payloads, as in each get script's header comment
BUILD_IMAGE="ghcr.io/rinkhals-community/rinkhals/build"
PYTHON_IMAGE="ghcr.io/jbatonnet/armv7-uclibc:rinkhals"

# Discover apps to consider.
APPS_DIR="apps"
found=0
opened=0
skipped=0

for manifest in "$APPS_DIR"/*/app.json; do
  app_dir=$(dirname "$manifest")
  app=$(basename "$app_dir")
  [ "$app" = "example" ] && continue

  # Bail if no upstream block.
  type=$(jq -r '.upstream.type // empty' "$manifest")
  if [ -z "$type" ]; then
    continue
  fi
  found=$((found + 1))

  repo=$(jq -r '.upstream.repo // empty' "$manifest")
  strip=$(jq -r '.upstream.strip_prefix // empty' "$manifest")
  branch=$(jq -r '.upstream.branch // "master"' "$manifest")

  log "checking $app  ($type $repo)"

  upstream=$(resolve_upstream "$type" "$repo" "$strip" "$branch")
  if [ -z "$upstream" ]; then
    note "upstream resolve failed; skipping"
    skipped=$((skipped + 1))
    continue
  fi

  pinned=$(read_pinned "$app_dir")
  if [ -z "$pinned" ]; then
    note "no pinned VERSION= in get-*.sh; nothing to bump"
    skipped=$((skipped + 1))
    continue
  fi

  # The store shows the committed app.json version, so it must track upstream
  # too - not just the get-script pin. Bump if either has drifted (an app whose
  # get-script is current but whose app.json lags would otherwise never be fixed
  # and would show "behind upstream" forever).
  manifest_ver=$(jq -r '.version // empty' "$manifest")
  from=${manifest_ver:-$pinned}

  if [ "$pinned" = "$upstream" ] && [ "$manifest_ver" = "$upstream" ]; then
    note "up to date ($upstream)"
    continue
  fi

  log "$app: $from -> $upstream"

  branch_name="chore/auto-bump-$app-$upstream"

  # Idempotency: if a PR is already open for this exact bump, skip.
  if [ "$DRY_RUN" -eq 0 ]; then
    existing=$(gh pr list --state open --head "$branch_name" --json number --jq '.[0].number' 2>/dev/null || true)
    if [ -n "$existing" ]; then
      note "PR #$existing already open for this bump; skipping"
      continue
    fi
  fi

  # Locate the get script and bump its VERSION line.
  get_script=$(ls "$app_dir"/get-*.sh 2>/dev/null | head -1 || true)
  if [ -z "$get_script" ]; then
    note "no get-*.sh found; skipping"
    skipped=$((skipped + 1))
    continue
  fi

  if [ "$DRY_RUN" -eq 1 ]; then
    note "[dry-run] would update $get_script and open PR on branch $branch_name"
    opened=$((opened + 1))
    continue
  fi

  # Reset to clean state on origin/master before each app's branch, so we
  # don't carry edits from a previous iteration into the next PR's diff.
  git checkout --quiet master
  git reset --quiet --hard origin/master
  git checkout --quiet -B "$branch_name"

  # Quote like the existing line: if the value was double-quoted, keep quotes.
  case $(grep -hE '^[[:space:]]*[A-Z_]+VERSION=' "$get_script" | head -1) in
    *VERSION=\"*) replacement="\"$upstream\"" ;;
    *)            replacement="$upstream" ;;
  esac
  # sed with a unique delimiter so version strings containing / don't break us.
  sed -i.bak -E "s|^([[:space:]]*[A-Z_]+VERSION=).*|\1$replacement|" "$get_script"
  rm -f "$get_script.bak"

  # Also bump the committed app.json version. The store reads this value from
  # the repo at the release tag and compares it to upstream; without bumping it
  # here the app shows "behind upstream" forever even after the get-script bump.
  # The "$version" schema key is not matched by this pattern (no ": \"x\"" form).
  sed -i.bak "s/\"version\": *\"[^\"]*\"/\"version\": \"$upstream\"/" "$manifest"
  rm -f "$manifest.bak"

  # The SWU build packages the app directory as committed and never runs the
  # get scripts, so bumping the pin alone changed the label and not the
  # payload. Refresh the downloaded files, and the armv7 Python packages if the
  # app has any, so the PR ships what it says. The containers run as root, so
  # hand the files back before git touches them again.
  docker run --rm -v "$PWD/apps:/apps" "$BUILD_IMAGE" sh "/apps/$app/$(basename "$get_script")"
  if [ -f "$app_dir/get-packages.sh" ]; then
    docker run --rm --platform linux/arm/v7 -v "$PWD/apps:/apps" "$PYTHON_IMAGE" sh "/apps/$app/get-packages.sh"
  fi
  docker run --rm -v "$PWD/apps:/apps" "$BUILD_IMAGE" chown -R "$(id -u):$(id -g)" "/apps/$app"

  git add -A "$app_dir"
  git commit --quiet -m "chore($app): auto-bump $from -> $upstream

Resolved from $(jq -r '.upstream.type' "$manifest") at $repo."

  git push --quiet -u origin "$branch_name"

  body=$(cat <<EOF
Auto-bump from the upstream-tracking bot.

| app | from | to | source |
|---|---|---|---|
| $app | \`$from\` | \`$upstream\` | $type \`$repo\` |

This PR was opened by \`scripts/auto-bump-versions.sh\`. Merging will trigger a
rebuild of the affected SWUs on the next Rinkhals.Apps release. If you don't
want this bump (e.g. upstream changelog has a regression), close this PR -
the bot will re-open it next week unless the manifest's \`upstream\` block is
updated or removed.
EOF
)
  gh pr create --base master --head "$branch_name" \
    --title "chore($app): auto-bump $from -> $upstream" \
    --body "$body" >&2
  opened=$((opened + 1))

  # Close the bumps this one replaces, so they don't pile up week after week
  for old in $(gh pr list --state open --limit 100 --json headRefName --jq '.[].headRefName'); do
    case "$old" in
      "$branch_name") ;;
      "chore/auto-bump-$app-"*)
        note "closing superseded $old"
        gh pr close "$old" --delete-branch --comment "Superseded by the bump to $upstream." >&2 || true
        ;;
    esac
  done
done

log "summary: $found apps with upstream block, $opened PRs opened/queued, $skipped skipped"
