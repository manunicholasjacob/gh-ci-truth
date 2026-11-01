"""Collect evidence from GitHub and hand it to the classifier.

This module owns every API call. The classifier owns every decision.
Keeping them apart is what lets the test suite exercise the decisions
against recorded payloads with no network at all.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

from .classify import DEFAULT_EXPIRY_GAP_DAYS, classify
from .client import GitHubClient, GitHubError
from .model import CheckRun, Evidence, Verdict, WorkflowRun

__all__ = [
    "PULL_REQUEST_EVENTS",
    "analyze_pull_request",
    "analyze_pull_requests",
    "list_open_pull_requests",
    "collect_evidence",
]

#: Workflow run events that correspond to a pull request being checked.
PULL_REQUEST_EVENTS = frozenset({"pull_request", "pull_request_target"})

#: Fetching the jobs endpoint once per run is the expensive part. Cap it so
#: that a pull request with a pathological number of runs cannot hang.
MAX_RUNS_INSPECTED = 60


def _job_count(client: GitHubClient, repo: str, run_id: int) -> Optional[int]:
    """``total_count`` from the jobs endpoint, or ``None`` if unavailable.

    ``None`` is not zero. Zero means GitHub says no job ever existed;
    ``None`` means we failed to ask. The classifier treats them
    differently and will not assert an expiry on ``None``.
    """
    try:
        payload = client.get(f"repos/{repo}/actions/runs/{run_id}/jobs")
    except GitHubError:
        return None
    if not isinstance(payload, dict):
        return None
    total = payload.get("total_count")
    if isinstance(total, bool) or not isinstance(total, int):
        jobs = payload.get("jobs")
        return len(jobs) if isinstance(jobs, list) else None
    return total


def _runs_for_sha(
    client: GitHubClient,
    repo: str,
    sha: str,
    with_jobs: bool = True,
) -> List[WorkflowRun]:
    """Every workflow run attached to one commit SHA.

    The ``head_sha`` filter matches exactly. An abbreviated SHA returns
    an empty list, which is why callers must pass the full 40 characters.
    """
    # per_page is explicit: the endpoint defaults to 30, and a commit in a
    # repository with many workflows can exceed that. A run beyond the first
    # page would be invisible, which for an expired run means the verdict
    # silently becomes TESTED_PASSING.
    payload = client.get(f"repos/{repo}/actions/runs?head_sha={sha}&per_page=100")
    if not isinstance(payload, dict) or "workflow_runs" not in payload:
        # Never read an unexpected payload as "this commit has no runs".
        # That is the one mistake this tool cannot afford, because an empty
        # run list classifies as NO_CI_CONFIGURED at high confidence.
        raise GitHubError(
            f"the workflow runs endpoint returned no workflow_runs array for "
            f"{repo}@{sha[:12]}; refusing to read that as zero runs"
        )
    raw: Iterable[Dict[str, Any]] = payload.get("workflow_runs") or []
    total = payload.get("total_count")
    runs: List[WorkflowRun] = []
    truncated = False
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        if index >= MAX_RUNS_INSPECTED:
            truncated = True
            break
        count = (
            _job_count(client, repo, int(item.get("id") or 0)) if with_jobs else None
        )
        runs.append(WorkflowRun.from_api(item, job_count=count))
    if truncated or (isinstance(total, int) and total > len(runs)):
        raise GitHubError(
            f"{repo}@{sha[:12]} has more workflow runs ({total}) than this "
            f"tool inspects ({MAX_RUNS_INSPECTED}); a partial view could hide "
            "an expired run, so no verdict is given"
        )
    return runs


def _check_runs_for_sha(
    client: GitHubClient, repo: str, sha: str
) -> Optional[List[CheckRun]]:
    """What the rollup sees: check runs attached to this commit.

    Returns ``None`` when the endpoint could not be read. ``None`` is not
    an empty list: the reports state plainly that the checks panel is
    empty, and that claim must not be made about a panel nobody managed
    to look at.
    """
    try:
        payload = client.get(f"repos/{repo}/commits/{sha}/check-runs?per_page=100")
    except GitHubError:
        return None
    if not isinstance(payload, dict) or "check_runs" not in payload:
        return None
    items = payload.get("check_runs") or []
    return [CheckRun.from_api(item) for item in items if isinstance(item, dict)]


def _pull_request_commits(
    client: GitHubClient, repo: str, number: int
) -> Optional[List[str]]:
    """The pull request's commit SHAs, oldest first, or ``None`` on failure.

    ``None`` is not an empty list. An empty list would let the caller
    conclude there is no older tested commit, which it has not
    established.
    """
    try:
        items = client.paginate(f"repos/{repo}/pulls/{number}/commits", "", limit=250)
    except GitHubError:
        return None
    shas: List[str] = []
    for item in items:
        if isinstance(item, dict) and item.get("sha"):
            shas.append(str(item["sha"]))
    return shas


def _repo_workflows(client: GitHubClient, repo: str) -> Optional[int]:
    try:
        payload = client.get(f"repos/{repo}/actions/workflows")
    except GitHubError:
        return None
    if not isinstance(payload, dict):
        return None
    total = payload.get("total_count")
    if isinstance(total, int) and not isinstance(total, bool):
        return total
    workflows = payload.get("workflows")
    return len(workflows) if isinstance(workflows, list) else None


def _repo_has_pull_request_workflow(client: GitHubClient, repo: str) -> Optional[bool]:
    """Has any workflow in this repository ever run for a pull request?

    This is a proxy for "a pull_request trigger exists". Reading every
    workflow file to parse its ``on:`` block would be more direct but
    costs one content request per workflow and still misses reusable
    workflows. The proxy is honest about being a proxy: the classifier
    marks a verdict that rests on it as medium confidence.
    """
    for event in sorted(PULL_REQUEST_EVENTS):
        try:
            payload = client.get(f"repos/{repo}/actions/runs?event={event}&per_page=1")
        except GitHubError:
            return None
        if isinstance(payload, dict) and (payload.get("total_count") or 0) > 0:
            return True
    return False


def collect_evidence(
    client: GitHubClient,
    repo: str,
    number: int,
    head_sha: str,
    deep: bool = True,
) -> Evidence:
    """Gather everything the classifier needs for one pull request.

    ``deep=False`` skips the older-commit and repository-workflow
    lookups. That is three fewer requests per pull request and is
    adequate when the head commit does have runs, but it leaves
    ``NO_CI_CONFIGURED`` and ``STALE_RESULTS_FOR_OLDER_COMMIT``
    indistinguishable from ``UNKNOWN``.
    """
    evidence = Evidence(head_sha=head_sha)
    evidence.runs_on_head = _runs_for_sha(client, repo, head_sha)
    checks = _check_runs_for_sha(client, repo, head_sha)
    if checks is None:
        evidence.check_runs_unavailable = True
    else:
        evidence.check_runs_on_head = checks

    if evidence.runs_on_head or not deep:
        return evidence

    # Only when the head commit is bare is it worth paying for the rest.
    all_shas = _pull_request_commits(client, repo, number)
    if all_shas is None:
        evidence.commits_unavailable = True
        all_shas = []
    commit_shas = [sha for sha in all_shas if sha != head_sha]
    evidence.older_commit_shas = commit_shas
    older: List[WorkflowRun] = []
    for sha in reversed(commit_shas[-5:]):
        try:
            older.extend(_runs_for_sha(client, repo, sha))
        except GitHubError:
            evidence.commits_unavailable = True
            continue
        if older:
            break
    evidence.runs_on_older_commits = older

    evidence.repo_workflow_count = _repo_workflows(client, repo)
    if evidence.repo_workflow_count:
        evidence.repo_has_pull_request_workflow = _repo_has_pull_request_workflow(
            client, repo
        )
    elif evidence.repo_workflow_count == 0:
        evidence.repo_has_pull_request_workflow = False
    return evidence


def _pull_request(client: GitHubClient, repo: str, number: int) -> Dict[str, Any]:
    payload = client.get(f"repos/{repo}/pulls/{number}")
    if not isinstance(payload, dict) or not payload.get("number"):
        raise GitHubError(f"{repo}#{number} could not be read as a pull request")
    return payload


def analyze_pull_request(
    client: GitHubClient,
    repo: str,
    number: int,
    deep: bool = True,
    min_gap_days: float = DEFAULT_EXPIRY_GAP_DAYS,
) -> Verdict:
    """Classify one pull request by number."""
    pull = _pull_request(client, repo, number)
    head_sha = str(((pull.get("head") or {}).get("sha")) or "")
    evidence = collect_evidence(client, repo, number, head_sha, deep=deep)
    return classify(
        evidence,
        repo=repo,
        number=int(pull.get("number") or number),
        title=str(pull.get("title") or ""),
        author=str(((pull.get("user") or {}).get("login")) or ""),
        url=str(pull.get("html_url") or ""),
        min_gap_days=min_gap_days,
    )


def list_open_pull_requests(
    client: GitHubClient,
    repo: str,
    limit: int = 100,
) -> List[int]:
    """Open pull request numbers, most recently updated first."""
    items = client.paginate(
        f"repos/{repo}/pulls?state=open&sort=updated&direction=desc", "", limit=limit
    )
    numbers: List[int] = []
    for item in items:
        if isinstance(item, dict) and item.get("number"):
            numbers.append(int(item["number"]))
    return numbers


def analyze_pull_requests(
    client: GitHubClient,
    repo: str,
    numbers: Sequence[int],
    deep: bool = True,
    min_gap_days: float = DEFAULT_EXPIRY_GAP_DAYS,
    on_error: Optional[Any] = None,
) -> List[Verdict]:
    """Classify several pull requests in one repository.

    A failure on one pull request does not abort the batch. If
    ``on_error`` is given it is called with ``(number, exception)``.
    """
    verdicts: List[Verdict] = []
    for number in numbers:
        try:
            verdicts.append(
                analyze_pull_request(
                    client, repo, number, deep=deep, min_gap_days=min_gap_days
                )
            )
        except GitHubError as exc:
            if on_error is not None:
                on_error(number, exc)
            else:
                raise
    return verdicts
