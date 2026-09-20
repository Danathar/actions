#!/usr/bin/env python3
"""
factory_health_resolve — decide which factory alert issues have recovered.

``.github/workflows/factory-health.yml`` files one issue per (repo, pipeline)
whose 24h success rate falls below the threshold, and deduplicates new alerts by
matching the open issue titles against the prefix

    fix(factory): [<repo>] [<pipeline>] success rate dropped

Nothing ever closed those issues again, so a single stale alert permanently
muted its pipeline: every later breach of the same repo+pipeline hit the dedupe
branch and was silently dropped (projectbluefin/actions#490 — dakota Promote
recovered to 100% on the rerun and the alert stayed open regardless).

This module maps the health results plus the open issue list onto the issues
that should now be closed: the pipeline is healthy again *and* an alert issue
for it is still open.

Usage (from the workflow):
    python3 scripts/factory_health_resolve.py \
        --results results.json \
        --open-issues open-issues.json

``--results`` is the array produced by the monitor loop (one object per
pipeline, with ``repo``, ``pipeline``, ``workflow``, ``status``, ``rate_display``,
``success`` and ``total``).  ``--open-issues`` is ``gh issue list --json
number,title,url`` output.  A JSON array of recovery records is written to
stdout (or to ``--output``), each shaped:

    {
        "number": 490,
        "url": "https://github.com/projectbluefin/actions/issues/490",
        "title": "fix(factory): [projectbluefin/dakota] [Promote] ...",
        "repo": "projectbluefin/dakota",
        "pipeline": "Promote",
        "workflow": "Publish Bluefin dakota",
        "rate_display": "100%",
        "success": 4,
        "total": 4
    }
"""

from __future__ import annotations

import argparse
import json
import sys


def alert_title_prefix(repo: str, pipeline: str) -> str:
    """
    Build the alert issue title prefix for a pipeline.

    This must stay byte-identical to the ``title_prefix`` that
    factory-health.yml uses when it opens and deduplicates alerts — the close
    pass and the open pass have to agree on what counts as "the issue for this
    pipeline".
    """
    return f"fix(factory): [{repo}] [{pipeline}] success rate dropped"


def recovered_alerts(
    results: list[dict],
    open_issues: list[dict],
) -> list[dict]:
    """
    Return the open alert issues whose pipeline is healthy again.

    A pipeline qualifies only when its status is ``healthy``.  ``no-runs`` is
    deliberately *not* treated as a recovery: a window with no completed runs is
    an absence of evidence, not evidence the pipeline was fixed, so its alert
    stays open until a real run proves otherwise.

    Every open issue matching the pipeline's title prefix is returned, not just
    the first — duplicates from earlier breaches are equally stale once the
    pipeline is healthy.
    """
    recovered: list[dict] = []

    for result in results:
        if result.get("status") != "healthy":
            continue

        repo = result.get("repo", "")
        pipeline = result.get("pipeline", "")
        prefix = alert_title_prefix(repo, pipeline)

        for issue in open_issues:
            title = issue.get("title", "")
            if not title.startswith(prefix):
                continue
            recovered.append(
                {
                    "number": issue.get("number"),
                    "url": issue.get("url", ""),
                    "title": title,
                    "repo": repo,
                    "pipeline": pipeline,
                    "workflow": result.get("workflow", ""),
                    "rate_display": result.get("rate_display", ""),
                    "success": result.get("success", 0),
                    "total": result.get("total", 0),
                }
            )

    return recovered


def main() -> int:  # pragma: no cover
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results", required=True, help="Pipeline health results JSON path")
    ap.add_argument("--open-issues", required=True, help="gh issue list JSON path")
    ap.add_argument("--output", help="Write the JSON array here instead of stdout")
    args = ap.parse_args()

    with open(args.results, encoding="utf-8") as handle:
        results = json.load(handle)

    with open(args.open_issues, encoding="utf-8") as handle:
        open_issues = json.load(handle)

    recovered = recovered_alerts(results, open_issues)
    payload = json.dumps(recovered, indent=2)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(payload)
    else:
        print(payload)

    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
