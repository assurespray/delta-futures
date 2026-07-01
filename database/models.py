"""Pydantic models for database schemas with asset lock support."""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict
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



class StrategyPreset(BaseModel):
    """Model for storing user-defined strategy presets."""
    
    id: Optional[PyObjectId] = Field(default_factory=PyObjectId, alias="_id")
    user_id: str
    preset_name: str
    strategy_type: str  # e.g., "single_supertrend", "dual_supertrend"
    parameters: dict  # Generic dictionary of parameters
    is_default: bool = False  # To mark system defaults
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str, datetime: lambda v: v.isoformat()}

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
    indicator: str  # "dual_supertrend", "single_supertrend"
    preset_id: Optional[str] = None
    indicator_params: dict = Field(default_factory=dict)
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
    api_id: Optional[str] = None
    api_name: Optional[str] = None
    
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
    pending_entry_side: Optional[str] = None
    pending_entry_direction_signal: Optional[int] = None
    pending_entry_order_id: Optional[int] = None
    last_entry_order_id: Optional[int] = None
    entry_price: Optional[float] = None
    entry_time: Optional[datetime] = None
    
    # Stop Loss / Trailing Info
    stop_loss_order_id: Optional[int] = None
    pending_sl_price: Optional[float] = None
    
    # Signals & Indicators
    entry_signal: Optional[str] = None  # "uptrend" or "downtrend"
    exit_signal: Optional[str] = None  # exit reason
    last_primary_signal: Optional[int] = None
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
    """Model for caching indicator values for debugging & dashboard."""
    
    id: Optional[PyObjectId] = Field(default_factory=PyObjectId, alias="_id")
    
    # Setup linkage
    setup_id: str
    setup_type: str  # "algo" or "screener"
    setup_name: str
    is_paper_trade: bool
    
    # Asset & Timeframe
    asset: str
    timeframe: str
    
    # Price Context
    current_price: float
    
    # Primary Indicator (entry signal — e.g., Perusu ST, Single ST, Range Breakout)
    primary_name: str = "Primary"  # Display name for this indicator
    primary_signal: int  # 1 or -1
    primary_signal_text: str  # "Uptrend" or "Downtrend"
    primary_value: float
    
    # Secondary Indicator (exit/SL — e.g., Sirusu ST, EMA)
    secondary_name: str = "Secondary"  # Display name for this indicator
    secondary_signal: int
    secondary_signal_text: str
    secondary_value: float
    
    # Generic strategy state persisted across cycles
    # Each strategy stores whatever it needs here (e.g., {"primary_signal": 1})
    strategy_state: dict = Field(default_factory=dict)
    
    # Dynamic indicator details for dashboard display
    # Each strategy populates this with all important values for cross-checking
    # e.g., {"Upper": 77958.5, "Lower": 77832.5, "Mid": 77895.5, "Signal": "Inside Channel"}
    display_details: dict = Field(default_factory=dict)
    
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
    indicator: str  # "dual_supertrend", "single_supertrend"
    preset_id: Optional[str] = None
    indicator_params: dict = Field(default_factory=dict)
    asset_selection_type: str  # "every", "gainers", "losers", "mixed", "volume", "top_oi", "meme", "solana", "new", "ai", "defi", "layer1", "layer2", "gaming"
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
    Global asset position lock to prevent multi-timeframe conflicts.
    Only ONE setup can trade an asset per API account at a time.
    
    Compound unique index on (symbol, api_id) allows the same asset
    to be traded on different exchange accounts simultaneously.
    
    Stored in MongoDB as documents:
    {
        "_id": ObjectId(...),
        "symbol": "ADAUSD",
        "api_id": "abc123...",
        "setup_id": "123abc...",
        "setup_name": "ADA Scalper",
        "locked_at": datetime.utcnow()
    }
    """
    
    id: Optional[PyObjectId] = Field(default_factory=PyObjectId, alias="_id")
    symbol: str  # Asset symbol (e.g., "ADAUSD")
    api_id: str  # API credential ID — compound key with symbol
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


class BacktestResult(BaseModel):
    """
    Model for storing backtest results permanently in MongoDB.
    
    Stores the configuration used, all 18+ performance metrics,
    Monte Carlo / curve-fitting statistics, and the full trade log.
    Raw candle data is NOT stored here (kept in temporary CSV files).
    """
    
    id: Optional[PyObjectId] = Field(default_factory=PyObjectId, alias="_id")
    user_id: str
    
    # ===== Configuration Snapshot =====
    symbol: str                          # e.g. "BTCUSD"
    timeframe: str                       # e.g. "1m", "15m", "1h"
    strategy: str                        # e.g. "dual_supertrend"
    strategy_params: Dict = Field(default_factory=dict)  # Full indicator params
    direction: str = "both"              # "both", "long_only", "short_only"
    lot_size: int = 1
    leverage: int = 1
    initial_balance: float = 10000.0     # Starting virtual balance
    
    # ===== Time Range =====
    backtest_start: datetime             # Start of historical data window
    backtest_end: datetime               # End of historical data window
    total_candles: int = 0               # Number of candles processed
    
    # ===== Profitability Metrics =====
    overall_profit: float = 0.0          # Total PnL (USD)
    overall_profit_pct: float = 0.0      # Total PnL (%)
    num_trades: int = 0                  # Total trades executed
    avg_profit_per_trade: float = 0.0    # Average PnL per trade
    win_pct: float = 0.0                 # Win percentage
    loss_pct: float = 0.0               # Loss percentage
    avg_win: float = 0.0                 # Average profit on winning trades
    avg_loss: float = 0.0               # Average loss on losing trades
    max_profit_single: float = 0.0       # Best single trade
    max_loss_single: float = 0.0         # Worst single trade
    
    # ===== Risk & Drawdown Metrics =====
    max_drawdown: float = 0.0            # Maximum drawdown (USD)
    max_drawdown_pct: float = 0.0        # Maximum drawdown (%)
    max_drawdown_duration_days: int = 0  # Duration of max drawdown (days)
    max_drawdown_start: Optional[str] = None   # Date string: start of max DD
    max_drawdown_end: Optional[str] = None     # Date string: end of max DD
    max_trades_in_drawdown: int = 0      # Max trades during any drawdown
    
    # ===== Ratios & Streaks =====
    return_over_max_dd: float = 0.0      # Return / MaxDD ratio
    reward_to_risk: float = 0.0          # Avg Win / Avg Loss
    expectancy_ratio: float = 0.0        # (Win% * AvgWin - Loss% * AvgLoss) / AvgLoss
    max_win_streak: int = 0              # Longest consecutive wins
    max_loss_streak: int = 0             # Longest consecutive losses
    profit_factor: float = 0.0           # Gross Profit / Gross Loss
    
    # ===== Monte Carlo & Curve Fitting =====
    monte_carlo_risk_of_ruin: float = 0.0     # % chance of account blowup
    monte_carlo_max_dd_95: float = 0.0        # 95th percentile max drawdown
    monte_carlo_max_dd_99: float = 0.0        # 99th percentile max drawdown
    r_squared: float = 0.0                    # Equity curve R² (curve fitting check)
    sharpe_ratio: float = 0.0                 # Risk-adjusted return
    sortino_ratio: float = 0.0                # Downside risk-adjusted return
    
    # ===== Final Balance & Equity Curve =====
    final_balance: float = 0.0           # Ending balance
    equity_curve: List[float] = Field(default_factory=list)  # Balance after each trade
    
    # ===== Trade Log (compact) =====
    # Each entry: {entry_time, exit_time, direction, entry_price, exit_price,
    #              pnl, exit_reason, indicator_value}
    trade_log: List[Dict] = Field(default_factory=list)
    
    # ===== Metadata =====
    debug_mode: bool = False             # Whether indicator dump was generated
    run_duration_seconds: float = 0.0    # How long the backtest took to run
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str, datetime: lambda v: v.isoformat()}

