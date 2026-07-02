from pydantic_settings import BaseSettings, SettingsConfigDict


class RelaySettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CHAMELEON_",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    MY_DOMAIN: str
    RELAY_HOSTNAME: str
    LISTEN_HOST: str = "0.0.0.0"
    LISTEN_PORT: int = 1025
    MAX_MESSAGE_SIZE: int = 26_214_400
    API_HOST: str = "127.0.0.1"
    API_PORT: int = 8080
    API_TOKEN: str
    PUBLIC_KEY: str
    QUEUE_DB_PATH: str = "/data/queue.db"
    # How long an undelivered message survives in the queue before the sweeper
    # secure-deletes it. Bounds the window during which mail is recoverable if
    # the local server is offline. Default 24h.
    QUEUE_RETAIN_MINUTES: int = 1440
    LOG_LEVEL: str = "INFO"
    TLS_CERT_PATH: str | None = None
    TLS_KEY_PATH: str | None = None
