"""Tests for the board handler (HTTP request routing and board rendering)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest import mock
from urllib.request import urlopen

import pytest

if TYPE_CHECKING:
    pass

from tests.server.conftest import (
    _post_config_sync,
    _start_test_server,
)

# ---------------------------------------------------------------------------
# POST /config-sync tests
# ---------------------------------------------------------------------------


def test_config_sync_success_returns_200_json(single_db: str) -> None:
    import json as _json

    from robotsix_auto_mail.config.config_sync_agent import (
        ConfigSyncResult,
        DriftProposal,
    )

    fake_result = ConfigSyncResult(
        proposals=[
            DriftProposal(
                title="Default mismatch",
                body="The YAML default differs from the dataclass default.",
                affected_field="timeout",
                confidence="high",
            )
        ]
    )

    import urllib.request

    server, port = _start_test_server(single_db)
    try:
        with mock.patch(
            "robotsix_auto_mail.config.config_sync_agent.run_config_sync_agent",
            return_value=fake_result,
        ) as mocked:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/config-sync",
                data=b"",
                method="POST",
            )
            resp = urlopen(req)
            assert resp.status == 200
            assert resp.headers.get("Content-Type", "").startswith("application/json")
            payload = _json.loads(resp.read().decode("utf-8"))

        assert list(payload.keys()) == ["proposals"]
        assert len(payload["proposals"]) == 1
        proposal = payload["proposals"][0]
        assert proposal["title"] == "Default mismatch"
        assert proposal["affected_field"] == "timeout"
        assert proposal["confidence"] == "high"
        assert "body" in proposal

        # Verify the agent was invoked with a live DB connection so the
        # dedup ledger wiring is exercised.
        assert mocked.call_count == 1
        assert "conn" in mocked.call_args.kwargs
        assert mocked.call_args.kwargs["conn"] is not None
    finally:
        server.shutdown()


def test_config_sync_error_returns_503_json(single_db: str) -> None:
    import json as _json

    from robotsix_auto_mail.config.config_sync_agent import ConfigSyncError

    server, port = _start_test_server(single_db)
    try:
        with mock.patch(
            "robotsix_auto_mail.config.config_sync_agent.run_config_sync_agent",
            side_effect=ConfigSyncError("No LLM API key found"),
        ):
            status, body = _post_config_sync(port)
        assert status == 503
        payload = _json.loads(body)
        assert "error" in payload
        assert "No LLM API key found" in payload["error"]
    finally:
        server.shutdown()


def test_config_sync_unknown_post_path_returns_404() -> None:
    import urllib.error
    import urllib.request

    server, port = _start_test_server(":memory:")
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/no-such-endpoint",
            data=b"",
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urlopen(req)
        assert exc_info.value.code == 404
    finally:
        server.shutdown()
