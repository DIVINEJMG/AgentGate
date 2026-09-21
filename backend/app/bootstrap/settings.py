import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str = os.getenv("AUDORYN_ENV", "development")
    service_name: str = "audoryn-api"


settings = Settings()
