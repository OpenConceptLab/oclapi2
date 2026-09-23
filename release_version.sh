#!/bin/bash
# Repo-agnostic release helper. Copy this file as-is into any OCL repo and
# only edit the two config lines below.
#
# Version format: RELEASE.MAJOR.MINOR[-channel], e.g. "3.0.0-alpha".
# RELEASE and MAJOR (the first two numbers) and the channel label are set by
# hand by editing VERSION_FILE. MINOR (the third number) is bumped
# automatically by `publish`.
#
# Usage:
#   ./release_version.sh sha [raw-sha]     print an 8-char short sha (raw-sha, else $GITHUB_SHA, else git HEAD, else "dev")
#   ./release_version.sh current           print the version currently in VERSION_FILE
#   ./release_version.sh full-version      print "<current>-<sha>" for build artifacts
#   ./release_version.sh publish           create a published GitHub Release with a changelog for the current
#                                           version, then bump MINOR in VERSION_FILE and push the bump commit
set -euo pipefail

# --- Per-repo config (only these two lines differ across repos) ---
VERSION_FILE="core/__init__.py"
VERSION_KEY="API_VERSION"
# --------------------------------------------------------------

cmd_sha() {
  local raw="${1:-}"
  [ -n "$raw" ] || raw="${GITHUB_SHA:-}"
  [ -n "$raw" ] || raw="$(git rev-parse HEAD 2>/dev/null || true)"
  [ -n "$raw" ] || raw="dev"
  echo "${raw:0:8}"
}

cmd_current() {
  grep -m1 -E "^[[:space:]]*[\"']?${VERSION_KEY}[\"']?[[:space:]]*[:=]" "$VERSION_FILE" \
    | sed -E "s/.*[\"']([0-9]+\.[0-9]+\.[0-9]+[^\"']*)[\"'].*/\1/"
}

cmd_full_version() {
  echo "$(cmd_current)-$(cmd_sha)"
}

next_version() {
  local current="$1" release major rest leading suffix
  release=$(cut -d. -f1 <<<"$current")
  major=$(cut -d. -f2 <<<"$current")
  rest=$(cut -d. -f3- <<<"$current")
  if [[ "$rest" =~ ^([0-9]+)(.*)$ ]]; then
    leading="${BASH_REMATCH[1]}"
    suffix="${BASH_REMATCH[2]}"
  else
    leading=0
    suffix=""
  fi
  echo "${release}.${major}.$((leading + 1))${suffix}"
}

is_prerelease() {
  local current="$1" rest
  rest=$(cut -d. -f3- <<<"$current")
  [[ "$rest" =~ ^[0-9]+(.*)$ ]] && [ -n "${BASH_REMATCH[1]}" ]
}

write_version() {
  local new_version="$1"
  sed -i -E "/^[[:space:]]*[\"']?${VERSION_KEY}[\"']?[[:space:]]*[:=]/ s/([\"'])[0-9]+\.[0-9]+\.[0-9]+[^\"']*([\"'])/\1${new_version}\2/" "$VERSION_FILE"
}

cmd_publish() {
  local current_version sha tag prev_tag changelog new_version default_branch
  local commit_count changelog_limit=50 pushed=false prerelease_args=()

  current_version="$(cmd_current)"
  sha="$(cmd_sha "${GITHUB_SHA:-}")"
  tag="${current_version}"

  git fetch --tags --quiet || true
  prev_tag="$(git describe --tags --abbrev=0 2>/dev/null || true)"
  if [ -n "$prev_tag" ]; then
    changelog="$(git log "${prev_tag}..HEAD" --pretty=format:'- %s (%h)')"
  else
    changelog="$(git log -n "$changelog_limit" --pretty=format:'- %s (%h)')"
    commit_count="$(git rev-list --count HEAD 2>/dev/null || echo 0)"
    if [ "$commit_count" -gt "$changelog_limit" ]; then
      changelog="${changelog}"$'\n\n'"Showing the latest ${changelog_limit} commits because this repository has no previous release tag."
    fi
  fi
  [ -n "$changelog" ] || changelog="No changes recorded."
  changelog="Source commit: ${sha}"$'\n\n'"${changelog}"

  is_prerelease "$current_version" && prerelease_args=(--prerelease)

  echo "Publishing release ${tag}"
  gh release create "$tag" \
    --target "${GITHUB_SHA:-HEAD}" \
    --title "$tag" \
    --notes "$changelog" \
    "${prerelease_args[@]}"

  new_version="$(next_version "$current_version")"
  echo "Bumping version: ${current_version} -> ${new_version}"

  default_branch="${GITHUB_REF_NAME:-master}"
  git config user.email "github-actions[bot]@users.noreply.github.com"
  git config user.name "github-actions[bot]"
  git fetch origin "$default_branch" --quiet
  git checkout -B "$default_branch" "origin/${default_branch}"

  write_version "$new_version"
  git add "$VERSION_FILE"
  git commit -m "[skip ci] Bump version to ${new_version}"

  for attempt in 1 2 3; do
    if git push origin "$default_branch"; then
      pushed=true
      break
    fi
    echo "Push rejected, rebasing and retrying (${attempt})..."
    git fetch origin "$default_branch" --quiet
    if ! git rebase "origin/${default_branch}"; then
      git rebase --abort || true
      echo "Failed to rebase version bump onto origin/${default_branch}" >&2
      exit 1
    fi
  done
  if [ "$pushed" != true ]; then
    echo "Failed to push version bump after 3 attempts" >&2
    exit 1
  fi
}

case "${1:-}" in
  sha) shift; cmd_sha "${1:-}" ;;
  current) cmd_current ;;
  full-version) cmd_full_version ;;
  publish) cmd_publish ;;
  *) echo "Usage: $0 {sha [raw-sha]|current|full-version|publish}" >&2; exit 1 ;;
esac
