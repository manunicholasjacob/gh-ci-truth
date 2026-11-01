"""Tests for the GitHub Action entry point and the client layer."""

from __future__ import annotations

import json
import os

import pytest

from conftest import cassette_path, load_cassette
from gh_ci_truth import GitHubError, RecordedClient
from gh_ci_truth.client import GhCliClient, TokenClient, default_client

#: "cafe latte" with acute accents, spelled as code points to keep this
#: file ASCII. Decoding it wrongly is the bug the regression test guards.
_ACCENTED = "caf" + chr(0xE9) + " latt" + chr(0xE9)


@pytest.fixture
def action_env(monkeypatch, tmp_path):
    """A plausible GitHub Actions environment with no network behind it."""
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": 2031}}), encoding="ascii")
    output = tmp_path / "output.txt"
    summary = tmp_path / "summary.md"
    output.write_text("", encoding="ascii")
    summary.write_text("", encoding="ascii")
    for name in list(os.environ):
        if name.startswith("INPUT_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY", "NVIDIA/garak")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("GITHUB_TOKEN", "not-a-real-token")
    return {"output": output, "summary": summary, "event": event}


def _patch_client(monkeypatch, key):
    recorded = RecordedClient.from_file(cassette_path(key))
    monkeypatch.setattr(
        "gh_ci_truth.action.TokenClient", lambda **kwargs: recorded
    )
    return recorded


def _parsed_outputs(path):
    """Parse the GITHUB_OUTPUT file, including heredoc style values."""
    values = {}
    lines = path.read_text(encoding="utf-8").splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if "<<" in line:
            key, _, delimiter = line.partition("<<")
            body = []
            index += 1
            while index < len(lines) and lines[index] != delimiter:
                body.append(lines[index])
                index += 1
            values[key] = "\n".join(body)
        elif "=" in line:
            key, _, value = line.partition("=")
            values[key] = value
        index += 1
    return values


def test_the_action_classifies_the_event_pull_request(action_env, monkeypatch, capsys):
    _patch_client(monkeypatch, "expired_2031")
    from gh_ci_truth.action import main

    assert main() == 0
    outputs = _parsed_outputs(action_env["output"])
    assert outputs["category"] == "NEVER_RAN_EXPIRED"
    assert outputs["tested"] == "false"
    assert outputs["never-actually-tested"] == "true"
    assert outputs["misleading"] == "true"
    assert outputs["should-label"] == "true"
    assert outputs["jobs-executed"] == "0"
    assert outputs["label"] == "ci-never-ran"


def test_the_action_writes_a_comment_body_and_a_job_summary(action_env, monkeypatch, capsys):
    _patch_client(monkeypatch, "expired_2031")
    from gh_ci_truth.action import main

    main()
    outputs = _parsed_outputs(action_env["output"])
    assert "### gh-ci-truth: `NEVER_RAN_EXPIRED`" in outputs["comment-body"]
    assert "gh-ci-truth" in action_env["summary"].read_text(encoding="utf-8")


def test_the_action_emits_a_json_output_that_parses(action_env, monkeypatch, capsys):
    _patch_client(monkeypatch, "expired_2031")
    from gh_ci_truth.action import main

    main()
    payload = json.loads(_parsed_outputs(action_env["output"])["json"])
    assert payload["category"] == "NEVER_RAN_EXPIRED"


def test_the_action_warns_in_the_log_for_an_untested_pull_request(
    action_env, monkeypatch, capsys
):
    _patch_client(monkeypatch, "expired_2031")
    from gh_ci_truth.action import main

    main()
    assert "::warning title=gh-ci-truth::" in capsys.readouterr().out


def test_the_action_does_not_label_a_passing_pull_request(action_env, monkeypatch, capsys):
    _patch_client(monkeypatch, "passing_2358")
    monkeypatch.setenv("INPUT_PR_NUMBER", "2358")
    from gh_ci_truth.action import main

    assert main() == 0
    outputs = _parsed_outputs(action_env["output"])
    assert outputs["category"] == "TESTED_PASSING"
    assert outputs["should-label"] == "false"
    assert outputs["misleading"] == "false"


