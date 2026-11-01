"""Command line interface, also the body of the ``gh ci-truth`` extension."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from typing import List, Optional, Sequence

from ._version import __version__
from .analyze import analyze_pull_request, list_open_pull_requests
from .classify import DEFAULT_EXPIRY_GAP_DAYS
from .client import GhCliClient, GitHubClient, GitHubError, RecordedClient, TokenClient
from .model import Verdict
from .report import exit_code, render_json, render_text

__all__ = ["build_parser", "main", "resolve_repo"]

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

_EPILOG = """\
exit status:
  0  nothing to report
  1  (with --strict) at least one pull request was never actually tested
  2  (with --strict) at least one verdict is UNKNOWN
  3  operational failure

examples:
  gh ci-truth 2031 --repo NVIDIA/garak
  gh ci-truth 2031 2032 --repo NVIDIA/garak --verbose
  gh ci-truth --repo NVIDIA/garak --all-open --json
  gh ci-truth --repo owner/name --all-open --strict
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gh ci-truth",
        description=(
            "Report whether a pull request's CI actually ran. "
            "gh pr checks and statusCheckRollup show only check runs "
            "attached to the head commit, so a pull request whose "
            "workflows were never approved is indistinguishable from one "
            "whose checks all passed."
        ),
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "numbers",
        metavar="PR",
        nargs="*",
        type=int,
        help="pull request number(s); omit with --all-open",
    )
    parser.add_argument(
        "-R",
        "--repo",
        metavar="OWNER/REPO",
        help="target repository; defaults to the current directory's remote",
    )
    parser.add_argument(
        "--all-open",
        action="store_true",
        help="examine every open pull request in the repository",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        metavar="N",
        help="maximum pull requests to examine with --all-open (default 50)",
    )
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="emit JSON instead of text",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="list every workflow run and check run considered",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero when a pull request was never actually tested",
    )
    parser.add_argument(
        "--fail-on-tested-failing",
        action="store_true",
        help="with --strict, also exit non-zero for a genuine test failure",
    )
    parser.add_argument(
        "--shallow",
        action="store_true",
        help=(
            "skip the older-commit and repository-workflow lookups; faster, "
            "but cannot distinguish NO_CI_CONFIGURED or "
            "STALE_RESULTS_FOR_OLDER_COMMIT from UNKNOWN"
        ),
    )
    parser.add_argument(
        "--expiry-gap-days",
        type=float,
        default=DEFAULT_EXPIRY_GAP_DAYS,
        metavar="D",
        help=(
            "minimum created-to-updated gap, in days, for a zero-job failure "
            f"to count as an expired approval (default {DEFAULT_EXPIRY_GAP_DAYS})"
        ),
    )
    parser.add_argument(
        "--token",
        action="store_true",
        help="use GITHUB_TOKEN over the REST API instead of the gh CLI",
    )
    parser.add_argument(
        "--cassette",
        metavar="PATH",
        help="replay a recorded JSON cassette instead of calling GitHub",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"gh-ci-truth {__version__}",
    )
    return parser


def _git_remote_repo(cwd: Optional[str] = None) -> Optional[str]:
    """Read ``owner/repo`` from the local git remote, without using gh."""
    for remote in ("origin", "upstream"):
        try:
            completed = subprocess.run(
                ["git", "remote", "get-url", remote],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
                cwd=cwd,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if completed.returncode != 0:
            continue
        url = (completed.stdout or "").strip()
        match = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?$", url)
        if match and _REPO_RE.match(match.group(1)):
            return match.group(1)
    return None


def resolve_repo(explicit: Optional[str]) -> str:
    """Work out which repository to query.

    Order: the flag, then ``GH_REPO``, then ``GITHUB_REPOSITORY`` (set in
    every GitHub Actions runner), then the local git remote.
    """
    for candidate in (
        explicit,
        os.environ.get("GH_REPO"),
        os.environ.get("GITHUB_REPOSITORY"),
    ):
        if candidate:
            if not _REPO_RE.match(candidate):
                raise GitHubError(f"'{candidate}' is not of the form OWNER/REPO")
            return candidate
    inferred = _git_remote_repo()
    if inferred:
        return inferred
    raise GitHubError(
        "could not determine the repository; pass --repo OWNER/REPO"
    )


def _make_client(args: argparse.Namespace) -> GitHubClient:
    if args.cassette:
        return RecordedClient.from_file(args.cassette)
    if args.token:
        return TokenClient()
    return GhCliClient()


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if not args.numbers and not args.all_open:
        parser.error("give at least one pull request number, or use --all-open")
    if args.numbers and args.all_open:
        parser.error("--all-open cannot be combined with explicit pull request numbers")

    try:
        repo = resolve_repo(args.repo)
        client = _make_client(args)
        numbers: List[int] = list(args.numbers)
        if args.all_open:
            numbers = list_open_pull_requests(client, repo, limit=args.limit)
            if not numbers:
                print(f"{repo}: no open pull requests", file=sys.stderr)
    except (GitHubError, OSError, ValueError) as exc:
        # ValueError covers json.JSONDecodeError and UnicodeDecodeError from
        # a hand written --cassette file.
        print(f"gh-ci-truth: {exc}", file=sys.stderr)
        return 3

    verdicts: List[Verdict] = []
    failures: List[str] = []
    for number in numbers:
        try:
            verdicts.append(
                analyze_pull_request(
                    client,
                    repo,
                    number,
                    deep=not args.shallow,
                    min_gap_days=args.expiry_gap_days,
                )
            )
        except GitHubError as exc:
            failures.append(f"#{number}: {exc}")

    if args.as_json:
        print(render_json(verdicts))
    else:
        print(render_text(verdicts, verbose=args.verbose))

    for failure in failures:
        print(f"gh-ci-truth: {failure}", file=sys.stderr)
    if failures:
        # Any pull request we failed to examine is an operational failure,
        # even when others were examined fine. Reporting success here would
        # let a --strict gate pass over pull requests nobody looked at,
        # which is the same silence the tool exists to break.
        print(
            f"gh-ci-truth: {len(failures)} pull request(s) could not be "
            "examined; no verdict is given for them",
            file=sys.stderr,
        )
        return 3

    return exit_code(
        verdicts,
        strict=args.strict,
        fail_on_tested_failing=args.fail_on_tested_failing,
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
