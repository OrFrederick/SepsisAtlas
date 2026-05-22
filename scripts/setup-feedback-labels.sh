#!/usr/bin/env bash
# Idempotently creates the labels used by the feedback feature. Run once per
# environment (production + any test repo configured via GITHUB_FEEDBACK_REPO).
#
# Usage:
#   scripts/setup-feedback-labels.sh [owner/repo]
# Defaults to OrFrederick/SepsisAtlas.

set -euo pipefail
REPO="${1:-OrFrederick/SepsisAtlas}"

create_label() {
  local name="$1" color="$2" desc="$3"
  if gh label list --repo "${REPO}" --json name --jq '.[].name' | grep -Fxq "${name}"; then
    echo "exists: ${name}"
    return
  fi
  gh label create "${name}" --repo "${REPO}" --color "${color}" --description "${desc}"
  echo "created: ${name}"
}

create_label "feedback"             "cccccc" "Submitted via the website feedback form"
create_label "from-website"         "cccccc" "Distinguishes from manually-filed issues"
create_label "needs-triage"         "fbca04" "Awaiting maintainer review"
create_label "feedback:bug"         "d73a4a" "Bug report submitted via feedback form"
create_label "feedback:wrong-data"  "e99695" "Data correction submitted via feedback form"
create_label "feedback:idea"        "0e8a16" "Feature request submitted via feedback form"
create_label "feedback:other"       "ededed" "Other feedback submitted via feedback form"

echo "Done. paper:* labels are created lazily by the API route."
