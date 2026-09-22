#!/usr/bin/env bats
# Tests for scripts/renovate-automerge-wiring-check.sh
# Wiring assertion for the Renovate/MergeRaptor auto-merge path (issue #403).
#
# Covers:
#
#   - allow_auto_merge must be true
#   - mergeraptor must be the SOLE bypass actor on main (app type; no user/team,
#     no second app, present exactly once)
#   - a branch ruleset must enforce main
#   - the live mergeraptor/renovate PR report renders a found PR or "(none)"
#   - any failed assertion makes the script exit non-zero
#   - a token lacking push/administration access omits allow_auto_merge and
#     bypass_actors from the API response (JSON null / missing key) rather
#     than reporting them false/empty — this must be reported as a distinct
#     "unreadable" failure, never conflated with "the setting is off"
#     (projectbluefin/actions#514)
#   - a missing branch ruleset must not also emit the bypass-actor-count
#     failure (bypass_readable must gate that block in every unreadable path)

setup() {
  SCRIPT="${BATS_TEST_DIRNAME}/../../scripts/renovate-automerge-wiring-check.sh"
  export MOCK_DIR="$(mktemp -d)/bin"
  mkdir -p "$MOCK_DIR"
  export PATH="${MOCK_DIR}:${PATH}"
  export GH_TOKEN="test"
  export OWNER="projectbluefin"
  export REPO="actions"

  # Default healthy wiring; individual tests override via set_gh_mock.
  set_healthy_gh
}

teardown() {
  rm -rf "${MOCK_DIR%/*}"
}

# Build the mock gh script from three strings: the repo allow_auto_merge field,
# the ruleset JSON (post-`gh api`/`--jq`, i.e. what the script receives), and the
# graphql body (post-`--jq`, one line per PR). Payloads contain double quotes but
# no single quotes, so single-quoting them in the generated mock is safe.
write_gh_mock() {
  local repo_field="$1" ruleset="$2" graphql="$3"
  # shellcheck disable=SC2086,SC2054
  cat > "${MOCK_DIR}/gh" <<EOF
#!/usr/bin/env bash
case "\$1 \$2" in
  "api repos/projectbluefin/actions") echo '${repo_field}' ;;
  "api repos/projectbluefin/actions/rulesets") echo '${ruleset}' ;;
  "api graphql") printf '%s\\n' '${graphql}' ;;
esac
EOF
  chmod +x "${MOCK_DIR}/gh"
}

# Same idea, but exercises the real production path: `gh api .../rulesets`
# returns an array (as GitHub actually does), and the script fetches the
# matching branch ruleset's detail separately by id.
write_gh_mock_ruleset_list() {
  local repo_field="$1" list_json="$2" detail_json="$3" graphql="$4"
  # shellcheck disable=SC2086,SC2054
  cat > "${MOCK_DIR}/gh" <<EOF
#!/usr/bin/env bash
case "\$1 \$2" in
  "api repos/projectbluefin/actions") echo '${repo_field}' ;;
  "api repos/projectbluefin/actions/rulesets") echo '${list_json}' ;;
  "api repos/projectbluefin/actions/rulesets/42") echo '${detail_json}' ;;
  "api graphql") printf '%s\\n' '${graphql}' ;;
esac
EOF
  chmod +x "${MOCK_DIR}/gh"
}

set_healthy_gh() {
  write_gh_mock 'true' \
    '{"target":"branch","conditions":{"ref_name":{"include":["refs/heads/main"]}},"bypass_actors":[{"actor_name":"mergeraptor","actor_type":"app"}]}' \
    '  #449 app/mergeraptor autoMerge=2026-09-01T00:00:00Z'
}

ok() { grep -q "Auto-merge wiring check: OK" <<<"$output"; }
not_ok() { grep -q "Auto-merge wiring check: FAILED" <<<"$output"; }

@test "healthy wiring passes and reports the live mergeraptor PR" {
  run "$SCRIPT"
  [ "$status" -eq 0 ]
  [[ "$output" == *"allow_auto_merge = true"* ]]
  [[ "$output" == *'bypass_actors = [{"actor_name":"mergeraptor","actor_type":"app"}]'* ]]
  [[ "$output" == *"#449 app/mergeraptor autoMerge=2026-09-01T00:00:00Z"* ]]
  ok
}

@test "allow_auto_merge false fails" {
  write_gh_mock 'false' \
    '{"target":"branch","conditions":{"ref_name":{"include":["refs/heads/main"]}},"bypass_actors":[{"actor_name":"mergeraptor","actor_type":"app"}]}' \
    '  (none)'
  run "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"FAIL: repo allow_auto_merge is not true"* ]]
  not_ok
}

