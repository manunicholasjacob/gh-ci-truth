"""End to end tests against cassettes recorded from the live GitHub API.

The four ``garak_*`` cassettes hold payloads recorded on 2026-11-01 from
the public repository NVIDIA/garak. Pull requests 2031 and 2032 are the
author's own. These tests are the evidence that the tool's central claim
holds against real data, not only against hand written shapes.
"""

from __future__ import annotations

import json

import pytest

from conftest import cassette_path, client_for, load_cassette
from gh_ci_truth import Category, analyze_pull_request, list_open_pull_requests


# --------------------------------------------------------------------------
# The verified instances
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key,number",
    [("expired_2031", 2031), ("expired_2032", 2032)],
)
def test_the_garak_pull_requests_were_never_tested(key, number):
    verdict = analyze_pull_request(client_for(key), "NVIDIA/garak", number)
    assert verdict.category is Category.NEVER_RAN_EXPIRED
    assert verdict.tested is False
    assert verdict.never_actually_tested is True
    assert verdict.evidence.executed_job_count == 0


@pytest.mark.parametrize("key,number", [("expired_2031", 2031), ("expired_2032", 2032)])
def test_the_garak_pull_requests_look_clean_while_being_untested(key, number):
    """The condition the tool exists to detect, on real recorded data."""
    verdict = analyze_pull_request(client_for(key), "NVIDIA/garak", number)
    assert verdict.misleading is True
    conclusions = {c.conclusion for c in verdict.evidence.check_runs_on_head}
    assert conclusions == {"success"}, conclusions


@pytest.mark.parametrize("key,number", [("expired_2031", 2031), ("expired_2032", 2032)])
def test_the_only_attached_check_run_is_a_passing_dco_check(key, number):
    verdict = analyze_pull_request(client_for(key), "NVIDIA/garak", number)
    checks = verdict.evidence.check_runs_on_head
    assert len(checks) == 1
    assert checks[0].name == "DCO"
    assert checks[0].conclusion == "success"


@pytest.mark.parametrize("key,number", [("expired_2031", 2031), ("expired_2032", 2032)])
def test_every_expired_run_reports_failure_with_zero_jobs(key, number):
    verdict = analyze_pull_request(client_for(key), "NVIDIA/garak", number)
    runs = verdict.evidence.runs_on_head
    assert len(runs) == 5
    for run in runs:
        assert run.status == "completed"
        assert run.conclusion == "failure"
        assert run.job_count == 0


@pytest.mark.parametrize("key,number", [("expired_2031", 2031), ("expired_2032", 2032)])
def test_every_expired_run_sat_for_thirty_days(key, number):
    verdict = analyze_pull_request(client_for(key), "NVIDIA/garak", number)
    for run in verdict.evidence.runs_on_head:
        gap = run.lifetime_days
        assert gap is not None
        assert 29.9 < gap < 30.1, (run.id, gap)


def test_the_two_expired_pull_requests_have_different_head_commits():
    first = analyze_pull_request(client_for("expired_2031"), "NVIDIA/garak", 2031)
    second = analyze_pull_request(client_for("expired_2032"), "NVIDIA/garak", 2032)
    assert first.evidence.head_sha != second.evidence.head_sha
    assert len(first.evidence.head_sha) == 40
    assert len(second.evidence.head_sha) == 40


def test_a_pull_request_awaiting_approval_is_not_reported_as_expired():
    verdict = analyze_pull_request(client_for("awaiting"), "NVIDIA/garak", 9001)
    assert verdict.category is Category.NEVER_RAN_AWAITING_APPROVAL
    assert verdict.evidence.executed_job_count == 0


def test_the_awaiting_cassette_carries_the_observed_field_placement():
    """Regression guard for the field the live API actually uses.

    A pending run reports ``status="completed"`` with
    ``conclusion="action_required"``. Reading only ``status`` would
    classify it as an ordinary completed run.
    """
    verdict = analyze_pull_request(client_for("awaiting"), "NVIDIA/garak", 9001)
    for run in verdict.evidence.runs_on_head:
        assert run.status == "completed"
        assert run.conclusion == "action_required"
        assert run.is_awaiting_approval is True


def test_a_tested_pull_request_is_reported_as_passing():
    verdict = analyze_pull_request(client_for("passing_2358"), "NVIDIA/garak", 2358)
    assert verdict.category is Category.TESTED_PASSING
    assert verdict.tested is True
    assert verdict.misleading is False
    assert verdict.evidence.executed_job_count > 0


def test_the_passing_pull_request_has_many_attached_check_runs():
    verdict = analyze_pull_request(client_for("passing_2358"), "NVIDIA/garak", 2358)
    assert len(verdict.evidence.check_runs_on_head) > 10


def test_passing_and_expired_differ_in_jobs_not_in_the_rollup_shape():
    """The discriminator is the job count, which the rollup never shows."""
    passing = analyze_pull_request(client_for("passing_2358"), "NVIDIA/garak", 2358)
    expired = analyze_pull_request(client_for("expired_2031"), "NVIDIA/garak", 2031)
    assert passing.evidence.executed_job_count > 0
    assert expired.evidence.executed_job_count == 0
    for check in list(passing.evidence.check_runs_on_head) + list(
        expired.evidence.check_runs_on_head
    ):
        assert check.conclusion == "success"


# --------------------------------------------------------------------------
# The remaining categories
# --------------------------------------------------------------------------


