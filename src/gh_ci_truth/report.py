"""Rendering. Plain text, JSON, and Markdown for a pull request comment."""

from __future__ import annotations

import json
from typing import Dict, List, Sequence

from .model import Category, Verdict

__all__ = [
    "CATEGORY_HEADLINE",
    "render_text",
    "render_json",
    "render_markdown",
    "exit_code",
]

#: One line per category, written for someone who has just been told
#: their pull request is not what the checks panel said it was.
CATEGORY_HEADLINE: Dict[Category, str] = {
    Category.TESTED_PASSING: "CI ran on this commit and passed",
    Category.TESTED_FAILING: "CI ran on this commit and failed",
    Category.NEVER_RAN_AWAITING_APPROVAL: (
        "CI has never run: workflows are queued for maintainer approval"
    ),
    Category.NEVER_RAN_EXPIRED: (
        "CI has never run: the approval request expired after 30 days"
    ),
    Category.NO_CI_CONFIGURED: "no CI is configured for this change",
    Category.STALE_RESULTS_FOR_OLDER_COMMIT: (
        "CI has never run on this commit: the only results belong to an older commit"
    ),
    Category.UNKNOWN: "the real CI state could not be determined",
}

_LABEL_WIDTH = 32


def _bar(width: int = 72) -> str:
    return "-" * width


def render_text(verdicts: Sequence[Verdict], verbose: bool = False) -> str:
    """Human readable report."""
    lines: List[str] = []
    for verdict in verdicts:
        lines.append(f"{verdict.repo}#{verdict.number}  {verdict.category.value}")
        if verdict.title:
            lines.append(f"  title       {verdict.title}")
        if verdict.author:
            lines.append(f"  author      {verdict.author}")
        lines.append(f"  verdict     {CATEGORY_HEADLINE[verdict.category]}")
        lines.append(f"  because     {verdict.reason}")
        lines.append(f"  confidence  {verdict.confidence}")
        lines.append(f"  head        {verdict.evidence.head_sha}")
        lines.append(f"  rollup says {verdict.rollup_summary}")
        lines.append(f"  jobs run    {verdict.evidence.executed_job_count}")
        if verdict.misleading:
            lines.append(
                "  WARNING     the checks panel does not show a failure for "
                "this commit, so this pull request reads as clean while "
                "having no test result"
            )
        for note in verdict.notes:
            lines.append(f"  note        {note}")
        if verbose:
            for run in verdict.evidence.runs_on_head:
                gap = run.lifetime_days
                gap_text = "?" if gap is None else f"{gap:.2f}d"
                lines.append(
                    f"    run {run.id}  {run.name}  status={run.status} "
                    f"conclusion={run.conclusion} jobs={run.job_count} "
                    f"lifetime={gap_text}"
                )
            for check in verdict.evidence.check_runs_on_head:
                lines.append(
                    f"    check {check.name}  status={check.status} "
                    f"conclusion={check.conclusion}"
                )
            for run in verdict.evidence.runs_on_older_commits:
                lines.append(
                    f"    older run {run.id}  {run.name}  "
                    f"sha={run.head_sha[:7]} jobs={run.job_count}"
                )
        if verdict.url:
            lines.append(f"  url         {verdict.url}")
        lines.append("")

    untested = [v for v in verdicts if v.never_actually_tested]
    misleading = [v for v in verdicts if v.misleading]
    unknown = [v for v in verdicts if v.category is Category.UNKNOWN]
    lines.append(_bar())
    lines.append(
        f"{len(verdicts)} pull request(s) examined, "
        f"{len(untested)} never actually tested, "
        f"{len(misleading)} of those look clean, "
        f"{len(unknown)} undetermined"
    )
    if misleading:
        listed = ", ".join(f"#{v.number}" for v in misleading)
        lines.append(f"look clean but were never tested: {listed}")
    return "\n".join(lines)


def render_json(verdicts: Sequence[Verdict], indent: int = 2) -> str:
    """Machine readable report. Stable keys; safe to parse in a workflow."""
    payload = {
        "schema": "gh-ci-truth/v1",
        "summary": {
            "examined": len(verdicts),
            "never_actually_tested": sum(1 for v in verdicts if v.never_actually_tested),
            "misleading": sum(1 for v in verdicts if v.misleading),
            "unknown": sum(1 for v in verdicts if v.category is Category.UNKNOWN),
            "by_category": {
                category.value: sum(1 for v in verdicts if v.category is category)
                for category in Category
            },
        },
        "pull_requests": [v.to_dict() for v in verdicts],
    }
    return json.dumps(payload, indent=indent, sort_keys=False)


def render_markdown(verdict: Verdict, action_name: str = "gh-ci-truth") -> str:
    """A pull request comment body for the GitHub Action.

    Written to be useful to a maintainer who is about to merge, and to a
    contributor who cannot see why nothing is running.
    """
    lines: List[str] = []
    lines.append(f"### {action_name}: `{verdict.category.value}`")
    lines.append("")
    lines.append(CATEGORY_HEADLINE[verdict.category] + ".")
    lines.append("")
    lines.append(f"- Head commit: `{verdict.evidence.head_sha}`")
    lines.append(f"- Jobs executed on this commit: **{verdict.evidence.executed_job_count}**")
    lines.append(f"- Checks panel: {verdict.rollup_summary}")
    lines.append(f"- Reason: {verdict.reason}")
    lines.append(f"- Confidence: {verdict.confidence}")
    lines.append("")
    if verdict.misleading:
        lines.append(
            "> The checks panel shows no failure for this commit, so this "
            "pull request reads as clean. No test has run against it."
        )
        lines.append("")
    if verdict.notes:
        lines.append("Notes:")
        for note in verdict.notes:
            lines.append(f"- {note}")
        lines.append("")
    if verdict.evidence.runs_on_head:
        lines.append("<details><summary>Workflow runs on the head commit</summary>")
        lines.append("")
        lines.append("| run | workflow | status | conclusion | jobs | lifetime (days) |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for run in verdict.evidence.runs_on_head:
            gap = run.lifetime_days
            gap_text = "?" if gap is None else f"{gap:.2f}"
            jobs = "?" if run.job_count is None else str(run.job_count)
            lines.append(
                f"| [{run.id}]({run.html_url}) | {run.name} | {run.status} "
                f"| {run.conclusion} | {jobs} | {gap_text} |"
            )
        lines.append("")
        lines.append("</details>")
        lines.append("")
    return "\n".join(lines)


def exit_code(
    verdicts: Sequence[Verdict],
    strict: bool = False,
    fail_on_tested_failing: bool = False,
) -> int:
    """Process exit status.

    * ``0`` nothing to report
    * ``1`` at least one pull request was never actually tested
    * ``2`` at least one verdict is ``UNKNOWN`` and nothing scored ``1``
    * ``3`` reserved for operational failure, set by the caller

    Without ``--strict`` the exit status is always ``0`` for a successful
    run, so the tool can be used for reporting without breaking a shell
    script that does not expect it.
    """
    if not strict:
        return 0
    if any(v.never_actually_tested for v in verdicts):
        return 1
    if fail_on_tested_failing and any(
        v.category is Category.TESTED_FAILING for v in verdicts
    ):
        return 1
    if any(v.category is Category.UNKNOWN for v in verdicts):
        return 2
    return 0
