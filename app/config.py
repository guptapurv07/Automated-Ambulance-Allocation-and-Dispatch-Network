"""Environment-driven application settings."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    db_host: str = "127.0.0.1"
    db_port: int = 3306
    db_user: str = "root"
    db_password: str = ""
    db_name: str = "ambulance_dispatch"

    # How the allocator acquires ambulance rows. See app/core/allocation.py.
    #   none        -> plain SELECT then UPDATE (unsafe; baseline for comparison)
    #   for_update  -> SELECT ... FOR UPDATE
    #   skip_locked -> SELECT ... FOR UPDATE SKIP LOCKED
    locking_mode: str = "for_update"

    # Used to turn great-circle distance into an ETA, per the project's
    # "standard travel times" assumption.
    avg_speed_kmph: float = 32.0

    @property
    def database_url(self) -> str:
        return (
            f"mysql+pymysql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}?charset=utf8mb4"
        )

    @property
    def server_url(self) -> str:
        """Connection URL without a database, for bootstrapping the schema."""
        return (
            f"mysql+pymysql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/?charset=utf8mb4"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
