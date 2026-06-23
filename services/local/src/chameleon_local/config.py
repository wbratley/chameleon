from pydantic_settings import BaseSettings, SettingsConfigDict


class LocalSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CHAMELEON_",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    RELAY_WS_URL: str
    RELAY_TOKEN: str
    MAILDIR_PATH: str = "/srv/mail/chameleon"
    LOG_LEVEL: str = "INFO"
