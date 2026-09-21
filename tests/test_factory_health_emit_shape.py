"""The workflow's jq row must carry the same keys the Python mirror returns.

`scripts/monitor_pipeline.py` is the unit-testable mirror of the inline
`monitor_pipeline()` in `.github/workflows/factory-health.yml`, and its
docstring states that "the output JSON matches the shape written by
monitor_pipeline() in the workflow". That claim was aspirational: the mirror
returned `min_runs` and the workflow's `jq -n` emit did not, so the two shapes
had silently diverged (projectbluefin/actions#560 review).

A docstring is not a mechanism. This test parses the key list out of the
workflow's jq object literal and compares it against the keys
`compute_pipeline_health()` actually returns, plus the three identity fields
(`repo`, `pipeline`, `workflow`) that only the workflow knows.

Like `test_factory_health_classify_sync.py`, this lives in pytest rather than
bats because `unit-tests.yml` triggers the pytest job on
`.github/workflows/factory-health.yml`, so editing the workflow runs it.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re

# The script under test — conftest.py already patches sys.path
from monitor_pipeline import compute_pipeline_health

REPO_ROOT = Path(__file__).parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "factory-health.yml"

# Fields the workflow supplies from the loop, not from the health computation.
IDENTITY_FIELDS = {"repo", "pipeline", "workflow"}


def _emitted_keys():
    """Keys in the `jq -n '{...}'` object the workflow writes per pipeline."""
    text = WORKFLOW.read_text(encoding="utf-8")
    match = re.search(r"jq -n \\\n(.*?)'\{\n(.*?)\n\s*\}'", text, re.DOTALL)
    assert match, (
        f"Could not locate the `jq -n '{{...}}'` row emit in {WORKFLOW.name}. "
        "If the emit moved or changed form, update this test rather than "
        "deleting it — it is the only guard that the workflow row and "
        "scripts/monitor_pipeline.py agree on a shape."
    )
    body = match.group(2)
    return [
        m.group(1)
        for m in re.finditer(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:", body, re.MULTILINE)
    ]


def _computed_keys():
    """Keys `compute_pipeline_health()` returns for a representative window."""
    now = datetime.now(timezone.utc)
    cutoff_epoch = (now - timedelta(hours=24)).timestamp()
    runs = [
        {
            "conclusion": "success",
            "status": "completed",
            "createdAt": (now - timedelta(hours=1)).isoformat(),
            "url": "https://example.invalid/1",
        }
    ]
    return set(compute_pipeline_health(runs, cutoff_epoch))


def test_workflow_row_and_python_mirror_agree_on_keys():
    emitted = set(_emitted_keys())
    expected = _computed_keys() | IDENTITY_FIELDS

    missing = expected - emitted
    extra = emitted - expected

    assert not missing and not extra, (
        "The factory-health row shape has drifted from "
        "scripts/monitor_pipeline.py.\n\n"
        f"  in compute_pipeline_health() but not emitted by the workflow: "
        f"{sorted(missing) or 'none'}\n"
        f"  emitted by the workflow but not in compute_pipeline_health(): "
        f"{sorted(extra) or 'none'}\n\n"
        "Add the field to whichever side is behind, or stop claiming parity "
        "in the monitor_pipeline.py docstring."
    )


def test_emit_has_no_duplicate_keys():
    """A duplicated jq key silently wins over the earlier one."""
    emitted = _emitted_keys()
    duplicates = sorted({k for k in emitted if emitted.count(k) > 1})
    assert not duplicates, (
        f"The workflow's jq row emits duplicate key(s): {duplicates}. "
        "jq keeps the last, so the earlier value is silently discarded."
    )
