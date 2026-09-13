from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path("data")
    admin_token: str = ""
    ors_api_key: str = ""
    app_version: str = "dev"

    def __post_init__(self) -> None:
        object.__setattr__(self, "data_dir", Path(self.data_dir))

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
        )

