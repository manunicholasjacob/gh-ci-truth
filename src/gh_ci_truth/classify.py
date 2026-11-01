"""The classifier.

A pure function from recorded evidence to a category. No I/O, no
network, no subprocess, no clock. Everything it needs is in the
``Evidence`` it is handed, which makes it trivially testable against
recorded API payloads and safe for other tools to import.
"""

from __future__ import annotations

from typing import List, Optional, Sequence

from .model import (
    EXPIRY_CONCLUSIONS,
    Category,
    Evidence,
    Verdict,
    WorkflowRun,
    summarize_rollup,
)

__all__ = [
    "DEFAULT_EXPIRY_GAP_DAYS",
    "APPROVAL_WINDOW_DAYS",
    "is_expiry_signature",
    "classify",
]

#: GitHub documents a 30 day approval window for workflow runs from forks.
APPROVAL_WINDOW_DAYS = 30.0

#: Minimum created_at -> updated_at gap, in days, for a zero-job failure to
#: be read as an expired approval rather than a genuine failure. Slightly
#: under 30 so that clock skew and the observed few-minute sweep delay do
#: not push a real expiry below the threshold.
DEFAULT_EXPIRY_GAP_DAYS = 29.0

#: Conclusions that mean the workflow itself could not start. These produce
#: zero jobs too, but they surface in the checks panel as a failure, so they
#: do not create the false-clean condition this tool detects.
STARTUP_FAILURE_CONCLUSIONS = frozenset({"startup_failure"})

#: Conclusions on a run that did execute jobs which mean the result is not a
#: pass. ``startup_failure`` is listed for completeness even though a run
#: that failed to start has no jobs and so cannot reach this test.
#: ``stale`` is included because GitHub sets it when it has invalidated the
#: result, which is not the same as the result having passed.
FAILING_CONCLUSIONS = frozenset(
    {"failure", "timed_out", "startup_failure", "stale", "action_required"}
)


def is_expiry_signature(
    run: WorkflowRun,
    min_gap_days: float = DEFAULT_EXPIRY_GAP_DAYS,
) -> bool:
    """Does this run carry the signature of an expired approval request?

    The signature, as observed on GitHub.com:

    * ``status == "completed"``
    * ``conclusion`` in :data:`~gh_ci_truth.model.EXPIRY_CONCLUSIONS`
      (observed: ``failure``)
    * zero jobs, so ``total_count`` on the jobs endpoint is ``0``
    * a ``created_at`` to ``updated_at`` gap of about 30 days

    All four must hold. A real test failure has jobs. A workflow that
    could not start has a gap of seconds, not weeks. ``job_count`` of
    ``None`` means the jobs endpoint was not consulted, in which case the
    signature cannot be asserted and this returns ``False``.
    """
    if run.status != "completed":
        return False
    conclusion = (run.conclusion or "").lower()
    if conclusion in STARTUP_FAILURE_CONCLUSIONS:
        return False
    if conclusion not in EXPIRY_CONCLUSIONS:
        return False
    if run.job_count is None or run.job_count > 0:
        return False
    gap = run.lifetime_days
    if gap is None:
        return False
    return gap >= min_gap_days


def _test_bearing(runs: Sequence[WorkflowRun]) -> List[WorkflowRun]:
    """Runs that actually executed at least one job."""
    return [r for r in runs if (r.job_count or 0) > 0]


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" + ("" if count == 1 else "s")


