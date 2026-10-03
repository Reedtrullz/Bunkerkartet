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
    operations_log_dir: Path | None = None
    enrichment_path: Path | None = None
    freshness_policy_days: int = 0
    pilot_offline_enabled: bool = False
    pilot_historical_enabled: bool = False
    pilot_readers_enabled: bool = False
    pilot_publication_enabled: bool = False
    pilot_geography_enabled: bool = False
    pilot_geography_descriptors: tuple = ()
    geography_descriptor_path: Path | None = None
    attachment_policy_path: Path | None = None
    raster_policy_path: Path | None = None
    route_profiles: tuple[str, ...] = ("foot-hiking",)

    def __post_init__(self) -> None:
        object.__setattr__(self, "data_dir", Path(self.data_dir))
        if not self.route_profiles or any(profile not in {"foot-hiking", "foot-walking"} for profile in self.route_profiles):
            raise ValueError("route profiles must be an approved allowlist")
        if isinstance(self.freshness_policy_days,bool) or not 0 <= self.freshness_policy_days <= 3650:
            raise ValueError("freshness policy days must be 0..3650")
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
            pilot_offline_enabled=os.getenv("BUNKERKARTET_PILOT_OFFLINE", "0") == "1",
            pilot_historical_enabled=os.getenv("BUNKERKARTET_PILOT_HISTORICAL", "0") == "1",
            pilot_readers_enabled=os.getenv("BUNKERKARTET_PILOT_READERS", "0") == "1",
            pilot_publication_enabled=os.getenv("BUNKERKARTET_PILOT_PUBLICATION", "0") == "1",
            pilot_geography_enabled=os.getenv("BUNKERKARTET_PILOT_GEOGRAPHY", "0") == "1",
            geography_descriptor_path=Path(os.environ["BUNKERKARTET_GEOGRAPHY_DESCRIPTOR_PATH"]) if os.getenv("BUNKERKARTET_GEOGRAPHY_DESCRIPTOR_PATH") else None,
            attachment_policy_path=Path(os.environ["BUNKERKARTET_ATTACHMENT_POLICY_PATH"]) if os.getenv("BUNKERKARTET_ATTACHMENT_POLICY_PATH") else None,
            raster_policy_path=Path(os.environ["BUNKERKARTET_RASTER_POLICY_PATH"]) if os.getenv("BUNKERKARTET_RASTER_POLICY_PATH") else None,
            route_profiles=tuple(value.strip() for value in os.getenv("BUNKERKARTET_ROUTE_PROFILES", "foot-hiking").split(",") if value.strip()),
            freshness_policy_days=int(os.getenv("BUNKERKARTET_FRESHNESS_POLICY_DAYS", "0")),
            idle_lock_seconds=int(os.getenv("BUNKERKARTET_IDLE_LOCK_SECONDS", "0")),
            hidden_lock_seconds=int(os.getenv("BUNKERKARTET_HIDDEN_LOCK_SECONDS", "0")),
            map_providers=tuple(value.strip() for value in os.getenv("BUNKERKARTET_MAP_PROVIDERS", "").split(",") if value.strip()),
            operations_log_dir=Path(os.environ["BUNKERKARTET_OPERATIONS_LOG_DIR"]) if os.getenv("BUNKERKARTET_OPERATIONS_LOG_DIR") else None,
            enrichment_path=Path(os.environ["BUNKERKARTET_ENRICHMENT_PATH"]) if os.getenv("BUNKERKARTET_ENRICHMENT_PATH") else None,
        )

