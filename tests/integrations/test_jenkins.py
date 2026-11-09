"""Unit tests for the Jenkins integration.

Covers the config layer, the REST client (against ``httpx.MockTransport`` —
no live Jenkins), the response-shaping helpers, and the investigation tools.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from integrations import jenkins as jenkins_module
from integrations.jenkins import (
    JenkinsConfig,
    build_jenkins_config,
    jenkins_config_from_env,
    validate_jenkins_config,
)
from integrations.jenkins.client import (
    JenkinsClient,
    _flatten_jobs,
    _iso_from_ms,
    _job_api_path,
    _safe_job_name,
    _status_from_color,
    make_jenkins_client,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

Handler = Callable[[httpx.Request], httpx.Response]


class _FakeResponse:
    """Minimal stand-in for httpx.Response used to mock module-level httpx.request."""

    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = ""

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "error",
                request=httpx.Request("GET", "http://jenkins.local"),
                response=httpx.Response(self.status_code),
            )

    def json(self) -> Any:
        return self._payload


def _client_with_handler(handler: Handler, monkeypatch: pytest.MonkeyPatch) -> JenkinsClient:
    """Build a JenkinsClient whose HTTP calls route through a MockTransport."""
    config = JenkinsConfig(base_url="http://jenkins.local", username="u", api_token="t")
    client = JenkinsClient(config)
    mock = httpx.Client(
        base_url=config.api_base_url,
        transport=httpx.MockTransport(handler),
    )
    monkeypatch.setattr(client, "_get_client", lambda: mock)
    return client


# ---------------------------------------------------------------------------
# Config layer
# ---------------------------------------------------------------------------


class TestJenkinsConfig:
    def test_api_base_url_strips_trailing_slash(self) -> None:
        cfg = JenkinsConfig(base_url="http://jenkins.local/")
        assert cfg.api_base_url == "http://jenkins.local"

    def test_base_url_whitespace_normalized(self) -> None:
        cfg = JenkinsConfig(base_url="  http://jenkins.local  ")
        assert cfg.api_base_url == "http://jenkins.local"

    def test_auth_is_username_token_tuple(self) -> None:
        cfg = JenkinsConfig(base_url="http://x", username="alice", api_token="secret")
        assert cfg.auth == ("alice", "secret")

    def test_is_configured_requires_url_username_and_token(self) -> None:
        assert JenkinsConfig(base_url="http://x", username="u", api_token="t").is_configured
        assert not JenkinsConfig(base_url="http://x", api_token="t").is_configured  # no username
        assert not JenkinsConfig(base_url="http://x", username="u").is_configured  # no token
        assert not JenkinsConfig(username="u", api_token="t").is_configured  # no url

    def test_timeout_zero_raises(self) -> None:
        with pytest.raises(ValidationError):
            JenkinsConfig(base_url="http://x", timeout_seconds=0)

    def test_unknown_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            JenkinsConfig.model_validate({"base_url": "http://x", "bad_field": 1})


class TestBuildAndEnvConfig:
    def test_build_from_empty(self) -> None:
        cfg = build_jenkins_config(None)
        assert cfg.base_url == ""

    def test_env_returns_none_without_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("JENKINS_URL", raising=False)
        monkeypatch.setenv("JENKINS_API_TOKEN", "t")
        assert jenkins_config_from_env() is None

    def test_env_returns_none_without_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("JENKINS_URL", "http://jenkins.local")
        monkeypatch.delenv("JENKINS_API_TOKEN", raising=False)
        assert jenkins_config_from_env() is None

    def test_env_loads_full_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("JENKINS_URL", "http://jenkins.local")
        monkeypatch.setenv("JENKINS_USER", "alice")
        monkeypatch.setenv("JENKINS_API_TOKEN", "tok")
        cfg = jenkins_config_from_env()
        assert cfg is not None
        assert cfg.api_base_url == "http://jenkins.local"
        assert cfg.username == "alice"
        assert cfg.api_token == "tok"


class TestValidateConfig:
    def test_fails_without_base_url(self) -> None:
        result = validate_jenkins_config(JenkinsConfig(base_url="", api_token="t"))
        assert not result.ok
        assert "base URL" in result.detail

    def test_fails_without_token(self) -> None:
        result = validate_jenkins_config(JenkinsConfig(base_url="http://x", username="u"))
        assert not result.ok
        assert "token" in result.detail

    def test_fails_without_username(self) -> None:
        result = validate_jenkins_config(
            JenkinsConfig(base_url="http://x", username="", api_token="t")
        )
        assert not result.ok
        assert "username" in result.detail

    def test_fails_on_missing_scheme(self) -> None:
        result = validate_jenkins_config(
            JenkinsConfig(base_url="localhost:8080", username="u", api_token="t")
        )
        assert not result.ok
        assert "http" in result.detail.lower()

    def test_passes_on_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            jenkins_module.httpx,
            "request",
            lambda *_, **__: _FakeResponse({"nodeName": "controller"}),
        )
        result = validate_jenkins_config(
            JenkinsConfig(base_url="http://jenkins.local", username="u", api_token="t")
        )
        assert result.ok
        assert "controller" in result.detail

    def test_fails_on_http_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            jenkins_module.httpx,
            "request",
            lambda *_, **__: _FakeResponse({}, status_code=401),
        )
        result = validate_jenkins_config(
            JenkinsConfig(base_url="http://jenkins.local", api_token="bad")
        )
        assert not result.ok


# ---------------------------------------------------------------------------
# Shaping helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_iso_from_ms_converts(self) -> None:
        # 1780150032692 ms -> 2026-05-30 (verifies ms, not seconds)
        assert _iso_from_ms(1780150032692).startswith("2026-05-30")

    def test_iso_from_ms_invalid_returns_empty(self) -> None:
        assert _iso_from_ms(None) == ""
        assert _iso_from_ms("not-a-number") == ""
        assert _iso_from_ms(0) == ""

    def test_status_from_color(self) -> None:
        assert _status_from_color("blue") == ("SUCCESS", False)
        assert _status_from_color("red") == ("FAILURE", False)
        assert _status_from_color("yellow") == ("UNSTABLE", False)

    def test_status_from_color_anime_means_building(self) -> None:
        status, building = _status_from_color("blue_anime")
        assert status == "SUCCESS"
        assert building is True

    def test_status_from_color_unknown(self) -> None:
        assert _status_from_color("") == ("UNKNOWN", False)

    def test_safe_job_name_validates(self) -> None:
        assert _safe_job_name("demo-fail") == "demo-fail"
        assert _safe_job_name("team/payment-service") == "team/payment-service"  # folder
        assert _safe_job_name("  team / svc  ") == "team/svc"  # trims segments
        assert _safe_job_name("../etc") is None
        assert _safe_job_name("a\\b") is None
        assert _safe_job_name("a//b") is None  # empty segment
        assert _safe_job_name("team/ /svc") is None  # whitespace-only segment
        assert _safe_job_name("") is None

    def test_coerce_build_number(self) -> None:
        from integrations.jenkins.client import _coerce_build_number

        assert _coerce_build_number(4) == 4
        assert _coerce_build_number("7") == 7
        assert _coerce_build_number(0) is None
        assert _coerce_build_number(-1) is None
        assert _coerce_build_number("abc") is None
        assert _coerce_build_number(None) is None
        assert _coerce_build_number(True) is None  # bool is not a build number

    def test_job_api_path_maps_folders(self) -> None:
        assert _job_api_path("demo") == "job/demo"
        assert _job_api_path("team/payment-service") == "job/team/job/payment-service"


# ---------------------------------------------------------------------------
# Service client
# ---------------------------------------------------------------------------


_BUILDS_PAYLOAD = {
    "builds": [
        {"number": 4, "result": "FAILURE", "timestamp": 1780150032692, "duration": 11, "url": "u4"},
        {"number": 3, "result": "SUCCESS", "timestamp": 1780150031599, "duration": 10, "url": "u3"},
        {"number": 2, "result": None, "timestamp": 1780150030571, "building": True, "url": "u2"},
    ]
}


class TestListBuilds:
    def test_returns_shaped_builds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/job/demo/api/json"
            return httpx.Response(200, json=_BUILDS_PAYLOAD)

        client = _client_with_handler(handler, monkeypatch)
        result = client.list_builds("demo")
        assert result["success"]
        assert result["total"] == 3
        assert result["builds"][0]["status"] == "FAILURE"
        assert result["builds"][2]["status"] == "RUNNING"  # null result -> RUNNING
        assert len(result["failed_builds"]) == 1

    def test_status_filter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _client_with_handler(
            lambda _: httpx.Response(200, json=_BUILDS_PAYLOAD), monkeypatch
        )
        result = client.list_builds("demo", status="success")
        assert result["total"] == 1
        assert result["builds"][0]["status"] == "SUCCESS"

    def test_invalid_job_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _client_with_handler(lambda _: httpx.Response(200, json={}), monkeypatch)
        result = client.list_builds("../evil")
        assert not result["success"]
        assert "invalid job name" in result["error"]

    def test_folder_job_maps_to_nested_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/job/team/job/svc/api/json"
            return httpx.Response(200, json=_BUILDS_PAYLOAD)

        client = _client_with_handler(handler, monkeypatch)
        result = client.list_builds("team/svc")
        assert result["success"]
        assert result["job"] == "team/svc"

    def test_server_side_cap_in_tree_query(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["tree"] = request.url.params.get("tree", "")
            return httpx.Response(200, json=_BUILDS_PAYLOAD)

        client = _client_with_handler(handler, monkeypatch)
        client.list_builds("demo", limit=5)
        # range specifier caps the server response (no full-history transfer)
        assert "{0,5}" in captured["tree"]

    def test_http_error_returns_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _client_with_handler(lambda _: httpx.Response(404, text="nope"), monkeypatch)
        result = client.list_builds("demo")
        assert not result["success"]
        assert "404" in result["error"]

    def test_handles_non_dict_response(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _client_with_handler(
            lambda _: httpx.Response(200, json=["unexpected"]), monkeypatch
        )
        result = client.list_builds("demo")
        assert result["success"] is True
        assert result["builds"] == []

    def test_handles_null_builds_field(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _client_with_handler(
            lambda _: httpx.Response(200, json={"builds": None}), monkeypatch
        )
        result = client.list_builds("demo")
        assert result["success"] is True
        assert result["builds"] == []


class TestGetBuildLog:
    def test_returns_log(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/job/demo/4/consoleText"
            return httpx.Response(200, text="ERROR: boom\nFinished: FAILURE")

        client = _client_with_handler(handler, monkeypatch)
        result = client.get_build_log("demo", 4)
        assert result["success"]
        assert "ERROR: boom" in result["log"]
        assert result["truncated"] is False

    def test_tail_truncation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        body = "x" * 10 + "TAIL_MARKER"
        client = _client_with_handler(lambda _: httpx.Response(200, text=body), monkeypatch)
        result = client.get_build_log("demo", 1, max_chars=5)
        assert result["truncated"] is True
        assert result["log"] == "ARKER"  # keeps the tail

    def test_invalid_build_number(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _client_with_handler(lambda _: httpx.Response(200, text=""), monkeypatch)
        result = client.get_build_log("demo", "abc")  # type: ignore[arg-type]
        assert not result["success"]

    def test_rejects_nonpositive_build_number(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _client_with_handler(lambda _: httpx.Response(200, text=""), monkeypatch)
        zero = client.get_build_log("demo", 0)
        assert not zero["success"]
        assert "invalid build number" in zero["error"]
        assert not client.get_build_log("demo", -3)["success"]


class TestGetPipelineStages:
    def test_parses_stages_for_pipeline_build(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = {
            "name": "#5",
            "status": "FAILED",
            "stages": [
                {"name": "Build", "status": "SUCCESS", "durationMillis": 1200},
                {"name": "Deploy", "status": "FAILED", "durationMillis": 800},
            ],
        }

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/job/pipe/5/wfapi/describe"
            return httpx.Response(200, json=payload)

        client = _client_with_handler(handler, monkeypatch)
        result = client.get_pipeline_stages("pipe", 5)
        assert result["success"]
        assert result["is_pipeline"] is True
        assert [s["name"] for s in result["stages"]] == ["Build", "Deploy"]
        assert result["stages"][1]["status"] == "FAILED"

    def test_freestyle_404_returns_empty_not_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _client_with_handler(lambda _: httpx.Response(404, text="Not Found"), monkeypatch)
        result = client.get_pipeline_stages("demo-fail", 4)
        assert result["success"] is True
        assert result["is_pipeline"] is False
        assert result["stages"] == []

    def test_invalid_build_number(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _client_with_handler(lambda _: httpx.Response(200, json={}), monkeypatch)
        result = client.get_pipeline_stages("pipe", "abc")  # type: ignore[arg-type]
        assert not result["success"]


class TestListJobs:
    def test_tree_descends_folders(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["tree"] = request.url.params.get("tree", "")
            return httpx.Response(200, json={"jobs": []})

        client = _client_with_handler(handler, monkeypatch)
        result = client.list_jobs()
        # nested jobs[...] in the tree means folders are descended
        assert captured["tree"].count("jobs[") > 1
        assert result["truncated"] is False

    def test_flattens_folder_jobs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = {
            "jobs": [
                {"name": "top", "url": "ut", "color": "blue", "lastBuild": {"number": 2}},
                {
                    "name": "team",
                    "jobs": [
                        {
                            "name": "payment-service",
                            "url": "up",
                            "color": "red",
                            "lastBuild": {"number": 7},
                        }
                    ],
                },
            ]
        }
        client = _client_with_handler(lambda _: httpx.Response(200, json=payload), monkeypatch)
        result = client.list_jobs()
        statuses = {j["name"]: j["status"] for j in result["jobs"]}
        # folder job reported by its full folder/job path
        assert statuses == {"top": "SUCCESS", "team/payment-service": "FAILURE"}

    def test_decodes_color_status(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = {
            "jobs": [
                {
                    "name": "demo-fail",
                    "url": "uf",
                    "color": "red",
                    "lastBuild": {"number": 4, "timestamp": 1780150032692},
                },
                {"name": "demo-pass", "url": "up", "color": "blue", "lastBuild": {"number": 3}},
            ]
        }
        client = _client_with_handler(lambda _: httpx.Response(200, json=payload), monkeypatch)
        result = client.list_jobs()
        assert result["success"]
        statuses = {j["name"]: j["status"] for j in result["jobs"]}
        assert statuses == {"demo-fail": "FAILURE", "demo-pass": "SUCCESS"}

    def test_folder_beyond_depth_limit_sets_truncated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression: a folder found exactly at the depth limit comes back
        from Jenkins with no ``jobs`` key at all (the tree query didn't ask
        for one), which _flatten_jobs used to silently misread as a genuine
        leaf job with unknown status -- with the tree's deepest-level probe,
        it must instead be dropped and reported as depth-truncated."""
        monkeypatch.setattr("integrations.jenkins.client._MAX_FOLDER_DEPTH", 1)
        payload = {
            "jobs": [
                {"name": "top", "url": "ut", "color": "blue", "lastBuild": {"number": 2}},
                {
                    "name": "deep-folder",
                    # No color/lastBuild -- a real folder, with a child one
                    # level beyond what depth=1 was asked to fully fetch.
                    "jobs": [{"name": "buried-job"}],
                },
            ]
        }
        client = _client_with_handler(lambda _: httpx.Response(200, json=payload), monkeypatch)
        result = client.list_jobs()
        assert result["success"]
        assert result["truncated"] is True
        names = {j["name"] for j in result["jobs"]}
        assert names == {"top"}
        assert "deep-folder" not in names


