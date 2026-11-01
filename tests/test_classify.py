"""Classifier tests: the decision logic, driven directly from evidence."""

from __future__ import annotations

import pytest

from gh_ci_truth import (
    Category,
    CheckRun,
    Evidence,
    WorkflowRun,
    classify,
    is_expiry_signature,
)
from gh_ci_truth.classify import DEFAULT_EXPIRY_GAP_DAYS


def run(**kwargs) -> WorkflowRun:
    base = dict(
        id=1,
        name="pytest - Linux",
        status="completed",
        conclusion="success",
        created_at="2026-10-01T00:00:00Z",
        updated_at="2026-10-01T00:12:00Z",
        head_sha="a" * 40,
        event="pull_request",
        job_count=1,
    )
    base.update(kwargs)
    return WorkflowRun(**base)


EXPIRED = dict(
    status="completed",
    conclusion="failure",
    created_at="2026-08-28T01:13:15Z",
    updated_at="2026-09-27T01:17:20Z",
    job_count=0,
)

AWAITING = dict(
    status="completed",
    conclusion="action_required",
    created_at="2026-11-01T10:34:26Z",
    updated_at="2026-11-01T10:34:26Z",
    job_count=0,
)


# --------------------------------------------------------------------------
# The expiry signature itself
# --------------------------------------------------------------------------


def test_expiry_signature_matches_the_observed_shape():
    assert is_expiry_signature(run(**EXPIRED)) is True


def test_expiry_signature_gap_is_thirty_days():
    gap = run(**EXPIRED).lifetime_days
    assert gap is not None
    assert 29.9 < gap < 30.1


def test_a_real_failure_with_jobs_is_not_an_expiry():
    failed = run(conclusion="failure", job_count=1, updated_at="2026-10-01T00:12:00Z")
    assert is_expiry_signature(failed) is False


def test_a_zero_job_failure_that_finished_quickly_is_not_an_expiry():
    quick = run(
        conclusion="failure",
        job_count=0,
        created_at="2026-10-01T00:00:00Z",
        updated_at="2026-10-01T00:00:08Z",
    )
    assert is_expiry_signature(quick) is False


def test_a_startup_failure_is_not_an_expiry_even_with_a_long_gap():
    startup = run(
        conclusion="startup_failure",
        job_count=0,
        created_at="2026-08-28T01:13:15Z",
        updated_at="2026-09-27T01:17:20Z",
    )
    assert is_expiry_signature(startup) is False


def test_an_unknown_job_count_cannot_assert_an_expiry():
    unknown = run(**{**EXPIRED, "job_count": None})
    assert is_expiry_signature(unknown) is False


def test_an_awaiting_run_is_not_yet_an_expiry():
    assert is_expiry_signature(run(**AWAITING)) is False


def test_an_unparseable_timestamp_cannot_assert_an_expiry():
    broken = run(**{**EXPIRED, "updated_at": "not a date"})
    assert broken.lifetime_days is None
    assert is_expiry_signature(broken) is False


@pytest.mark.parametrize("gap_days,expected", [(28.0, True), (29.0, True), (31.0, False)])
def test_the_gap_threshold_is_configurable(gap_days, expected):
    assert is_expiry_signature(run(**EXPIRED), min_gap_days=gap_days) is expected


# --------------------------------------------------------------------------
# Category assignment
# --------------------------------------------------------------------------


def test_expired_runs_classify_as_never_ran_expired():
    evidence = Evidence(
        head_sha="a" * 40,
        runs_on_head=[run(id=i, **EXPIRED) for i in range(1, 6)],
        check_runs_on_head=[CheckRun(name="DCO", status="completed", conclusion="success")],
    )
    verdict = classify(evidence, repo="o/r", number=1)
    assert verdict.category is Category.NEVER_RAN_EXPIRED
    assert verdict.tested is False
    assert verdict.never_actually_tested is True


def test_awaiting_approval_outranks_expiry():
    """A commit can carry both. The actionable state wins."""
    evidence = Evidence(
        head_sha="a" * 40,
        runs_on_head=[run(id=1, **EXPIRED), run(id=2, **AWAITING)],
    )
    verdict = classify(evidence, repo="o/r", number=1)
    assert verdict.category is Category.NEVER_RAN_AWAITING_APPROVAL


def test_awaiting_approval_is_detected_from_the_conclusion_field():
    """The observed shape puts the pending marker in conclusion, not status."""
    parked = run(**AWAITING)
    assert parked.status == "completed"
    assert parked.is_awaiting_approval is True


def test_awaiting_approval_is_also_detected_from_the_status_field():
    """Cover the shape the REST status enumeration implies."""
    parked = run(status="action_required", conclusion=None, job_count=0)
    assert parked.is_awaiting_approval is True


