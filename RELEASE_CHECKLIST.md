# Release checklist for v0.1.0

Everything in this file is a manual step for Manu. None of it has been done.
Nothing in this repository has been pushed, published, tagged, posted or
registered. The working tree and its local git history are the only artifacts
that exist.

## What was already verified, so you do not have to redo it

Checked on 2026-11-01:

- `https://github.com/manunicholasjacob/gh-ci-truth` returns 404, so the
  repository name is free on your account.
- A GitHub repository search for `gh-ci-truth in:name` returns 0 results, so
  no project anywhere is using the name.
- `https://pypi.org/pypi/gh-ci-truth/json` returns 404, as do `ghcitruth` and
  `gh_ci_truth`, so the PyPI name is free.
- The test suite passes: 167 tests.
- The CLI works against the live API, read only, for four of the seven
  categories (`NEVER_RAN_EXPIRED`, `NEVER_RAN_AWAITING_APPROVAL`,
  `TESTED_PASSING`, and `TESTED_FAILING` via a cassette).

Re-check the two name claims if you are reading this more than a few weeks
later. Names get taken.

## 1. Local checks before anything leaves the machine

```
cd C:/Users/manun/OneDrive/Documents/Claudecode/gh-ci-truth

python -m pytest -v

# Pure ASCII. Must print nothing.
LC_ALL=C grep -rnP '[^\x00-\x7F]' --exclude-dir=.git .

# Build and validate the distribution.
python -m pip install --upgrade build twine
python -m build
python -m twine check dist/*
```

## 2. Decide the GitHub account detail

`pyproject.toml`, `CITATION.cff`, `README.md` and `action.yml` all assume the
owner is `manunicholasjacob`. If you publish under a different account or an
organization, change it in all four before tagging, because
`manunicholasjacob/gh-ci-truth@v0.1.0` is baked into the README's Action
example.

The author email in `pyproject.toml` is `manunicholasjacob@gmail.com` and
becomes public on PyPI. Swap it for a forwarding address first if you would
rather it did not.

## 3. Create the repository and push

```
# Confirm the name is still free.
gh api repos/manunicholasjacob/gh-ci-truth    # expect 404

gh repo create gh-ci-truth --public \
  --description "Tell whether a pull request's CI actually ran, instead of whether its checks panel looks clean." \
  --homepage "https://github.com/manunicholasjacob/gh-ci-truth"

git remote add origin https://github.com/manunicholasjacob/gh-ci-truth.git
git branch -M main
git push -u origin main
```

Then in the repository settings, add topics so the extension is findable:
`gh-extension`, `github-actions`, `continuous-integration`, `pull-requests`,
`cli`, `python`.

`gh-extension` is the topic the `gh extension search` index uses. Without it
the extension is effectively unlisted.

## 4. Confirm CI is green before tagging

Push first, let `.github/workflows/ci.yml` run, and look at it yourself. Note
that the `action` job only runs on a `pull_request` event, so it will not run
on the initial push to `main`. To exercise it, open a throwaway pull request
against your own repository.

Two things to watch:

- The matrix includes Python 3.9. If anything in the code has drifted to
  newer syntax, 3.9 is where it shows.
- The `ascii` job is the enforcement of the no-em-dash and ASCII-only rule.
  It should pass; it is there to stop a later commit breaking it.

Do not tag on a red CI.

## 5. Tag and release

The Action is consumed as `manunicholasjacob/gh-ci-truth@v0.1.0`, so the tag
must exist and must point at a commit where `action.yml` is at the repository
root.

```
git tag -a v0.1.0 -m "gh-ci-truth 0.1.0"
git push origin v0.1.0

gh release create v0.1.0 \
  --title "gh-ci-truth 0.1.0" \
  --notes-file CHANGELOG.md
```

Also move the floating major tag, which is the form most workflows pin to:

```
git tag -f v0.1
git push -f origin v0.1
```

Consider a `v0` tag too, and remember to move both on every future release or
consumers silently stay on 0.1.0.

## 6. Publish to PyPI

Use TestPyPI first. A bad first upload to PyPI cannot be replaced, only
yanked, and the version number is burned either way.

```
python -m twine upload --repository testpypi dist/*
python -m pip install --index-url https://test.pypi.org/simple/ \
  --no-deps gh-ci-truth
gh-ci-truth --version
```

Then the real thing:

```
python -m twine upload dist/*
```

Prefer a scoped API token over a password, and prefer Trusted Publishing from
the `ci.yml` build job over a long lived token if you intend to release more
than once.

## 7. Verify the two dependent paths actually work

This is the step that decides whether the project gets a "Used by" list at
all, and it is worth doing properly.

### The `gh` extension

```
gh extension install manunicholasjacob/gh-ci-truth
gh ci-truth 2031 --repo NVIDIA/garak
gh extension list
```

Check it on a machine where the repository is not already present. The shim
resolves the bundled library through `PYTHONPATH`, and that path is the one
thing a local checkout can hide a bug in.

### The Action

Add the workflow from the README to a repository of your own, open a pull
request, and confirm the step runs and emits a `category`. Until a real
workflow file in some repository names
`manunicholasjacob/gh-ci-truth@v0.1.0`, GitHub publishes no dependents list
for the Action.

### The library

Likewise, the PyPI "Used by" list needs another repository to name
`gh-ci-truth` in a manifest. Your own projects count.

## 8. Optional, and all of it is your call

- **GitHub Marketplace.** Listing the Action needs the Marketplace terms
  accepted on the account and a published release. It raises visibility; it
  also puts your name on a marketplace listing, which you may not want.
- **Zenodo.** Enabling the GitHub integration before creating the release
  mints a DOI for the tag, which fits the pattern of your other artifacts.
  `CITATION.cff` is already in place for it. Note that Zenodo picks up the
  release only if the integration is enabled first.
- **A note in your contribution ledger.** This tool was built from the garak
  2031 and 2032 finding, and the README's documented-versus-observed table is
  the kind of thing that is easier to cite later than to reconstruct.

## What must not be claimed

The README says the project has no users, no dependents and no endorsement,
because on the day it was written that was true. If you quote the project
anywhere, keep that straight. In particular:

- The garak pull requests are your own and the README says so. It does not
  characterize the repository's maintainers and should not start to. An
  approval queue going unattended for 30 days is ordinary.
- The 30 day expiry signature is documented in prose by GitHub but the
  specific API shape is an observation, and the README's table says which is
  which. Do not let that distinction get flattened in a post or an abstract.
- "167 tests passing" is accurate and verifiable. "Validated against live
  data for four of seven categories" is also accurate. Neither means the tool
  is in use anywhere.

## Things deliberately left undone

- No `py.typed` stub validation; the package ships `py.typed` but has not
  been run through mypy.
- No code coverage measurement or badge.
- `TESTED_PASSING` and `TESTED_FAILING` are the only categories with a
  second, independent recorded instance. `NO_CI_CONFIGURED`,
  `STALE_RESULTS_FOR_OLDER_COMMIT` and `UNKNOWN` are covered by synthetic
  cassettes only, because no public example was to hand. Replacing those with
  recorded ones would strengthen the suite.
- Not tested against GitHub Enterprise Server, where the documentation
  describes different behaviour. The README says so.