class TestFlattenJobs:
    def test_leaf_jobs_at_any_depth_are_collected(self) -> None:
        raw = [{"name": "top", "color": "blue"}]
        flat, depth_truncated = _flatten_jobs(raw, max_depth=5)
        assert [name for name, _ in flat] == ["top"]
        assert depth_truncated is False

    def test_folder_within_depth_is_recursed_and_prefixed(self) -> None:
        raw = [{"name": "team", "jobs": [{"name": "svc", "color": "red"}]}]
        flat, depth_truncated = _flatten_jobs(raw, max_depth=1)
        assert [name for name, _ in flat] == ["team/svc"]
        assert depth_truncated is False

    def test_folder_at_max_depth_with_children_is_dropped_and_flagged(self) -> None:
        raw = [{"name": "team", "jobs": [{"name": "svc"}]}]
        flat, depth_truncated = _flatten_jobs(raw, max_depth=0)
        assert flat == []
        assert depth_truncated is True

    def test_folder_at_max_depth_with_no_children_is_not_flagged(self) -> None:
        """An empty `jobs: []` at the depth limit means the folder genuinely
        has no children -- not that children exist but were unfetched."""
        raw = [{"name": "team", "jobs": []}]
        flat, depth_truncated = _flatten_jobs(raw, max_depth=0)
        assert flat == []
        assert depth_truncated is False


