"""gh-ci-truth: tell whether a pull request's CI actually ran.

``gh pr checks`` and ``gh pr view --json statusCheckRollup`` report only
check runs attached to the head commit. A workflow run that was never
approved, or whose approval request expired, produces zero jobs and
therefore attaches no check run, so the pull request renders exactly like
one whose checks all passed.

This package separates the two states. The classifier is a pure function
over recorded evidence, so other tools can import it without inheriting
any network behaviour:

    from gh_ci_truth import Category, classify, Evidence, WorkflowRun

    evidence = Evidence(head_sha="4b3e..." , runs_on_head=[...])
    verdict = classify(evidence, repo="owner/name", number=2031)
    verdict.category is Category.NEVER_RAN_EXPIRED

To go to GitHub for the evidence:

    from gh_ci_truth import GhCliClient, analyze_pull_request

    verdict = analyze_pull_request(GhCliClient(), "NVIDIA/garak", 2031)
"""

from ._version import __version__
from .analyze import (
    analyze_pull_request,
    analyze_pull_requests,
    collect_evidence,
    list_open_pull_requests,
)
from .classify import (
    APPROVAL_WINDOW_DAYS,
    DEFAULT_EXPIRY_GAP_DAYS,
    classify,
    is_expiry_signature,
)
from .client import (
    GhCliClient,
    GitHubClient,
    GitHubError,
    RecordedClient,
    TokenClient,
    default_client,
)
from .model import (
    UNTESTED_CATEGORIES,
    Category,
    CheckRun,
    Evidence,
    Verdict,
    WorkflowRun,
)
from .report import (
    CATEGORY_HEADLINE,
    exit_code,
    render_json,
    render_markdown,
    render_text,
)

__author__ = "Manu Nicholas Jacob"
__license__ = "MIT"

__all__ = [
    "__version__",
    "__author__",
    "__license__",
    "APPROVAL_WINDOW_DAYS",
    "CATEGORY_HEADLINE",
    "DEFAULT_EXPIRY_GAP_DAYS",
    "UNTESTED_CATEGORIES",
    "Category",
    "CheckRun",
    "Evidence",
    "GhCliClient",
    "GitHubClient",
    "GitHubError",
    "RecordedClient",
    "TokenClient",
    "Verdict",
    "WorkflowRun",
    "analyze_pull_request",
    "analyze_pull_requests",
    "classify",
    "collect_evidence",
    "default_client",
    "exit_code",
    "is_expiry_signature",
    "list_open_pull_requests",
    "render_json",
    "render_markdown",
    "render_text",
]
