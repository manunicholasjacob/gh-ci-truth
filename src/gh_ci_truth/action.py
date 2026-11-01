"""Entry point used by ``action.yml``.

Runs inside a GitHub Actions runner. It reads the pull request number
from the event payload, classifies it, writes step outputs and a job
summary, and prints the body a maintainer would want posted.

It performs no write calls of its own. Labelling and commenting are left
to the caller's own workflow step, using this step's outputs, so that the
permissions needed for a write stay in the consuming repository's
workflow where its maintainer can see them.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from typing import Any, Dict, Optional

from .analyze import analyze_pull_request
from .classify import DEFAULT_EXPIRY_GAP_DAYS
from .client import GitHubError, TokenClient
from .model import Category, Verdict
from .report import CATEGORY_HEADLINE, render_markdown, render_text

__all__ = ["main"]


def _env_flag(name: str, default: bool = False) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        print(
            f"::warning title=gh-ci-truth::{name}={raw!r} is not a number; "
            f"using {default}"
        )
        return default


def _event_payload() -> Dict[str, Any]:
    path = os.environ.get("GITHUB_EVENT_PATH") or ""
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _pull_request_number() -> Optional[int]:
    explicit = (os.environ.get("INPUT_PR_NUMBER") or "").strip()
    if explicit:
        try:
            return int(explicit)
        except ValueError:
            return None
    payload = _event_payload()
    for key in ("pull_request", "issue"):
        candidate = payload.get(key)
        if isinstance(candidate, dict) and candidate.get("number"):
            return int(candidate["number"])
    number = payload.get("number")
    if isinstance(number, int):
        return number
    return None


def _write_outputs(values: Dict[str, str]) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        for key, value in values.items():
            print(f"{key}={value}")
        return
    # The heredoc delimiter is generated per call. A fixed delimiter would
    # be a real hole: the reason, comment-body and json outputs embed
    # workflow run names, which on a fork pull request come from the
    # contributor's own workflow file. A contributor who named a workflow
    # after a known delimiter could inject arbitrary step outputs,
    # including misleading=false, and so switch off whatever gating the
    # consuming workflow built on this action.
    delimiter = "ghcitruth_" + uuid.uuid4().hex
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            text = value.replace("\r\n", "\n").replace("\r", "\n")
            if delimiter in text:
                # Astronomically unlikely, but the failure mode is output
                # injection, so the value is refused rather than escaped.
                text = "[value withheld: it contained this run's delimiter]"
            if "\n" in text:
                handle.write(f"{key}<<{delimiter}\n{text}\n{delimiter}\n")
            else:
                handle.write(f"{key}={text}\n")


def _write_summary(text: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")
    except OSError:
        pass


def _fail(message: str) -> int:
    print(f"::error title=gh-ci-truth::{message}")
    return 1


def main() -> int:
    repo = (os.environ.get("INPUT_REPO") or os.environ.get("GITHUB_REPOSITORY") or "").strip()
    if "/" not in repo:
        return _fail("no repository: set the 'repo' input or run inside a repository")

    number = _pull_request_number()
    if number is None:
        return _fail(
            "no pull request number: run this on a pull_request event or set "
            "the 'pr-number' input"
        )

    token = (os.environ.get("INPUT_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()
    if not token:
        return _fail("no token: pass the 'token' input or set GITHUB_TOKEN")

    api_root = (os.environ.get("GITHUB_API_URL") or "https://api.github.com").rstrip("/") + "/"
    client = TokenClient(token=token, api_root=api_root)

    try:
        verdict: Verdict = analyze_pull_request(
            client,
            repo,
            number,
            deep=not _env_flag("INPUT_SHALLOW"),
            min_gap_days=_env_float("INPUT_EXPIRY_GAP_DAYS", DEFAULT_EXPIRY_GAP_DAYS),
        )
    except GitHubError as exc:
        return _fail(str(exc))

    comment_body = render_markdown(verdict)
    _write_outputs(
        {
            "category": verdict.category.value,
            "tested": "true" if verdict.tested else "false",
            "never-actually-tested": "true" if verdict.never_actually_tested else "false",
            "misleading": "true" if verdict.misleading else "false",
            "confidence": verdict.confidence,
            "head-sha": verdict.evidence.head_sha,
            "jobs-executed": str(verdict.evidence.executed_job_count),
            "reason": verdict.reason,
            "headline": CATEGORY_HEADLINE[verdict.category],
            "label": (os.environ.get("INPUT_LABEL") or "ci-never-ran").strip(),
            "should-label": "true" if verdict.never_actually_tested else "false",
            "comment-body": comment_body,
            "json": json.dumps(verdict.to_dict(), sort_keys=False),
        }
    )
    _write_summary(comment_body)
    print(render_text([verdict], verbose=True))

    if verdict.never_actually_tested:
        print(
            f"::warning title=gh-ci-truth::{repo}#{number} is "
            f"{verdict.category.value}: {verdict.reason}"
        )

    if _env_flag("INPUT_FAIL_ON_UNTESTED") and verdict.never_actually_tested:
        return _fail(
            f"{repo}#{number} has never been tested ({verdict.category.value})"
        )
    if _env_flag("INPUT_FAIL_ON_UNKNOWN") and verdict.category is Category.UNKNOWN:
        return _fail(f"{repo}#{number} could not be classified")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
