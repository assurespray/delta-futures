"""P&L and Fee Accounting Engine."""
from typing import Dict, Any, Tuple
from utils.market_utils import get_contract_multiplier

class PnLEngine:
    def __init__(self, maker_fee_pct: float = 0.0002, taker_fee_pct: float = 0.0005):
        # Default Delta Exchange Futures Fees: 0.02% Maker, 0.05% Taker
        self.maker_fee_pct = maker_fee_pct
        self.taker_fee_pct = taker_fee_pct

    def calculate_notional(self, price: float, quantity: int, asset: str) -> float:
        """Calculate exact notional value based on contract multiplier."""
        multiplier = get_contract_multiplier(asset)
        return price * quantity * multiplier

    def calculate_fee(self, price: float, quantity: int, asset: str, is_maker: bool = False) -> float:
        """Calculate theoretical fee for a given trade."""
        notional = self.calculate_notional(price, quantity, asset)
        fee_rate = self.maker_fee_pct if is_maker else self.taker_fee_pct
        return notional * fee_rate

    def calculate_trade_pnl(self, entry_price: float, exit_price: float, 
                           quantity: int, asset: str, direction: str,
                           actual_entry_fee: float = None, actual_exit_fee: float = None) -> Tuple[float, float, float]:
        """
        Calculates Gross PnL, Total Fees, and Net PnL.
        If actual fees from exchange are not provided, falls back to theoretical taker fees.
        """
        notional_entry = self.calculate_notional(entry_price, quantity, asset)
        notional_exit = self.calculate_notional(exit_price, quantity, asset)
        
        if direction.lower() == "long":
            gross_pnl = notional_exit - notional_entry
        else:
            gross_pnl = notional_entry - notional_exit
            
        entry_fee = actual_entry_fee if actual_entry_fee is not None else self.calculate_fee(entry_price, quantity, asset, is_maker=False)
        exit_fee = actual_exit_fee if actual_exit_fee is not None else self.calculate_fee(exit_price, quantity, asset, is_maker=False)
        
        total_fees = entry_fee + exit_fee
        net_pnl = gross_pnl - total_fees
        
        return gross_pnl, total_fees, net_pnl

pnl_engine = PnLEngine()
