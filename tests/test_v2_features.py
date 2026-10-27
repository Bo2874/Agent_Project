import pytest
from pydantic import ValidationError

from agent_core.contracts import FEATURE_NAMES, FeatureFlags
from backend.core.config import Settings


def settings_without_v2_environment(monkeypatch, **overrides) -> Settings:
    for name in ["V2_ENABLED", *(f"V2_{feature.upper()}_ENABLED" for feature in FEATURE_NAMES)]:
        monkeypatch.delenv(name, raising=False)
    return Settings(
        _env_file=None,
        db_password="test-only-password",
        auth_secret_key="test-only-auth-key-with-at-least-32-characters",
        **overrides,
    )


def test_settings_default_to_legacy_runtime(monkeypatch) -> None:
    settings = settings_without_v2_environment(monkeypatch)
    assert not settings.v2_enabled
    assert not any(settings.v2_features.is_enabled(name) for name in FEATURE_NAMES)
    assert not any(getattr(settings.v2_features, name) for name in FEATURE_NAMES)
    assert settings.mcp_demo_enabled and settings.python_sandbox_enabled


def test_master_switch_can_disable_every_selected_feature() -> None:
    flags = FeatureFlags(enabled=False, **dict.fromkeys(FEATURE_NAMES, True))
    assert not any(flags.is_enabled(name) for name in FEATURE_NAMES)
    enabled = FeatureFlags(enabled=True, **dict.fromkeys(FEATURE_NAMES, True))
    assert all(enabled.is_enabled(name) for name in FEATURE_NAMES)


@pytest.mark.parametrize("values", [{"parallel": True}])
def test_invalid_flag_dependencies_fail_fast(values) -> None:
    with pytest.raises(ValidationError, match="requires"):
        FeatureFlags(**values)


def test_planning_can_be_enabled_without_mandatory_jev_routing():
    assert FeatureFlags(enabled=True, planning=True).is_enabled("planning")


def test_settings_also_validate_flag_dependencies(monkeypatch) -> None:
    with pytest.raises(ValidationError, match="requires planning"):
        settings_without_v2_environment(monkeypatch, v2_parallel_enabled=True)


def test_environment_flags_are_parsed_as_booleans_and_master_switch_applies(monkeypatch) -> None:
    settings_without_v2_environment(monkeypatch)
    monkeypatch.setenv("V2_ENABLED", "true")
    monkeypatch.setenv("V2_ROUTING_ENABLED", "true")
    settings = Settings(_env_file=None, db_password="test-password", auth_secret_key="x" * 32)
    assert settings.v2_features.is_enabled("routing")
    assert not settings.v2_features.is_enabled("rag")
    monkeypatch.setenv("V2_ENABLED", "false")
    settings = Settings(_env_file=None, db_password="test-password", auth_secret_key="x" * 32)
    assert settings.v2_routing_enabled
    assert not settings.v2_features.is_enabled("routing")


def test_unknown_feature_does_not_silently_enable_a_branch() -> None:
    with pytest.raises(ValueError, match="Unknown V2 feature"):
        FeatureFlags(enabled=True).is_enabled("misspelled")
