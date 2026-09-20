"""
The factory health monitor's run fetch must be bounded by the 24h window, not
by a run count (projectbluefin/actions#503 follow-up).

`gh run list --limit N` returns a pipeline's N most recent runs regardless of
when they ran, and the monitor then drops everything older than the window in
jq. That makes the measured sample a function of run volume: once a pipeline
produces more than N runs inside the window, the oldest in-window runs are
never fetched and the rate is computed from a silently truncated sample. In
the worst case every fetched run predates the window and a healthy pipeline
reports `no-runs`.

The `run:` block is shell inside YAML, so no job in CI executes it. These are
contract tests over the source: they pin the properties that make the fetch
window-bounded, so a future edit cannot quietly reintroduce the count-bounded
behaviour.
"""
from pathlib import Path
import re

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "factory-health.yml"


@pytest.fixture(scope="module")
def monitor_script() -> str:
    """The shell body of the workflow's monitoring step."""
    jobs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    scripts = [
        step.get("run", "")
        for step in jobs["monitor"]["steps"]
        if "gh run list" in step.get("run", "")
    ]
    assert len(scripts) == 1, "expected exactly one step that lists workflow runs"
    return scripts[0]


@pytest.fixture(scope="module")
def run_list_invocation(monitor_script: str) -> str:
    """The `gh run list ... --json ...` command, line continuations joined."""
    joined = monitor_script.replace("\\\n", " ")
    # Anchored on the assignment so a prose mention of `gh run list` in a
    # comment cannot be mistaken for the invocation.
    match = re.search(r"runs_json=\$\(gh run list\b.*", joined)
    assert match, "no `runs_json=$(gh run list ...)` invocation found in the monitoring step"
    return match.group(0)


def test_run_fetch_is_bounded_by_the_window(run_list_invocation: str):
    # The load-bearing assertion: without --created the fetch is bounded only
    # by --limit and the window is defended by nothing but run volume.
    assert '--created ">=${CUTOFF_ISO}"' in run_list_invocation


def test_fetch_bound_and_rate_cutoff_share_one_source(monitor_script: str):
    # CUTOFF_ISO must be the same instant as CUTOFF_EPOCH, and CUTOFF_EPOCH the
    # same WINDOW_HOURS the alert body reports. Deriving one from the other is
    # what stops the fetched window and the measured window from drifting.
    assert 'CUTOFF_EPOCH=$(date -u -d "${WINDOW_HOURS} hours ago" \'+%s\')' in monitor_script
    assert 'CUTOFF_ISO=$(date -u -d "@${CUTOFF_EPOCH}" \'+%Y-%m-%dT%H:%M:%SZ\')' in monitor_script

    # ...and CUTOFF_ISO is derived after CUTOFF_EPOCH exists, not before.
    assert monitor_script.index("CUTOFF_EPOCH=$(") < monitor_script.index("CUTOFF_ISO=$(")


def test_fetch_limit_is_a_named_constant_not_a_literal(run_list_invocation: str):
    # A literal here cannot be compared against by the saturation check below.
    assert '--limit "${RUN_FETCH_LIMIT}"' in run_list_invocation
    assert not re.search(r"--limit\s+\d", run_list_invocation)


def test_fetch_limit_is_declared_as_an_integer(monitor_script: str):
    match = re.search(r"^\s*RUN_FETCH_LIMIT=(\d+)\s*$", monitor_script, re.MULTILINE)
    assert match, "RUN_FETCH_LIMIT must be declared as a bare integer"
    assert int(match.group(1)) >= 100, "the cap must not regress below the historical fetch size"


def test_saturating_the_cap_is_reported(monitor_script: str):
    # A truncated sample must be visible. Reporting a rate computed from a
    # partial window as if it were the whole window is the failure mode.
    assert "fetched=$(jq 'length' <<<\"${runs_json}\")" in monitor_script
    assert "(( fetched >= RUN_FETCH_LIMIT ))" in monitor_script

    warning = next(
        (line for line in monitor_script.splitlines() if "saturating" in line),
        None,
    )
    assert warning is not None, "no warning emitted when the fetch cap saturates"
    assert "::warning::" in warning
    # monitor_pipeline() writes its result JSON to stdout and the caller
    # captures it, so a workflow command on stdout would corrupt that JSON.
    # The runner parses ::warning:: from stderr too, so >&2 costs nothing.
    assert warning.rstrip().endswith(">&2")


def test_client_side_cutoff_filter_is_retained(monitor_script: str):
    # --created is a search qualifier applied by the API, not a guarantee about
    # the returned set. The rate must be computed over exactly the window the
    # alert claims, so the jq cutoff stays regardless.
    assert 'jq --argjson cutoff "${CUTOFF_EPOCH}"' in monitor_script
    assert "select((.createdAt | fromdateiso8601) >= $cutoff)" in monitor_script


def test_fetched_is_function_local(monitor_script: str):
    # `fetched` is assigned inside monitor_pipeline(), which runs in a loop over
    # every monitored pipeline; leaking it would carry one pipeline's count into
    # the next iteration's warning.
    local_decl = next(
        line for line in monitor_script.splitlines() if line.strip().startswith("local runs_json")
    )
    assert re.search(r"\bfetched\b", local_decl)
