"""Data model for gh-ci-truth.

Pure data types. No I/O, no network, no subprocess.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence

__all__ = [
    "Category",
    "UNTESTED_CATEGORIES",
    "WorkflowRun",
    "CheckRun",
    "Evidence",
    "Verdict",
    "parse_timestamp",
]


class Category(str, Enum):
    """The real CI state of a pull request head commit.

    The two categories that existing tooling conflates are
    ``TESTED_PASSING`` and the ``NEVER_RAN_*`` pair. ``gh pr checks`` and
    ``statusCheckRollup`` report only check runs attached to the head
    commit, so a head commit with no attached check runs is rendered the
    same way as a head commit whose checks all passed.
    """

    TESTED_PASSING = "TESTED_PASSING"
    TESTED_FAILING = "TESTED_FAILING"
    NEVER_RAN_AWAITING_APPROVAL = "NEVER_RAN_AWAITING_APPROVAL"
    NEVER_RAN_EXPIRED = "NEVER_RAN_EXPIRED"
    NO_CI_CONFIGURED = "NO_CI_CONFIGURED"
    STALE_RESULTS_FOR_OLDER_COMMIT = "STALE_RESULTS_FOR_OLDER_COMMIT"
    UNKNOWN = "UNKNOWN"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


#: Categories in which the head commit has produced no test result of its own.
UNTESTED_CATEGORIES = frozenset(
    {
        Category.NEVER_RAN_AWAITING_APPROVAL,
        Category.NEVER_RAN_EXPIRED,
        Category.STALE_RESULTS_FOR_OLDER_COMMIT,
    }
)

#: Conclusions GitHub has been observed to record on an approval-pending
#: workflow run that reached its 30 day limit without being approved.
EXPIRY_CONCLUSIONS = frozenset({"failure", "cancelled", "stale", "skipped"})

#: Run statuses that mean "queued behind a human".
#:
#: Observed on GitHub.com on 2026-11-01: a run pending approval reports
#: ``status="completed"`` with ``conclusion="action_required"``, not
#: ``status="action_required"``. The documented ``status`` filter value
#: ``action_required`` in fact matches that conclusion. Both fields are
#: checked, because the REST enumeration for the list endpoint conflates
#: statuses and conclusions and GitHub does not document which field
#: carries the pending state.
AWAITING_APPROVAL_STATUSES = frozenset({"action_required", "waiting"})

#: Conclusions that mean "queued behind a human".
AWAITING_APPROVAL_CONCLUSIONS = frozenset({"action_required"})

#: Run statuses that mean "still moving".
IN_FLIGHT_STATUSES = frozenset({"queued", "in_progress", "requested", "pending"})


def parse_timestamp(value: Optional[str]) -> Optional[_dt.datetime]:
    """Parse a GitHub ISO-8601 ``Z`` timestamp into an aware datetime.

    Returns ``None`` for ``None`` or an unparseable value rather than
    raising, because a classifier must never crash on an unexpected
    payload; an unparseable timestamp degrades the verdict to
    ``UNKNOWN`` instead.
    """
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = _dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed


@dataclass(frozen=True)
class WorkflowRun:
    """One GitHub Actions workflow run, reduced to the fields that matter.

    ``job_count`` is the ``total_count`` of
    ``GET /repos/{owner}/{repo}/actions/runs/{run_id}/jobs``. It is the
    load bearing field: an approval-pending run that expired reports
    ``status="completed"``, ``conclusion="failure"`` and ``job_count=0``,
    which is indistinguishable from a real failure on the run object
    alone.
    """

    id: int
    name: str
    status: str
    conclusion: Optional[str]
    created_at: Optional[str]
    updated_at: Optional[str]
    head_sha: str = ""
    event: str = ""
    path: str = ""
    run_attempt: int = 1
    html_url: str = ""
    job_count: Optional[int] = None

    @classmethod
    def from_api(cls, payload: Dict[str, Any], job_count: Optional[int] = None) -> "WorkflowRun":
        """Build from a ``workflow_runs[]`` element of the REST list endpoint."""
        return cls(
            id=int(payload.get("id") or 0),
            name=str(payload.get("name") or ""),
            status=str(payload.get("status") or ""),
            conclusion=payload.get("conclusion"),
            created_at=payload.get("created_at"),
            updated_at=payload.get("updated_at"),
            head_sha=str(payload.get("head_sha") or ""),
            event=str(payload.get("event") or ""),
            path=str(payload.get("path") or ""),
            run_attempt=int(payload.get("run_attempt") or 1),
            html_url=str(payload.get("html_url") or ""),
            job_count=job_count,
        )

    @property
    def lifetime_days(self) -> Optional[float]:
        """Days between ``created_at`` and ``updated_at``, or ``None``."""
        start = parse_timestamp(self.created_at)
        end = parse_timestamp(self.updated_at)
        if start is None or end is None:
            return None
        return (end - start).total_seconds() / 86400.0

    @property
    def is_awaiting_approval(self) -> bool:
        """Is this run parked waiting for a maintainer to approve it?

        True when either field carries the pending marker. On GitHub.com
        the observed shape is ``status="completed"`` with
        ``conclusion="action_required"``; the ``status`` arm covers the
        shape the REST enumeration implies and any future change back to
        it.
        """
        if self.status in AWAITING_APPROVAL_STATUSES:
            return True
        return (self.conclusion or "").lower() in AWAITING_APPROVAL_CONCLUSIONS

    @property
    def is_in_flight(self) -> bool:
        return self.status in IN_FLIGHT_STATUSES

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "conclusion": self.conclusion,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "head_sha": self.head_sha,
            "event": self.event,
            "path": self.path,
            "run_attempt": self.run_attempt,
            "html_url": self.html_url,
            "job_count": self.job_count,
            "lifetime_days": (
                None if self.lifetime_days is None else round(self.lifetime_days, 4)
            ),
        }


@dataclass(frozen=True)
class CheckRun:
    """A check run attached to the head commit, as the rollup sees it."""

    name: str
    status: str
    conclusion: Optional[str]
    workflow_name: str = ""
    details_url: str = ""

    @classmethod
    def from_rollup(cls, payload: Dict[str, Any]) -> "CheckRun":
        """Build from one element of ``gh pr view --json statusCheckRollup``."""
        return cls(
            name=str(payload.get("name") or payload.get("context") or ""),
            status=str(payload.get("status") or payload.get("state") or "").lower(),
            conclusion=(
                payload["conclusion"].lower()
                if isinstance(payload.get("conclusion"), str) and payload.get("conclusion")
                else None
            ),
            workflow_name=str(payload.get("workflowName") or ""),
            details_url=str(payload.get("detailsUrl") or payload.get("targetUrl") or ""),
        )

    @classmethod
    def from_api(cls, payload: Dict[str, Any]) -> "CheckRun":
        """Build from one element of the Checks REST ``check_runs[]`` array."""
        return cls(
            name=str(payload.get("name") or ""),
            status=str(payload.get("status") or "").lower(),
            conclusion=(
                payload["conclusion"].lower()
                if isinstance(payload.get("conclusion"), str) and payload.get("conclusion")
                else None
            ),
            workflow_name=str((payload.get("app") or {}).get("name") or ""),
            details_url=str(payload.get("details_url") or ""),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "conclusion": self.conclusion,
            "workflow_name": self.workflow_name,
            "details_url": self.details_url,
        }


@dataclass
class Evidence:
    """Everything the classifier looked at, so a verdict can be audited."""

    head_sha: str = ""
    runs_on_head: List[WorkflowRun] = field(default_factory=list)
    check_runs_on_head: List[CheckRun] = field(default_factory=list)
    runs_on_older_commits: List[WorkflowRun] = field(default_factory=list)
    repo_workflow_count: Optional[int] = None
    repo_has_pull_request_workflow: Optional[bool] = None
    older_commit_shas: List[str] = field(default_factory=list)
    #: True when the check runs endpoint could not be read. An empty
    #: ``check_runs_on_head`` then means "not known", not "none exist", and
    #: the reports must not claim the checks panel is empty.
    check_runs_unavailable: bool = False
    #: True when the pull request's commit list could not be read, so an
    #: older tested commit cannot be ruled out.
    commits_unavailable: bool = False

    @property
    def executed_job_count(self) -> int:
        """Total jobs that actually ran on the head commit."""
        return sum(r.job_count or 0 for r in self.runs_on_head)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "head_sha": self.head_sha,
            "runs_on_head": [r.to_dict() for r in self.runs_on_head],
            "check_runs_on_head": [c.to_dict() for c in self.check_runs_on_head],
            "runs_on_older_commits": [r.to_dict() for r in self.runs_on_older_commits],
            "repo_workflow_count": self.repo_workflow_count,
            "repo_has_pull_request_workflow": self.repo_has_pull_request_workflow,
            "older_commit_shas": list(self.older_commit_shas),
            "executed_job_count": self.executed_job_count,
            "check_runs_unavailable": self.check_runs_unavailable,
            "commits_unavailable": self.commits_unavailable,
        }


@dataclass
class Verdict:
    """The classification of one pull request, with its justification."""

    repo: str
    number: int
    category: Category
    reason: str
    confidence: str = "high"
    title: str = ""
    author: str = ""
    url: str = ""
    rollup_summary: str = ""
    notes: List[str] = field(default_factory=list)
    evidence: Evidence = field(default_factory=Evidence)

    @property
    def tested(self) -> bool:
        """True only when the head commit produced at least one test result."""
        return self.category in (Category.TESTED_PASSING, Category.TESTED_FAILING)

    @property
    def never_actually_tested(self) -> bool:
        """True when the head commit has produced no test result of its own."""
        return self.category in UNTESTED_CATEGORIES

    @property
    def misleading(self) -> bool:
        """True when the head commit is untested but the rollup looks clean.

        This is the condition the tool exists to detect: nothing attached
        to the head commit is failing, so ``gh pr checks`` renders a clean
        or empty panel, while no test has in fact run.
        """
        if not self.never_actually_tested:
            return False
        if self.evidence.check_runs_unavailable:
            # The panel was not read, so no claim is made about how it
            # looks. The pull request is still untested either way.
            return False
        return not any(
            (c.conclusion or "") in ("failure", "cancelled", "timed_out", "action_required")
            for c in self.evidence.check_runs_on_head
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "repo": self.repo,
            "number": self.number,
            "category": self.category.value,
            "reason": self.reason,
            "confidence": self.confidence,
            "title": self.title,
            "author": self.author,
            "url": self.url,
            "tested": self.tested,
            "never_actually_tested": self.never_actually_tested,
            "misleading": self.misleading,
            "rollup_summary": self.rollup_summary,
            "notes": list(self.notes),
            "evidence": self.evidence.to_dict(),
        }


def summarize_rollup(
    check_runs: Sequence[CheckRun], unavailable: bool = False
) -> str:
    """One line describing what ``gh pr checks`` would show.

    Used to make the gap between the rollup and reality explicit in
    output, rather than leaving the reader to infer it. When the endpoint
    could not be read this says so, because "the panel is empty" is a
    claim about the world and must not be made on a failed request.
    """
    if unavailable:
        return "the checks panel could not be read"
    if not check_runs:
        return "no check runs attached to the head commit (empty checks panel)"
    counts: Dict[str, int] = {}
    for check in check_runs:
        key = check.conclusion or check.status or "unknown"
        counts[key] = counts.get(key, 0) + 1
    parts = ", ".join(f"{count} {name}" for name, count in sorted(counts.items()))
    return f"{len(check_runs)} check run(s) attached: {parts}"
