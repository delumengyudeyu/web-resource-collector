from urllib.parse import urlsplit
from typing import Literal
from pydantic import BaseModel, Field, field_validator


class JobSpec(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=100)
    max_depth: int = Field(default=0, ge=0, le=10)
    max_pages: int = Field(default=10, ge=1, le=1000)
    max_resources: int = Field(default=100, ge=1, le=5000)
    max_requests: int = Field(default=600, ge=1, le=20000)
    max_links_per_page: int = Field(default=150, ge=1, le=2000)
    concurrency: int = Field(default=3, ge=1, le=8)
    interval_seconds: float = Field(default=0.5, ge=0.1, le=60)
    retries: int = Field(default=2, ge=0, le=5)
    timeout_seconds: int = Field(default=45, ge=5, le=180)
    max_file_mb: int = Field(default=100, ge=1, le=2048)
    max_total_mb: int = Field(default=500, ge=1, le=10000)
    browser: bool = False
    browser_wait_ms: int = Field(default=1500, ge=0, le=10000)
    scroll_steps: int = Field(default=3, ge=0, le=30)
    include_assets: bool = False
    media: bool = False
    media_quality: Literal["low", "best"] = "low"
    gallery: bool = False
    allowed_domains: list[str] = Field(default_factory=list)
    path_prefix: str = "/"
    download_selectors: list[str] = Field(default_factory=list, max_length=10)
    profile: str | None = None
    extract: bool = True
    network_mode: Literal['system', 'direct', 'strict'] = 'system'
    proxy_ids: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("urls")
    @classmethod
    def validate_urls(cls, values):
        for value in values:
            p = urlsplit(value)
            if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
                raise ValueError("Only HTTP(S) URLs without embedded credentials are allowed")
        return list(dict.fromkeys(values))

    @field_validator("profile")
    @classmethod
    def validate_profile(cls, value):
        if value and (not value.replace("-", "").replace("_", "").isalnum() or len(value) > 60):
            raise ValueError("Profile must be a simple name")
        return value
