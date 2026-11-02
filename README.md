# gh-ci-truth

[![PyPI](https://img.shields.io/pypi/v/gh-ci-truth)](https://pypi.org/project/gh-ci-truth/)
[![CI](https://github.com/manunicholasjacob/gh-ci-truth/actions/workflows/ci.yml/badge.svg)](https://github.com/manunicholasjacob/gh-ci-truth/actions/workflows/ci.yml)
[![Python](https://img.shields.io/pypi/pyversions/gh-ci-truth)](https://pypi.org/project/gh-ci-truth/)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

Tell whether a pull request's CI actually ran, instead of whether its checks panel looks clean.

A `gh` CLI extension, an importable Python library, and a GitHub Action, all driven by one classifier.

## The problem

`gh pr checks` and `gh pr view --json statusCheckRollup` report check runs attached to the head commit. That is all they report. GitHub's own documentation is explicit that the rollup is commit scoped: `StatusCheckRollup.contexts` is "A list of status contexts and check runs for this commit." ([GraphQL reference: commits](https://docs.github.com/en/graphql/reference/commits)).

For an outside contributor, GitHub holds workflow runs for maintainer approval. A held run has executed no job, so it attaches no check run. If nobody approves it within 30 days, GitHub closes the window: "awaiting approval for more than 30 days expire" and "and are marked as failed" ([Approving workflow runs from forks](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/approve-runs-from-forks)). The run reaches a terminal state having still executed no job, so it still attaches no check run.

The result is that a pull request whose CI has never produced a single test result renders identically to one with no CI required. A blank checks panel reads as a clean one, to maintainers and to contributors alike. The distinction between "no checks exist" and "checks exist and are green" is the distinction the existing tooling destroys, and it is the one this tool restores.

## The verified evidence

Two pull requests in the public repository [NVIDIA/garak](https://github.com/NVIDIA/garak), numbers 2031 and 2032. They are the author's own pull requests, used here as a worked example because the author can speak to their full history. Nothing below characterizes the repository's maintainers; approval queues go unattended for ordinary reasons.

Recorded from the live REST API on 2026-11-01:

```
$ gh pr view 2031 --repo NVIDIA/garak --json headRefOid -q .headRefOid
4b3e3374cf8355388460e99f055314209d73089e

$ gh api "repos/NVIDIA/garak/actions/runs?head_sha=4b3e3374cf8355388460e99f055314209d73089e" \
    -q '.workflow_runs[]|"\(.name)|\(.created_at)|\(.status)|\(.conclusion)"'
Garak pytest - MacOS|2026-08-28T01:13:15Z|completed|failure
Garak pip - install from repo|2026-08-28T01:13:15Z|completed|failure
Garak pytest - Linux|2026-08-28T01:13:15Z|completed|failure
Garak pytest - Windows|2026-08-28T01:13:15Z|completed|failure
Documentation build|2026-08-28T01:13:15Z|completed|failure

$ gh api repos/NVIDIA/garak/actions/runs/33132180993/jobs -q .total_count
0
```

Every run was created `2026-08-28T01:13:15Z` and reached `2026-09-27T01:17:20Z`. That is 30 days and about four minutes. Every one reports `status: completed`, `conclusion: failure`, and zero jobs.

Now the part that matters. What does the checks panel show for that same commit?

```
$ gh pr view 2031 --repo NVIDIA/garak --json statusCheckRollup
{"statusCheckRollup":[{"__typename":"CheckRun","conclusion":"SUCCESS","name":"DCO",...}]}
```

One check run. It is green. It is a signature check, not a test. Five test workflows produced nothing at all and are absent from the panel entirely. The pull request reads as mergeable with a passing check, and has never been tested.

`gh-ci-truth` says so instead:

```
$ gh ci-truth 2031 --repo NVIDIA/garak
NVIDIA/garak#2031  NEVER_RAN_EXPIRED
  title       fix(analyze): count prompt characters, not dict length, in count_tokens
  author      manunicholasjacob
  verdict     CI has never run: the approval request expired after 30 days
  because     5 workflow runs on the head commit sat unapproved for 30.0 days and were
              expired by GitHub with zero jobs: Documentation build, Garak pip - install
              from repo, Garak pytest - Linux, Garak pytest - MacOS, Garak pytest - Windows
  confidence  high
  head        4b3e3374cf8355388460e99f055314209d73089e
  rollup says 1 check run(s) attached: 1 success
  jobs run    0
  WARNING     the checks panel does not show a failure for this commit, so this pull
              request reads as clean while having no test result
```

Run across the repository's open pull requests on 2026-11-01, the first eight gave four distinct categories, five of the eight never actually tested while all five looked clean:

```
$ gh ci-truth --repo NVIDIA/garak --all-open --limit 8
NVIDIA/garak#2385  NEVER_RAN_AWAITING_APPROVAL
NVIDIA/garak#2243  TESTED_PASSING
NVIDIA/garak#2383  NEVER_RAN_AWAITING_APPROVAL
NVIDIA/garak#2378  TESTED_PASSING
NVIDIA/garak#2257  TESTED_PASSING
NVIDIA/garak#2032  NEVER_RAN_EXPIRED
NVIDIA/garak#2031  NEVER_RAN_EXPIRED
NVIDIA/garak#2030  NEVER_RAN_AWAITING_APPROVAL
------------------------------------------------------------------------
8 pull request(s) examined, 5 never actually tested, 5 of those look clean, 0 undetermined
look clean but were never tested: #2385, #2383, #2032, #2031, #2030
```

### The second trap: a fix can make it worse

Pushing a new commit to an unapproved pull request re-queues its CI. A merge that resolves a conflict therefore moves the pull request from "has stale results on an older commit" to "has no results at all", and the checks panel gets emptier, not fuller. That is the `STALE_RESULTS_FOR_OLDER_COMMIT` category.

## Categories

| Category | Meaning | Does the panel mislead? |
| --- | --- | --- |
| `TESTED_PASSING` | Runs on the head commit executed jobs and none failed. | No |
| `TESTED_FAILING` | Runs on the head commit executed jobs and at least one failed. Also covers a workflow that failed to start, which does surface as a red check. | No |
| `NEVER_RAN_AWAITING_APPROVAL` | Runs exist for the head commit but are parked for maintainer approval with zero jobs. Actionable today. | Yes |
| `NEVER_RAN_EXPIRED` | Runs sat unapproved for about 30 days and were expired with zero jobs. Needs a new push to re-queue. | Yes |
| `NO_CI_CONFIGURED` | No run for the head commit, and the repository has no workflows, no workflow that fires on pull requests, or skipped every one. | No |
| `STALE_RESULTS_FOR_OLDER_COMMIT` | No run for the head commit, but an earlier commit of the pull request did execute jobs. | Yes |
| `UNKNOWN` | The evidence does not settle it. Reported as undetermined rather than guessed. | Unclear |

Each verdict also carries `confidence` (`high`, `medium`, `low`) and a `misleading` flag, which is true only when the pull request is untested *and* nothing attached to the head commit is failing. A pull request with an expired run and a separate failing lint check is untested but not misleading: the panel is already red, so nobody is deceived. Gate labels and comments on `misleading`, not on `category`.

## The detection recipe

The signature of an expired approval, as opposed to a real test failure:

1. `status == "completed"`
2. `conclusion == "failure"` (also accepted: `cancelled`, `stale`, `skipped`)
3. the jobs endpoint reports `total_count == 0`
4. `created_at` to `updated_at` is about 30 days

All four must hold. A real failure has jobs. A workflow that could not start has a gap of seconds. The job count is the load bearing field and the one the rollup never shows you.

A run still awaiting approval has a different shape, and it is not the shape the REST documentation implies. Observed on 2026-11-01:

```
$ gh api "repos/NVIDIA/garak/actions/runs?status=action_required&per_page=1" \
    -q '.workflow_runs[]|"\(.status)|\(.conclusion)|\(.created_at)|\(.updated_at)"'
completed|action_required|2026-11-01T10:34:26Z|2026-11-01T10:34:26Z
```

`status` is `completed`; the pending marker is in `conclusion`. The documented `status` filter value `action_required` in fact matches the conclusion. Reading only `status` would classify a parked run as an ordinary completed one. This tool checks both fields.

Note also that `head_sha` matches exactly. A seven character prefix of a real head SHA returned `total_count: 0` when tested on 2026-11-01. Always pass the full 40 characters.

## Install

### As a `gh` extension

```
gh extension install manunicholasjacob/gh-ci-truth
gh ci-truth --help
```

Needs Python 3.9 or newer on `PATH`. No third party Python packages. The extension is a shim over the bundled library, so there is one classifier, not two.

### As a library

```
pip install gh-ci-truth
```

### As a GitHub Action

```yaml
# .github/workflows/ci-truth.yml
name: ci-truth

on:
  pull_request_target:
    types: [opened, synchronize, reopened]
  schedule:
    - cron: "17 6 * * *"

permissions:
  contents: read
  pull-requests: write
  actions: read

jobs:
  ci-truth:
    runs-on: ubuntu-latest
    steps:
      - id: truth
        uses: manunicholasjacob/gh-ci-truth@v0.1.0

      - name: Label a pull request that only looks tested
        if: steps.truth.outputs.misleading == 'true'
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          gh pr edit "${{ github.event.pull_request.number }}" \
            --repo "${{ github.repository }}" \
            --add-label "${{ steps.truth.outputs.label }}"
```

The action makes no write calls of its own. It classifies and emits outputs; labelling and commenting are left to your own step, so the write permission stays visible in your workflow where you can audit it. `steps.truth.outputs.comment-body` is a ready made Markdown report if you would rather comment than label.

On `pull_request_target` the workflow runs with the base repository's permissions, which is what lets it label a fork's pull request. It checks out nothing and runs no code from the fork.

## Usage

```
gh ci-truth 2031 --repo NVIDIA/garak
gh ci-truth 2031 2032 --repo NVIDIA/garak --verbose
gh ci-truth --repo NVIDIA/garak --all-open --limit 50
gh ci-truth --repo owner/name --all-open --json
gh ci-truth --repo owner/name --all-open --strict
```

| Flag | Effect |
| --- | --- |
| `-R`, `--repo OWNER/REPO` | Target repository. Falls back to `GH_REPO`, then `GITHUB_REPOSITORY`, then the local git remote. |
| `--all-open` | Examine every open pull request. |
| `--limit N` | Cap for `--all-open`. Default 50. |
| `--json` | Machine readable output under the `gh-ci-truth/v1` schema. |
| `-v`, `--verbose` | List every workflow run and check run considered. |
| `--strict` | Exit non-zero when a pull request was never actually tested. |
| `--fail-on-tested-failing` | With `--strict`, also fail for a genuine test failure. Off by default, because `gh pr checks` already reports those correctly. |
| `--shallow` | Skip the older-commit and repository-workflow lookups. Three fewer requests per pull request, at the cost of collapsing `NO_CI_CONFIGURED` and `STALE_RESULTS_FOR_OLDER_COMMIT` into `UNKNOWN`. |
| `--expiry-gap-days D` | Gap threshold for the expiry signature. Default 29.0. |
| `--token` | Use `GITHUB_TOKEN` over REST instead of the `gh` CLI. |
| `--cassette PATH` | Replay a recorded JSON cassette instead of calling GitHub. |

Exit status: `0` nothing to report, `1` something was never actually tested, `2` something is `UNKNOWN`, `3` operational failure. Without `--strict` a successful run is always `0`.

## Library API

The classifier is a pure function from recorded evidence to a verdict. It has no clock, no network and no subprocess, which is why it is safe to depend on.

```python
from gh_ci_truth import Category, Evidence, WorkflowRun, classify

evidence = Evidence(
    head_sha="4b3e3374cf8355388460e99f055314209d73089e",
    runs_on_head=[
        WorkflowRun(
            id=33132180993,
            name="Garak pytest - MacOS",
            status="completed",
            conclusion="failure",
            created_at="2026-08-28T01:13:15Z",
            updated_at="2026-09-27T01:17:20Z",
            job_count=0,
        )
    ],
)

verdict = classify(evidence, repo="NVIDIA/garak", number=2031)
verdict.category is Category.NEVER_RAN_EXPIRED   # True
verdict.never_actually_tested                    # True
verdict.misleading                               # True
verdict.reason                                   # a sentence naming the workflows
```

To let the library fetch the evidence:

```python
from gh_ci_truth import GhCliClient, analyze_pull_request, render_json

verdict = analyze_pull_request(GhCliClient(), "NVIDIA/garak", 2031)
print(render_json([verdict]))
```

Public surface:

- Model: `Category`, `UNTESTED_CATEGORIES`, `WorkflowRun`, `CheckRun`, `Evidence`, `Verdict`
- Classifier: `classify`, `is_expiry_signature`, `DEFAULT_EXPIRY_GAP_DAYS`, `APPROVAL_WINDOW_DAYS`
- Access: `GitHubClient` (a `Protocol`), `GhCliClient`, `TokenClient`, `RecordedClient`, `default_client`, `GitHubError`
- Orchestration: `analyze_pull_request`, `analyze_pull_requests`, `collect_evidence`, `list_open_pull_requests`
- Rendering: `render_text`, `render_json`, `render_markdown`, `CATEGORY_HEADLINE`, `exit_code`

`GitHubClient` is a two method `Protocol` (`get`, `paginate`). Implement it to drive the classifier from a cache, a GraphQL layer, or a different forge.

## Limitations

Stated plainly, because a tool about misleading signals should not produce any.

- **The 30 day gap is a heuristic, not a flag.** GitHub does not expose "this run expired". The documentation says an unapproved run is "marked as failed" in prose, but the REST reference never documents which `conclusion` an expired run receives, and no `expired` value exists in any documented enumeration. The four part signature is an inference from observed data. A workflow that genuinely took 30 days to fail would be misread, and `--expiry-gap-days` exists for that case.
- **`UNKNOWN` is a real answer.** A head commit with no runs in a repository that does have pull request workflows is reported as `UNKNOWN`, not guessed at. A path filter, a branch filter, a `paths-ignore`, or a deleted run all produce that state and the API does not distinguish them.
- **`NO_CI_CONFIGURED` partly rests on a proxy.** "Has any workflow in this repository ever run for a `pull_request` event" stands in for "a `pull_request` trigger exists", because reading every workflow file costs a request each and still misses reusable workflows. Verdicts resting on the proxy are marked `medium` confidence.
- **Cost.** One request for the pull request, one for its runs, one per run for the job count, one for the check runs. A pull request with five workflows is about eight requests. `--all-open` across a busy repository will feel it, and `--shallow` only helps when the head commit has no runs.
- **Job counts, not job results.** The tool asks whether jobs ran, then trusts the run level `conclusion` for pass or fail. It does not inspect individual job conclusions, annotations, or logs.
- **Required checks are not consulted.** It does not read branch protection, so it cannot say whether the workflows that never ran were ones the repository requires.
- **Only `action_required` expiry is modelled.** The 35 day total run limit and the 24 hour self-hosted queue limit are separate mechanisms and are not detected.
- **GitHub Enterprise Server differs.** The GHES 3.19 documentation says runs awaiting approval over 30 days "are automatically deleted" ([GHES 3.19](https://docs.github.com/en/enterprise-server@3.19/actions/how-tos/manage-workflow-runs/approve-runs-from-forks)), which contradicts the GitHub.com page's "marked as failed". A deleted run has no conclusion to read, so on GHES the expected verdict is `UNKNOWN` or `NO_CI_CONFIGURED`, not `NEVER_RAN_EXPIRED`. This tool has been tested against GitHub.com only.
- **A commit with an unusual number of workflows is refused, not guessed.** The tool inspects at most 60 runs per commit. Beyond that it raises rather than returning a partial view, because a partial view could omit the one expired run that matters.
- **Any evidence it could not gather becomes `UNKNOWN`, not a pass.** If the jobs endpoint fails for one run, if the workflow runs endpoint returns something unexpected, or if the commit list cannot be read, the verdict degrades and says why. The tool is built to prefer "I do not know" over a clean answer it has not earned, and five of its tests exist only to hold that line.
- **Not adopted by anyone.** This is a new tool with no users, no dependents and no endorsement. Version 0.1.0.

## What is documented and what is observed

Keeping these apart is the point of the tool, so the tool's own claims are kept apart too.

| Claim | Status | Source |
| --- | --- | --- |
| Unapproved fork runs expire after 30 days and are marked as failed | **Documented** | ["awaiting approval for more than 30 days expire"](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/approve-runs-from-forks) |
| First time and external contributors need approval; the options are "Require approval for first-time contributors who are new to GitHub", "Require approval for first-time contributors", "Require approval for all external contributors" | **Documented** | [Managing GitHub Actions settings for a repository](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/managing-github-actions-settings-for-a-repository) |
| First time contributors require approval by default | **Documented** | [Disabling or limiting GitHub Actions for your organization](https://docs.github.com/en/organizations/managing-organization-settings/disabling-or-limiting-github-actions-for-your-organization) |
| A contributor with any prior merged commit does not need approval under the first two settings | **Documented** | same page |
| `action_required` is a valid `status` filter value | **Documented** | [REST: workflow runs](https://docs.github.com/en/rest/actions/workflow-runs) |
| The rollup is scoped to one commit | **Documented** | [GraphQL reference: commits](https://docs.github.com/en/graphql/reference/commits) |
| An expired run receives `status: completed`, `conclusion: failure` | **Observed, not documented.** The REST reference gives no description or enumeration for run level `conclusion`, and no `expired` value exists anywhere. | garak runs 33132180993 and four others, read 2026-11-01 |
| An expired run reports zero jobs, so it attaches no check run | **Observed, not documented** | jobs endpoint `total_count: 0`, read 2026-11-01 |
| A pending run reports `status: completed` with `conclusion: action_required` | **Observed, and contrary to what the documented `status` enumeration implies** | `?status=action_required`, read 2026-11-01 |
| `head_sha` requires the full 40 characters | **Observed. Not documented either way**; the parameter says only "Only returns workflow runs that are associated with the specified head_sha." | 7 character prefix returned `total_count: 0`, 2026-11-01 |
| GHES deletes expired runs rather than failing them | **Documented, and contradicts the GitHub.com page** | [GHES 3.19](https://docs.github.com/en/enterprise-server@3.19/actions/how-tos/manage-workflow-runs/approve-runs-from-forks) |

## Tests

167 tests, all offline. Every test runs against a cassette recorded from the live API or a clearly labelled synthetic one, and a fixture fails the suite if any test opens a socket.

```
pip install -e ".[test]"
python -m pytest
```

The four `tests/fixtures/garak_*.json` cassettes hold payloads recorded from NVIDIA/garak on 2026-11-01, trimmed to the fields the classifier reads and transcoded to ASCII, with no values altered. The five `synth_*.json` cassettes are invented and say so in their `provenance` field; a test asserts that they do.

## Contributing

Issues and pull requests welcome. A bug report is most useful with the `gh api` output that produced the wrong verdict, so it can become a cassette.

## Author

Manu Nicholas Jacob, Independent Researcher.
ORCID [0009-0007-6589-6572](https://orcid.org/0009-0007-6589-6572).
GitHub [@manunicholasjacob](https://github.com/manunicholasjacob).

## License

MIT. See [LICENSE](LICENSE).
