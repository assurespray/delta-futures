"""
Quantitative Metrics Calculator for Backtesting

Calculates 18+ institutional-grade performance metrics from a raw trade log.
Fully decoupled from the trading engine to allow isolated unit testing.
"""

import logging
from typing import List, Dict, Any
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def calculate_rolling_stats(trade_log: List[Dict], initial_balance: float) -> Dict[str, Any]:
    """Calculate Weekly and Monthly rolling returns and consistency."""
    if not trade_log:
        return {"weekly": None, "monthly": None}
    
    try:
        # Build daily equity series
        df = pd.DataFrame(trade_log)
        df['date'] = pd.to_datetime(df['exit_time'], unit='s', utc=True).dt.floor('D')
        
        df['cumulative_pnl'] = df['pnl'].cumsum()
        df['balance'] = initial_balance + df['cumulative_pnl']
        
        # Last balance of each day
        daily_balances = df.groupby('date')['balance'].last()
        
        start_date = daily_balances.index.min()
        end_date = daily_balances.index.max()
        
        if pd.isna(start_date) or pd.isna(end_date):
            return {"weekly": None, "monthly": None}
            
        idx = pd.date_range(start_date, end_date)
        daily_balances = daily_balances.reindex(idx).ffill()
        daily_balances = daily_balances.fillna(initial_balance)
        
        def get_rolling(days):
            if len(daily_balances) <= days:
                return None
            rolling_returns = daily_balances.pct_change(periods=days) * 100.0
            rolling_returns = rolling_returns.dropna()
            
            if len(rolling_returns) == 0:
                return None
                
            wins = (rolling_returns > 0).sum()
            total = len(rolling_returns)
            
            return {
                "best": float(rolling_returns.max()),
                "worst": float(rolling_returns.min()),
                "avg": float(rolling_returns.mean()),
                "win_rate": float((wins / total) * 100.0) if total > 0 else 0.0
            }
            
        return {
            "weekly": get_rolling(7),
            "monthly": get_rolling(30)
        }
    except Exception as e:
        logger.error(f"[BT-METRICS] Error calculating rolling stats: {e}")
        return {"weekly": None, "monthly": None}

