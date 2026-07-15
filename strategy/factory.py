from typing import Dict, Any
from strategy.base import BaseStrategy
from strategy.dual_supertrend import DualSuperTrendStrategy
from strategy.single_supertrend import SingleSuperTrendStrategy
from strategy.range_breakout import RangeBreakoutStrategy
from strategy.donchian_breakout import DonchianBreakoutStrategy
from strategy.ohlc_breakout import OHLCBreakoutStrategy
from strategy.evasive_supertrend import EvasiveSuperTrendStrategy
from strategy.recovery_supertrend import RecoverySuperTrendStrategy


class StrategyFactory:
    @staticmethod
    def get_strategy(strategy_type: str, params: Dict[str, Any] = None) -> BaseStrategy:
        if strategy_type == "single_supertrend":
            return SingleSuperTrendStrategy(params)
        elif strategy_type == "range_breakout_lazybear":
            return RangeBreakoutStrategy(params)
        elif strategy_type == "donchian":
            return DonchianBreakoutStrategy(params)
        elif strategy_type == "ohlc_breakout":
            return OHLCBreakoutStrategy(params)
        elif strategy_type == "evasive_supertrend":
            return EvasiveSuperTrendStrategy(params)
        elif strategy_type == "recovery_supertrend":
            return RecoverySuperTrendStrategy(params)
        else:
            return DualSuperTrendStrategy(params)