def test_an_expiry_outranks_a_visible_failure():
    """A visible failure is already reported; an expiry is not."""
    evidence = Evidence(
        head_sha="a" * 40,
        runs_on_head=[
            run(id=1, **EXPIRED),
            run(id=2, conclusion="failure", job_count=3),
        ],
    )
    verdict = classify(evidence, repo="o/r", number=1)
    assert verdict.category is Category.NEVER_RAN_EXPIRED
    assert any("did execute jobs" in note for note in verdict.notes)


def test_executed_passing_runs_classify_as_tested_passing():
    evidence = Evidence(
        head_sha="a" * 40,
        runs_on_head=[run(id=1, job_count=4), run(id=2, job_count=2)],
    )
    verdict = classify(evidence, repo="o/r", number=1)
    assert verdict.category is Category.TESTED_PASSING
    assert verdict.tested is True
    assert verdict.evidence.executed_job_count == 6


def test_an_executed_failure_classifies_as_tested_failing():
    evidence = Evidence(
        head_sha="a" * 40,
        runs_on_head=[run(id=1, conclusion="failure", job_count=1)],
    )
    assert classify(evidence, repo="o/r", number=1).category is Category.TESTED_FAILING


def test_a_timed_out_run_classifies_as_tested_failing():
    evidence = Evidence(
        head_sha="a" * 40,
        runs_on_head=[run(id=1, conclusion="timed_out", job_count=1)],
    )
    assert classify(evidence, repo="o/r", number=1).category is Category.TESTED_FAILING


def test_a_startup_failure_classifies_as_tested_failing_not_never_ran():
    evidence = Evidence(
        head_sha="a" * 40,
        runs_on_head=[run(id=1, conclusion="startup_failure", job_count=0)],
    )
    verdict = classify(evidence, repo="o/r", number=1)
    assert verdict.category is Category.TESTED_FAILING
    assert any("does not create a falsely clean panel" in n for n in verdict.notes)


def test_no_runs_and_no_workflows_is_no_ci_configured():
    evidence = Evidence(head_sha="a" * 40, repo_workflow_count=0)
    verdict = classify(evidence, repo="o/r", number=1)
    assert verdict.category is Category.NO_CI_CONFIGURED


def test_no_runs_but_workflows_that_never_see_pull_requests_is_no_ci_configured():
    evidence = Evidence(
        head_sha="a" * 40,
        repo_workflow_count=3,
        repo_has_pull_request_workflow=False,
    )
    verdict = classify(evidence, repo="o/r", number=1)
    assert verdict.category is Category.NO_CI_CONFIGURED
    assert verdict.confidence == "medium"


def test_results_only_on_an_older_commit_are_flagged_as_stale():
    evidence = Evidence(
        head_sha="c" * 40,
        runs_on_head=[],
        runs_on_older_commits=[run(id=9, head_sha="a" * 40, job_count=1)],
        older_commit_shas=["a" * 40],
    )
    verdict = classify(evidence, repo="o/r", number=1)
    assert verdict.category is Category.STALE_RESULTS_FOR_OLDER_COMMIT
    assert verdict.never_actually_tested is True


def test_an_older_commit_with_no_executed_jobs_does_not_count_as_stale_results():
    evidence = Evidence(
        head_sha="c" * 40,
        runs_on_head=[],
        runs_on_older_commits=[run(id=9, head_sha="a" * 40, **EXPIRED)],
        repo_workflow_count=0,
    )
    verdict = classify(evidence, repo="o/r", number=1)
    assert verdict.category is Category.NO_CI_CONFIGURED


def test_a_bare_head_with_workflows_present_is_unknown_not_a_guess():
    evidence = Evidence(
        head_sha="a" * 40,
        repo_workflow_count=2,
        repo_has_pull_request_workflow=True,
    )
    verdict = classify(evidence, repo="o/r", number=1)
    assert verdict.category is Category.UNKNOWN
    assert verdict.confidence == "medium"


def test_a_bare_head_without_the_workflow_lookup_is_low_confidence_unknown():
    evidence = Evidence(head_sha="a" * 40, repo_workflow_count=None)
    verdict = classify(evidence, repo="o/r", number=1)
    assert verdict.category is Category.UNKNOWN
    assert verdict.confidence == "low"


def test_a_missing_head_sha_is_unknown():
    verdict = classify(Evidence(head_sha=""), repo="o/r", number=1)
    assert verdict.category is Category.UNKNOWN
    assert verdict.confidence == "low"


