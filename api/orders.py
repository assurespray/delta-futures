"""Order placement and management operations."""
import logging
from typing import Dict, Any, Optional, List
from api.delta_client import DeltaExchangeClient
from config.constants import ORDER_TYPE_MARKET, ORDER_TYPE_LIMIT, ORDER_SIDE_BUY, ORDER_SIDE_SELL

logger = logging.getLogger(__name__)


async def place_order(client: DeltaExchangeClient, product_id: int, size: int, 
                     side: str, order_type: str = ORDER_TYPE_MARKET,
                     limit_price: Optional[float] = None, reduce_only: bool = False) -> Optional[Dict[str, Any]]:
    """
    Place an order on Delta Exchange.
    
    Args:
        client: Delta Exchange client instance
        product_id: Product ID from Delta Exchange
        size: Order size (number of contracts)
        side: "buy" or "sell"
        order_type: "market_order" or "limit_order"
        limit_price: Limit price (required for limit orders)
        reduce_only: Whether order is reduce-only (for stop-loss)
    
    Returns:
        Order response or None on failure
    """
    try:
        order_data = {
            "product_id": product_id,
            "size": size,
            "side": side,
            "order_type": order_type,
            "time_in_force": "gtc",
            "reduce_only": reduce_only
        }
        
        if order_type == ORDER_TYPE_LIMIT and limit_price:
            order_data["limit_price"] = str(limit_price)
        
        response = await client.post("/v2/orders", order_data)
        
        if response and response.get("success"):
            order = response.get("result", {})
            logger.info(f"✅ Order placed: {side} {size} contracts at {order_type}")
            return order
        
        logger.error(f"❌ Failed to place order: {response}")
        return None
        
    except Exception as e:
        logger.error(f"❌ Exception placing order: {e}")
        return None


async def place_market_order(client: DeltaExchangeClient, product_id: int, 
                            size: int, side: str) -> Optional[Dict[str, Any]]:
    """
    Place a market order.
    
    Args:
        client: Delta Exchange client instance
        product_id: Product ID
        size: Order size
        side: "buy" or "sell"
    
    Returns:
        Order response or None
    """
    return await place_order(client, product_id, size, side, ORDER_TYPE_MARKET)


async def place_stop_loss_order(client: DeltaExchangeClient, product_id: int,
                                size: int, side: str, stop_price: float) -> Optional[Dict[str, Any]]:
    """
    Place a reduce-only stop-loss limit order.
    
    Args:
        client: Delta Exchange client instance
        product_id: Product ID
        size: Order size (should match position size)
        side: "buy" for short protection, "sell" for long protection
        stop_price: Stop-loss trigger price
    
    Returns:
        Order response or None
    """
    return await place_order(
        client=client,
        product_id=product_id,
        size=size,
        side=side,
        order_type=ORDER_TYPE_LIMIT,
        limit_price=stop_price,
        reduce_only=True
    )


async def get_open_orders(client: DeltaExchangeClient) -> Optional[List[Dict[str, Any]]]:
    """
    Get all open orders.
    
    Args:
        client: Delta Exchange client instance
    
    Returns:
        List of open orders or None
    """
    try:
        response = await client.get("/v2/orders")
        
        if response and response.get("success"):
            orders = response.get("result", [])
            logger.info(f"✅ Retrieved {len(orders)} open orders")
            return orders
        
        logger.error(f"❌ Failed to get open orders: {response}")
        return None
        
    except Exception as e:
        logger.error(f"❌ Exception getting open orders: {e}")
        return None


async def cancel_order(client: DeltaExchangeClient, order_id: int) -> bool:
    """
    Cancel an order by ID.
    
    Args:
        client: Delta Exchange client instance
        order_id: Order ID to cancel
    
    Returns:
        True if successful, False otherwise
    """
    try:
        response = await client.delete(f"/v2/orders/{order_id}")
        
        if response and response.get("success"):
            logger.info(f"✅ Order cancelled: {order_id}")
            return True
        
        logger.error(f"❌ Failed to cancel order: {response}")
        return False
        
    except Exception as e:
        logger.error(f"❌ Exception cancelling order: {e}")
        return False


async def cancel_all_orders(client: DeltaExchangeClient, product_id: Optional[int] = None) -> int:
    """
    Cancel all open orders (optionally for specific product).
    
    Args:
        client: Delta Exchange client instance
        product_id: Optional product ID to filter orders
    
    Returns:
        Number of orders cancelled
    """
    try:
        orders = await get_open_orders(client)
        
        if not orders:
            return 0
        
        cancelled_count = 0
        
        for order in orders:
            # Filter by product if specified
            if product_id and order.get("product_id") != product_id:
                continue
            
            order_id = order.get("id")
            if order_id and await cancel_order(client, order_id):
                cancelled_count += 1
        
        logger.info(f"✅ Cancelled {cancelled_count} orders")
        return cancelled_count
        
    except Exception as e:
        logger.error(f"❌ Exception cancelling all orders: {e}")
        return 0


async def format_orders_display(orders: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Format orders for display in Telegram.
    
    Args:
        orders: List of raw order data
    
    Returns:
        List of formatted order data
    """
    formatted = []
    
    for order in orders:
        try:
            product = order.get("product", {})
            symbol = product.get("symbol", "Unknown")
            
            formatted_order = {
                "order_id": order.get("id"),
                "symbol": symbol,
                "side": order.get("side", "").capitalize(),
                "size": order.get("size", 0),
                "order_type": order.get("order_type", "").replace("_", " ").title(),
                "limit_price": round(float(order.get("limit_price", 0)), 2) if order.get("limit_price") else None,
                "filled": order.get("unfilled_size", 0),
                "status": order.get("state", "").capitalize(),
                "reduce_only": order.get("reduce_only", False)
            }
            
            formatted.append(formatted_order)
            
        except Exception as e:
            logger.error(f"❌ Error formatting order: {e}")
            continue
    
    return formatted
  
