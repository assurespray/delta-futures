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
        
        # Contract multiplier (fixes margin/position size mapping for altcoins)
        from utils.market_utils import get_contract_multiplier
        self.symbol = self.params.get("symbol", "BTCUSD")
        self.contract_multiplier = get_contract_multiplier(self.symbol)
        
        # Time Window Rules
        from utils.time_utils import parse_time, is_time_in_window, is_time_to_hard_exit
        self.time_window = self.params.get("time_window")
        if self.time_window:
            self.tw_start = parse_time(self.time_window["start"])
            self.tw_stop_entries = parse_time(self.time_window["stop_entries"])
            self.tw_hard_exit = parse_time(self.time_window["hard_exit"])
        
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
            
            # Exact entry support for breakout strategies
            exact_entry_price_arr = signals.get("exact_entry_price")
            
            # Dynamic metadata support
            meta_keys = [k for k in signals.keys() if k.startswith("meta_")]
            
            # TP support: strategies can return rr_ratio for auto TP computation
            rr_ratio = signals.get("rr_ratio", 0)
            
            # 3. Simulate chronological ticks (the core trading loop)
            # We iterate from start_idx to end of chunk.
            from utils.time_utils import IST
            
            from api.delta_client import DeltaExchangeClient
            client = DeltaExchangeClient("", "")
            symbol = self.params.get("symbol", "BTCUSD")
            timeframe = self.params.get("timeframe", "15m")
            
            try:
                for i in range(start_idx, len(times)):
                    t_time = int(times[i])
                    t_open = float(opens[i])
                    t_high = float(highs[i])
                    t_low = float(lows[i])
                    t_close = float(closes[i])
                    
                    # Check Time Window Rules
                    current_dt = datetime.fromtimestamp(t_time, tz=IST)
                    current_time = current_dt.time()
                    
                    is_within_entry_window = True
                    if self.time_window:
                        from utils.time_utils import is_time_in_window, is_time_to_hard_exit
                        is_within_entry_window = is_time_in_window(current_time, self.tw_start, self.tw_stop_entries)
                        
                        # Hard Exit Check
                        if self.open_trade and is_time_to_hard_exit(current_time, self.tw_hard_exit, self.tw_start):
                            self._close_position(
                                exit_price=t_open,
                                exit_time=t_time,
                                reason="Time Hard Exit",
                                indicator_value=float(indicator_val_arr[i-1]) if 'indicator_val_arr' in locals() else 0.0
                            )
                    
                    # 1. Exit Evaluation (Start of candle)
                    if self.open_trade:
                        signal_exit = exit_long_arr[i-1] if self.open_trade["direction"] == "long" else exit_short_arr[i-1]
                        await self._evaluate_candle_exits(
                            client, symbol, timeframe, t_time, t_open, t_high, t_low, t_close, 
                            indicator_val_arr[i-1], signal_exit
                        )
                    
                    # 2. Entry Evaluation
                    trade_opened_this_candle = False
                    if not self.open_trade:
                        if exact_entry_price_arr is not None and int(entry_signal_arr[i]) != 0 and exact_entry_price_arr[i] > 0:
                            signal = int(entry_signal_arr[i])
                            if not is_within_entry_window:
                                signal = 0
                            
                            if signal == 2:
                                # Resolve entry ambiguity
                                e_long = float(signals.get("exact_entry_price_long", [0]*len(times))[i])
                                e_short = float(signals.get("exact_entry_price_short", [0]*len(times))[i])
                                resolved_dir = await self._resolve_intrabar_entry_ambiguity(client, symbol, timeframe, t_time, e_long, e_short)
                                signal = 1 if resolved_dir == "long" else -1
                                # Update exact_entry_price so the downstream logic uses the correct one
                                exact_entry_price_arr[i] = e_long if signal == 1 else e_short
                            
                            if signal != 0:
                                target_entry = float(exact_entry_price_arr[i])
                                if signal == 1 and t_open > target_entry: entry_price = t_open
                                elif signal == -1 and t_open < target_entry: entry_price = t_open
                                else: entry_price = target_entry
                                    
                                sl_price = float(sl_price_long_arr[i]) if signal == 1 else float(sl_price_short_arr[i])
                                tp_price = entry_price + (entry_price - sl_price) * rr_ratio if signal == 1 else entry_price - (sl_price - entry_price) * rr_ratio
                                if rr_ratio <= 0: tp_price = 0.0
                                
                                meta_kwargs = {k.replace("meta_", ""): float(signals[k][i]) for k in meta_keys}
                                
                                if signal == 1 and self.direction in ["both", "long_only"]:
                                    self._open_position(direction="long", entry_price=entry_price, entry_time=t_time, sl_price=sl_price, tp_price=tp_price, indicator_value=float(indicator_val_arr[i]), **meta_kwargs)
                                    trade_opened_this_candle = True
                                elif signal == -1 and self.direction in ["both", "short_only"]:
                                    self._open_position(direction="short", entry_price=entry_price, entry_time=t_time, sl_price=sl_price, tp_price=tp_price, indicator_value=float(indicator_val_arr[i]), **meta_kwargs)
                                    trade_opened_this_candle = True
                        else:
                            signal = int(entry_signal_arr[i-1])
                            
                            # Do not double-execute intrabar breakout signals from the previous candle
                            if exact_entry_price_arr is not None and exact_entry_price_arr[i-1] > 0:
                                signal = 0
                                
                            if not is_within_entry_window:
                                signal = 0
                                
                            if signal != 0:
                                meta_kwargs = {k.replace("meta_", ""): float(signals[k][i-1]) for k in meta_keys}
                                
                            if signal == 1 and self.direction in ["both", "long_only"]:
                                entry_price = t_open
                                sl_price = float(sl_price_long_arr[i-1])
                                tp_price = entry_price + (entry_price - sl_price) * rr_ratio if rr_ratio > 0 else 0.0
                                self._open_position(direction="long", entry_price=entry_price, entry_time=t_time, sl_price=sl_price, tp_price=tp_price, indicator_value=float(indicator_val_arr[i-1]), **meta_kwargs)
                                trade_opened_this_candle = True
                            elif signal == -1 and self.direction in ["both", "short_only"]:
                                entry_price = t_open
                                sl_price = float(sl_price_short_arr[i-1])
                                tp_price = entry_price - (sl_price - entry_price) * rr_ratio if rr_ratio > 0 else 0.0
                                self._open_position(direction="short", entry_price=entry_price, entry_time=t_time, sl_price=sl_price, tp_price=tp_price, indicator_value=float(indicator_val_arr[i-1]), **meta_kwargs)
                                trade_opened_this_candle = True
                                
                    # 3. Same-Candle Exit Evaluation (Fixing the blind spot)
                    if self.open_trade and trade_opened_this_candle:
                        # Cannot use signal_exit for the entry candle, only SL/TP matters
                        await self._evaluate_candle_exits(
                            client, symbol, timeframe, t_time, t_open, t_high, t_low, t_close, 
                            indicator_val_arr[i], False
                        )
            finally:
                await client.close()
                # HARD MEMORY LIMIT: Stop simulating if trades exceed 50,000
                if len(self.trade_log) >= 50000:
                    logger.warning(f"[BT-ENGINE] Trade limit reached (50,000). Stopping simulation early to save memory.")
                    self._abort = True
                    break
                            
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
                exit_price=float(closes[-1]),
                exit_time=int(times[-1]),
                reason="End of Data",
                indicator_value=float(indicator_val_arr[-1]) if 'indicator_val_arr' in locals() else 0.0
            )

        logger.info(f"[BT-ENGINE] Backtest complete. Total Trades: {len(self.trade_log)}. Final Balance: ${self.balance:.2f}")

        return {
            "trade_log": self.trade_log,
            "equity_curve": self.equity_curve,
            "final_balance": self.balance,
            "total_candles": processed_rows
        }



    async def _resolve_intrabar_ambiguity(self, client, symbol, timeframe, t_time, t_open, sl_price, tp_price, direction):
        from api.market_data import get_candles
        from config.constants import TIMEFRAME_SECONDS
        import asyncio
        
        # Don't micro-fetch if we are already on 1m
        if timeframe == "1m":
            return None # fallback to pessimistic
            
        tf_secs = TIMEFRAME_SECONDS.get(timeframe, 900)
        end_time = t_time + tf_secs
        
        try:
            # Wait 0.2s to respect API rate limits
            await asyncio.sleep(0.2)
            candles = await get_candles(client, symbol, "1m", start_time=t_time, end_time=end_time)
            if not candles:
                return None
                
            for c in candles:
                c_low = float(c["low"])
                c_high = float(c["high"])
                
                if direction == "long":
                    if c_low <= sl_price:
                        return {"price": sl_price, "reason": "Stop Loss"}
                    if tp_price > 0 and c_high >= tp_price:
                        return {"price": tp_price, "reason": "Take Profit"}
                elif direction == "short":
                    if c_high >= sl_price:
                        return {"price": sl_price, "reason": "Stop Loss"}
                    if tp_price > 0 and c_low <= tp_price:
                        return {"price": tp_price, "reason": "Take Profit"}
                        
        except Exception as e:
            logger.error(f"[BT-ENGINE] Error in micro-fetch: {e}")
            
        return None

    async def _resolve_intrabar_entry_ambiguity(self, client, symbol, timeframe, t_time, long_price, short_price):
        from api.market_data import get_candles
        from config.constants import TIMEFRAME_SECONDS
        import asyncio
        
        # Don't micro-fetch if we are already on 1m
        if timeframe == "1m":
            return "long" # fallback bias if already 1m
            
        tf_secs = TIMEFRAME_SECONDS.get(timeframe, 900)
        end_time = t_time + tf_secs
        
        try:
            # Wait 0.2s to respect API rate limits
            await asyncio.sleep(0.2)
            candles = await get_candles(client, symbol, "1m", start_time=t_time, end_time=end_time)
            if not candles:
                return "long"
                
            for c in candles:
                c_low = float(c["low"])
                c_high = float(c["high"])
                
                hit_long = c_high >= long_price
                hit_short = c_low <= short_price
                
                if hit_long and hit_short:
                    return "long"
                if hit_long:
                    return "long"
                if hit_short:
                    return "short"
                    
        except Exception as e:
            logger.error(f"[BT-ENGINE] Error in entry micro-fetch: {e}")
            
        return "long"

    async def _evaluate_candle_exits(self, client, symbol, timeframe, t_time, t_open, t_high, t_low, t_close, indicator_val_prev, signal_exit):
        """
        Evaluate if the open trade should exit on this candle.
        Returns True if the trade was closed, False otherwise.
        """
        if not self.open_trade:
            return False
            
        direction = self.open_trade["direction"]
        sl_price = self.open_trade["sl_price"]
        tp_price = self.open_trade.get("tp_price", 0)
        
        exit_triggered = False
        exit_price = 0.0
        exit_reason = ""
        ambiguity = False
        
        if direction == "long":
            sl_hit = t_low <= sl_price
            tp_hit = tp_price > 0 and t_high >= tp_price
            
            if signal_exit:
                exit_triggered = True
                exit_price = t_open
                exit_reason = "Signal Exit"
            elif sl_hit and tp_hit:
                ambiguity = True
            elif sl_hit:
                exit_triggered = True
                exit_price = t_open if t_open < sl_price else sl_price
                exit_reason = "Stop Loss"
            elif tp_hit:
                exit_triggered = True
                exit_price = t_open if t_open > tp_price else tp_price
                exit_reason = "Take Profit"
                
        elif direction == "short":
            sl_hit = t_high >= sl_price
            tp_hit = tp_price > 0 and t_low <= tp_price
            
            if signal_exit:
                exit_triggered = True
                exit_price = t_open
                exit_reason = "Signal Exit"
            elif sl_hit and tp_hit:
                ambiguity = True
            elif sl_hit:
                exit_triggered = True
                exit_price = t_open if t_open > sl_price else sl_price
                exit_reason = "Stop Loss"
            elif tp_hit:
                exit_triggered = True
                exit_price = t_open if t_open < tp_price else tp_price
                exit_reason = "Take Profit"
                
        if ambiguity:
            # 1m Micro-fetch resolution
            resolved_exit = await self._resolve_intrabar_ambiguity(client, symbol, timeframe, t_time, t_open, sl_price, tp_price, direction)
            if resolved_exit:
                exit_triggered = True
                exit_price = resolved_exit["price"]
                exit_reason = resolved_exit["reason"]
            else:
                # Fallback to pessimistic rule
                exit_triggered = True
                exit_reason = "Stop Loss"
                if direction == "long":
                    exit_price = t_open if t_open < sl_price else sl_price
                else:
                    exit_price = t_open if t_open > sl_price else sl_price

        if exit_triggered:
            self._close_position(
                exit_price=float(exit_price),
                exit_time=t_time,
                reason=exit_reason,
                indicator_value=float(indicator_val_prev)
            )
            return True
            
        return False

    def _open_position(self, direction: str, entry_price: float, entry_time: int, sl_price: float, tp_price: float, indicator_value: float, **kwargs):
        """Open a mock position with exact margin and lot size math."""
        quantity = self.lot_size
        position_size_usd = entry_price * quantity * self.contract_multiplier
        
        # Initial Margin = Position Notional / Leverage
        initial_margin = position_size_usd / self.leverage if self.leverage > 0 else position_size_usd
        
        # Max Margin Required = Initial Margin + Max Potential Loss (Distance to SL)
        # Assumes the user wants to avoid liquidation before the SL triggers
        sl_distance = abs(entry_price - sl_price)
        max_potential_loss = sl_distance * quantity * self.contract_multiplier
        max_margin_required = initial_margin + max_potential_loss
        
        fee = position_size_usd * PAPER_TRADE_TAKER_FEE
        self.balance -= fee
        
        self.open_trade = {
            "direction": direction,
            "entry_price": entry_price,
            "entry_time": entry_time,
            "sl_price": sl_price,
            "tp_price": tp_price,
            "quantity": quantity,
            "position_size_usd": position_size_usd,
            "initial_margin": initial_margin,
            "max_margin_required": max_margin_required,
            "indicator_value": indicator_value,
            "entry_fee": fee,
            **kwargs
        }

    def _close_position(self, exit_price: float, exit_time: int, reason: str, indicator_value: float):
        """Close the mock position, calculate PnL, deduct fees, and record the trade."""
        direction = self.open_trade["direction"]
        entry_price = self.open_trade["entry_price"]
        quantity = self.open_trade["quantity"]
        position_size_usd = self.open_trade["position_size_usd"]
        
        # Calculate Gross PnL
        if direction == "long":
            gross_pnl = (exit_price - entry_price) * quantity * self.contract_multiplier
        else:
            gross_pnl = (entry_price - exit_price) * quantity * self.contract_multiplier
            
        # Calculate Exit Fees (Assuming Market/Taker on exits too to be conservative)
        exit_notional = exit_price * quantity * self.contract_multiplier
        exit_fee = exit_notional * PAPER_TRADE_TAKER_FEE
        
        total_fee = self.open_trade["entry_fee"] + exit_fee
        
        # Balance was already deducted entry_fee in _open_position
        # So we only add gross_pnl and subtract exit_fee from balance
        self.balance += (gross_pnl - exit_fee)
        self.equity_curve.append(self.balance)
        
        net_pnl = gross_pnl - total_fee
        
        # Create Trade Record
        trade_record = {
            "entry_time": self.open_trade["entry_time"],
            "exit_time": exit_time,
            "direction": direction,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "quantity": quantity,
            "notional_size": position_size_usd,
            "initial_margin": self.open_trade["initial_margin"],
            "max_margin_required": self.open_trade["max_margin_required"],
            "gross_pnl": gross_pnl,
            "fee_paid": total_fee,
            "pnl": net_pnl,
            "pnl_pct": (net_pnl / position_size_usd) * 100.0,
            "roe_pct": (net_pnl / self.open_trade["initial_margin"]) * 100.0 if self.open_trade["initial_margin"] > 0 else 0.0,
            "exit_reason": reason,
            "entry_indicator": self.open_trade["indicator_value"],
            "exit_indicator": indicator_value
        }
        
        # Add dynamic metadata
        base_keys = {
            "direction", "entry_price", "entry_time", "sl_price", "tp_price", "quantity",
            "position_size_usd", "initial_margin", "max_margin_required", "indicator_value", "entry_fee"
        }
        for k, v in self.open_trade.items():
            if k not in base_keys:
                trade_record[k] = v
                
        self.trade_log.append(trade_record)
        
        self.open_trade = None
