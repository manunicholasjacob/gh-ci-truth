"""GitHub access layer.

Three implementations of one small interface:

* :class:`GhCliClient` shells out to ``gh api``, so it inherits the
  authentication the user already has.
* :class:`TokenClient` talks to the REST API directly with ``urllib``,
  for use inside a GitHub Action where ``GITHUB_TOKEN`` is present and
  ``gh`` may not be.
* :class:`RecordedClient` replays JSON recorded from the real API, which
  is what the test suite uses. No test makes a network call.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

__all__ = [
    "GitHubError",
    "GitHubClient",
    "GhCliClient",
    "TokenClient",
    "RecordedClient",
    "default_client",
]

_API_ROOT = "https://api.github.com/"


class GitHubError(RuntimeError):
    """Any failure to obtain data from GitHub."""


@runtime_checkable
class GitHubClient(Protocol):
    """The whole surface the analyzer needs.

    Implement this to drive the classifier from any other source: a
    cache, a GraphQL layer, a different forge with a compatible shape.
    """

    def get(self, path: str) -> Any:
        """GET an API path such as ``repos/o/r/actions/runs?head_sha=...``."""
        raise NotImplementedError

    def paginate(self, path: str, key: str, limit: int = 300) -> List[Dict[str, Any]]:
        """GET an API path and follow pages, collecting ``key`` from each."""
        raise NotImplementedError


def _strip(path: str) -> str:
    return path.lstrip("/")


class _PaginateByPageParam:
    """Page by appending ``page=`` and ``per_page=``.

    Shared by the CLI and token clients. Link-header following is not
    used because ``gh api -q`` does not expose headers; incrementing the
    page parameter until a short page arrives is equivalent for these
    endpoints and keeps both clients on one code path.
    """

    def get(self, path: str) -> Any:  # pragma: no cover - overridden
        raise NotImplementedError

    #: Hard cap on pages, so a pathological response cannot loop forever.
    MAX_PAGES = 20

    def paginate(self, path: str, key: str, limit: int = 300) -> List[Dict[str, Any]]:
        per_page = min(100, max(1, limit))
        collected: List[Dict[str, Any]] = []
        page = 1
        while len(collected) < limit:
            joiner = "&" if "?" in path else "?"
            paged = f"{path}{joiner}per_page={per_page}&page={page}"
            payload = self.get(paged)
            if isinstance(payload, list):
                batch: List[Any] = payload
            elif isinstance(payload, dict):
                # A dict where a list was expected is an API error object
                # far more often than it is an empty collection. Reading it
                # as "nothing here" is how a tool reports a falsely clean
                # result, so it is an error instead.
                if key and key in payload:
                    found = payload.get(key)
                    batch = found if isinstance(found, list) else []
                else:
                    raise GitHubError(
                        f"expected a list from {path}, got an object with "
                        f"keys {sorted(payload)[:5]}"
                    )
            else:
                raise GitHubError(f"expected a list from {path}, got {type(payload).__name__}")
            collected.extend(batch)
            if len(batch) < per_page:
                break
            page += 1
            if page > self.MAX_PAGES:
                raise GitHubError(
                    f"{path} has more than {self.MAX_PAGES * per_page} items; "
                    "refusing to return a silently truncated list"
                )
        return collected[:limit]


class GhCliClient(_PaginateByPageParam):
    """Read the API through ``gh api``. Read only: never sends a method."""

    def __init__(self, gh_path: Optional[str] = None, timeout: int = 60) -> None:
        self.gh_path = gh_path or shutil.which("gh") or "gh"
        self.timeout = timeout

    def get(self, path: str) -> Any:
        command = [self.gh_path, "api", "-X", "GET", _strip(path)]
        try:
            # The encoding must be stated. With text=True and no encoding,
            # Python decodes using the locale, which on a Windows console is
            # cp1252; a pull request title containing an accented character
            # then raises UnicodeDecodeError inside subprocess's reader
            # thread, leaving stdout empty and the failure invisible. The
            # GitHub API always answers in UTF-8.
            completed = subprocess.run(
                command,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise GitHubError(
                "the gh CLI was not found on PATH; install it from "
                "https://cli.github.com or use a token based client"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise GitHubError(f"gh api timed out after {self.timeout}s on {path}") from exc
        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout or "").strip()
            raise GitHubError(f"gh api failed for {path}: {message}")
        if completed.stdout is None:
            raise GitHubError(
                f"gh api produced no readable output for {path}; this is an "
                "error, not an empty result"
            )
        text = completed.stdout.strip()
        if not text:
            raise GitHubError(
                f"gh api returned an empty body for {path}; this is an error, "
                "not an empty result"
            )
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise GitHubError(f"gh api returned non-JSON for {path}") from exc


class TokenClient(_PaginateByPageParam):
    """Read the API with a bearer token, for use inside a GitHub Action."""

    def __init__(
        self,
        token: Optional[str] = None,
        api_root: str = _API_ROOT,
        timeout: int = 60,
    ) -> None:
        self.token = token or os.environ.get("GITHUB_TOKEN") or ""
        self.api_root = api_root if api_root.endswith("/") else api_root + "/"
        self.timeout = timeout

    def get(self, path: str) -> Any:
        url = self.api_root + _strip(path)
        request = urllib.request.Request(url, method="GET")
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        request.add_header("User-Agent", "gh-ci-truth")
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise GitHubError(f"HTTP {exc.code} for {path}") from exc
        except urllib.error.URLError as exc:
            raise GitHubError(f"could not reach {url}: {exc.reason}") from exc
        if not body.strip():
            raise GitHubError(
                f"empty response body for {path}; this is an error, not an "
                "empty result"
            )
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise GitHubError(f"non-JSON response for {path}") from exc


class RecordedClient:
    """Replay recorded API responses.

    A cassette is a JSON object with a ``responses`` map from API path to
    the payload that path returned. Paths are matched exactly first, then
    with pagination parameters stripped, so a cassette recorded without
    ``per_page``/``page`` still satisfies a paginated request.
    """

    def __init__(self, responses: Dict[str, Any], strict: bool = True) -> None:
        self.responses = dict(responses)
        self.strict = strict
        self.requested: List[str] = []

    @classmethod
    def from_file(cls, path: str, strict: bool = True) -> "RecordedClient":
        # UTF-8, not ASCII: this project's own cassettes are ASCII, but a
        # user recording their own will have whatever GitHub returned in a
        # pull request title, and a decode error here is not a useful
        # failure mode.
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        responses = data.get("responses", data)
        return cls(responses, strict=strict)

    @staticmethod
    def _without_paging(path: str) -> str:
        head, _, query = _strip(path).partition("?")
        if not query:
            return head
        kept = [
            part
            for part in query.split("&")
            if part and not part.startswith(("per_page=", "page="))
        ]
        return head + ("?" + "&".join(kept) if kept else "")

    def get(self, path: str) -> Any:
        key = _strip(path)
        self.requested.append(key)
        if key in self.responses:
            return self.responses[key]
        bare = self._without_paging(key)
        if bare in self.responses:
            return self.responses[bare]
        if self.strict:
            raise GitHubError(f"no recorded response for {key}")
        return None

    def paginate(self, path: str, key: str, limit: int = 300) -> List[Dict[str, Any]]:
        payload = self.get(path)
        if isinstance(payload, dict):
            items = payload.get(key) or []
        elif isinstance(payload, list):
            items = payload
        else:
            items = []
        return list(items)[:limit] if isinstance(items, list) else []


def default_client() -> GitHubClient:
    """Pick a client for the current environment.

    Prefers ``gh`` when it is on PATH, because it already holds the
    user's credentials. Falls back to ``GITHUB_TOKEN``, which is the
    situation inside a GitHub Action runner.
    """
    if shutil.which("gh"):
        return GhCliClient()
    if os.environ.get("GITHUB_TOKEN"):
        return TokenClient()
    raise GitHubError(
        "no way to reach the GitHub API: install the gh CLI or set GITHUB_TOKEN"
    )
