"""Environment-driven settings (no database code).

All fields are optional: missing means the feature is off. Nothing is read at
import time -- instantiate `Settings` explicitly. Every value is a `SecretStr`
so `repr()`/`str()` never leak secrets.
"""

from __future__ import annotations

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """One settings object for the whole ETL."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    DATABASE_URL: SecretStr | None = None
    DATABASE_URL_UNPOOLED: SecretStr | None = None
    NVIDIA_API_KEY: SecretStr | None = None
    GROQ_API_KEY: SecretStr | None = None
    GEMINI_API_KEY: SecretStr | None = None
    OPENROUTER_API_KEY: SecretStr | None = None
    FREELLMAPI_URL: SecretStr | None = None
    FREELLMAPI_TOKEN: SecretStr | None = None
    RAW_ARCHIVE_TOKEN: SecretStr | None = None
    HC_COLLECT_URL: SecretStr | None = None
    HC_PROCESS_URL: SecretStr | None = None
    HC_MAIL_URL: SecretStr | None = None
    HC_OPS_URL: SecretStr | None = None
    TELEGRAM_BOT_TOKEN: SecretStr | None = None
    TELEGRAM_CHAT_ID: SecretStr | None = None

    def __repr__(self) -> str:
        parts = []
        for name in type(self).model_fields:
            val = getattr(self, name, None)
            if val is None:
                parts.append(f"{name}=None")
            else:
                parts.append(f"{name}=SecretStr('**********')")
        return f"Settings({', '.join(parts)})"

    def __str__(self) -> str:
        return self.__repr__()


__all__ = ["Settings"]