def test_the_action_can_be_asked_to_fail_the_job(action_env, monkeypatch, capsys):
    _patch_client(monkeypatch, "expired_2031")
    monkeypatch.setenv("INPUT_FAIL_ON_UNTESTED", "true")
    from gh_ci_truth.action import main

    assert main() == 1
    assert "::error title=gh-ci-truth::" in capsys.readouterr().out


def test_the_action_does_not_fail_the_job_by_default(action_env, monkeypatch, capsys):
    _patch_client(monkeypatch, "expired_2031")
    from gh_ci_truth.action import main

    assert main() == 0


def test_the_action_honours_a_custom_label(action_env, monkeypatch, capsys):
    _patch_client(monkeypatch, "expired_2031")
    monkeypatch.setenv("INPUT_LABEL", "needs-ci-approval")
    from gh_ci_truth.action import main

    main()
    assert _parsed_outputs(action_env["output"])["label"] == "needs-ci-approval"


def test_the_action_fails_without_a_pull_request_number(action_env, monkeypatch, capsys):
    _patch_client(monkeypatch, "expired_2031")
    action_env["event"].write_text("{}", encoding="ascii")
    from gh_ci_truth.action import main

    assert main() == 1
    assert "no pull request number" in capsys.readouterr().out


def test_the_action_fails_without_a_repository(action_env, monkeypatch, capsys):
    _patch_client(monkeypatch, "expired_2031")
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    from gh_ci_truth.action import main

    assert main() == 1
    assert "no repository" in capsys.readouterr().out


def test_the_action_fails_without_a_token(action_env, monkeypatch, capsys):
    _patch_client(monkeypatch, "expired_2031")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    from gh_ci_truth.action import main

    assert main() == 1
    assert "no token" in capsys.readouterr().out


def test_the_action_surfaces_an_api_failure_as_an_error(action_env, monkeypatch, capsys):
    monkeypatch.setattr(
        "gh_ci_truth.action.TokenClient",
        lambda **kwargs: RecordedClient({}, strict=True),
    )
    from gh_ci_truth.action import main

    assert main() == 1
    assert "::error title=gh-ci-truth::" in capsys.readouterr().out


# --------------------------------------------------------------------------
# Client layer
# --------------------------------------------------------------------------