def test_an_abbreviated_head_sha_is_called_out():
    evidence = Evidence(head_sha="4b3e337", repo_workflow_count=0)
    verdict = classify(evidence, repo="o/r", number=1)
    assert any("40 characters" in note for note in verdict.notes)


def test_an_in_flight_run_is_unknown_not_passing():
    evidence = Evidence(
        head_sha="a" * 40,
        runs_on_head=[run(id=1, status="in_progress", conclusion=None, job_count=0)],
    )
    verdict = classify(evidence, repo="o/r", number=1)
    assert verdict.category is Category.UNKNOWN


def test_a_run_whose_jobs_were_never_counted_is_unknown():
    evidence = Evidence(
        head_sha="a" * 40,
        runs_on_head=[run(id=1, conclusion="failure", job_count=None)],
    )
    verdict = classify(evidence, repo="o/r", number=1)
    assert verdict.category is Category.UNKNOWN
    assert verdict.confidence == "low"


def test_all_runs_skipped_is_no_ci_configured():
    evidence = Evidence(
        head_sha="a" * 40,
        runs_on_head=[
            run(id=1, conclusion="skipped", job_count=0,
                updated_at="2026-10-01T00:00:02Z"),
        ],
    )
    verdict = classify(evidence, repo="o/r", number=1)
    assert verdict.category is Category.NO_CI_CONFIGURED


def test_all_executed_runs_cancelled_is_unknown():
    evidence = Evidence(
        head_sha="a" * 40,
        runs_on_head=[run(id=1, conclusion="cancelled", job_count=2)],
    )
    verdict = classify(evidence, repo="o/r", number=1)
    assert verdict.category is Category.UNKNOWN


def test_a_partial_cancellation_still_passes_but_is_noted():
    evidence = Evidence(
        head_sha="a" * 40,
        runs_on_head=[
            run(id=1, conclusion="cancelled", job_count=1),
            run(id=2, conclusion="success", job_count=1),
        ],
    )
    verdict = classify(evidence, repo="o/r", number=1)
    assert verdict.category is Category.TESTED_PASSING
    assert any("cancelled" in note for note in verdict.notes)


# --------------------------------------------------------------------------
# The misleading flag: the whole point of the tool
# --------------------------------------------------------------------------


def test_an_expiry_with_only_a_green_dco_check_is_misleading():
    evidence = Evidence(
        head_sha="a" * 40,
        runs_on_head=[run(id=1, **EXPIRED)],
        check_runs_on_head=[CheckRun(name="DCO", status="completed", conclusion="success")],
    )
    assert classify(evidence, repo="o/r", number=1).misleading is True


def test_an_expiry_with_no_checks_at_all_is_misleading():
    evidence = Evidence(head_sha="a" * 40, runs_on_head=[run(id=1, **EXPIRED)])
    verdict = classify(evidence, repo="o/r", number=1)
    assert verdict.misleading is True
    assert "empty checks panel" in verdict.rollup_summary


def test_an_expiry_alongside_a_failing_check_is_not_misleading():
    """If something red is attached, the panel is not lying to anyone."""
    evidence = Evidence(
        head_sha="a" * 40,
        runs_on_head=[run(id=1, **EXPIRED)],
        check_runs_on_head=[
            CheckRun(name="lint", status="completed", conclusion="failure")
        ],
    )
    verdict = classify(evidence, repo="o/r", number=1)
    assert verdict.never_actually_tested is True
    assert verdict.misleading is False


def test_a_tested_passing_pull_request_is_never_misleading():
    evidence = Evidence(head_sha="a" * 40, runs_on_head=[run(id=1, job_count=1)])
    assert classify(evidence, repo="o/r", number=1).misleading is False


def test_every_category_has_a_reason_and_a_valid_confidence():
    cases = [
        Evidence(head_sha="a" * 40, runs_on_head=[run(id=1, **EXPIRED)]),
        Evidence(head_sha="a" * 40, runs_on_head=[run(id=1, **AWAITING)]),
        Evidence(head_sha="a" * 40, runs_on_head=[run(id=1, job_count=1)]),
        Evidence(head_sha="a" * 40, runs_on_head=[run(id=1, conclusion="failure", job_count=1)]),
        Evidence(head_sha="a" * 40, repo_workflow_count=0),
        Evidence(head_sha="a" * 40, runs_on_older_commits=[run(id=2, head_sha="b" * 40)]),
        Evidence(head_sha=""),
    ]
    seen = set()
    for evidence in cases:
        verdict = classify(evidence, repo="o/r", number=1)
        assert verdict.reason
        assert verdict.confidence in ("high", "medium", "low")
        seen.add(verdict.category)
    assert len(seen) == 7, seen


