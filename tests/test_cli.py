"""CLI and reporting tests. The cassette flag keeps them offline."""

from __future__ import annotations

import json

import pytest

from conftest import cassette_path, client_for
from gh_ci_truth import Category, analyze_pull_request, exit_code
from gh_ci_truth.cli import build_parser, main, resolve_repo
from gh_ci_truth.client import GitHubError
from gh_ci_truth.report import render_json, render_markdown, render_text

#: Characters the project forbids in any output, spelled as code points so
#: that this source file stays pure ASCII itself.
_EM_DASH = chr(0x2014)
_FORBIDDEN_CHARS = (
    chr(0x2014),  # em dash
    chr(0x2013),  # en dash
    chr(0x2018),  # left single quotation mark
    chr(0x2019),  # right single quotation mark
    chr(0x201C),  # left double quotation mark
    chr(0x201D),  # right double quotation mark
    chr(0x00A0),  # no-break space
)


def run_cli(args, capsys):
    code = main(args)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# --------------------------------------------------------------------------
# Invocation shapes
# --------------------------------------------------------------------------


def test_a_single_pull_request(capsys):
    code, out, _ = run_cli(
        ["2031", "--repo", "NVIDIA/garak", "--cassette", cassette_path("expired_2031")],
        capsys,
    )
    assert code == 0
    assert "NEVER_RAN_EXPIRED" in out
    assert "NVIDIA/garak#2031" in out


def test_several_pull_requests_in_one_invocation(capsys):
    """Two numbers against a two-pull-request cassette."""
    code, out, _ = run_cli(
        [
            "11",
            "12",
            "--repo",
            "example-org/example-repo",
            "--cassette",
            cassette_path("all_open"),
        ],
        capsys,
    )
    assert code == 0
    assert "#11" in out
    assert "#12" in out
    assert "2 pull request(s) examined" in out


def test_a_pull_request_that_could_not_be_examined_exits_three(capsys):
    """A partial sweep must not look like a clean one.

    Reporting success after failing to examine a pull request would let a
    --strict gate pass over pull requests nobody looked at, which is the
    same silence this tool exists to break.
    """
    code, out, err = run_cli(
        [
            "2031",
            "2032",
            "--repo",
            "NVIDIA/garak",
            "--cassette",
            cassette_path("expired_2031"),
        ],
        capsys,
    )
    assert "NEVER_RAN_EXPIRED" in out
    assert "#2032" in err
    assert "could not be examined" in err
    assert code == 3


def test_all_open_examines_every_open_pull_request(capsys):
    code, out, _ = run_cli(
        [
            "--repo",
            "example-org/example-repo",
            "--all-open",
            "--cassette",
            cassette_path("all_open"),
        ],
        capsys,
    )
    assert code == 0
    assert "TESTED_FAILING" in out
    assert "NEVER_RAN_EXPIRED" in out
    assert "2 pull request(s) examined" in out