def test_the_cli_client_reports_a_missing_gh_binary(monkeypatch):
    client = GhCliClient(gh_path="a-binary-that-does-not-exist")

    def boom(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr("subprocess.run", boom)
    with pytest.raises(GitHubError) as excinfo:
        client.get("repos/o/r/pulls/1")
    assert "gh CLI was not found" in str(excinfo.value)


def test_the_cli_client_surfaces_a_non_zero_exit(monkeypatch):
    class Completed:
        returncode = 1
        stdout = ""
        stderr = "gh: Not Found (HTTP 404)"

    monkeypatch.setattr("subprocess.run", lambda *a, **k: Completed())
    with pytest.raises(GitHubError) as excinfo:
        GhCliClient().get("repos/o/r/pulls/1")
    assert "404" in str(excinfo.value)


def test_the_cli_client_only_ever_issues_a_get(monkeypatch):
    seen = {}

    class Completed:
        returncode = 0
        stdout = "{}"
        stderr = ""

    def record(command, **kwargs):
        seen["command"] = command
        return Completed()

    monkeypatch.setattr("subprocess.run", record)
    GhCliClient(gh_path="gh").get("repos/o/r/pulls/1")
    assert seen["command"][1:4] == ["api", "-X", "GET"]


def test_the_cli_client_decodes_as_utf8_not_as_the_locale(monkeypatch):
    """Regression guard.

    With ``text=True`` and no explicit encoding, Python decodes a
    subprocess's output using the locale encoding, which is cp1252 on a
    Windows console. A pull request title containing an accented
    character then raised UnicodeDecodeError inside subprocess's reader
    thread, stdout came back as None, and ``--all-open`` reported "no
    open pull requests" for a repository that had plenty. An unreadable
    response must be an error, never an empty result.
    """
    captured = {}

    class Completed:
        returncode = 0
        # The accented title is built from code points so that this
        # source file stays pure ASCII.
        stdout = '[{"number": 1, "title": "' + _ACCENTED + '"}]'
        stderr = ""

    def record(command, **kwargs):
        captured.update(kwargs)
        return Completed()

    monkeypatch.setattr("subprocess.run", record)
    payload = GhCliClient().get("repos/o/r/pulls")
    assert payload == [{"number": 1, "title": _ACCENTED}]
    assert captured["encoding"] == "utf-8"
    assert "text" not in captured


def test_the_cli_client_treats_unreadable_output_as_an_error(monkeypatch):
    class Completed:
        returncode = 0
        stdout = None
        stderr = ""

    monkeypatch.setattr("subprocess.run", lambda *a, **k: Completed())
    with pytest.raises(GitHubError) as excinfo:
        GhCliClient().get("repos/o/r/pulls")
    assert "not an empty result" in str(excinfo.value)


def test_the_cli_client_rejects_non_json(monkeypatch):
    class Completed:
        returncode = 0
        stdout = "not json at all"
        stderr = ""

    monkeypatch.setattr("subprocess.run", lambda *a, **k: Completed())
    with pytest.raises(GitHubError):
        GhCliClient().get("repos/o/r/pulls/1")


def test_the_token_client_builds_the_expected_request(monkeypatch):
    client = TokenClient(token="t", api_root="https://api.github.example/")
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"ok": true}'

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["method"] = request.get_method()
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assert client.get("repos/o/r/pulls/1") == {"ok": True}
    assert captured["url"] == "https://api.github.example/repos/o/r/pulls/1"
    assert captured["method"] == "GET"
    assert captured["headers"]["Authorization"] == "Bearer t"
    assert captured["headers"]["X-github-api-version"] == "2022-11-28"


