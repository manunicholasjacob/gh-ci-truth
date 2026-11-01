# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Nothing yet.

## [0.1.0] - 2026-11-01

First release. Unpublished at the time of writing: the tag, the GitHub
repository and the PyPI upload are listed in `RELEASE_CHECKLIST.md` and have
not been performed.

### Added

- `classify`, a pure function from recorded evidence to one of seven
  categories: `TESTED_PASSING`, `TESTED_FAILING`,
  `NEVER_RAN_AWAITING_APPROVAL`, `NEVER_RAN_EXPIRED`, `NO_CI_CONFIGURED`,
  `STALE_RESULTS_FOR_OLDER_COMMIT`, `UNKNOWN`.
- Detection of the expired approval signature: `status: completed`,
  `conclusion: failure`, zero jobs, and a created-to-updated gap of about 30
  days. All four conditions are required.
- A `misleading` flag, true only when a pull request is untested and nothing
  attached to the head commit is failing. This is the condition the project
  exists to detect and the one to gate a label or comment on.
- `gh ci-truth`, a `gh` CLI extension, supporting a single pull request, a
  list, `--repo`, `--all-open`, `--json`, `--verbose`, `--shallow`,
  `--strict`, `--fail-on-tested-failing`, `--expiry-gap-days`, `--token` and
  `--cassette`.
- An importable library with the same classifier. Public surface listed in
  `README.md`. `GitHubClient` is a two method `Protocol`, so the classifier
  can be driven from a cache or another forge.
- Three API clients: `GhCliClient` over `gh api`, `TokenClient` over REST
  for use inside a runner, and `RecordedClient` for replaying cassettes.
- `action.yml`, a composite GitHub Action emitting `category`, `misleading`,
  `never-actually-tested`, `comment-body`, `json` and nine other outputs. It
  makes no write calls; labelling is left to the consuming workflow so that
  the write permission stays visible there.
- 167 offline tests. Four cassettes recorded from the live GitHub REST API
  against NVIDIA/garak on 2026-11-01, five labelled synthetic. A fixture
  fails the suite if any test opens a socket.
- `README.md` with a table separating what GitHub documents from what was
  only observed, and a limitations section.

### Notes on observed behaviour

- A workflow run pending approval reports `status: completed` with
  `conclusion: action_required`, not `status: action_required`. The
  documented `status` filter value matches the conclusion. Both fields are
  checked.
- GitHub documents that an unapproved run is "marked as failed" after 30
  days, but the REST reference documents no `conclusion` value for it and no
  `expired` value exists in any enumeration. The signature is therefore an
  inference from observed data, and `--expiry-gap-days` is provided for
  cases where it does not hold.
- `head_sha` on the workflow runs endpoint matches exactly. A seven
  character prefix returned zero results. The 40 character requirement is
  not documented either way.

### Fixed during development

An independent review pass found several cases where the tool would have
reported a falsely clean result, which is the one failure mode it exists to
prevent. Each now has a regression test.

- A run whose job count could not be collected was dropped silently, because
  the code that treats runs as test-bearing counts an uncollected count as
  zero. On a commit where some other run passed, one failed jobs request
  produced `TESTED_PASSING` at high confidence with no note. Uncollected job
  counts are now detected before any execution question is asked, and
  produce `UNKNOWN` or an explicit note.
- An unexpected payload from the workflow runs endpoint, including an API
  error object or an empty body, was read as "this commit has no runs" and
  classified as `NO_CI_CONFIGURED` at high confidence, indistinguishable
  from a genuinely empty run list. Both clients now raise on an empty body,
  and the analyzer refuses to read a payload without a `workflow_runs` array
  as zero runs.
- A pull request that could not be examined did not affect the exit status,
  so `--all-open --strict` could pass over pull requests nobody looked at.
  Any such failure now exits 3.
- The workflow runs request did not set `per_page`, taking GitHub's default
  of 30. A commit in a repository with more than 30 triggered workflows
  could hide an expired run. It now requests 100 and refuses to give a
  verdict on a truncated list.
- A run carrying a pending-approval marker that had nonetheless executed
  jobs was reported as never run, with a reason that said it had produced no
  job. GitHub also uses `waiting` for environment approvals, which can gate
  a run that has already done work. The never-ran categories now require
  zero jobs.
- A `stale` conclusion on a run that executed jobs was reported as
  `TESTED_PASSING`. GitHub sets `stale` when it has invalidated the result.
- When the check runs endpoint could not be read, the reports stated that
  the checks panel was empty, which is a claim about the world made on a
  failed request. The evidence now carries an explicit unavailable flag and
  the reports say the panel could not be read.
- The GitHub Action wrote multi-line step outputs with a fixed, public
  heredoc delimiter. The `reason`, `comment-body` and `json` outputs embed
  workflow run names, which on a fork pull request come from the
  contributor's own workflow file, so a contributor could have injected
  arbitrary step outputs including `misleading=false` and switched off a
  consuming workflow's gating. The delimiter is now generated per call.
- The pagination helper read an unexpected object as an empty collection,
  hardcoded `per_page` to 100 regardless of the limit, and truncated
  silently at its page cap. It now raises in the first and third cases and
  sizes the page to the limit. The helper had also never been exercised by a
  test, because recorded cassettes bypass it; it now has seven.
- `RecordedClient` opened cassettes as ASCII, so a user-supplied cassette
  containing a non-ASCII pull request title raised an uncaught
  `UnicodeDecodeError`. Cassettes are now read as UTF-8, and a malformed one
  is reported as an error rather than a traceback.
- A failing `origin` remote lookup aborted the `upstream` lookup instead of
  continuing to it.
- `pyproject.toml` used three forms setuptools has deprecated. It now uses
  an SPDX `license` expression with `license-files` under `[project]`.
- The CI matrix paired `macos-latest` with Python 3.9. Those runners are
  arm64 and `actions/setup-python` publishes no CPython 3.9 build for
  darwin-arm64, so that cell could never resolve. It is excluded, with
  `macos-13` covering 3.9 instead.
- The ASCII check in CI used `if grep ...`, which treats grep's error status
  of 2 as "clean", so a step that could not read the tree would have
  reported it verified.

- `GhCliClient` decoded `gh api` output using the locale encoding, which is
  cp1252 on a Windows console. A non-ASCII pull request title raised
  `UnicodeDecodeError` inside subprocess's reader thread, leaving stdout
  empty, and `--all-open` reported "no open pull requests" for a repository
  that had many. Output is now decoded as UTF-8, and unreadable output
  raises instead of returning an empty result.

[Unreleased]: https://github.com/manunicholasjacob/gh-ci-truth/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/manunicholasjacob/gh-ci-truth/releases/tag/v0.1.0
