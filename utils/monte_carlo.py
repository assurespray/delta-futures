"""
Monte Carlo & Curve Fitting Analytics

Provides institutional-grade risk analysis:
1. Monte Carlo Permutations (Risk of Ruin, 95th/99th Percentile Drawdowns)
2. Curve Fitting Analysis (R-Squared of Equity Curve)
3. Sharpe and Sortino Ratios
"""

import logging
import numpy as np
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

def run_advanced_analytics(trade_log: List[Dict], initial_balance: float) -> Dict[str, float]:
    """
    Run Monte Carlo and Statistical analysis on a backtest trade log.
    
    Args:
        trade_log: List of executed trades.
        initial_balance: Starting portfolio balance.
        
    Returns:
        Dict of advanced statistical metrics.
    """
    if not trade_log or len(trade_log) < 5:
        logger.warning("[BT-ANALYTICS] Not enough trades for advanced analytics.")
        return _empty_analytics()

    pnls = [t["pnl"] for t in trade_log]
    pnl_pcts = [t.get("pnl_pct", (t["pnl"] / 1000.0) * 100.0) for t in trade_log]  # fallback to standard $1k lot size
    
    # 1. Curve Fitting Check (R-Squared)
    r_squared = _calculate_r_squared(pnls, initial_balance)
    
    # 2. Risk-Adjusted Returns (Sharpe & Sortino)
    sharpe, sortino = _calculate_ratios(pnl_pcts)
    
    # 3. Monte Carlo Simulation (1,000 Iterations)
    mc_results = _run_monte_carlo(pnls, initial_balance, iterations=1000, ruin_threshold_pct=0.50)
    
    return {
        "r_squared": r_squared,
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "monte_carlo_risk_of_ruin": mc_results["risk_of_ruin_pct"],
        "monte_carlo_max_dd_95": mc_results["max_dd_95"],
        "monte_carlo_max_dd_99": mc_results["max_dd_99"],
    }


def _calculate_r_squared(pnls: List[float], initial_balance: float) -> float:
    """
    Calculate the R-Squared of the Equity Curve.
    A value close to 1.0 means smooth, robust growth.
    A low value means volatile, potentially curve-fitted jumps.
    """
    try:
        # Rebuild equity curve
        equity_curve = [initial_balance]
        current = initial_balance
        for p in pnls:
            current += p
            equity_curve.append(current)
            
        y = np.array(equity_curve)
        x = np.arange(len(y))
        
        # Fit a linear regression line (degree 1)
        slope, intercept = np.polyfit(x, y, 1)
        trendline = slope * x + intercept
        
        # Calculate R-squared
        ss_res = np.sum((y - trendline) ** 2)
        ss_tot = np.sum((y - np.mean(y)) ** 2)
        
        if ss_tot == 0:
            return 1.0
            
        r2 = 1 - (ss_res / ss_tot)
        
        # Ensure it stays within [0.0, 1.0] despite floating point errors
        return max(0.0, min(1.0, r2))
    except Exception as e:
        logger.error(f"[BT-ANALYTICS] Error calculating R2: {e}")
        return 0.0


def _calculate_ratios(pnl_pcts: List[float], risk_free_rate: float = 0.0) -> tuple:
    """
    Calculate Per-Trade Sharpe and Sortino Ratios.
    Note: These are per-trade ratios, not annualized.
    """
    try:
        returns = np.array(pnl_pcts) / 100.0  # Convert to decimals
        
        if len(returns) == 0:
            return 0.0, 0.0
            
        avg_return = np.mean(returns) - risk_free_rate
        std_dev = np.std(returns)
        
        # Sharpe
        sharpe = avg_return / std_dev if std_dev != 0 else 0.0
        
        # Sortino (only use negative returns for standard deviation)
        negative_returns = returns[returns < 0]
        downside_std_dev = np.std(negative_returns) if len(negative_returns) > 0 else 0.0
        
        sortino = avg_return / downside_std_dev if downside_std_dev != 0 else float('inf') if avg_return > 0 else 0.0
        
        return float(sharpe), float(sortino)
    except Exception as e:
        logger.error(f"[BT-ANALYTICS] Error calculating Ratios: {e}")
        return 0.0, 0.0


def _run_monte_carlo(pnls: List[float], initial_balance: float, iterations: int = 1000, ruin_threshold_pct: float = 0.50) -> Dict[str, float]:
    """
    Run Monte Carlo permutation tests.
    Shuffles the trade order randomly to simulate alternate realities.
    
    Args:
        pnls: List of trade PnLs.
        initial_balance: Starting balance.
        iterations: Number of simulated alternate realities.
        ruin_threshold_pct: Drop % that is considered a blown account (e.g. 0.50 = 50% loss)
    """
    try:
        max_drawdowns = []
        ruined_count = 0
        ruin_balance = initial_balance * (1.0 - ruin_threshold_pct)
        
        # Convert to numpy array for faster shuffling
        pnls_arr = np.array(pnls)
        
        for _ in range(iterations):
            # Shuffle in place
            np.random.shuffle(pnls_arr)
            
            running_balance = initial_balance
            peak_balance = initial_balance
            max_dd_pct = 0.0
            ruined = False
            
            # Fast numpy cumulative sum to build equity curve
            cumulative = initial_balance + np.cumsum(pnls_arr)
            
            # Use numpy functions for fast DD calculation
            peaks = np.maximum.accumulate(cumulative)
            drawdowns = (peaks - cumulative) / peaks
            
            max_dd_pct = np.max(drawdowns) * 100.0
            max_drawdowns.append(max_dd_pct)
            
            # Check for ruin
            if np.any(cumulative <= ruin_balance):
                ruined_count += 1
                
        # Calculate Percentiles
        max_drawdowns.sort()
        idx_95 = int(iterations * 0.95)
        idx_99 = int(iterations * 0.99)
        
        # Safeguard indices
        idx_95 = min(idx_95, len(max_drawdowns) - 1)
        idx_99 = min(idx_99, len(max_drawdowns) - 1)
        
        return {
            "risk_of_ruin_pct": (ruined_count / iterations) * 100.0,
            "max_dd_95": float(max_drawdowns[idx_95]),
            "max_dd_99": float(max_drawdowns[idx_99])
        }
        
    except Exception as e:
        logger.error(f"[BT-ANALYTICS] Error running Monte Carlo: {e}")
        return {
            "risk_of_ruin_pct": 0.0,
            "max_dd_95": 0.0,
            "max_dd_99": 0.0
        }


def _empty_analytics() -> Dict[str, float]:
    """Return zeroed data if not enough trades."""
    return {
        "r_squared": 0.0,
        "sharpe_ratio": 0.0,
        "sortino_ratio": 0.0,
        "monte_carlo_risk_of_ruin": 0.0,
        "monte_carlo_max_dd_95": 0.0,
        "monte_carlo_max_dd_99": 0.0,
    }
