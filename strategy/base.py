"""
Base strategy interface for the modular trading engine.

Every strategy must inherit from BaseStrategy and implement:
- calculate_indicators(): fetch candles and compute indicator values
- generate_entry_signal(): detect entry conditions, return EntrySignal or None
- generate_exit_signal(): detect exit conditions, return ExitSignal or None
- should_invalidate_pending_entry(): check if a pending order should be cancelled
- get_cache_mapping(): map indicator results to IndicatorCache fields + strategy_state
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, Any, Optional, List
from api.delta_client import DeltaExchangeClient


@dataclass
class EntrySignal:
    """Standardized entry signal returned by all strategies."""
    side: str                    # "long" or "short"
    trigger_price: float         # Entry price (market price or breakout level)
    stop_loss: float             # Initial stop-loss price
    immediate: bool              # True = market order, False = stop-market order
    reason: str                  # Human-readable entry reason


@dataclass
class ExitSignal:
    """Standardized exit signal returned by all strategies."""
    reason: str                  # Human-readable exit reason
    stop_loss: float = 0.0       # Current SL value at time of exit


class BaseStrategy(ABC):
    """
    Abstract base class for all trading strategies.

    Every strategy is self-contained: it owns its indicator calculations,
    entry logic, stop-loss logic, exit logic, and logging.

    The execution engine is strategy-agnostic — it only interacts with
    strategies through this interface.
    """

    # Override in strategies with transient one-candle signals (e.g. Range Breakout).
    # When True, the screener engine skips flip detection and uses the strategy's
    # generate_entry_signal() directly — the signal itself IS the trigger.
    uses_transient_signals: bool = False

    @abstractmethod
    async def calculate_indicators(
        self,
        client: DeltaExchangeClient,
        symbol: str,
        timeframe: str,
        skip_boundary_check: bool = False,
        force_recalc: bool = False,
        historical_candles: Optional[List[Dict[str, Any]]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch candles and calculate all indicators for this strategy.

        Returns:
            Dict with strategy-specific indicator data, or None if
            calculation failed or was skipped (e.g., duplicate candle).
        """
        ...

    @abstractmethod
    def generate_entry_signal(
        self,
        setup_id: str,
        previous_state: Optional[Dict[str, Any]],
        indicators_data: Dict[str, Any]
    ) -> Optional[EntrySignal]:
        """
        Check for entry conditions based on current indicators and previous state.

        Args:
            setup_id: The algo/screener setup ID.
            previous_state: The strategy_state dict from the last cycle's
                           IndicatorCache, or None on first run.
            indicators_data: The dict returned by calculate_indicators().

        Returns:
            EntrySignal if entry conditions are met, None otherwise.
        """
        ...

    @abstractmethod
    def generate_exit_signal(
        self,
        setup_id: str,
        position_side: str,
        indicators_data: Dict[str, Any]
    ) -> Optional[ExitSignal]:
        """
        Check for exit conditions on an open position.

        Args:
            setup_id: The algo/screener setup ID.
            position_side: "long" or "short".
            indicators_data: The dict returned by calculate_indicators().

        Returns:
            ExitSignal if exit conditions are met, None otherwise.
        """
        ...

    @abstractmethod
    def should_invalidate_pending_entry(
        self,
        pending_side: str,
        indicators_data: Dict[str, Any]
    ) -> bool:
        """
        Check if a pending stop-market entry order should be cancelled
        because indicators have flipped against the entry direction.

        Args:
            pending_side: "long" or "short" — the side of the pending entry.
            indicators_data: The dict returned by calculate_indicators().

        Returns:
            True if the pending entry should be cancelled, False otherwise.
        """
        ...

    @abstractmethod
    def get_cache_mapping(
        self,
        indicators_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Map indicator results to IndicatorCache fields for dashboard display
        and strategy state persistence.
        """
        ...

    def generate_backtest_signals(self, df) -> None:
        """
        Vectorized signal generation for the backtester.
        
        Args:
            df: pandas DataFrame containing OHLCV data.
            
        Returns:
            The same DataFrame with appended columns:
            - entry_signal (1 for long, -1 for short, 0 for none)
            - exit_long (bool: True to exit long position)
            - exit_short (bool: True to exit short position)
            - sl_price_long (float: stop loss price if long entry triggers)
            - sl_price_short (float: stop loss price if short entry triggers)
            - indicator_value (float: main indicator value for logging)
        """
        raise NotImplementedError(f"Backtest signal vectorization not implemented for {self.__class__.__name__}")