@test "allow_auto_merge omitted by the API (null) is reported as unreadable, not false" {
  write_gh_mock 'null' \
    '{"target":"branch","conditions":{"ref_name":{"include":["refs/heads/main"]}},"bypass_actors":[{"actor_name":"mergeraptor","actor_type":"app"}]}' \
    '  (none)'
  run "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"FAIL: repo allow_auto_merge is unreadable"* ]]
  [[ "$output" != *"FAIL: repo allow_auto_merge is not true"* ]]
}

@test "bypass_actors omitted by the API is reported as unreadable, not zero actors" {
  write_gh_mock 'true' \
    '{"target":"branch","conditions":{"ref_name":{"include":["refs/heads/main"]}}}' \
    '  (none)'
  run "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"FAIL: main branch ruleset bypass_actors is unreadable"* ]]
  [[ "$output" != *"FAIL: mergeraptor app is not exactly one bypass actor on main"* ]]
}

@test "no bypass actors on main fails" {
  write_gh_mock 'true' \
    '{"target":"branch","conditions":{"ref_name":{"include":["refs/heads/main"]}},"bypass_actors":[]}' \
    '  (none)'
  run "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"FAIL: mergeraptor app is not exactly one bypass actor on main"* ]]
}

@test "mergeraptor present as a user (not app) fails" {
  write_gh_mock 'true' \
    '{"target":"branch","conditions":{"ref_name":{"include":["refs/heads/main"]}},"bypass_actors":[{"actor_name":"mergeraptor","actor_type":"user"}]}' \
    '  (none)'
  run "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"FAIL: mergeraptor app is not exactly one bypass actor on main"* ]]
}

@test "a second, non-mergeraptor bypass actor fails" {
  write_gh_mock 'true' \
    '{"target":"branch","conditions":{"ref_name":{"include":["refs/heads/main"]}},"bypass_actors":[{"actor_name":"mergeraptor","actor_type":"app"},{"actor_name":"bob","actor_type":"user"}]}' \
    '  (none)'
  run "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"FAIL: non-mergeraptor bypass actor(s) present on main"* ]]
}

@test "two mergeraptor apps fails (must be exactly one)" {
  write_gh_mock 'true' \
    '{"target":"branch","conditions":{"ref_name":{"include":["refs/heads/main"]}},"bypass_actors":[{"actor_name":"mergeraptor","actor_type":"app"},{"actor_name":"mergeraptor","actor_type":"app"}]}' \
    '  (none)'
  run "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"FAIL: mergeraptor app is not exactly one bypass actor on main"* ]]
}

@test "no branch ruleset enforcing main fails, without a spurious bypass-actor message" {
  write_gh_mock 'true' 'null' '  (none)'
  run "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"FAIL: no branch ruleset enforces main"* ]]
  [[ "$output" != *"mergeraptor app is not exactly one bypass actor on main"* ]]
}

@test "array rulesets response: detail fetch finds main ruleset and reads bypass_actors" {
  write_gh_mock_ruleset_list 'true' \
    '[{"id":42,"target":"branch","enforcement":"active"}]' \
    '{"target":"branch","conditions":{"ref_name":{"include":["refs/heads/main"]}},"bypass_actors":[{"actor_name":"mergeraptor","actor_type":"app"}]}' \
    '  (none)'
  run "$SCRIPT"
  [ "$status" -eq 0 ]
  [[ "$output" == *'bypass_actors = [{"actor_name":"mergeraptor","actor_type":"app"}]'* ]]
  ok
}

@test "array rulesets response: detail fetch omits bypass_actors (non-admin token) is unreadable" {
  write_gh_mock_ruleset_list 'true' \
    '[{"id":42,"target":"branch","enforcement":"active"}]' \
    '{"target":"branch","conditions":{"ref_name":{"include":["refs/heads/main"]}}}' \
    '  (none)'
  run "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"FAIL: main branch ruleset bypass_actors is unreadable"* ]]
  [[ "$output" != *"FAIL: no branch ruleset enforces main"* ]]
  [[ "$output" != *"mergeraptor app is not exactly one bypass actor on main"* ]]
}

@test "live PR report shows (none) when no qualifying PR exists" {
  write_gh_mock 'true' \
    '{"target":"branch","conditions":{"ref_name":{"include":["refs/heads/main"]}},"bypass_actors":[{"actor_name":"mergeraptor","actor_type":"app"}]}' \
    '  (none)'
  run "$SCRIPT"
  [ "$status" -eq 0 ]
  [[ "$output" == *"live mergeraptor/renovate PRs with auto-merge enabled"* ]]
  [[ "$output" == *"(none)"* ]]
  ok
}

@test "renovate bot author spelling is recognised in the live report" {
  write_gh_mock 'true' \
    '{"target":"branch","conditions":{"ref_name":{"include":["refs/heads/main"]}},"bypass_actors":[{"actor_name":"mergeraptor","actor_type":"app"}]}' \
    '  #9 app/renovate[bot] autoMerge=null'
  run "$SCRIPT"
  [ "$status" -eq 0 ]
  [[ "$output" == *"#9 app/renovate[bot] autoMerge=null"* ]]
}