def test_a_failing_pull_request_is_reported_as_failing():
    verdict = analyze_pull_request(
        client_for("failing"), "example-org/example-repo", 11
    )
    assert verdict.category is Category.TESTED_FAILING
    assert verdict.tested is True
    assert verdict.never_actually_tested is False


def test_a_repository_without_workflows_is_reported_as_no_ci():
    verdict = analyze_pull_request(client_for("no_ci"), "example-org/example-repo", 12)
    assert verdict.category is Category.NO_CI_CONFIGURED
    assert verdict.never_actually_tested is False


def test_results_belonging_to_an_older_commit_are_reported_as_stale():
    verdict = analyze_pull_request(client_for("stale"), "example-org/example-repo", 13)
    assert verdict.category is Category.STALE_RESULTS_FOR_OLDER_COMMIT
    assert verdict.never_actually_tested is True
    assert verdict.evidence.executed_job_count == 0
    assert verdict.evidence.runs_on_older_commits


def test_a_bare_head_in_a_repository_with_ci_is_reported_as_unknown():
    verdict = analyze_pull_request(client_for("unknown"), "example-org/example-repo", 14)
    assert verdict.category is Category.UNKNOWN
    assert verdict.confidence == "medium"


def test_shallow_mode_cannot_tell_no_ci_from_unknown():
    verdict = analyze_pull_request(
        client_for("no_ci"), "example-org/example-repo", 12, deep=False
    )
    assert verdict.category is Category.UNKNOWN
    assert verdict.confidence == "low"


def test_listing_open_pull_requests():
    numbers = list_open_pull_requests(client_for("all_open"), "example-org/example-repo")
    assert numbers == [11, 12]


def test_a_mixed_repository_separates_failing_from_never_tested():
    client = client_for("all_open")
    repo = "example-org/example-repo"
    verdicts = [analyze_pull_request(client, repo, n) for n in [11, 12]]
    assert verdicts[0].category is Category.TESTED_FAILING
    assert verdicts[1].category is Category.NEVER_RAN_EXPIRED
    assert verdicts[1].misleading is True


# --------------------------------------------------------------------------
# Cassette hygiene
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "expired_2031",
        "expired_2032",
        "passing_2358",
        "awaiting",
        "failing",
        "no_ci",
        "stale",
        "unknown",
        "all_open",
    ],
)
def test_every_cassette_is_ascii_and_documents_its_provenance(key):
    with open(cassette_path(key), "rb") as handle:
        raw = handle.read()
    assert all(byte < 128 for byte in raw), key
    document = load_cassette(key)
    assert document["description"].strip()
    assert document["provenance"].strip()
    assert document["responses"]


@pytest.mark.parametrize("key", ["failing", "no_ci", "stale", "unknown", "all_open"])
def test_synthetic_cassettes_say_they_are_synthetic(key):
    assert load_cassette(key)["provenance"].startswith("Synthetic")


@pytest.mark.parametrize("key", ["expired_2031", "expired_2032", "passing_2358"])
def test_recorded_cassettes_say_where_they_came_from(key):
    provenance = load_cassette(key)["provenance"]
    assert "live GitHub REST API" in provenance
    assert "2026-11-01" in provenance
    assert "NVIDIA/garak" in provenance


def test_the_recorded_expiry_dates_are_the_ones_reported():
    """Guard the dated claim the README makes against the cassette."""
    document = load_cassette("expired_2031")
    runs = [
        value
        for key, value in document["responses"].items()
        if key.startswith("repos/NVIDIA/garak/actions/runs?head_sha=")
    ][0]["workflow_runs"]
    assert len(runs) == 5
    assert {run["created_at"] for run in runs} == {"2026-08-28T01:13:15Z"}
    assert {run["updated_at"] for run in runs} == {"2026-09-27T01:17:20Z"}


def test_an_unrecorded_path_raises_rather_than_returning_empty():
    from gh_ci_truth import GitHubError, RecordedClient

    client = RecordedClient({"repos/o/r/pulls/1": {"number": 1}})
    with pytest.raises(GitHubError):
        client.get("repos/o/r/pulls/2")


def test_a_lenient_recorded_client_returns_none_for_an_unrecorded_path():
    from gh_ci_truth import RecordedClient

    client = RecordedClient({}, strict=False)
    assert client.get("repos/o/r/pulls/2") is None


def test_the_recorded_client_ignores_pagination_parameters():
    from gh_ci_truth import RecordedClient

    client = RecordedClient({"repos/o/r/pulls": [{"number": 1}]})
    assert client.paginate("repos/o/r/pulls?per_page=100&page=1", "", limit=10) == [
        {"number": 1}
    ]


def test_an_abbreviated_sha_would_find_nothing_in_a_cassette():
    """Mirrors the live API: head_sha matches exactly, so a prefix misses.

    Verified against the live endpoint on 2026-11-01: a seven character
    prefix of a real head SHA returned total_count 0.
    """
    from gh_ci_truth import GitHubError, RecordedClient

    document = load_cassette("expired_2031")
    client = RecordedClient(document["responses"])
    with pytest.raises(GitHubError):
        client.get("repos/NVIDIA/garak/actions/runs?head_sha=4b3e337")


def test_the_verdict_round_trips_through_json():
    verdict = analyze_pull_request(client_for("expired_2031"), "NVIDIA/garak", 2031)
    payload = json.loads(json.dumps(verdict.to_dict()))
    assert payload["category"] == "NEVER_RAN_EXPIRED"
    assert payload["misleading"] is True
    assert payload["evidence"]["executed_job_count"] == 0
    assert len(payload["evidence"]["runs_on_head"]) == 5