def test_json_output_is_parseable_and_carries_a_schema(capsys):
    code, out, _ = run_cli(
        [
            "2031",
            "--repo",
            "NVIDIA/garak",
            "--json",
            "--cassette",
            cassette_path("expired_2031"),
        ],
        capsys,
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["schema"] == "gh-ci-truth/v1"
    assert payload["summary"]["never_actually_tested"] == 1
    assert payload["summary"]["misleading"] == 1
    assert payload["summary"]["by_category"]["NEVER_RAN_EXPIRED"] == 1
    assert payload["pull_requests"][0]["category"] == "NEVER_RAN_EXPIRED"


def test_json_summary_counts_every_category_key(capsys):
    _, out, _ = run_cli(
        [
            "--repo",
            "example-org/example-repo",
            "--all-open",
            "--json",
            "--cassette",
            cassette_path("all_open"),
        ],
        capsys,
    )
    by_category = json.loads(out)["summary"]["by_category"]
    assert set(by_category) == {category.value for category in Category}


def test_verbose_lists_each_run_and_check(capsys):
    _, out, _ = run_cli(
        [
            "2031",
            "--repo",
            "NVIDIA/garak",
            "--verbose",
            "--cassette",
            cassette_path("expired_2031"),
        ],
        capsys,
    )
    assert out.count("run 331") == 5
    assert "check DCO" in out
    assert "lifetime=30." in out


def test_the_warning_line_appears_for_a_misleading_pull_request(capsys):
    _, out, _ = run_cli(
        ["2031", "--repo", "NVIDIA/garak", "--cassette", cassette_path("expired_2031")],
        capsys,
    )
    assert "WARNING" in out
    assert "reads as clean" in out


def test_no_warning_line_for_a_passing_pull_request(capsys):
    _, out, _ = run_cli(
        ["2358", "--repo", "NVIDIA/garak", "--cassette", cassette_path("passing_2358")],
        capsys,
    )
    assert "TESTED_PASSING" in out
    assert "WARNING" not in out


# --------------------------------------------------------------------------
# Exit status
# --------------------------------------------------------------------------


def test_without_strict_an_untested_pull_request_still_exits_zero(capsys):
    code, _, _ = run_cli(
        ["2031", "--repo", "NVIDIA/garak", "--cassette", cassette_path("expired_2031")],
        capsys,
    )
    assert code == 0


def test_strict_exits_one_for_an_untested_pull_request(capsys):
    code, _, _ = run_cli(
        [
            "2031",
            "--repo",
            "NVIDIA/garak",
            "--strict",
            "--cassette",
            cassette_path("expired_2031"),
        ],
        capsys,
    )
    assert code == 1


def test_strict_exits_zero_for_a_passing_pull_request(capsys):
    code, _, _ = run_cli(
        [
            "2358",
            "--repo",
            "NVIDIA/garak",
            "--strict",
            "--cassette",
            cassette_path("passing_2358"),
        ],
        capsys,
    )
    assert code == 0


def test_strict_exits_zero_for_a_genuine_failure_by_default(capsys):
    """A real failure is already reported correctly by gh pr checks."""
    code, _, _ = run_cli(
        [
            "11",
            "--repo",
            "example-org/example-repo",
            "--strict",
            "--cassette",
            cassette_path("failing"),
        ],
        capsys,
    )
    assert code == 0


def test_the_opt_in_flag_makes_a_genuine_failure_strict(capsys):
    code, _, _ = run_cli(
        [
            "11",
            "--repo",
            "example-org/example-repo",
            "--strict",
            "--fail-on-tested-failing",
            "--cassette",
            cassette_path("failing"),
        ],
        capsys,
    )
    assert code == 1


def test_strict_exits_two_for_an_undetermined_pull_request(capsys):
    code, _, _ = run_cli(
        [
            "14",
            "--repo",
            "example-org/example-repo",
            "--strict",
            "--cassette",
            cassette_path("unknown"),
        ],
        capsys,
    )
    assert code == 2


def test_an_untested_pull_request_outscores_an_undetermined_one():
    untested = analyze_pull_request(client_for("expired_2031"), "NVIDIA/garak", 2031)
    unknown = analyze_pull_request(client_for("unknown"), "example-org/example-repo", 14)
    assert exit_code([unknown, untested], strict=True) == 1


def test_an_operational_failure_exits_three(capsys):
    code, _, err = run_cli(
        ["999", "--repo", "NVIDIA/garak", "--cassette", cassette_path("expired_2031")],
        capsys,
    )
    assert code == 3
    assert "gh-ci-truth" in err


def test_a_bad_repository_spelling_exits_three(capsys):
    code, _, err = run_cli(
        ["1", "--repo", "not-a-repo", "--cassette", cassette_path("expired_2031")],
        capsys,
    )
    assert code == 3
    assert "OWNER/REPO" in err


# --------------------------------------------------------------------------
# Argument handling
# --------------------------------------------------------------------------


def test_no_arguments_is_an_error(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--repo", "o/r"])
    assert excinfo.value.code == 2


def test_all_open_cannot_be_combined_with_numbers(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["1", "--all-open", "--repo", "o/r"])
    assert excinfo.value.code == 2


def test_version_is_reported(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert "gh-ci-truth" in capsys.readouterr().out


def test_the_parser_accepts_the_documented_flags():
    parser = build_parser()
    args = parser.parse_args(
        [
            "1",
            "2",
            "--repo",
            "o/r",
            "--json",
            "--strict",
            "--verbose",
            "--shallow",
            "--expiry-gap-days",
            "20",
        ]
    )
    assert args.numbers == [1, 2]
    assert args.as_json and args.strict and args.verbose and args.shallow
    assert args.expiry_gap_days == 20.0


def test_the_short_repo_flag_matches_the_gh_convention():
    assert build_parser().parse_args(["-R", "o/r", "1"]).repo == "o/r"


def test_the_repository_comes_from_the_environment_when_not_given(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/from-env")
    monkeypatch.delenv("GH_REPO", raising=False)
    assert resolve_repo(None) == "owner/from-env"


def test_gh_repo_wins_over_the_actions_variable(monkeypatch):
    monkeypatch.setenv("GH_REPO", "owner/preferred")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/ignored")
    assert resolve_repo(None) == "owner/preferred"


def test_an_explicit_repository_wins_over_everything(monkeypatch):
    monkeypatch.setenv("GH_REPO", "owner/ignored")
    assert resolve_repo("owner/explicit") == "owner/explicit"


def test_a_malformed_repository_is_rejected():
    with pytest.raises(GitHubError):
        resolve_repo("https://github.com/owner/name")


def test_the_gap_threshold_flag_changes_the_verdict(capsys):
    """Raise the threshold past 30 days and the expiry stops matching."""
    code, out, _ = run_cli(
        [
            "2031",
            "--repo",
            "NVIDIA/garak",
            "--expiry-gap-days",
            "60",
            "--cassette",
            cassette_path("expired_2031"),
        ],
        capsys,
    )
    assert code == 0
    assert "NEVER_RAN_EXPIRED" not in out
    assert "UNKNOWN" in out


# --------------------------------------------------------------------------
# Renderers
# --------------------------------------------------------------------------


def test_the_text_report_names_the_job_count():
    verdict = analyze_pull_request(client_for("expired_2031"), "NVIDIA/garak", 2031)
    text = render_text([verdict])
    assert "jobs run    0" in text
    assert "rollup says 1 check run(s) attached: 1 success" in text


def test_the_markdown_comment_is_fit_for_a_pull_request():
    verdict = analyze_pull_request(client_for("expired_2031"), "NVIDIA/garak", 2031)
    body = render_markdown(verdict)
    assert body.startswith("### gh-ci-truth: `NEVER_RAN_EXPIRED`")
    assert "Jobs executed on this commit: **0**" in body
    assert "<details>" in body
    assert "reads as clean" in body


def test_the_markdown_comment_omits_the_warning_when_not_misleading():
    verdict = analyze_pull_request(client_for("passing_2358"), "NVIDIA/garak", 2358)
    assert "reads as clean" not in render_markdown(verdict)


def test_the_renderers_emit_pure_ascii():
    for key, repo, number in [
        ("expired_2031", "NVIDIA/garak", 2031),
        ("passing_2358", "NVIDIA/garak", 2358),
        ("stale", "example-org/example-repo", 13),
    ]:
        verdict = analyze_pull_request(client_for(key), repo, number)
        for text in (
            render_text([verdict], verbose=True),
            render_json([verdict]),
            render_markdown(verdict),
        ):
            text.encode("ascii")
            assert _EM_DASH not in text


def test_no_output_contains_an_em_dash_or_a_smart_quote():
    verdict = analyze_pull_request(client_for("expired_2031"), "NVIDIA/garak", 2031)
    blob = render_text([verdict], verbose=True) + render_markdown(verdict)
    for forbidden in _FORBIDDEN_CHARS:
        assert forbidden not in blob


def test_every_category_has_a_headline():
    from gh_ci_truth.report import CATEGORY_HEADLINE

    assert set(CATEGORY_HEADLINE) == set(Category)
    for headline in CATEGORY_HEADLINE.values():
        # Headlines are fragments: the renderers append their own full stop.
        assert headline
        assert not headline.endswith(".")
        headline.encode("ascii")
