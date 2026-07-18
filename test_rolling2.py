import pandas as pd
import numpy as np

trade_log = [
    {"exit_time": 1700000000 + i * 86400, "pnl": float(i)} for i in range(200)
]
initial_balance = 1000.0

df = pd.DataFrame({
    'exit_time': [t['exit_time'] for t in trade_log],
    'pnl': [t['pnl'] for t in trade_log]
})
df['date'] = pd.to_datetime(df['exit_time'], unit='s', utc=True).dt.floor('D')

df['cumulative_pnl'] = df['pnl'].cumsum()
df['balance'] = initial_balance + df['cumulative_pnl']

daily_balances = df.groupby('date')['balance'].last()

start_date = daily_balances.index.min()
end_date = daily_balances.index.max()

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

print("Weekly:", get_rolling(7))
print("Monthly:", get_rolling(30))
