# Contributing

Thank you for looking at gh-ci-truth. This is a small project maintained by one person, and issues
and pull requests are genuinely welcome.

## Reporting a problem

Open an issue. The templates ask for the one thing that makes a report diagnosable:

-   The output of `gh ci-truth --repo OWNER/REPO <number> --json`, with anything
        private redacted. The classification, its confidence and the evidence it
        rested on are all in there.

GitHub does not document several of the API shapes this tool depends on, so its
behaviour is pinned to observed responses. If you find a pull request it classifies
wrongly, the recorded API response for that commit is the most useful thing you can
attach, because it becomes a regression fixture.

## Pull requests

- Open an issue first for anything beyond a typo or an obvious fix, so the approach can be
  agreed before you spend time on it.
- Keep one change per pull request.
- Add a test. A change to behaviour without a test that would have caught the old behaviour
  will be asked for one.
- Run the suite before pushing:

      python -m pip install -e ".[dev]"
      python -m pytest

## Two house rules that the CI enforces

- **Pure ASCII.** Every tracked file must contain only printable ASCII and newlines, with no
  em dashes, en dashes, smart quotes or non-breaking spaces. The exceptions, where there are
  any, are pinned in `.gitattributes` and exist because a test needs those exact bytes.
- **LF line endings.** CRLF breaks the shell heredocs in `action.yml` on a Linux runner, and
  carriage returns fail the ASCII check. `.gitattributes` pins this, so you should not have to
  think about it, but do not override it.

## Sign your commits off

This project uses the Developer Certificate of Origin. Add a sign-off line to every commit:

      git commit -s -m "your message"

That appends `Signed-off-by: Your Name <your@email>`, which certifies that you wrote the
contribution or otherwise have the right to submit it under this project's licence. The full
text of the DCO is at https://developercertificate.org/.

## Licence

By contributing you agree that your contribution is licensed under the MIT Licence, the same
terms that cover the rest of the project.