class TestListRunningBuilds:
    def test_filters_building(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = {
            "jobs": [
                {
                    "name": "demo",
                    "builds": [
                        {"number": 5, "building": True, "timestamp": 1780150032692, "url": "u5"},
                        {"number": 4, "building": False, "result": "SUCCESS", "url": "u4"},
                    ],
                }
            ]
        }
        captured: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["tree"] = request.url.params.get("tree", "")
            return httpx.Response(200, json=payload)

        client = _client_with_handler(handler, monkeypatch)
        result = client.list_running_builds()
        assert result["total"] == 1
        assert result["running_builds"][0]["number"] == 5
        assert result["running_builds"][0]["status"] == "RUNNING"
        # per-job build cap present, and the tree descends folders
        assert "{0,5}" in captured["tree"]
        assert captured["tree"].count("jobs[") > 1

    def test_running_builds_in_folder(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = {
            "jobs": [
                {
                    "name": "team",
                    "jobs": [
                        {
                            "name": "deploy",
                            "builds": [
                                {
                                    "number": 9,
                                    "building": True,
                                    "timestamp": 1780150032692,
                                    "url": "u9",
                                }
                            ],
                        }
                    ],
                }
            ]
        }
        client = _client_with_handler(lambda _: httpx.Response(200, json=payload), monkeypatch)
        result = client.list_running_builds()
        assert result["total"] == 1
        assert result["running_builds"][0]["job"] == "team/deploy"
        assert result["running_builds"][0]["number"] == 9


class TestClientErrorPaths:
    @pytest.mark.parametrize(
        ("method_name", "args"),
        [
            ("list_builds", ("demo",)),
            ("get_build_log", ("demo", 1)),
            ("get_pipeline_stages", ("demo", 1)),
            ("list_jobs", ()),
            ("list_running_builds", ()),
        ],
    )
    def test_client_methods_return_failure_on_timeout(
        self,
        method_name: str,
        args: tuple[Any, ...],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("jenkins timed out")

        client = _client_with_handler(handler, monkeypatch)

        result = getattr(client, method_name)(*args)

        assert result["success"] is False
        assert "jenkins timed out" in result["error"]

    @pytest.mark.parametrize(
        ("method_name", "args"),
        [
            ("list_builds", ("demo",)),
            ("get_pipeline_stages", ("demo", 1)),
            ("list_jobs", ()),
            ("list_running_builds", ()),
        ],
    )
    def test_json_methods_return_failure_on_invalid_json(
        self,
        method_name: str,
        args: tuple[Any, ...],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"not-json")

        client = _client_with_handler(handler, monkeypatch)

        result = getattr(client, method_name)(*args)

        assert result["success"] is False
        assert "expecting value" in result["error"].lower()


class TestMakeClient:
    def test_returns_none_without_creds(self) -> None:
        assert make_jenkins_client("", api_token="") is None
        assert make_jenkins_client("http://x", "u", "") is None  # no token
        assert make_jenkins_client("", "u", "t") is None  # no url
        assert make_jenkins_client("http://x", "", "t") is None  # no username
        assert make_jenkins_client("http://x", None, "t") is None  # username defaults to None

    def test_builds_client_with_creds(self) -> None:
        client = make_jenkins_client("http://jenkins.local", "alice", "tok")
        assert isinstance(client, JenkinsClient)
        assert client.config.auth == ("alice", "tok")


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


class _FakeToolClient:
    """Context-managed fake client returning canned method results for tool tests."""

    def __init__(self, **results: Any) -> None:
        self._results = results

    def __enter__(self) -> _FakeToolClient:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def list_builds(self, *_: Any, **__: Any) -> dict[str, Any]:
        return self._results["list_builds"]

    def list_jobs(self, *_: Any, **__: Any) -> dict[str, Any]:
        return self._results["list_jobs"]


class TestTools:
    def test_availability_requires_verified_connection(self) -> None:
        import integrations.jenkins.tools as jenkins_tool

        assert not jenkins_tool._jenkins_available({"jenkins": {}})
        assert not jenkins_tool._jenkins_available({"jenkins": {"connection_verified": False}})
        assert jenkins_tool._jenkins_available({"jenkins": {"connection_verified": True}})

    def test_build_tool_extract_params_soft_defaults_job_name(self) -> None:
        import integrations.jenkins.tools as jenkins_tool

        # job_name absent from sources -> empty default (LLM supplies it as a tool arg)
        params = jenkins_tool._list_jenkins_builds_extract_params(
            {"jenkins": {"connection_verified": True}}
        )
        assert params["job_name"] == ""

    def test_creds_mapping_from_source_dict(self) -> None:
        import integrations.jenkins.tools as jenkins_tool

        creds = jenkins_tool._jenkins_creds(
            {"base_url": "http://x", "username": "u", "api_token": "t"}
        )
        assert creds == {"jenkins_url": "http://x", "jenkins_user": "u", "jenkins_token": "t"}

    def test_resolve_client_needs_both_url_and_token_explicitly(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import integrations.jenkins.tools as jenkins_tool

        seen: list[tuple] = []
        monkeypatch.setattr(jenkins_tool, "jenkins_config_from_env", lambda: None)
        monkeypatch.setattr(
            jenkins_tool,
            "make_jenkins_client",
            lambda url, user, token: seen.append((url, user, token)) or "client",
        )
        # only url, no token -> falls through to env (None here), explicit path skipped
        assert jenkins_tool._resolve_client("http://x", None, None) is None
        assert seen == []
        # both present -> explicit path
        assert jenkins_tool._resolve_client("http://x", "u", "t") == "client"
        assert seen[-1] == ("http://x", "u", "t")

    def test_resolve_client_env_path_requires_complete_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import integrations.jenkins.tools as jenkins_tool

        # env has url+token but no username -> jenkins_config_from_env returns a
        # config, but it is not is_configured, so no client is built.
        incomplete = JenkinsConfig(base_url="http://x", api_token="t")
        assert not incomplete.is_configured
        monkeypatch.setattr(jenkins_tool, "jenkins_config_from_env", lambda: incomplete)
        called: list = []
        monkeypatch.setattr(
            jenkins_tool, "make_jenkins_client", lambda *a: called.append(a) or "client"
        )
        assert jenkins_tool._resolve_client(None, None, None) is None
        assert called == []

    def test_resolve_client_explicit_path_without_username_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import integrations.jenkins.tools as jenkins_tool

        # url + token explicitly present but no username (and no env) -> the
        # factory refuses to build an empty-username client -> None.
        monkeypatch.setattr(jenkins_tool, "jenkins_config_from_env", lambda: None)
        assert jenkins_tool._resolve_client("http://x", None, "t") is None

    def test_not_configured_when_no_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import integrations.jenkins.tools as jenkins_tool

        monkeypatch.setattr(jenkins_tool, "_resolve_client", lambda *_a, **_k: None)
        result = jenkins_tool.list_jenkins_builds("demo")
        assert result["available"] is False
        assert "not configured" in result["error"]

    def test_list_builds_tool_shapes_result(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import integrations.jenkins.tools as jenkins_tool

        fake = _FakeToolClient(
            list_builds={
                "success": True,
                "job": "demo",
                "builds": [{"number": 4, "status": "FAILURE"}],
                "failed_builds": [{"number": 4, "status": "FAILURE"}],
                "total": 1,
            }
        )
        monkeypatch.setattr(jenkins_tool, "_resolve_client", lambda *_a, **_k: fake)
        result = jenkins_tool.list_jenkins_builds("demo", jenkins_url="http://x", jenkins_token="t")
        assert result["available"] is True
        assert result["source"] == "jenkins"
        assert result["total"] == 1
        assert result["failed_builds"][0]["number"] == 4

    def test_tools_registered_in_registry(self) -> None:
        from tools.registry import get_registered_tools

        names = {t.name for t in get_registered_tools() if t.source == "jenkins"}
        assert names == {
            "list_jenkins_builds",
            "get_jenkins_build_log",
            "get_jenkins_pipeline_stages",
            "list_jenkins_jobs",
            "list_jenkins_running_builds",
        }
