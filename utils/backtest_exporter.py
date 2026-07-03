"""
Chart & CSV Exporter for Backtesting

Generates professional visual Equity Curve charts via matplotlib,
and exports Trade Logs to CSV files for deep "Truth Check" verification.
"""

import os
import csv
import logging
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from typing import List, Dict
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

# IST timezone definition
IST = timezone(timedelta(hours=5, minutes=30))

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "cache")

def generate_equity_curve_chart(trade_log: List[Dict], initial_balance: float, symbol: str, timeframe: str) -> str:
    """
    Generate an Equity Curve chart image.
    
    Args:
        trade_log: List of executed trades containing 'exit_time' and 'pnl'.
        initial_balance: Starting portfolio balance.
        symbol: e.g. "BTCUSD"
        timeframe: e.g. "1m"
        
    Returns:
        Absolute path to the saved .png image.
    """
    os.makedirs(CACHE_DIR, exist_ok=True)
    filename = f"equity_curve_{symbol}_{timeframe}_{int(datetime.utcnow().timestamp())}.png"
    filepath = os.path.join(CACHE_DIR, filename)
    
    try:
        if not trade_log:
            raise ValueError("Trade log is empty")
            
        # Rebuild times and balances
        times = [datetime.fromtimestamp(trade_log[0]["entry_time"], tz=IST)]
        balances = [initial_balance]
        
        current_balance = initial_balance
        for t in trade_log:
            current_balance += t["pnl"]
            dt = datetime.fromtimestamp(t["exit_time"], tz=IST)
            times.append(dt)
            balances.append(current_balance)
            
        # Plotting styling
        plt.style.use('dark_background')
        fig, ax = plt.subplots(figsize=(10, 5), dpi=150)
        
        # Draw the curve
        color = '#00FF7F' if current_balance >= initial_balance else '#FF4040'
        ax.plot(times, balances, color=color, linewidth=2, label='Equity')
        
        # Fill under the curve
        ax.fill_between(times, balances, min(balances) * 0.99, color=color, alpha=0.1)
        
        # Formatting
        ax.set_title(f"Backtest Equity Curve: {symbol} ({timeframe})", fontsize=14, pad=15)
        ax.set_ylabel("Portfolio Balance (USD)", fontsize=11)
        ax.grid(True, linestyle='--', alpha=0.3)
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
        plt.xticks(rotation=45)
        
        # Add a baseline
        ax.axhline(initial_balance, color='white', linestyle='--', alpha=0.5, label='Initial Capital')
        
        ax.legend(loc='upper left')
        
        # Tight layout to prevent label cutoff
        plt.tight_layout()
        
        # Save and close
        plt.savefig(filepath, bbox_inches='tight')
        plt.close(fig)
        
        logger.info(f"[BT-EXPORTER] Saved equity curve chart to {filepath}")
        return filepath
        
    except Exception as e:
        logger.error(f"[BT-EXPORTER] Error generating chart: {e}")
        # Make sure to close figure if it failed
        plt.close('all')
        return ""

def generate_trade_log_csv(trade_log: List[Dict], symbol: str, timeframe: str) -> str:
    """
    Generate a detailed TradeLog CSV for "Truth Checking".
    
    Args:
        trade_log: The raw trade log from the simulation engine.
        symbol: e.g. "BTCUSD"
        timeframe: e.g. "1m"
        
    Returns:
        Absolute path to the saved .csv file.
    """
    os.makedirs(CACHE_DIR, exist_ok=True)
    filename = f"TradeLog_{symbol}_{timeframe}_{int(datetime.utcnow().timestamp())}.csv"
    filepath = os.path.join(CACHE_DIR, filename)
    
    try:
        if not trade_log:
            logger.warning("[BT-EXPORTER] Trade log is empty, cannot generate CSV.")
            return ""
            
        columns = [
            "Trade #", 
            "Direction",
            "Entry Time (IST)", 
            "Exit Time (IST)", 
            "Entry Price", 
            "Exit Price", 
            "PnL (USD)", 
            "PnL (%)", 
            "Exit Reason", 
            "Entry Indicator Value",
            "Exit Indicator Value"
        ]
        
        with open(filepath, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(columns)
            
            for idx, t in enumerate(trade_log, 1):
                entry_dt = datetime.fromtimestamp(t["entry_time"], tz=IST).strftime('%Y-%m-%d %H:%M:%S')
                exit_dt = datetime.fromtimestamp(t["exit_time"], tz=IST).strftime('%Y-%m-%d %H:%M:%S')
                
                writer.writerow([
                    idx,
                    t["direction"].upper(),
                    entry_dt,
                    exit_dt,
                    f"{t['entry_price']:.5f}",
                    f"{t['exit_price']:.5f}",
                    f"{t['pnl']:.2f}",
                    f"{t.get('pnl_pct', 0.0):.2f}%",
                    t["exit_reason"],
                    f"{t.get('entry_indicator', 0):.5f}",
                    f"{t.get('exit_indicator', 0):.5f}"
                ])
                
        logger.info(f"[BT-EXPORTER] Saved Trade Log CSV to {filepath}")
        return filepath
        
    except Exception as e:
        logger.error(f"[BT-EXPORTER] Error generating trade log CSV: {e}")
        return ""
