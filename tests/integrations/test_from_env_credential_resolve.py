"""Package *_from_env helpers resolve secrets via env then the credentials file."""

from __future__ import annotations

import pytest

import config.llm_credentials as llm_credentials
from integrations.airflow.config import airflow_config_from_env
from integrations.gitlab import gitlab_config_from_env
from integrations.jenkins import jenkins_config_from_env
from integrations.posthog.config import posthog_config_from_env
from integrations.sentry import sentry_config_from_env
from integrations.tempo import tempo_config_from_env
from integrations.trello.config import trello_config_from_env


@pytest.fixture
def local_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)


def test_tempo_api_key_from_credentials_file(
    monkeypatch: pytest.MonkeyPatch, local_credentials: None
) -> None:
    monkeypatch.setenv("TEMPO_URL", "http://localhost:3200")
    monkeypatch.delenv("TEMPO_API_KEY", raising=False)
    monkeypatch.delenv("TEMPO_PASSWORD", raising=False)
    llm_credentials.save_credential("TEMPO_API_KEY", "tempo-stored")
    config = tempo_config_from_env()
    assert config is not None
    assert config.api_key == "tempo-stored"


def test_gitlab_token_from_credentials_file(
    monkeypatch: pytest.MonkeyPatch, local_credentials: None
) -> None:
    monkeypatch.delenv("GITLAB_ACCESS_TOKEN", raising=False)
    llm_credentials.save_credential("GITLAB_ACCESS_TOKEN", "glpat-stored")
    config = gitlab_config_from_env()
    assert config is not None
    assert config.auth_token == "glpat-stored"


def test_jenkins_token_from_credentials_file(
    monkeypatch: pytest.MonkeyPatch, local_credentials: None
) -> None:
    monkeypatch.setenv("JENKINS_URL", "https://jenkins.example.com")
    monkeypatch.delenv("JENKINS_API_TOKEN", raising=False)
    llm_credentials.save_credential("JENKINS_API_TOKEN", "jenkins-stored")
    config = jenkins_config_from_env()
    assert config is not None
    assert config.api_token == "jenkins-stored"


def test_posthog_personal_key_from_credentials_file(
    monkeypatch: pytest.MonkeyPatch, local_credentials: None
) -> None:
    monkeypatch.setenv("POSTHOG_PROJECT_ID", "123")
    monkeypatch.delenv("POSTHOG_PERSONAL_API_KEY", raising=False)
    llm_credentials.save_credential("POSTHOG_PERSONAL_API_KEY", "phc-stored")
    config = posthog_config_from_env()
    assert config is not None
    assert config.personal_api_key == "phc-stored"


def test_airflow_password_from_credentials_file(
    monkeypatch: pytest.MonkeyPatch, local_credentials: None
) -> None:
    monkeypatch.setenv("AIRFLOW_USERNAME", "admin")
    monkeypatch.delenv("AIRFLOW_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("AIRFLOW_PASSWORD", raising=False)
    llm_credentials.save_credential("AIRFLOW_PASSWORD", "airflow-stored")
    config = airflow_config_from_env()
    assert config is not None
    assert config.password == "airflow-stored"


def test_trello_secrets_from_credentials_file(
    monkeypatch: pytest.MonkeyPatch, local_credentials: None
) -> None:
    monkeypatch.delenv("TRELLO_API_KEY", raising=False)
    monkeypatch.delenv("TRELLO_TOKEN", raising=False)
    llm_credentials.save_credential("TRELLO_API_KEY", "trello-key")
    llm_credentials.save_credential("TRELLO_TOKEN", "trello-token")
    config = trello_config_from_env()
    assert config is not None
    assert config.api_key == "trello-key"
    assert config.token == "trello-token"


def test_sentry_helper_token_from_credentials_file(
    monkeypatch: pytest.MonkeyPatch, local_credentials: None
) -> None:
    monkeypatch.setenv("SENTRY_ORG_SLUG", "acme")
    monkeypatch.delenv("SENTRY_AUTH_TOKEN", raising=False)
    llm_credentials.save_credential("SENTRY_AUTH_TOKEN", "sentry-helper-stored")
    config = sentry_config_from_env()
    assert config is not None
    assert config.auth_token == "sentry-helper-stored"
