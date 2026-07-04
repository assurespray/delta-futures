"""
Rolling Chunk Simulation Engine for Backtesting

Simulates algorithmic trading over historical data using a memory-safe,
chunk-based approach. Processes 10,000 candles at a time with a 500-candle overlap 
to guarantee perfectly accurate technical indicator math without crashing the 512MB RAM limit.

Features:
- Industry-safe intra-candle evaluation (evaluates Stop Loss before Take Profit/Exits).
- Simulates realistic Taker and Maker fees.
- Outputs a detailed Trade Log and Equity Curve.
"""

import logging
import pandas as pd
import numpy as np
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone

from strategy.factory import StrategyFactory
from indicators.supertrend import SuperTrend, SIGNAL_UPTREND, SIGNAL_DOWNTREND
from config.constants import PAPER_TRADE_TAKER_FEE, PAPER_TRADE_MAKER_FEE, BREAKOUT_PIP_OFFSET

logger = logging.getLogger(__name__)

class BacktestEngine:
    def __init__(self, csv_path: str, params: Dict[str, Any]):
        """
        Initialize the Backtest Engine.
        
        Args:
            csv_path: Absolute path to the cached .csv file.
            params: Strategy configuration (initial_balance, leverage, etc).
        """
        self.csv_path = csv_path
        self.params = params
        
        # Strategy Parameters (Dual SuperTrend Default)
        self.perusu_atr = int(self.params.get("perusu_atr", 20))
        self.perusu_factor = float(self.params.get("perusu_factor", 20))
        self.sirusu_atr = int(self.params.get("sirusu_atr", 10))
        self.sirusu_factor = float(self.params.get("sirusu_factor", 10))
        self.direction = self.params.get("direction", "both")
        
        # Portfolio Parameters
        self.initial_balance = float(self.params.get("initial_balance", 10000.0))
        self.leverage = int(self.params.get("leverage", 1))
        self.lot_size = float(self.params.get("lot_size", 1.0))  # Assuming 1 coin/contract per trade for simplicity
        
        # State Tracking
        self.balance = self.initial_balance
        self.equity_curve = [self.initial_balance]
        self.trade_log = []
        self.open_trade = None
        
        # Chunking Config
        self.chunk_size = 10000
        self.overlap = 500  # Enough to warm up SuperTrend (max ATR is usually 20)
        
        self._abort = False

    def abort(self):
        """Signal the engine to stop mid-simulation."""
        self._abort = True

    async def run(self, progress_callback=None) -> Dict[str, Any]:
        """
        Execute the rolling chunk backtest.
        Returns a dict containing the final trade log and equity curve.
        """
        logger.info(f"[BT-ENGINE] Starting backtest using {self.csv_path}")
        
        strategy_name = self.params.get("strategy_name", "dual_supertrend")
        strategy = StrategyFactory.get_strategy(strategy_name, self.params)
        
        overlap_buffer = pd.DataFrame()
        
        # Determine total rows for progress bar (rough estimate via file size or full scan)
        try:
            total_rows = sum(1 for _ in open(self.csv_path)) - 1
        except Exception:
            total_rows = 100000
            
        processed_rows = 0
        
        # Read CSV in chunks
        chunk_iterator = pd.read_csv(self.csv_path, chunksize=self.chunk_size)
        
        for chunk in chunk_iterator:
            if self._abort:
                logger.warning("[BT-ENGINE] Backtest aborted by user.")
                break
                
            # If we have overlap, prepend it to the current chunk
            if not overlap_buffer.empty:
                df = pd.concat([overlap_buffer, chunk], ignore_index=True)
                start_idx = len(overlap_buffer)
            else:
                df = chunk
                start_idx = self.overlap  # Skip the first N candles to allow indicator warmup
                
            # Keep the last N rows for the next chunk's overlap
            overlap_buffer = chunk.tail(self.overlap)
            
            # Convert DF to list of dicts for the SuperTrend calculator
            candles = df.to_dict('records')
            
            # 1. Vectorized Indicator Math via Strategy
            try:
                signals = strategy.generate_backtest_signals(df)
            except NotImplementedError:
                logger.error(f"[BT-ENGINE] {strategy_name} does not support backtesting yet.")
                break
            except Exception as e:
                logger.error(f"[BT-ENGINE] Indicator calculation failed for chunk: {e}")
                continue
                
            # 2. Extract fast Numpy arrays
            times = df["time"].values
            opens = df["open"].values
            highs = df["high"].values
            lows = df["low"].values
            closes = df["close"].values
            
            entry_signal_arr = signals["entry_signal"]
            exit_long_arr = signals["exit_long"]
            exit_short_arr = signals["exit_short"]
            sl_price_long_arr = signals["sl_price_long"]
            sl_price_short_arr = signals["sl_price_short"]
            indicator_val_arr = signals["indicator_value"]
            
            # 3. Simulate chronological ticks (the core trading loop)
            # We iterate from start_idx to end of chunk.
            for i in range(start_idx, len(times)):
                t_time = times[i]
                t_open = opens[i]
                t_high = highs[i]
                t_low = lows[i]
                t_close = closes[i]
                
                # Check for open position exits FIRST
                if self.open_trade:
                    # Industry Safe Intra-Candle Logic:
                    # Always evaluate the worst-case scenario (Stop Loss hit before Take Profit).
                    
                    exit_triggered = False
                    exit_price = 0.0
                    exit_reason = ""
                    
                    if self.open_trade["direction"] == "long":
                        sl_price = self.open_trade["sl_price"]
                        if t_low <= sl_price:
                            exit_triggered = True
                            exit_price = t_open if t_open < sl_price else sl_price
                            exit_reason = "Stop Loss"
                        elif exit_long_arr[i-1]:
                            exit_triggered = True
                            exit_price = t_open
                            exit_reason = "Signal Exit"
                            
                    elif self.open_trade["direction"] == "short":
                        sl_price = self.open_trade["sl_price"]
                        if t_high >= sl_price:
                            exit_triggered = True
                            exit_price = t_open if t_open > sl_price else sl_price
                            exit_reason = "Stop Loss"
                        elif exit_short_arr[i-1]:
                            exit_triggered = True
                            exit_price = t_open
                            exit_reason = "Signal Exit"
                            
                    if exit_triggered:
                        self._close_position(
                            exit_price=exit_price,
                            exit_time=t_time,
                            reason=exit_reason,
                            indicator_value=indicator_val_arr[i-1]
                        )
                
                # Check for new entries (Only if we don't have an open trade)
                if not self.open_trade:
                    # Breakout Logic: Did the strategy trigger an entry on the previous candle?
                    # Using i-1 ensures we only act on *closed* candle signals.
                    signal = entry_signal_arr[i-1]
                    
                    if signal == 1 and self.direction in ["both", "long_only"]:
                        entry_price = t_open
                        self._open_position(
                            direction="long",
                            entry_price=entry_price,
                            entry_time=t_time,
                            sl_price=sl_price_long_arr[i-1],
                            indicator_value=indicator_val_arr[i-1]
                        )
                    elif signal == -1 and self.direction in ["both", "short_only"]:
                        entry_price = t_open
                        self._open_position(
                            direction="short",
                            entry_price=entry_price,
                            entry_time=t_time,
                            sl_price=sl_price_short_arr[i-1],
                            indicator_value=indicator_val_arr[i-1]
                        )
                            
            # Update progress
            processed_rows += len(chunk)
            if progress_callback:
                try:
                    msg = f"Simulating Trades... ({min(processed_rows, total_rows):,} / {total_rows:,})"
                    await progress_callback(min(processed_rows, total_rows), total_rows, msg)
                except Exception:
                    pass
                    
        # If there's still an open trade at the very end of history, close it out at the final close price
        if self.open_trade:
            self._close_position(
                exit_price=closes[-1],
                exit_time=times[-1],
                reason="End of Data",
                indicator_value=indicator_val_arr[-1] if 'indicator_val_arr' in locals() else 0.0
            )

        logger.info(f"[BT-ENGINE] Backtest complete. Total Trades: {len(self.trade_log)}. Final Balance: ${self.balance:.2f}")

        return {
            "trade_log": self.trade_log,
            "equity_curve": self.equity_curve,
            "final_balance": self.balance,
            "total_candles": processed_rows
        }

    def _open_position(self, direction: str, entry_price: float, entry_time: int, sl_price: float, indicator_value: float):
        """Open a mock position with exact margin and lot size math."""
        quantity = self.lot_size
        position_size_usd = entry_price * quantity
        
        # Initial Margin = Position Notional / Leverage
        initial_margin = position_size_usd / self.leverage if self.leverage > 0 else position_size_usd
        
        # Max Margin Required = Initial Margin + Max Potential Loss (Distance to SL)
        # Assumes the user wants to avoid liquidation before the SL triggers
        sl_distance = abs(entry_price - sl_price)
        max_potential_loss = sl_distance * quantity
        max_margin_required = initial_margin + max_potential_loss
        
        fee = position_size_usd * PAPER_TRADE_TAKER_FEE
        self.balance -= fee
        
        self.open_trade = {
            "direction": direction,
            "entry_price": entry_price,
            "entry_time": entry_time,
            "sl_price": sl_price,
            "quantity": quantity,
            "position_size_usd": position_size_usd,
            "initial_margin": initial_margin,
            "max_margin_required": max_margin_required,
            "indicator_value": indicator_value,
            "entry_fee": fee
        }

    def _close_position(self, exit_price: float, exit_time: int, reason: str, indicator_value: float):
        """Close the mock position, calculate PnL, deduct fees, and record the trade."""
        direction = self.open_trade["direction"]
        entry_price = self.open_trade["entry_price"]
        quantity = self.open_trade["quantity"]
        position_size_usd = self.open_trade["position_size_usd"]
        
        # Calculate PnL
        if direction == "long":
            pnl = (exit_price - entry_price) * quantity
        else:
            pnl = (entry_price - exit_price) * quantity
            
        # Add Leverage multiplier
        pnl *= self.leverage
            
        # Calculate Exit Fees (Assuming Market/Taker on exits too to be conservative)
        exit_notional = exit_price * quantity
        exit_fee = exit_notional * PAPER_TRADE_TAKER_FEE
        
        net_pnl = pnl - exit_fee
        self.balance += net_pnl
        self.equity_curve.append(self.balance)
        
        # Create Trade Record
        self.trade_log.append({
            "entry_time": self.open_trade["entry_time"],
            "exit_time": exit_time,
            "direction": direction,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "quantity": quantity,
            "notional_size": position_size_usd,
            "initial_margin": self.open_trade["initial_margin"],
            "max_margin_required": self.open_trade["max_margin_required"],
            "pnl": net_pnl,
            "pnl_pct": (net_pnl / position_size_usd) * 100.0,
            "roe_pct": (net_pnl / self.open_trade["initial_margin"]) * 100.0 if self.open_trade["initial_margin"] > 0 else 0.0,
            "exit_reason": reason,
            "entry_indicator": self.open_trade["indicator_value"],
            "exit_indicator": indicator_value
        })
        
        self.open_trade = None
