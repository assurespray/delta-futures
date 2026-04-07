"""Pydantic models for database schemas with asset lock support."""

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
    
    # ========== PAPER TRADING ==========
    is_paper_trade: bool = False  # True = virtual trade, False = real money
    paper_leverage: Optional[int] = None  # Leverage for paper trades (e.g., 10, 25, 50)
    
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str, datetime: lambda v: v.isoformat()}



class TradeState(BaseModel):
    """Unified model for all active and historical trades."""
    
    id: Optional[PyObjectId] = Field(default_factory=PyObjectId, alias="_id")
    user_id: str
    
    # Configuration linkage
    setup_id: str  # ID of the AlgoSetup or ScreenerSetup
    setup_type: str  # "algo" or "screener"
    setup_name: str
    
    # Core Trade Info
    asset: str
    product_id: Optional[int] = None
    direction: str  # "long" or "short"
    lot_size: int
    timeframe: str
    
    # Lifecycle Status
    status: str  # "pending_entry", "open", "closed", "cancelled"
    
    # Paper Trading Flags
    is_paper_trade: bool = False
    paper_leverage: Optional[int] = None
    paper_margin_used: Optional[float] = None
    paper_fees: Optional[float] = None
    paper_liquidation_price: Optional[float] = None
    
    # Entry Info
    entry_trigger_price: Optional[float] = None
    pending_entry_order_id: Optional[int] = None
    last_entry_order_id: Optional[int] = None
    entry_price: Optional[float] = None
    entry_time: Optional[datetime] = None
    
    # Stop Loss / Trailing Info
    stop_loss_order_id: Optional[int] = None
    pending_sl_price: Optional[float] = None
    
    # Signals & Indicators
    perusu_entry_signal: Optional[str] = None  # "uptrend" or "downtrend"
    sirusu_exit_signal: Optional[str] = None  # exit reason
    last_perusu_signal: Optional[int] = None
    last_signal_time: Optional[datetime] = None
    
    # Exit & PnL
    exit_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    pnl: Optional[float] = None  # In USD
    pnl_inr: Optional[float] = None  # In INR
    
    trade_date: Optional[str] = None  # YYYY-MM-DD format
    
    # Periodic Sync
    last_position_sync: Optional[datetime] = None
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
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
    
    # ========== PAPER TRADING ==========
    is_paper_trade: bool = False  # True = virtual trade, False = real money
    paper_leverage: Optional[int] = None  # Leverage for paper trades
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str, datetime: lambda v: v.isoformat()}


class PositionLock(BaseModel):
    """
    ✅ NEW: Global asset position lock to prevent multi-timeframe conflicts.
    Only ONE setup can trade an asset at a time.
    
    Stored in MongoDB as documents:
    {
        "_id": ObjectId(...),
        "symbol": "ADAUSD",
        "setup_id": "123abc...",
        "setup_name": "ADA Scalper",
        "locked_at": datetime.utcnow()
    }
    """
    
    id: Optional[PyObjectId] = Field(default_factory=PyObjectId, alias="_id")
    symbol: str  # Asset symbol (e.g., "ADAUSD")
    setup_id: str  # ID of the setup owning this lock
    setup_name: str  # Name of setup (for logging/display)
    locked_at: datetime = Field(default_factory=datetime.utcnow)  # When lock was acquired
    
    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str, datetime: lambda v: v.isoformat()}


class OrderRecord(BaseModel):
    id: Optional[PyObjectId] = Field(default_factory=PyObjectId, alias="_id")
    order_id: int  # Exchange order ID
    algo_setup_id: str
    user_id: str
    asset: str
    side: str
    size: int
    order_type: str
    status: str
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    reduce_only: Optional[bool] = None
    average_fill_price: Optional[float] = None
    submitted_at: datetime = Field(default_factory=datetime.utcnow)
    filled_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    extra_data: Optional[dict] = None  # Save raw or additional API fields if needed

    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str, datetime: lambda v: v.isoformat()}
        

class PaperBalance(BaseModel):
    """Model for tracking virtual paper trading balance per user."""
    
    id: Optional[PyObjectId] = Field(default_factory=PyObjectId, alias="_id")
    user_id: str
    balance: float = 10000.0  # Current virtual balance in USD
    initial_balance: float = 10000.0  # Starting balance for equity curve
    total_trades: int = 0
    total_wins: int = 0
    total_losses: int = 0
    total_pnl: float = 0.0  # Cumulative PnL in USD
    total_fees: float = 0.0  # Cumulative fees deducted
    locked_margin: float = 0.0  # Margin currently locked in open positions
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    last_reset_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str, datetime: lambda v: v.isoformat()}


