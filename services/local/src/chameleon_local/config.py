from pydantic_settings import BaseSettings, SettingsConfigDict


class LocalSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CHAMELEON_",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    LISTEN_HOST: str = "127.0.0.1"
    LISTEN_PORT: int = 2525
    MAILDIR_PATH: str = "/srv/mail/chameleon"
    ALLOWED_PEERS: str = "127.0.0.1"
    LOG_LEVEL: str = "INFO"

    @property
    def allowed_peer_set(self) -> set[str]:
        return {p.strip() for p in self.ALLOWED_PEERS.split(",")}
