#!/usr/bin/env bash
# Creates the "SepsisAtlas Feedback" GitHub Project (v2), adds fields, links
# the repo, and backfills currently-open issues into the board.
#
# Prereqs:
#   1. gh CLI authenticated with `project` scope:
#        gh auth refresh -s project
#   2. Run from anywhere; OWNER/REPO are hard-coded below.
#
# Post-step (manual, web UI ~30s):
#   - Open the project → Workflows → enable:
#       * Auto-add to project: filter `label:feedback is:issue is:open`
#       * Item closed → set Status to Done
#       * Item closed as not planned → set Status to Wontfix
#       * Pull request merged → set Status to Done
#
# Idempotent: re-running detects an existing project by title and reuses it.

set -euo pipefail

OWNER="OrFrederick"
REPO="${OWNER}/SepsisAtlas"
TITLE="SepsisAtlas Feedback"

STATUS_OPTIONS="Inbox,Triaged,In progress,In review,Done,Wontfix"
TYPE_OPTIONS="bug,wrong-data,idea,other"
PRIORITY_OPTIONS="P0,P1,P2,P3"

err() { echo "error: $*" >&2; exit 1; }
log() { echo "==> $*"; }

command -v gh >/dev/null || err "gh CLI not installed"
command -v jq >/dev/null || err "jq required"

# Verify project scope
if ! gh auth status 2>&1 | grep -q "project"; then
  err "token missing 'project' scope. Run: gh auth refresh -s project"
fi

# 1. Find existing project or create
log "Looking for existing project '${TITLE}' under ${OWNER}"
PROJECT_NUMBER=$(gh project list --owner "${OWNER}" --format json \
  | jq -r --arg t "${TITLE}" '.projects[] | select(.title == $t) | .number' \
  | head -n 1)

if [[ -z "${PROJECT_NUMBER}" ]]; then
  log "Creating project"
  PROJECT_NUMBER=$(gh project create --owner "${OWNER}" --title "${TITLE}" \
    --format json | jq -r '.number')
  log "Created project #${PROJECT_NUMBER}"
else
  log "Reusing project #${PROJECT_NUMBER}"
fi

# 2. Link to repo
log "Linking project to ${REPO}"
gh project link "${PROJECT_NUMBER}" --owner "${OWNER}" --repo "${REPO}" >/dev/null || true

# 3. Create fields (skip if already present)
existing_fields=$(gh project field-list "${PROJECT_NUMBER}" --owner "${OWNER}" \
  --format json | jq -r '.fields[].name')

create_field() {
  local name="$1" opts="$2"
  if grep -Fxq "${name}" <<<"${existing_fields}"; then
    log "Field '${name}' already exists, skipping"
    return
  fi
  log "Creating field '${name}'"
  gh project field-create "${PROJECT_NUMBER}" --owner "${OWNER}" \
    --name "${name}" --data-type SINGLE_SELECT \
    --single-select-options "${opts}" >/dev/null
}

# Note: GitHub auto-creates a "Status" field on new projects. We re-create only
# if missing; the default Status field's options ("Todo/In Progress/Done") will
# not be modified here — adjust manually in the UI or delete + recreate.
create_field "Status"   "${STATUS_OPTIONS}"
create_field "Type"     "${TYPE_OPTIONS}"
create_field "Priority" "${PRIORITY_OPTIONS}"

# 4. Backfill currently-open issues
log "Backfilling open issues from ${REPO}"
mapfile -t ISSUE_URLS < <(gh issue list --repo "${REPO}" --state open --limit 200 \
  --json url --jq '.[].url')

for url in "${ISSUE_URLS[@]}"; do
  log "  Adding ${url}"
  gh project item-add "${PROJECT_NUMBER}" --owner "${OWNER}" --url "${url}" >/dev/null || true
done

log "Done."
log ""
log "Project: https://github.com/users/${OWNER}/projects/${PROJECT_NUMBER}"
log ""
log "Next: open Workflows in the web UI and enable the four built-in"
log "workflows listed at the top of this script."
