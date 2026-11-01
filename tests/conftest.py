"""Shared test fixtures.

Every test in this suite runs against a recorded cassette. Nothing here
touches the network, and a guard fixture fails the run if anything tries.
"""

from __future__ import annotations

import json
import os
import socket
from typing import Any, Dict

import pytest

from gh_ci_truth import RecordedClient

FIXTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

CASSETTES = {
    "expired_2031": "garak_2031_expired.json",
    "expired_2032": "garak_2032_expired.json",
    "passing_2358": "garak_2358_passing.json",
    "awaiting": "garak_awaiting_approval.json",
    "failing": "synth_tested_failing.json",
    "no_ci": "synth_no_ci_configured.json",
    "stale": "synth_stale_results.json",
    "unknown": "synth_unknown_bare_head.json",
    "all_open": "synth_all_open.json",
}


def cassette_path(key: str) -> str:
    return os.path.join(FIXTURE_DIR, CASSETTES[key])


def load_cassette(key: str) -> Dict[str, Any]:
    with open(cassette_path(key), "r", encoding="ascii") as handle:
        return json.load(handle)


def client_for(key: str) -> RecordedClient:
    return RecordedClient.from_file(cassette_path(key))


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly if any test opens a socket.

    The suite is meant to be reproducible offline and in CI without a
    token. A test that reaches the network has stopped being a test of
    the classifier.
    """

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("this test suite must not open a network connection")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


@pytest.fixture
def fixture_dir() -> str:
    return FIXTURE_DIR