def test_default_client_prefers_gh_when_present(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/gh")
    assert isinstance(default_client(), GhCliClient)


def test_default_client_falls_back_to_a_token(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    assert isinstance(default_client(), TokenClient)


def test_default_client_refuses_when_there_is_no_way_in(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(GitHubError):
        default_client()


def test_a_cassette_can_be_loaded_from_disk():
    client = RecordedClient.from_file(cassette_path("expired_2031"))
    payload = client.get("repos/NVIDIA/garak/pulls/2031")
    assert payload["number"] == 2031
    assert len(payload["head"]["sha"]) == 40


def test_the_cassette_file_and_the_loader_agree():
    document = load_cassette("expired_2031")
    client = RecordedClient.from_file(cassette_path("expired_2031"))
    assert set(client.responses) == set(document["responses"])


# --------------------------------------------------------------------------
# Regression tests for bugs found in review
# --------------------------------------------------------------------------


def test_an_unexpected_runs_payload_is_not_read_as_zero_runs():
    """Regression guard.

    An error object, or None from an empty body, was read as "this commit
    has no workflow runs", which classified as NO_CI_CONFIGURED at high
    confidence with misleading False and a clean --strict exit. Identical
    output to a genuinely empty run list.
    """
    from gh_ci_truth import analyze_pull_request

    base = RecordedClient.from_file(cassette_path("expired_2031")).responses
    sha = base["repos/NVIDIA/garak/pulls/2031"]["head"]["sha"]
    runs_key = "repos/NVIDIA/garak/actions/runs?head_sha=" + sha
    for broken in (None, "", {"message": "Bad credentials"}, []):
        responses = dict(base)
        responses[runs_key] = broken
        with pytest.raises(GitHubError) as excinfo:
            analyze_pull_request(
                RecordedClient(responses), "NVIDIA/garak", 2031, deep=False
            )
        assert "workflow_runs" in str(excinfo.value)


def test_a_truncated_run_list_refuses_to_give_a_verdict():
    """A partial view could hide the one expired run that matters."""
    from gh_ci_truth import analyze_pull_request

    base = RecordedClient.from_file(cassette_path("expired_2031")).responses
    sha = base["repos/NVIDIA/garak/pulls/2031"]["head"]["sha"]
    runs_key = "repos/NVIDIA/garak/actions/runs?head_sha=" + sha
    responses = dict(base)
    responses[runs_key] = dict(responses[runs_key])
    responses[runs_key]["total_count"] = 999
    with pytest.raises(GitHubError) as excinfo:
        analyze_pull_request(
            RecordedClient(responses), "NVIDIA/garak", 2031, deep=False
        )
    assert "partial view" in str(excinfo.value)


def test_an_unreadable_checks_endpoint_sets_the_unavailable_flag():
    from gh_ci_truth import analyze_pull_request

    base = RecordedClient.from_file(cassette_path("expired_2031")).responses
    responses = {
        key: value for key, value in base.items() if "/check-runs" not in key
    }
    verdict = analyze_pull_request(
        RecordedClient(responses, strict=False), "NVIDIA/garak", 2031, deep=False
    )
    assert verdict.evidence.check_runs_unavailable is True
    assert "could not be read" in verdict.rollup_summary
    assert verdict.never_actually_tested is True


def test_the_runs_request_asks_for_a_full_page():
    """The endpoint defaults to 30 per page, which can hide a run."""
    from gh_ci_truth import analyze_pull_request

    client = RecordedClient.from_file(cassette_path("expired_2031"))
    analyze_pull_request(client, "NVIDIA/garak", 2031, deep=False)
    runs_requests = [p for p in client.requested if "head_sha=" in p]
    assert runs_requests
    for path in runs_requests:
        assert "per_page=100" in path


# --------------------------------------------------------------------------
# The pagination loop, which no cassette exercises
# --------------------------------------------------------------------------


class _FakePager(GhCliClient):
    """Drives the pagination helper over a scripted set of pages."""

    def __init__(self, pages):
        super().__init__(gh_path="unused")
        self.pages = list(pages)
        self.paths = []

    def get(self, path):
        self.paths.append(path)
        return self.pages.pop(0) if self.pages else []


def test_the_pagination_loop_follows_pages_and_stops_on_a_short_one():
    first = [{"number": n} for n in range(100)]
    second = [{"number": 100}, {"number": 101}]
    pager = _FakePager([first, second])
    items = pager.paginate("repos/o/r/pulls?state=open", "", limit=300)
    assert len(items) == 102
    assert "page=1" in pager.paths[0]
    assert "page=2" in pager.paths[1]
    assert len(pager.paths) == 2


def test_the_pagination_loop_sizes_the_page_to_the_limit():
    pager = _FakePager([[{"number": 1}]])
    pager.paginate("repos/o/r/pulls", "", limit=3)
    assert "per_page=3" in pager.paths[0]


def test_the_pagination_loop_honours_the_limit():
    pager = _FakePager([[{"number": n} for n in range(100)]] * 3)
    items = pager.paginate("repos/o/r/pulls", "", limit=150)
    assert len(items) == 150


def test_the_pagination_loop_extracts_a_named_key_from_a_dict_page():
    pager = _FakePager([{"workflow_runs": [{"id": 1}]}])
    assert pager.paginate("repos/o/r/actions/runs", "workflow_runs", limit=10) == [
        {"id": 1}
    ]


def test_the_pagination_loop_refuses_an_unexpected_object():
    """An error object must not be read as an empty collection."""
    pager = _FakePager([{"message": "Not Found"}])
    with pytest.raises(GitHubError) as excinfo:
        pager.paginate("repos/o/r/pulls", "", limit=10)
    assert "expected a list" in str(excinfo.value)


def test_the_pagination_loop_refuses_to_truncate_silently():
    pager = _FakePager([[{"number": n} for n in range(100)]] * 25)
    with pytest.raises(GitHubError) as excinfo:
        pager.paginate("repos/o/r/pulls", "", limit=5000)
    assert "truncated" in str(excinfo.value)


# --------------------------------------------------------------------------
# Step output safety
# --------------------------------------------------------------------------


def test_the_step_output_delimiter_is_unguessable_and_unique(
    action_env, monkeypatch, capsys
):
    """A fixed delimiter would let a fork inject arbitrary step outputs.

    The reason, comment-body and json outputs embed workflow run names,
    which on a fork pull request come from the contributor's own workflow
    file.
    """
    _patch_client(monkeypatch, "expired_2031")
    from gh_ci_truth.action import main

    main()
    first = action_env["output"].read_text(encoding="utf-8")
    assert "ghcitruth_eof_7f3a1b" not in first
    delimiters = {
        line.split("<<", 1)[1] for line in first.splitlines() if "<<" in line
    }
    assert delimiters
    for delimiter in delimiters:
        assert len(delimiter) > 20

    action_env["output"].write_text("", encoding="ascii")
    main()
    second = {
        line.split("<<", 1)[1]
        for line in action_env["output"].read_text(encoding="utf-8").splitlines()
        if "<<" in line
    }
    assert delimiters.isdisjoint(second)


def test_a_value_containing_the_delimiter_is_withheld(tmp_path, monkeypatch):
    """Belt and braces around an astronomically unlikely collision."""
    import gh_ci_truth.action as action_module

    output = tmp_path / "out.txt"
    output.write_text("", encoding="ascii")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setattr(
        action_module.uuid, "uuid4", lambda: type("U", (), {"hex": "f" * 32})()
    )
    marker = "ghcitruth_" + "f" * 32
    action_module._write_outputs({"evil": "a\n" + marker + "\nb"})
    written = output.read_text(encoding="utf-8")
    # The value is replaced, so the injected delimiter never reaches the
    # file and the replacement is single-line, needing no heredoc at all.
    assert "value withheld" in written
    assert marker not in written
    assert written.strip().startswith("evil=")
    assert "<<" not in written


def test_carriage_returns_are_normalised_in_step_outputs(tmp_path, monkeypatch):
    import gh_ci_truth.action as action_module

    output = tmp_path / "out.txt"
    output.write_text("", encoding="ascii")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    action_module._write_outputs({"k": "a\r\nb"})
    assert "\r" not in output.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Cassette reading
# --------------------------------------------------------------------------


def test_a_cassette_with_non_ascii_content_can_be_read(tmp_path):
    """A user's own recording will contain whatever GitHub returned."""
    cassette = tmp_path / "c.json"
    payload = {
        "responses": {"repos/o/r/pulls/1": {"number": 1, "title": _ACCENTED}}
    }
    cassette.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    client = RecordedClient.from_file(str(cassette))
    assert client.get("repos/o/r/pulls/1")["title"] == _ACCENTED


def test_a_malformed_cassette_is_an_error_not_a_traceback(tmp_path, capsys):
    from gh_ci_truth.cli import main as cli_main

    cassette = tmp_path / "bad.json"
    cassette.write_text("{not json", encoding="ascii")
    code = cli_main(["1", "--repo", "o/r", "--cassette", str(cassette)])
    assert code == 3
    assert "gh-ci-truth" in capsys.readouterr().err


def test_an_empty_gh_body_is_an_error_not_an_empty_result(monkeypatch):
    class Completed:
        returncode = 0
        stdout = "   "
        stderr = ""

    monkeypatch.setattr("subprocess.run", lambda *a, **k: Completed())
    with pytest.raises(GitHubError) as excinfo:
        GhCliClient().get("repos/o/r/pulls/1")
    assert "not an empty result" in str(excinfo.value)
