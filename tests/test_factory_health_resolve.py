"""
Tests for scripts/factory_health_resolve.py — factory alert recovery pass.

Covers: title-prefix agreement with factory-health.yml, healthy pipelines
closing their alerts, alerting/no-runs pipelines keeping them open, duplicate
alerts, cross-pipeline isolation, and the CLI round trip.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

# conftest.py already puts scripts/ on sys.path
from factory_health_resolve import alert_title_prefix, recovered_alerts

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "factory-health.yml"
SCRIPT = REPO_ROOT / "scripts" / "factory_health_resolve.py"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _result(
    repo: str = "projectbluefin/dakota",
    pipeline: str = "Promote",
    workflow: str = "Publish Bluefin dakota",
    status: str = "healthy",
    rate_display: str = "100%",
    success: int = 4,
    total: int = 4,
) -> dict:
    return {
        "repo": repo,
        "pipeline": pipeline,
        "workflow": workflow,
        "status": status,
        "rate_display": rate_display,
        "success": success,
        "total": total,
    }


def _issue(number: int = 490, repo: str = "projectbluefin/dakota", pipeline: str = "Promote") -> dict:
    title = f"{alert_title_prefix(repo, pipeline)} to 75% (24h window)"
    return {
        "number": number,
        "title": title,
        "url": f"https://github.com/projectbluefin/actions/issues/{number}",
    }


# ── alert_title_prefix ────────────────────────────────────────────────────────

class TestAlertTitlePrefix:
    def test_matches_the_issue_title_factory_health_opens(self):
        # Exactly the title of projectbluefin/actions#490.
        title = (
            "fix(factory): [projectbluefin/dakota] [Promote] "
            "success rate dropped to 75% (24h window)"
        )
        assert title.startswith(alert_title_prefix("projectbluefin/dakota", "Promote"))

    def test_prefix_stays_in_sync_with_the_workflow(self):
        """The close pass and the open pass must agree on the dedupe key."""
        workflow = WORKFLOW.read_text(encoding="utf-8")
        match = re.search(r'title_prefix="([^"]+)"', workflow)
        assert match, "factory-health.yml no longer defines title_prefix"
        expected = (
            match.group(1)
            .replace("${repo}", "projectbluefin/dakota")
            .replace("${pipeline}", "Promote")
        )
        assert expected == alert_title_prefix("projectbluefin/dakota", "Promote")


# ── recovered_alerts ──────────────────────────────────────────────────────────

class TestRecoveredAlerts:
    def test_healthy_pipeline_with_open_alert_is_recovered(self):
        recovered = recovered_alerts([_result()], [_issue()])
        assert [r["number"] for r in recovered] == [490]
        assert recovered[0]["rate_display"] == "100%"
        assert recovered[0]["workflow"] == "Publish Bluefin dakota"
        assert recovered[0]["success"] == 4
        assert recovered[0]["total"] == 4

    def test_healthy_pipeline_without_open_alert_is_a_noop(self):
        assert recovered_alerts([_result()], []) == []

    def test_alerting_pipeline_keeps_its_issue_open(self):
        results = [_result(status="alert", rate_display="75%", success=3, total=4)]
        assert recovered_alerts(results, [_issue()]) == []

    def test_no_runs_window_is_not_a_recovery(self):
        """An empty window is absence of evidence, not evidence of a fix."""
        results = [_result(status="no-runs", rate_display="n/a", success=0, total=0)]
        assert recovered_alerts(results, [_issue()]) == []

    def test_other_pipelines_in_the_same_repo_are_untouched(self):
        results = [_result(pipeline="Promote")]
        build_alert = _issue(number=482, pipeline="Build")
        assert recovered_alerts(results, [build_alert]) == []

    def test_same_pipeline_name_in_another_repo_is_untouched(self):
        results = [_result(repo="projectbluefin/dakota", pipeline="Promote")]
        lts_alert = _issue(number=481, repo="projectbluefin/bluefin-lts", pipeline="Promote")
        assert recovered_alerts(results, [lts_alert]) == []

    def test_duplicate_alerts_for_one_pipeline_are_all_closed(self):
        issues = [_issue(number=490), _issue(number=491)]
        recovered = recovered_alerts([_result()], issues)
        assert sorted(r["number"] for r in recovered) == [490, 491]

    def test_unrelated_issues_are_ignored(self):
        issues = [{"number": 546, "title": "Enforce thin-caller gate", "url": ""}]
        assert recovered_alerts([_result()], issues) == []

    def test_multiple_pipelines_recover_independently(self):
        results = [
            _result(pipeline="Promote"),
            _result(pipeline="Build", workflow="Build Bluefin dakota", status="alert"),
        ]
        issues = [_issue(number=490, pipeline="Promote"), _issue(number=482, pipeline="Build")]
        recovered = recovered_alerts(results, issues)
        assert [r["number"] for r in recovered] == [490]

    def test_missing_fields_do_not_raise(self):
        recovered = recovered_alerts([{"status": "healthy"}], [{"title": ""}])
        assert recovered == []


# ── CLI ───────────────────────────────────────────────────────────────────────

class TestCli:
    def test_cli_writes_the_recovery_array(self, tmp_path):
        results_path = tmp_path / "results.json"
        issues_path = tmp_path / "issues.json"
        results_path.write_text(json.dumps([_result()]), encoding="utf-8")
        issues_path.write_text(json.dumps([_issue()]), encoding="utf-8")

        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--results",
                str(results_path),
                "--open-issues",
                str(issues_path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert [r["number"] for r in json.loads(proc.stdout)] == [490]

    def test_cli_output_flag_writes_a_file(self, tmp_path):
        results_path = tmp_path / "results.json"
        issues_path = tmp_path / "issues.json"
        out_path = tmp_path / "recovered.json"
        results_path.write_text(json.dumps([_result()]), encoding="utf-8")
        issues_path.write_text(json.dumps([_issue()]), encoding="utf-8")

        subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--results",
                str(results_path),
                "--open-issues",
                str(issues_path),
                "--output",
                str(out_path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert [r["number"] for r in json.loads(out_path.read_text(encoding="utf-8"))] == [490]
