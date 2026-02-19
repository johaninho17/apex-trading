"""Apex DFS Configuration."""
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings loaded from environment."""
    
    # API Keys
    prop_odds_api_key: str = ""
    sleeper_base_url: str = "https://api.sleeper.app/v1"
    
    # DFS Platform Settings
    dfs_fixed_implied_prob: float = 0.545  # -119 implied probability
    
    # Strategy Settings
    edge_threshold: float = 0.03  # 3% minimum edge
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance."""
    return Settings()
