from pydantic_settings import BaseSettings, SettingsConfigDict


class LocalSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CHAMELEON_",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    RELAY_WS_URL: str
    RELAY_TOKEN: str
    MY_DOMAIN: str
    PRIVATE_KEY_PATH: str = "/run/secrets/chameleon_private_key"
    MAILDIR_PATH: str = "/srv/mail/chameleon"
    ALIAS_DB_PATH: str = "/srv/data/aliases.db"
    WEB_HOST: str = "0.0.0.0"
    WEB_PORT: int = 8080
    # Shared password for the alias UI (HTTP Basic auth, any username).
    # None/empty = no authentication (trusted-network mode).
    WEB_PASSWORD: str | None = None
    LOG_LEVEL: str = "INFO"
