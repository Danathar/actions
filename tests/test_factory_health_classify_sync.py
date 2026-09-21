"""The bats CLASSIFY_LOGIC heredoc must stay byte-identical to the workflow.

`tests/bats/test_factory_health_status.bats` tests the classification rules by
hand-copying them out of `.github/workflows/factory-health.yml` into a
`CLASSIFY_LOGIC` heredoc, because the logic lives inline in the workflow YAML
and cannot be sourced. That copy is only as good as the promise to keep it in
sync, and a comment is not a mechanism: an edit to the workflow's
`monitor_pipeline()` alone leaves the bats copy testing the old rules and
passing green.

This closes that loop the same way `test_factory_health_resolve.py` pins
`title_prefix` — by parsing the workflow and asserting the two copies agree.
It lives in pytest rather than bats on purpose: `unit-tests.yml` triggers the
pytest job on `.github/workflows/factory-health.yml`, so editing the workflow
runs this check.

The markers are the contract. If you move the block, move the markers with it;
do not delete them to make this pass.
"""
from pathlib import Path
import re

REPO_ROOT = Path(__file__).parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "factory-health.yml"
BATS = REPO_ROOT / "tests" / "bats" / "test_factory_health_status.bats"

BEGIN_MARKER = "# --- verbatim from factory-health.yml, monitor_pipeline() ---"
END_MARKER = "# --- end verbatim ---"


def _copied_block():
    """The lines the bats file claims are verbatim from the workflow."""
    text = BATS.read_text(encoding="utf-8")
    begin = text.index(BEGIN_MARKER) + len(BEGIN_MARKER)
    end = text.index(END_MARKER)
    return text[begin:end].strip("\n").splitlines()


def _workflow_block(expected):
    """The matching run of lines in the workflow, dedented to the copy's margin.

    Anchored on the copy's first line so the test reports "anchor line is gone"
    rather than a wall of diff when the block is edited or moved.
    """
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    anchor = expected[0]
    matches = [i for i, line in enumerate(lines) if line.strip() == anchor.strip()]
    assert matches, (
        f"{WORKFLOW.name} no longer contains the line the bats CLASSIFY_LOGIC "
        f"heredoc starts with:\n\n  {anchor}\n\n"
        "The copy in tests/bats/test_factory_health_status.bats is now testing "
        "rules the workflow does not have. Re-copy the block between the "
        "verbatim markers."
    )
    start = matches[0]
    indent = len(lines[start]) - len(lines[start].lstrip())
    block = []
    for line in lines[start : start + len(expected)]:
        if not line.strip():
            block.append("")
            continue
        assert line.startswith(" " * indent), (
            f"Unexpected outdent in {WORKFLOW.name} inside the copied block: {line!r}"
        )
        block.append(line[indent:])
    return block


def test_classify_logic_heredoc_matches_the_workflow():
    expected = _copied_block()
    actual = _workflow_block(expected)

    assert actual == expected, (
        "The CLASSIFY_LOGIC heredoc in tests/bats/test_factory_health_status.bats "
        "has drifted from monitor_pipeline() in .github/workflows/factory-health.yml.\n\n"
        "The bats suite is testing a stale copy of the classification rules. "
        "Re-copy the workflow block (dedented) between the verbatim markers.\n\n"
        + "\n".join(
            f"  line {i + 1}:\n    workflow: {a!r}\n    bats copy: {e!r}"
            for i, (a, e) in enumerate(zip(actual, expected))
            if a != e
        )
    )


def test_copied_block_covers_the_status_assignments():
    """Guard against the block being trimmed to a trivially-matching stub."""
    copied = "\n".join(_copied_block())
    for status in ("healthy", "alert", "low-sample", "no-runs"):
        assert f'status="{status}"' in copied, (
            f'The copied block no longer assigns status="{status}"; it has been '
            "narrowed to less than the classification logic the bats tests claim "
            "to cover."
        )
    for floor in ("MIN_RUNS", "MIN_CONSECUTIVE_FAILURES", "THRESHOLD"):
        assert floor in copied, f"The copied block no longer references {floor}."


def test_thresholds_in_bats_setup_match_the_workflow():
    """The copy is verbatim, but the values it runs against are set in bats setup()."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    bats = BATS.read_text(encoding="utf-8")

    for name in ("THRESHOLD", "MIN_RUNS", "MIN_CONSECUTIVE_FAILURES"):
        in_workflow = re.search(rf"^\s*{name}=(\d+)\s*$", workflow, re.MULTILINE)
        assert in_workflow, f"{name} is not assigned a literal in {WORKFLOW.name}"
        in_bats = re.search(rf"^\s*export {name}=(\d+)\s*$", bats, re.MULTILINE)
        assert in_bats, f"{name} is not exported in the bats setup()"
        assert in_workflow.group(1) == in_bats.group(1), (
            f"{name} is {in_workflow.group(1)} in {WORKFLOW.name} but "
            f"{in_bats.group(1)} in the bats setup(); the bats suite is "
            "exercising thresholds the workflow does not use."
        )