def test_the_default_gap_threshold_is_below_the_documented_window():
    from gh_ci_truth.classify import APPROVAL_WINDOW_DAYS

    assert DEFAULT_EXPIRY_GAP_DAYS < APPROVAL_WINDOW_DAYS


# --------------------------------------------------------------------------
# Regression tests for bugs found in review
# --------------------------------------------------------------------------


def test_an_uncollected_job_count_cannot_be_hidden_by_a_passing_run():
    """Regression guard for the worst failure this tool could have.

    _test_bearing treats an uncollected job count as zero and drops the
    run. Before this was fixed, a single failed jobs request on a commit
    where some other run passed produced TESTED_PASSING at high
    confidence with no note: a falsely clean verdict, from the tool whose
    entire purpose is to catch falsely clean verdicts.
    """
    evidence = Evidence(
        head_sha="a" * 40,
        runs_on_head=[
            run(id=1, conclusion="failure", job_count=None),
            run(id=2, conclusion="success", job_count=3),
        ],
    )
    verdict = classify(evidence, repo="o/r", number=1)
    assert verdict.category is not Category.TESTED_PASSING
    assert verdict.category is Category.UNKNOWN
    assert verdict.confidence == "low"
    assert "job count could not be collected" in verdict.reason


def test_an_uncollected_job_count_is_noted_when_an_expiry_is_also_present():
    """The expiry still wins, but the gap in the evidence is recorded."""
    evidence = Evidence(
        head_sha="a" * 40,
        runs_on_head=[
            run(id=1, **EXPIRED),
            run(id=2, conclusion="failure", job_count=None),
        ],
    )
    verdict = classify(evidence, repo="o/r", number=1)
    assert verdict.category is Category.NEVER_RAN_EXPIRED
    assert any("job count could not be collected" in n for n in verdict.notes)


def test_a_pending_marker_on_a_run_that_executed_is_not_never_ran():
    """GitHub also uses "waiting" for environment approvals.

    Such a run can already have done work, so calling it never-run and
    saying it "produced no job" would be flatly false.
    """
    evidence = Evidence(
        head_sha="a" * 40,
        runs_on_head=[run(id=1, status="waiting", conclusion=None, job_count=5)],
    )
    verdict = classify(evidence, repo="o/r", number=1)
    assert verdict.category is not Category.NEVER_RAN_AWAITING_APPROVAL


def test_a_parked_run_alongside_a_passing_run_is_noted():
    """Mirrors the note the expiry branch already produced."""
    evidence = Evidence(
        head_sha="a" * 40,
        runs_on_head=[run(id=1, **AWAITING), run(id=2, job_count=4)],
    )
    verdict = classify(evidence, repo="o/r", number=1)
    assert verdict.category is Category.NEVER_RAN_AWAITING_APPROVAL
    assert any("did execute jobs" in note for note in verdict.notes)


def test_the_awaiting_reason_agrees_with_itself_in_the_singular():
    evidence = Evidence(head_sha="a" * 40, runs_on_head=[run(id=1, **AWAITING)])
    reason = classify(evidence, repo="o/r", number=1).reason
    assert "run on the head commit is waiting" in reason
    assert "has produced no job" in reason


def test_a_stale_conclusion_with_jobs_is_not_reported_as_passing():
    """GitHub sets stale when it has invalidated the result."""
    evidence = Evidence(
        head_sha="a" * 40,
        runs_on_head=[run(id=1, conclusion="stale", job_count=3)],
    )
    verdict = classify(evidence, repo="o/r", number=1)
    assert verdict.category is not Category.TESTED_PASSING
    assert verdict.category is Category.TESTED_FAILING


def test_an_unreadable_checks_panel_is_not_called_empty():
    """"The panel is empty" is a claim, and must not rest on a failed read."""
    evidence = Evidence(
        head_sha="a" * 40,
        runs_on_head=[run(id=1, **EXPIRED)],
        check_runs_unavailable=True,
    )
    verdict = classify(evidence, repo="o/r", number=1)
    assert verdict.category is Category.NEVER_RAN_EXPIRED
    assert "could not be read" in verdict.rollup_summary
    assert "empty" not in verdict.rollup_summary
    # Untested either way, but no claim is made about appearances.
    assert verdict.never_actually_tested is True
    assert verdict.misleading is False


def test_an_unreadable_commit_list_does_not_become_no_ci_configured():
    evidence = Evidence(
        head_sha="a" * 40,
        runs_on_head=[],
        repo_workflow_count=0,
        commits_unavailable=True,
    )
    verdict = classify(evidence, repo="o/r", number=1)
    assert verdict.category is Category.UNKNOWN
    assert verdict.confidence == "low"
