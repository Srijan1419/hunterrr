"""config: optional env + .env, SecretStr everywhere, no import-time reads."""

import etl.core.config as config_mod
from etl.core.config import Settings
from pydantic import SecretStr


def test_all_fields_optional_missing_means_off():
    s = Settings(_env_file=None)
    for name in type(s).model_fields:
        assert getattr(s, name) is None


def test_every_field_is_secret_str_optional():
    for name, f in Settings.model_fields.items():
        ann = str(f.annotation or "")
        assert "SecretStr" in ann, f"{name} annotation {ann!r} is not SecretStr"


def test_repr_and_str_never_contain_secret(monkeypatch):
    secret = "sk-test-secret-xyz-12345"
    monkeypatch.setenv("NVIDIA_API_KEY", secret)
    monkeypatch.setenv("DATABASE_URL", "postgres://user:hunter2@localhost/db")
    s = Settings(_env_file=None)
    assert s.NVIDIA_API_KEY is not None
    assert s.NVIDIA_API_KEY.get_secret_value() == secret
    assert secret not in repr(s)
    assert secret not in str(s)
    assert "hunter2" not in repr(s)
    assert "hunter2" not in str(s)
    assert "**********" in repr(s)


def test_reads_env_and_optional_dotenv(monkeypatch, tmp_path):
    monkeypatch.setenv("GROQ_API_KEY", "grok-env-key")
    s = Settings(_env_file=None)
    assert isinstance(s.GROQ_API_KEY, SecretStr)
    assert s.GROQ_API_KEY.get_secret_value() == "grok-env-key"

    env_file = tmp_path / ".env"
    env_file.write_text("GEMINI_API_KEY=gemini-from-dotenv\n", encoding="utf-8")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    s2 = Settings(_env_file=str(env_file))
    assert s2.GEMINI_API_KEY is not None
    assert s2.GEMINI_API_KEY.get_secret_value() == "gemini-from-dotenv"


def test_nothing_read_at_import_time():
    assert not any(
        isinstance(v, Settings) for v in vars(config_mod).values()
    ), "Settings must not be instantiated at import time"
