"""Configuration settings and environment variables."""
from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    # Telegram Configuration
    telegram_bot_token: str = Field(..., env="TELEGRAM_BOT_TOKEN")
    telegram_logger_bot_token: str = Field(..., env="TELEGRAM_LOGGER_BOT_TOKEN")
    telegram_logger_chat_id: str = Field(..., env="TELEGRAM_LOGGER_CHAT_ID")
    
    # MongoDB Configuration
    mongodb_uri: str = Field(..., env="MONGODB_URI")
    mongodb_db_name: str = Field(default="delta_trading_bot", env="MONGODB_DB_NAME")
    
    # Deployment Configuration
    webhook_url: str = Field(..., env="WEBHOOK_URL")
    host: str = Field(default="0.0.0.0", env="HOST")
    port: int = Field(default=10000, env="PORT")
    environment: str = Field(default="production", env="ENVIRONMENT")
    
    # Currency Conversion
    usd_to_inr_rate: float = Field(default=85.0, env="USD_TO_INR_RATE")
    
    # Security
    encryption_key: str = Field(..., env="ENCRYPTION_KEY")
    
    # Delta Exchange
    delta_api_base_url: str = Field(default="https://api.india.delta.exchange")
    
    class Config:
        env_file = ".env"
        case_sensitive = False


# Global settings instance
settings = Settings()
