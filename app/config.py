from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path("data")
    admin_token: str = ""
    ors_api_key: str = ""
    app_version: str = "dev"
    idle_lock_seconds: int = 0
    hidden_lock_seconds: int = 0
    map_providers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "data_dir", Path(self.data_dir))
        for value in (self.idle_lock_seconds, self.hidden_lock_seconds):
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 86400:
                raise ValueError("lock intervals must be 0..86400 seconds")
        if any(provider not in {"kartverket", "esri"} for provider in self.map_providers):
            raise ValueError("unknown map provider")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "bunkerkartet.sqlite3"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            data_dir=Path(os.getenv("BUNKERKARTET_DATA_DIR", "data")),
            admin_token=os.getenv("ADMIN_TOKEN", ""),
            ors_api_key=os.getenv("ORS_API_KEY", ""),
            app_version=os.getenv("APP_VERSION", "dev"),
            idle_lock_seconds=int(os.getenv("BUNKERKARTET_IDLE_LOCK_SECONDS", "0")),
            hidden_lock_seconds=int(os.getenv("BUNKERKARTET_HIDDEN_LOCK_SECONDS", "0")),
            map_providers=tuple(value.strip() for value in os.getenv("BUNKERKARTET_MAP_PROVIDERS", "").split(",") if value.strip()),
        )

