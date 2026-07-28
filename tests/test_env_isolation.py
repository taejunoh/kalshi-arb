"""The suite must not read the host's production .env.

On EC2 that file exists and sets CF_ACCESS_TEAM_DOMAIN, which switched the
softbook API's Cloudflare Access gate on mid-test: four tests returned 403/401
instead of what they assert. They had been red there since 2026-06-13 while
passing on any dev box, because a dev box has no /home/ubuntu/kalshi-arb/.env.
"""
import os

import pytest

import softbook_app_api


def test_env_path_points_somewhere_empty():
    """conftest sets this at import time. If it ever stops, the softbook tests
    silently start reading production config on EC2 again."""
    assert os.environ.get("ENV_PATH") == os.devnull


def test_dotenv_loader_is_neutralised():
    """The second door. core/config.py calls load_dotenv() at import, which
    finds the production .env by walking up from the CWD and copies every key
    into os.environ — and `_env` reads os.environ before it reads ENV_PATH, so
    that path bypasses the isolation above. This is what made the failures
    full-suite-only and EC2-only: the first test to import core.config poisoned
    everything after it."""
    import dotenv

    assert dotenv.load_dotenv() is False, (
        "load_dotenv is live again — the suite will inherit the host's .env"
    )


def test_importing_core_config_does_not_inject_host_secrets():
    """The regression stated end-to-end, in the order that actually broke."""
    import core.config  # noqa: F401

    assert os.environ.get("CF_ACCESS_TEAM_DOMAIN") is None
    assert softbook_app_api._env("CF_ACCESS_TEAM_DOMAIN") is None


def test_production_env_file_is_not_consulted():
    """The real failure mode, stated directly: whatever the host has in
    /home/ubuntu/kalshi-arb/.env must not reach the code under test.

    Probes the two CF_ACCESS_* keys because they are the ones that caused the
    403s AND are file-only — nothing in the suite puts them in the process
    environment. Deliberately NOT probing APP_API_TOKEN: the scan and
    mute-rules tests set that themselves so their TestClient can authenticate,
    and `_env` preferring a real process variable over the file is correct
    behaviour, not leakage.
    """
    assert softbook_app_api._env("CF_ACCESS_TEAM_DOMAIN") is None
    assert softbook_app_api._env("CF_ACCESS_AUD") is None


def test_env_path_is_honoured_when_pointed_at_a_real_file(tmp_path, monkeypatch):
    """Pins the mechanism the isolation relies on — ENV_PATH must actually be
    read, or setting it to devnull would be a no-op that hides the problem."""
    envfile = tmp_path / ".env"
    envfile.write_text("CF_ACCESS_TEAM_DOMAIN=example.cloudflareaccess.com\n")
    monkeypatch.setenv("ENV_PATH", str(envfile))
    assert softbook_app_api._env("CF_ACCESS_TEAM_DOMAIN") == (
        "example.cloudflareaccess.com")


def test_real_process_env_still_wins_over_the_file(tmp_path, monkeypatch):
    envfile = tmp_path / ".env"
    envfile.write_text("APP_API_TOKEN=from-file\n")
    monkeypatch.setenv("ENV_PATH", str(envfile))
    monkeypatch.setenv("APP_API_TOKEN", "from-process")
    assert softbook_app_api._env("APP_API_TOKEN") == "from-process"


@pytest.mark.parametrize("module_name", [
    "softbook_app_api", "softbook_ev_core", "softbook_tripwire",
    "softbook_auto_settle_stuck", "softbook_ev_watch_eval",
])
def test_softbook_modules_all_route_through_env_path(module_name):
    """Every module that reads the production path does so via ENV_PATH, so one
    conftest line covers them all. kalshi_ws_record.py hardcodes the path with
    no ENV_PATH escape — it is excluded here because no test imports it, but
    that is the one module this isolation would not protect."""
    import importlib
    import inspect

    src = inspect.getsource(importlib.import_module(module_name))
    if "/home/ubuntu/kalshi-arb/.env" in src:
        assert 'getenv("ENV_PATH"' in src or "getenv('ENV_PATH'" in src, (
            f"{module_name} reads the production .env without an ENV_PATH "
            f"override — tests cannot isolate from it"
        )
