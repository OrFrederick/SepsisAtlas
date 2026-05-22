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

# Type and Priority are pure new fields — gh project handles them.
create_field "Type"     "${TYPE_OPTIONS}"
create_field "Priority" "${PRIORITY_OPTIONS}"

# Status is auto-created by GitHub with default Todo/In Progress/Done. Replace
# its options via GraphQL (gh project field-create cannot edit an existing
# single-select field).
log "Replacing Status field options"
STATUS_FIELD_ID=$(gh project field-list "${PROJECT_NUMBER}" --owner "${OWNER}" \
  --format json | jq -r '.fields[] | select(.name=="Status") | .id')
[[ -n "${STATUS_FIELD_ID}" ]] || err "Status field not found"

gh api graphql -f query='
mutation($fieldId: ID!) {
  updateProjectV2Field(input: {
    fieldId: $fieldId,
    singleSelectOptions: [
      {name: "Inbox",       color: GRAY,   description: "New, unreviewed"},
      {name: "Triaged",     color: BLUE,   description: "Reviewed, prioritized"},
      {name: "In progress", color: YELLOW, description: "Being worked"},
      {name: "In review",   color: PURPLE, description: "PR open"},
      {name: "Done",        color: GREEN,  description: "Shipped or closed"},
      {name: "Wontfix",     color: RED,    description: "Closed not planned"}
    ]
  }) { projectV2Field { ... on ProjectV2SingleSelectField { name } } }
}' -f fieldId="${STATUS_FIELD_ID}" >/dev/null

# 4. Backfill currently-open issues
log "Backfilling open issues from ${REPO}"
mapfile -t ISSUE_URLS < <(gh issue list --repo "${REPO}" --state open --limit 200 \
  --json url --jq '.[].url')

for url in "${ISSUE_URLS[@]}"; do
  log "  Adding ${url}"
  gh project item-add "${PROJECT_NUMBER}" --owner "${OWNER}" --url "${url}" >/dev/null || true
done

# 5. Set Status="Inbox" on every item that has no status yet
log "Defaulting items without Status to Inbox"
PROJECT_INFO=$(gh api graphql -f query='
query($login: String!, $num: Int!) {
  user(login: $login) {
    projectV2(number: $num) {
      id
      field(name: "Status") {
        ... on ProjectV2SingleSelectField {
          id
          options(names: ["Inbox"]) { id name }
        }
      }
      items(first: 100) {
        nodes {
          id
          fieldValueByName(name: "Status") {
            ... on ProjectV2ItemFieldSingleSelectValue { name }
          }
        }
      }
    }
  }
}' -F login="${OWNER}" -F num="${PROJECT_NUMBER}")

PROJECT_ID=$(jq -r '.data.user.projectV2.id'                <<<"${PROJECT_INFO}")
STATUS_FID=$(jq -r '.data.user.projectV2.field.id'          <<<"${PROJECT_INFO}")
INBOX_OID=$( jq -r '.data.user.projectV2.field.options[0].id' <<<"${PROJECT_INFO}")
mapfile -t UNSET_ITEM_IDS < <(jq -r '
  .data.user.projectV2.items.nodes[]
  | select(.fieldValueByName == null) | .id' <<<"${PROJECT_INFO}")

for item in "${UNSET_ITEM_IDS[@]}"; do
  gh api graphql -f query='
    mutation($p: ID!, $i: ID!, $f: ID!, $o: String!) {
      updateProjectV2ItemFieldValue(input: {
        projectId: $p, itemId: $i, fieldId: $f,
        value: { singleSelectOptionId: $o }
      }) { projectV2Item { id } }
    }' -f p="${PROJECT_ID}" -f i="${item}" -f f="${STATUS_FID}" \
       -f o="${INBOX_OID}" >/dev/null
  log "  ${item} → Inbox"
done

log "Done."
log ""
log "Project: https://github.com/users/${OWNER}/projects/${PROJECT_NUMBER}"
log ""
log "Next: open Workflows in the web UI and enable the four built-in"
log "workflows listed at the top of this script."
