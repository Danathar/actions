"""Regression coverage for factory-drift.yml's Check 4 (issue #546).

Check 4 shells out to `scripts/validate_thin_caller.py --root <consumer_root>`
against a fetched consumer workflow snapshot instead of reimplementing the
size rule with `wc -l` against a single hardcoded filename. `bats` is not
available in this environment to execute the embedded shell verbatim, so
this exercises the same script invocation the workflow performs and confirms
the behavior the fix depends on: real reusable-workflow violations are
caught, composite-action-only callers are not, and a workflow that is over
50 raw lines but under 50 effective lines (comments/blank lines) -- exactly
what tripped the old `wc -l` check on bluefin's `promote-testing-to-main.yml`
after it was extracted to a reusable -- does not false-positive.
"""
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
VALIDATOR = REPO_ROOT / "scripts" / "validate_thin_caller.py"
FACTORY_DRIFT = REPO_ROOT / ".github" / "workflows" / "factory-drift.yml"


def _run_validator(consumer_root):
    return subprocess.run(
        [sys.executable, str(VALIDATOR), "--root", str(consumer_root), "--max-lines", "50"],
        capture_output=True,
        text=True,
    )


def test_check4_flags_oversized_reusable_caller(tmp_path):
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    caller = wf_dir / "build-image-testing.yml"
    lines = ["name: Build\n", "uses: projectbluefin/actions/.github/workflows/reusable-build.yml@v1\n"]
    lines.extend([f"key_{i}: value_{i}\n" for i in range(60)])
    caller.write_text("".join(lines))

    result = _run_validator(tmp_path)
    assert result.returncode == 1
    assert "build-image-testing.yml" in result.stdout


def test_check4_ignores_composite_action_only_caller(tmp_path):
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    caller = wf_dir / "pr-validation.yml"
    lines = ["name: Validate\n", "uses: projectbluefin/actions/bootc-build/validate-pr@v1\n"]
    lines.extend([f"key_{i}: value_{i}\n" for i in range(200)])
    caller.write_text("".join(lines))

    result = _run_validator(tmp_path)
    assert result.returncode == 0
    assert "pr-validation.yml" not in result.stdout


def test_check4_no_false_positive_on_comment_heavy_caller_under_threshold(tmp_path):
    # Mirrors bluefin's real promote-testing-to-main.yml after extraction:
    # 54 physical lines (with comments/blank lines) but only 41 effective
    # lines. The old `wc -l`-based Check 4 flagged this as drift; the
    # canonical validator must not.
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    caller = wf_dir / "promote-testing-to-main.yml"
    lines = ["# comment\n"] * 13
    lines.append("name: Promote\n")
    lines.append("uses: projectbluefin/actions/.github/workflows/reusable-promote-squash.yml@v1\n")
    lines.extend([f"key_{i}: value_{i}\n" for i in range(39)])
    caller.write_text("".join(lines))
    assert len(lines) == 54

    result = _run_validator(tmp_path)
    assert result.returncode == 0
    assert "promote-testing-to-main.yml" not in result.stdout


def test_empty_snapshot_exits_zero_so_the_workflow_must_detect_it_itself(tmp_path):
    """A failed consumer fetch is indistinguishable from a clean repo by exit code.

    The fetch loop `mkdir -p`s `<root>/.github/workflows` before the `gh api`
    calls, so a rate-limited or errored fetch leaves an existing but empty
    directory. The validator reports "nothing to check" and exits 0 -- correct
    for the validator, useless for a backstop. Check 4 must therefore count the
    snapshot itself rather than trusting a zero exit.
    """
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)

    result = _run_validator(tmp_path)
    assert result.returncode == 0
    assert "nothing to check" in result.stdout
    assert not any(wf_dir.iterdir())


def _check4_block():
    text = FACTORY_DRIFT.read_text()
    start = text.index("Check 4:")
    end = text.index("Check 5:")
    return text[start:end]


def test_check4_uses_the_exit_code_not_just_grepped_stdout():
    """Regression: the verdict is the exit status, not the presence of `  - ` lines.

    Grepping stdout alone reports "no drift" for a crashed validator, since a
    traceback contains no violation lines -- the one failure mode a backstop
    cannot have.
    """
    block = _check4_block()
    assert "gate_status=$?" in block
    assert re.search(r"if\s+\(\(\s*gate_status\s*==\s*0\s*\)\)", block)
    # The old form piped the validator straight into grep and dropped the status.
    assert not re.search(r"validate_thin_caller\.py[^\n]*\n\s*\|\s*grep", block)


def test_check4_reports_empty_snapshots_and_validator_errors_as_drift():
    block = _check4_block()
    assert "thin-caller-snapshot-empty" in block
    assert "thin-caller-gate-error" in block
    assert "snapshot_count == 0" in block
