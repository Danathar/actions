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
    """An unfetched consumer is indistinguishable from a clean repo by exit code.

    The fetch loop `mkdir -p`s `<root>/.github/workflows` before the `gh api`
    calls, so an empty directory is a reachable state. The validator reports
    "nothing to check" and exits 0 -- correct for the validator, useless for a
    backstop. Check 4 must therefore count the snapshot itself rather than
    trusting a zero exit.
    """
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)

    result = _run_validator(tmp_path)
    assert result.returncode == 0
    assert "nothing to check" in result.stdout
    assert not any(wf_dir.iterdir())


def test_a_zero_byte_workflow_passes_the_validator(tmp_path):
    """Why Check 4 counts non-empty files and compares against an expected count.

    A per-file fetch failure is the quiet one: the redirect creates the file
    before `gh api` runs, so a failure leaves a 0-byte stub. The validator has
    nothing to complain about in an empty file, so a snapshot that silently
    lost the one oversized caller exits 0 and reads as compliant. The fetch
    step therefore deletes such stubs, and Check 4 compares the number of
    files that landed against the number the listing advertised.
    """
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "promote-testing-to-main.yml").write_text("")

    result = _run_validator(tmp_path)
    assert result.returncode == 0
    assert "promote-testing-to-main.yml" not in result.stdout


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


def test_failed_fetch_stub_is_removed_and_empty_files_still_count():
    """A failed per-file fetch leaves a 0-byte stub; the fetch step must delete
    it so the shortfall shows up in the count. Check 4 must then count every
    file that is present, including a legitimately empty workflow file:
    filtering on size there would report a permanent shortfall for a consumer
    whose advertised count includes that empty file."""
    text = FACTORY_DRIFT.read_text()
    fetch_block = text.split("- name: Detect drift", 1)[0]
    assert re.search(r'rm -f "\$\{dest\}/\$\{wf\}"', fetch_block), (
        "the fetch loop must remove the 0-byte stub a failed fetch leaves behind"
    )
    block = _check4_block()
    assert "-size +0" not in block, (
        "Check 4 must not filter on size: a successfully fetched empty file "
        "is advertised and present, and excluding it makes advertised > landed forever."
    )


def test_check4_reports_a_shortfall_against_the_advertised_count():
    """Losing some files must not read the same as losing none."""
    block = _check4_block()
    assert "thin-caller-snapshot-incomplete" in block
    assert re.search(r"snapshot_count\s*<\s*expected_count", block)


def test_fetch_step_records_a_failed_listing_as_that_consumers_drift():
    """A failed listing is loud for its consumer and contained to it.

    The earlier form piped `gh api ... 2>/dev/null` straight into a `while`
    loop, so a failed listing produced an empty snapshot that read as a clean
    consumer. The next form aborted the whole step, which skipped every check
    for every other consumer that week. Now the listing is tested directly,
    a failure writes a `.listing-failed` marker for that consumer and moves
    on, and Check 4 turns the marker into a drift item before it counts
    anything.
    """
    text = FACTORY_DRIFT.read_text()
    start = text.index("Fetch workflow files")
    block = text[start : text.index("Detect drift")]

    assert re.search(r"if ! wf_list=\$\(\s*\n\s*gh api", block), (
        "The consumer workflow listing must be tested directly so a non-zero "
        "gh api is recorded for that consumer instead of aborting the step."
    )
    assert ".listing-failed" in block
    assert not re.search(r"gh api[^\n]*contents/\.github/workflows\"[^\n]*2>/dev/null", block), (
        "A listing failure must not be swallowed into an empty snapshot."
    )
    check4 = _check4_block()
    assert "thin-caller-listing-failed" in check4
    assert check4.index(".listing-failed") < check4.index("snapshot_count == 0"), (
        "Check 4 must read the listing-failed marker before any count, or a "
        "listing failure reads as an empty snapshot."
    )


def test_check4_does_not_flag_a_consumer_that_advertises_no_workflows():
    """Zero advertised files means nothing to gate, not a snapshot failure."""
    check4 = _check4_block()
    assert re.search(r"expected_count == 0", check4)
    assert check4.index("expected_count == 0") < check4.index("snapshot_count == 0")


def test_fetch_step_removes_a_failed_per_file_stub():
    """The per-file fetch must not leave a 0-byte stub behind."""
    text = FACTORY_DRIFT.read_text()
    block = text[text.index("Fetch workflow files") : text.index("Detect drift")]
    assert "rm -f" in block
    assert not re.search(r"base64 -d >[^\n]*\|\|\s*true", block), (
        "A swallowed per-file fetch leaves a 0-byte file that counts as a "
        "fetched workflow."
    )
