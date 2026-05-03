"""Polling utilities to capture exact order fills and fees from Delta Exchange."""
import asyncio
import logging
from typing import Dict, Any, Optional
from api.orders import get_order_history

logger = logging.getLogger(__name__)

async def get_exact_fill_details(client, order_id: str, product_id: int, max_wait_seconds: int = 15) -> Optional[Dict[str, Any]]:
    """
    Polls the order history endpoint to capture exact average_fill_price and fee.
    Uses exponential backoff up to max_wait_seconds.
    Returns None if the order is not found or not filled within the timeframe.
    """
    # Polling intervals in seconds
    delays = [1, 2, 3, 4, 5]
    total_wait = 0

    for delay in delays:
        try:
            history = await get_order_history(client, product_id, page_size=20)
            if history:
                for order in history:
                    if str(order.get("id")) == str(order_id):
                        state = order.get("state", "").lower()
                        if state in ["closed", "filled"]:
                            return {
                                "fill_price": float(order.get("average_fill_price", 0) or 0),
                                "fee": float(order.get("fee", 0) or 0),
                                "state": state
                            }
                        elif state in ["cancelled", "rejected"]:
                            # Order is terminal but not filled
                            return {
                                "fill_price": 0.0,
                                "fee": 0.0,
                                "state": state
                            }
        except Exception as e:
            logger.warning(f"Polling error for order {order_id}: {e}")
            
        await asyncio.sleep(delay)
        total_wait += delay
        if total_wait >= max_wait_seconds:
            break
            
    return None