def classify(
    evidence: Evidence,
    repo: str = "",
    number: int = 0,
    title: str = "",
    author: str = "",
    url: str = "",
    min_gap_days: float = DEFAULT_EXPIRY_GAP_DAYS,
) -> Verdict:
    """Classify a pull request head commit from recorded evidence.

    Precedence is deliberate. ``NEVER_RAN_*`` outranks a visible failure
    because a visible failure is already reported correctly by existing
    tooling, whereas a never-run workflow is not reported at all. Within
    the never-ran pair, awaiting approval outranks expired, because a run
    still in the queue is actionable by a maintainer today.
    """
    notes: List[str] = []
    runs = list(evidence.runs_on_head)
    rollup = summarize_rollup(
        evidence.check_runs_on_head, evidence.check_runs_unavailable
    )

    def verdict(
        category: Category,
        reason: str,
        confidence: str = "high",
    ) -> Verdict:
        return Verdict(
            repo=repo,
            number=number,
            category=category,
            reason=reason,
            confidence=confidence,
            title=title,
            author=author,
            url=url,
            rollup_summary=rollup,
            notes=notes,
            evidence=evidence,
        )

    if not evidence.head_sha:
        return verdict(
            Category.UNKNOWN,
            "no head commit SHA was available for this pull request",
            confidence="low",
        )

    if len(evidence.head_sha) != 40:
        notes.append(
            "head SHA is not 40 characters; the workflow runs endpoint "
            "matches head_sha exactly and returns an empty list for an "
            "abbreviated SHA"
        )

    # A run parked for approval has executed nothing. A run that carries a
    # pending marker but has jobs is a different animal: GitHub also uses
    # "waiting" for environment and deployment approvals, which can gate a
    # run that has already done work. Requiring zero jobs keeps the
    # never-ran categories honest about the word "never".
    awaiting = [
        r for r in runs if r.is_awaiting_approval and (r.job_count or 0) == 0
    ]
    expired = [r for r in runs if is_expiry_signature(r, min_gap_days)]
    executed = _test_bearing(runs)

    # Runs whose job count could not be collected. This is checked before
    # anything else about execution, because _test_bearing treats an
    # uncollected count as zero and would otherwise drop the run silently.
    # A single dropped job count on a commit where some other run passed
    # would produce TESTED_PASSING at high confidence, which is exactly the
    # false clean verdict this tool exists to prevent.
    uncounted = [
        r
        for r in runs
        if r.job_count is None and not r.is_awaiting_approval and not r.is_in_flight
    ]
    if uncounted and not awaiting and not expired:
        names = ", ".join(sorted({r.name for r in uncounted})) or "unnamed workflow"
        return verdict(
            Category.UNKNOWN,
            f"the job count could not be collected for "
            f"{_plural(len(uncounted), 'workflow run')} on the head commit, "
            f"so an expired approval cannot be distinguished from a real "
            f"result: {names}",
            confidence="low",
        )
    if uncounted:
        notes.append(
            f"the job count could not be collected for "
            f"{_plural(len(uncounted), 'run')} on the head commit; those runs "
            "were excluded from the verdict"
        )

    if expired:
        notes.append(
            "expired runs report status=completed and conclusion=failure "
            "with zero jobs, so they attach no check run and leave the "
            "checks panel looking clean"
        )

    # 1. Still queued behind a maintainer approval.
    if awaiting:
        names = ", ".join(sorted({r.name for r in awaiting})) or "unnamed workflow"
        if executed:
            notes.append(
                f"{_plural(len(executed), 'other run')} on the head commit did "
                "execute jobs, so the panel is not empty, but the parked "
                "workflows have produced no result"
            )
        return verdict(
            Category.NEVER_RAN_AWAITING_APPROVAL,
            f"{_plural(len(awaiting), 'workflow run')} on the head commit "
            f"{'is' if len(awaiting) == 1 else 'are'} waiting for maintainer "
            f"approval and {'has' if len(awaiting) == 1 else 'have'} produced "
            f"no job: {names}",
        )

    # 2. Approval was never granted and GitHub closed the window.
    if expired:
        names = ", ".join(sorted({r.name for r in expired})) or "unnamed workflow"
        gaps = [r.lifetime_days for r in expired if r.lifetime_days is not None]
        gap_text = (
            f"{min(gaps):.1f} to {max(gaps):.1f} days"
            if gaps and max(gaps) - min(gaps) > 0.05
            else (f"{gaps[0]:.1f} days" if gaps else "an unknown period")
        )
        if executed:
            notes.append(
                f"{_plural(len(executed), 'other run')} on the head commit did "
                "execute jobs, so the panel is not empty, but the expired "
                "workflows never produced a result"
            )
        return verdict(
            Category.NEVER_RAN_EXPIRED,
            f"{_plural(len(expired), 'workflow run')} on the head commit sat "
            f"unapproved for {gap_text} and {'was' if len(expired) == 1 else 'were'} "
            f"expired by GitHub with zero jobs: {names}",
        )

    # 3. Nothing at all was created for this commit.
    if not runs:
        stale = _test_bearing(evidence.runs_on_older_commits)
        if stale:
            shas = ", ".join(sorted({r.head_sha[:7] for r in stale if r.head_sha}))
            notes.append(
                "pushing a new commit re-queues CI for an unapproved "
                "contributor, so results for an older commit can be the "
                "only results the pull request has ever had"
            )
            return verdict(
                Category.STALE_RESULTS_FOR_OLDER_COMMIT,
                f"no workflow run exists for the head commit, but "
                f"{_plural(len(stale), 'run')} executed on earlier commit(s) "
                f"of this pull request ({shas})",
            )
        if evidence.commits_unavailable:
            notes.append(
                "the pull request's commit list could not be read, so an "
                "older tested commit cannot be ruled out"
            )
            return verdict(
                Category.UNKNOWN,
                "no workflow run exists for the head commit and the pull "
                "request's own commit list could not be read, so results on "
                "an earlier commit cannot be ruled out",
                confidence="low",
            )
        if evidence.repo_workflow_count == 0:
            return verdict(
                Category.NO_CI_CONFIGURED,
                "the repository has no GitHub Actions workflows and no "
                "workflow run exists for the head commit",
            )
        if evidence.repo_has_pull_request_workflow is False:
            return verdict(
                Category.NO_CI_CONFIGURED,
                "no workflow run exists for the head commit and no workflow "
                "in this repository is triggered by pull_request events",
                confidence="medium",
            )
        if evidence.repo_workflow_count is None:
            return verdict(
                Category.UNKNOWN,
                "no workflow run exists for the head commit and the "
                "repository workflow list was not consulted, so an absent "
                "run cannot be distinguished from an absent workflow",
                confidence="low",
            )
        return verdict(
            Category.UNKNOWN,
            f"no workflow run exists for the head commit although the "
            f"repository has {_plural(evidence.repo_workflow_count, 'workflow')}; "
            "the run may have been skipped by a path or branch filter, or "
            "deleted",
            confidence="medium",
        )

    # 4. Runs exist. Did any of them execute?
    if not executed:
        in_flight = [r for r in runs if r.is_in_flight]
        if in_flight:
            return verdict(
                Category.UNKNOWN,
                f"{_plural(len(in_flight), 'workflow run')} on the head commit "
                "has started but has not yet produced a job result",
                confidence="medium",
            )
        startup = [
            r for r in runs if (r.conclusion or "").lower() in STARTUP_FAILURE_CONCLUSIONS
        ]
        if startup:
            notes.append(
                "a startup failure produces zero jobs but does surface as a "
                "failing check, so it does not create a falsely clean panel"
            )
            return verdict(
                Category.TESTED_FAILING,
                f"{_plural(len(startup), 'workflow run')} on the head commit "
                "failed to start, so no job ran",
            )
        skipped = [r for r in runs if (r.conclusion or "").lower() == "skipped"]
        if skipped and len(skipped) == len(runs):
            return verdict(
                Category.NO_CI_CONFIGURED,
                f"every workflow run on the head commit was skipped "
                f"({_plural(len(skipped), 'run')}), so no test was selected "
                "to run for this change",
                confidence="medium",
            )
        return verdict(
            Category.UNKNOWN,
            f"{_plural(len(runs), 'workflow run')} on the head commit "
            "produced zero jobs without the expiry signature",
            confidence="low",
        )

    # 5. Jobs ran. Now the ordinary pass/fail question is meaningful.
    bad = [
        r for r in executed if (r.conclusion or "").lower() in FAILING_CONCLUSIONS
    ]
    if bad:
        names = ", ".join(sorted({r.name for r in bad})) or "unnamed workflow"
        return verdict(
            Category.TESTED_FAILING,
            f"{_plural(len(bad), 'workflow run')} on the head commit executed "
            f"jobs and failed: {names}",
        )

    pending = [r for r in executed if r.status != "completed"]
    if pending:
        return verdict(
            Category.UNKNOWN,
            f"{_plural(len(pending), 'workflow run')} on the head commit is "
            "still running; no run has failed so far",
            confidence="medium",
        )

    cancelled = [r for r in executed if (r.conclusion or "").lower() == "cancelled"]
    if cancelled and len(cancelled) == len(executed):
        return verdict(
            Category.UNKNOWN,
            f"every executing workflow run on the head commit was cancelled "
            f"({_plural(len(cancelled), 'run')}), so no complete result exists",
            confidence="medium",
        )
    if cancelled:
        notes.append(
            f"{_plural(len(cancelled), 'run')} on the head commit was cancelled"
        )

    job_total = sum(r.job_count or 0 for r in executed)
    return verdict(
        Category.TESTED_PASSING,
        f"{_plural(len(executed), 'workflow run')} on the head commit "
        f"executed {_plural(job_total, 'job')} and none failed",
    )
