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

    # Artificially widens the gap between reading a vehicle's availability and
    # writing the claim. Zero in normal operation. The benchmark sets it so the
    # unsafe baseline fails reproducibly instead of once in every few runs; it
    # is applied identically in every locking mode, so comparisons stay fair.
    race_window_ms: float = 0.0

    # --- Concurrency --------------------------------------------------------
    # Number of worker threads draining the dispatch queue. Allocation work is
    # I/O-bound on database waits, so threads genuinely overlap despite the GIL.
    worker_pool_size: int = 4

    # --- Simulation clock ---------------------------------------------------
    # Advances active dispatches through their lifecycle so vehicles return to
    # service instead of being claimed once and never released.
    sim_clock_enabled: bool = True
    # 60.0 means one simulated minute passes per real second, so a call that
    # would take ~25 minutes completes in ~25 seconds of demo time.
    sim_time_scale: float = 60.0
    sim_tick_seconds: float = 1.0

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
