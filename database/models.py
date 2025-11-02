"""Pydantic models for database schemas."""

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from bson import ObjectId


class PyObjectId(ObjectId):
    """Custom ObjectId type for Pydantic."""
    
    @classmethod
    def __get_validators__(cls):
        yield cls.validate
    
    @classmethod
    def validate(cls, v):
        if not ObjectId.is_valid(v):
            raise ValueError("Invalid ObjectId")
        return ObjectId(v)
    
    @classmethod
    def __get_pydantic_json_schema__(cls, field_schema):
        field_schema.update(type="string")


class APICredential(BaseModel):
    """Model for storing API credentials."""
    
    id: Optional[PyObjectId] = Field(default_factory=PyObjectId, alias="_id")
    user_id: str
    api_name: str
    api_key: str  # Will be encrypted
    api_secret: str  # Will be encrypted
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}


class AlgoSetup(BaseModel):
    """Model for algo trading setup configuration."""
    
    id: Optional[PyObjectId] = Field(default_factory=PyObjectId, alias="_id")
    user_id: str
    setup_name: str
    description: str
    api_id: str  # Reference to APICredential
    api_name: str  # Cached for quick display
    indicator: str  # "dual_supertrend"
    direction: str  # "both", "long_only", "short_only"
    timeframe: str  # "1m", "5m", "15m", "30m", "1h", "4h", "1d"
    asset: str  # Symbol like "BTCUSD"
    product_id: Optional[int] = None  # Delta Exchange product ID
    lot_size: int
    additional_protection: bool
    is_active: bool = True
    current_position: Optional[str] = None  # "long", "short", None
    last_entry_price: Optional[float] = None
    last_signal_time: Optional[datetime] = None
    
    # ✅ NEW: Track last Perusu signal state
    last_perusu_signal: Optional[int] = None  # 1=uptrend, -1=downtrend
    pending_entry_order_id: Optional[int] = None  # Stop-market entry order ID
    entry_trigger_price: Optional[float] = None  # Breakout trigger price

    # ✅ FIXED: Track stop-loss order ID for proper cancellation
    stop_loss_order_id: Optional[int] = None  # Stop-loss order ID (for cancellation)
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str, datetime: lambda v: v.isoformat()}


class AlgoActivity(BaseModel):
    """Model for trade activity logs."""
    
    id: Optional[PyObjectId] = Field(default_factory=PyObjectId, alias="_id")
    user_id: str
    algo_setup_id: str
    algo_setup_name: str
    entry_time: datetime
    entry_price: float
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    direction: str  # "long" or "short"
    lot_size: int
    pnl: Optional[float] = None  # In USD
    pnl_inr: Optional[float] = None  # In INR
    perusu_entry_signal: str  # "uptrend" or "downtrend"
    sirusu_exit_signal: Optional[str] = None  # "uptrend" or "downtrend"
    asset: str
    trade_date: str  # YYYY-MM-DD format
    is_closed: bool = False
    
    # ✅ NEW: Track entry trigger price
    entry_trigger_price: Optional[float] = None
    
    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str, datetime: lambda v: v.isoformat()}


class IndicatorCache(BaseModel):
    """Model for caching indicator values."""
    
    id: Optional[PyObjectId] = Field(default_factory=PyObjectId, alias="_id")
    algo_setup_id: str
    indicator_name: str  # "perusu" or "sirusu"
    asset: str
    timeframe: str
    last_signal: int  # 1 for uptrend, -1 for downtrend
    last_value: float  # SuperTrend line value
    calculated_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str, datetime: lambda v: v.isoformat()}


class ScreenerSetup(BaseModel):
    """Model for screener multi-asset setup."""
    
    id: Optional[PyObjectId] = Field(default_factory=PyObjectId, alias="_id")
    user_id: str
    setup_name: str
    description: str
    api_id: str
    api_name: str
    indicator: str  # "dual_supertrend"
    asset_selection_type: str  # "every", "gainers", "losers", "mixed"
    timeframe: str
    direction: str  # "both", "long_only", "short_only"
    lot_size: int
    additional_protection: bool
    is_active: bool = True
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str, datetime: lambda v: v.isoformat()}
        

class PositionLock:
    """
    Global asset position lock to prevent multi-timeframe conflicts.
    Only ONE setup can trade an asset at a time.
    """
    
    def __init__(self, 
                 symbol: str,
                 setup_id: str,
                 setup_name: str,
                 locked_at: datetime = None):
        """
        Args:
            symbol: Asset symbol (e.g., "ADAUSD")
            setup_id: ID of the setup owning this lock
            setup_name: Name of setup (for logging)
            locked_at: When lock was acquired
        """
        self.symbol = symbol
        self.setup_id = setup_id
        self.setup_name = setup_name
        self.locked_at = locked_at or datetime.utcnow()
                     
