"""
Configuration management for Polymarket Frontrun Bot.
Uses Pydantic for validation and automatic .env loading.
"""

from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings
from pydantic import Field, validator


class Settings(BaseSettings):
    """Bot configuration with validation and defaults."""
    
    # Authentication
    private_key: str = Field(
        default="",
        description="Polygon L1 private key (without 0x prefix)"
    )
    polymarket_api_key: Optional[str] = Field(
        default=None,
        description="L2 API key (auto-derived if not set)"
    )
    polymarket_api_secret: Optional[str] = Field(
        default=None,
        description="L2 API secret"
    )
    polymarket_api_passphrase: Optional[str] = Field(
        default=None,
        description="L2 API passphrase"
    )
    
    # Network
    rpc_url: str = Field(
        default="https://polygon-rpc.com",
        description="Polygon RPC URL"
    )
    clob_url: str = Field(
        default="https://clob.polymarket.com",
        description="Polymarket CLOB API URL"
    )
    chain_id: int = Field(
        default=137,
        description="Polygon chain ID"
    )
    
    # Trading Parameters
    bankroll: float = Field(
        default=100.0,
        ge=1.0,
        description="Total bankroll in USD"
    )
    max_trade_percent: float = Field(
        default=1.0,
        ge=0.1,
        le=10.0,
        description="Max % of bankroll per trade"
    )
    micro_order_size: int = Field(
        default=3,
        ge=1,
        le=5,
        description="Size of bait orders (1-5 shares)"
    )
    spread_threshold: float = Field(
        default=0.10,
        ge=0.01,
        description="Minimum spread to consider (USD)"
    )
    polling_interval: float = Field(
        default=0.2,
        ge=0.1,
        le=5.0,
        description="Order book polling interval (seconds) - optimized for speed"
    )
    
    # Risk Management
    max_daily_loss_percent: float = Field(
        default=5.0,
        ge=1.0,
        le=50.0,
        description="Max daily loss before circuit breaker"
    )
    max_concurrent_trades: int = Field(
        default=1,
        ge=1,
        le=3,
        description="Max simultaneous open trades"
    )
    min_counter_order_size: int = Field(
        default=50,
        ge=10,
        description="Minimum counter-order size to trigger frontrun"
    )
    reaction_time_threshold: float = Field(
        default=1.0,
        ge=0.5,
        le=5.0,
        description="Max time for counter-order detection (seconds)"
    )
    
    # Logging
    log_level: str = Field(
        default="INFO",
        description="Logging level"
    )
    log_file: str = Field(
        default="bot.log",
        description="Log file path"
    )
    
    @validator('private_key')
    def validate_private_key(cls, v):
        """Remove 0x prefix if present."""
        if v and v.startswith('0x'):
            return v[2:]
        return v
    
    @property
    def max_trade_amount(self) -> float:
        """Calculate max trade amount based on bankroll."""
        return self.bankroll * (self.max_trade_percent / 100)
    
    @property
    def is_configured(self) -> bool:
        """Check if essential configuration is set."""
        return bool(self.private_key and len(self.private_key) == 64)
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


# Global settings instance
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Get or create settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def update_settings(**kwargs) -> Settings:
    """Update settings with new values."""
    global _settings
    current = get_settings()
    new_values = current.model_dump()
    new_values.update(kwargs)
    _settings = Settings(**new_values)
    return _settings
