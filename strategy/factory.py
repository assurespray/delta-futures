from typing import Dict, Any
from strategy.dual_supertrend import DualSuperTrendStrategy
from strategy.single_supertrend import SingleSuperTrendStrategy
from strategy.range_breakout import RangeBreakoutStrategy

class StrategyFactory:
    @staticmethod
    def get_strategy(strategy_type: str, params: Dict[str, Any] = None):
        if strategy_type == "single_supertrend":
            return SingleSuperTrendStrategy(params)
        elif strategy_type == "range_breakout_lazybear":
            return RangeBreakoutStrategy(params)
        else:
            return DualSuperTrendStrategy(params)