def calculate_metrics(trade_log: List[Dict], initial_balance: float) -> Dict[str, Any]:
    """
    Calculate comprehensive performance metrics from a list of executed trades.
    
    Args:
        trade_log: List of dictionaries representing executed trades.
                   Expected keys: 'pnl', 'exit_time'
        initial_balance: Starting portfolio balance (used for % math).
        
    Returns:
        Dict containing all advanced metrics.
    """
    if not trade_log:
        logger.warning("[BT-METRICS] Cannot calculate metrics on empty trade log.")
        return _empty_metrics(initial_balance)
        
    num_trades = len(trade_log)
    
    # Core lists for aggregations
    pnls = [t["pnl"] for t in trade_log]
    winning_trades = [pnl for pnl in pnls if pnl > 0]
    losing_trades = [pnl for pnl in pnls if pnl <= 0]  # Consider breakeven as loss/neutral
    
    overall_profit = sum(pnls)
    overall_profit_pct = (overall_profit / initial_balance) * 100.0
    
    # Averages
    avg_profit_per_trade = overall_profit / num_trades if num_trades > 0 else 0.0
    avg_win = sum(winning_trades) / len(winning_trades) if winning_trades else 0.0
    avg_loss = sum(losing_trades) / len(losing_trades) if losing_trades else 0.0
    
    # Win/Loss Percentages
    win_pct = (len(winning_trades) / num_trades) * 100.0 if num_trades > 0 else 0.0
    loss_pct = (len(losing_trades) / num_trades) * 100.0 if num_trades > 0 else 0.0
    
    # Extremes
    max_profit_single = max(pnls) if winning_trades else 0.0
    max_loss_single = min(pnls) if losing_trades else 0.0
    
    # Streaks
    max_win_streak = 0
    max_loss_streak = 0
    current_win_streak = 0
    current_loss_streak = 0
    
    for pnl in pnls:
        if pnl > 0:
            current_win_streak += 1
            current_loss_streak = 0
            if current_win_streak > max_win_streak:
                max_win_streak = current_win_streak
        else:
            current_loss_streak += 1
            current_win_streak = 0
            if current_loss_streak > max_loss_streak:
                max_loss_streak = current_loss_streak
                
    # Ratios
    # Reward to Risk: Avg Win / abs(Avg Loss)
    reward_to_risk = avg_win / abs(avg_loss) if avg_loss != 0 else float('inf')
    
    # Expectancy Ratio: (Win% * AvgWin - Loss% * abs(AvgLoss)) / abs(AvgLoss)
    win_rate_dec = win_pct / 100.0
    loss_rate_dec = loss_pct / 100.0
    if avg_loss != 0:
        expectancy_ratio = ((win_rate_dec * avg_win) - (loss_rate_dec * abs(avg_loss))) / abs(avg_loss)
    else:
        expectancy_ratio = float('inf') if avg_win > 0 else 0.0
        
    profit_factor = sum(winning_trades) / abs(sum(losing_trades)) if sum(losing_trades) != 0 else float('inf')

    # Drawdown Calculation (Time-Series)
    # We rebuild the equity curve to track exact timestamps of peaks and troughs
    running_balance = initial_balance
    peak_balance = initial_balance
    peak_time = trade_log[0]["exit_time"]
    
    max_drawdown = 0.0  # In USD (negative number or absolute, we'll store positive absolute internally)
    max_drawdown_pct = 0.0
    
    current_dd_start_time = peak_time
    max_dd_start_time = peak_time
    max_dd_end_time = peak_time
    
    current_dd_trades = 0
    max_dd_trades = 0
    
    for trade in trade_log:
        running_balance += trade["pnl"]
        
        # New Peak Hit
        if running_balance >= peak_balance:
            peak_balance = running_balance
            peak_time = trade["exit_time"]
            current_dd_trades = 0
            current_dd_start_time = peak_time
        else:
            # We are in a drawdown
            current_dd_trades += 1
            dd_amount = peak_balance - running_balance
            dd_pct = (dd_amount / peak_balance) * 100.0
            
            # Did we hit a new max drawdown?
            if dd_pct > max_drawdown_pct:
                max_drawdown_pct = dd_pct
                max_drawdown = dd_amount
                max_dd_start_time = current_dd_start_time
                max_dd_end_time = trade["exit_time"]
                max_dd_trades = current_dd_trades

    # Format Drawdown Dates
    try:
        dt_start = datetime.fromtimestamp(max_dd_start_time, tz=timezone.utc).strftime("%d/%m/%Y")
        dt_end = datetime.fromtimestamp(max_dd_end_time, tz=timezone.utc).strftime("%d/%m/%Y")
        duration_days = max(1, int((max_dd_end_time - max_dd_start_time) / 86400))
    except Exception:
        dt_start, dt_end, duration_days = "N/A", "N/A", 0

    return_over_max_dd = overall_profit_pct / max_drawdown_pct if max_drawdown_pct > 0 else float('inf')

    # Calculate rolling stats and margin requirements
    rolling_stats = calculate_rolling_stats(trade_log, initial_balance)
    
    initial_margins = [t.get("initial_margin", 0) for t in trade_log]
    max_margins = [t.get("max_margin_required", 0) for t in trade_log]
    avg_initial_margin = sum(initial_margins) / len(initial_margins) if initial_margins else 0.0
    avg_max_margin = sum(max_margins) / len(max_margins) if max_margins else 0.0
    peak_margin = max(max_margins) if max_margins else 0.0

    return {
        "overall_profit": overall_profit,
        "overall_profit_pct": overall_profit_pct,
        "avg_initial_margin": avg_initial_margin,
        "avg_max_margin_required": avg_max_margin,
        "peak_margin_required": peak_margin,
        "rolling_stats": rolling_stats,
        "num_trades": num_trades,
        "avg_profit_per_trade": avg_profit_per_trade,
        "win_pct": win_pct,
        "loss_pct": loss_pct,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "max_profit_single": max_profit_single,
        "max_loss_single": max_loss_single,
        
        "max_drawdown": -max_drawdown,  # Return as negative to match requested layout
        "max_drawdown_pct": max_drawdown_pct,
        "max_drawdown_duration_days": duration_days,
        "max_drawdown_start": dt_start,
        "max_drawdown_end": dt_end,
        "max_trades_in_drawdown": max_dd_trades,
        
        "return_over_max_dd": return_over_max_dd,
        "reward_to_risk": reward_to_risk,
        "expectancy_ratio": expectancy_ratio,
        "profit_factor": profit_factor,
        
        "max_win_streak": max_win_streak,
        "max_loss_streak": max_loss_streak,
        
        "final_balance": running_balance
    }


def _empty_metrics(initial_balance: float) -> Dict[str, Any]:
    """Return zeroed metrics for an empty trade log."""
    return {
        "overall_profit": 0.0,
        "overall_profit_pct": 0.0,
        "avg_initial_margin": 0.0,
        "avg_max_margin_required": 0.0,
        "peak_margin_required": 0.0,
        "rolling_stats": {
            "weekly": None,
            "monthly": None,
        },
        "num_trades": 0,
        "avg_profit_per_trade": 0.0,
        "win_pct": 0.0,
        "loss_pct": 0.0,
        "avg_win": 0.0,
        "avg_loss": 0.0,
        "max_profit_single": 0.0,
        "max_loss_single": 0.0,
        "max_drawdown": 0.0,
        "max_drawdown_pct": 0.0,
        "max_drawdown_duration_days": 0,
        "max_drawdown_start": "N/A",
        "max_drawdown_end": "N/A",
        "max_trades_in_drawdown": 0,
        "return_over_max_dd": 0.0,
        "reward_to_risk": 0.0,
        "expectancy_ratio": 0.0,
        "profit_factor": 0.0,
        "max_win_streak": 0,
        "max_loss_streak": 0,
        "final_balance": initial_balance
    }
