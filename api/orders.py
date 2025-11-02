"""Order placement and management operations."""
import logging
from typing import Dict, Any, Optional, List
from api.delta_client import DeltaExchangeClient
from config.constants import (
    ORDER_TYPE_MARKET, ORDER_TYPE_LIMIT, 
    ORDER_TYPE_STOP_LIMIT, ORDER_TYPE_STOP_MARKET,
    ORDER_SIDE_BUY, ORDER_SIDE_SELL
)

logger = logging.getLogger(__name__)


async def place_order(client: DeltaExchangeClient, product_id: int, size: int, 
                     side: str, order_type: str = ORDER_TYPE_MARKET,
                     limit_price: Optional[float] = None, 
                     stop_price: Optional[float] = None,
                     stop_order_type: Optional[str] = None,
                     reduce_only: bool = False) -> Optional[Dict[str, Any]]:
    """
    Place an order on Delta Exchange.
    
    Args:
        client: Delta Exchange client instance
        product_id: Product ID from Delta Exchange
        size: Order size (number of contracts)
        side: "buy" or "sell"
        order_type: "market_order" or "limit_order"
        limit_price: Limit price (required for limit orders)
        stop_price: Stop trigger price (for stop orders)
        stop_order_type: "stop_loss_order" for stop orders
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
        
        # Add limit price for limit orders
        if order_type == ORDER_TYPE_LIMIT and limit_price:
            order_data["limit_price"] = str(limit_price)
        
        # Add stop parameters for stop orders
        if stop_price and stop_order_type:
            order_data["stop_price"] = str(stop_price)
            order_data["stop_order_type"] = stop_order_type
        
        response = await client.post("/v2/orders", order_data)
        
        if response and response.get("success"):
            order = response.get("result", {})
            logger.info(f"✅ Order placed: {order_type} {side.upper()} {size} contracts")
            if stop_price:
                logger.info(f"   Stop trigger: ${stop_price}")
            if limit_price:
                logger.info(f"   Limit price: ${limit_price}")
            return order
        
        logger.error(f"❌ Failed to place order: {response}")
        return None
        
    except Exception as e:
        logger.error(f"❌ Exception placing order: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None


async def place_market_order(client: DeltaExchangeClient, product_id: int, 
                            size: int, side: str) -> Optional[Dict[str, Any]]:
    """Place a market order."""
    return await place_order(client, product_id, size, side, ORDER_TYPE_MARKET)


async def place_stop_market_entry_order(client: DeltaExchangeClient, product_id: int,
                                        size: int, side: str, 
                                        stop_price: float) -> Optional[Dict[str, Any]]:
    """Place a stop-market order for breakout entry."""
    logger.info(f"🎯 Placing breakout entry: {side.upper()} stop-market @ ${stop_price}")
    
    return await place_order(
        client=client,
        product_id=product_id,
        size=size,
        side=side,
        order_type=ORDER_TYPE_MARKET,
        stop_price=stop_price,
        stop_order_type="stop_loss_order",
        reduce_only=False
    )


async def place_stop_limit_entry_order(client: DeltaExchangeClient, product_id: int,
                                      size: int, side: str, 
                                      stop_price: float,
                                      slippage_pct: float = 0.005) -> Optional[Dict[str, Any]]:
    """Place a stop-limit order for breakout entry."""
    if side == "buy":
        limit_price = stop_price * (1 + slippage_pct)
    else:
        limit_price = stop_price * (1 - slippage_pct)
    
    logger.info(f"🎯 Placing breakout entry: {side.upper()} stop-limit")
    logger.info(f"   Stop: ${stop_price:.5f}")
    logger.info(f"   Limit: ${limit_price:.5f}")
    
    return await place_order(
        client=client,
        product_id=product_id,
        size=size,
        side=side,
        order_type=ORDER_TYPE_LIMIT,
        limit_price=limit_price,
        stop_price=stop_price,
        stop_order_type="stop_loss_order",
        reduce_only=False
    )


async def place_stop_loss_order(client: DeltaExchangeClient, product_id: int,
                                size: int, side: str, stop_price: float,
                                use_stop_market: bool = True) -> Optional[Dict[str, Any]]:
    """Place a reduce-only stop-loss order."""
    if use_stop_market:
        logger.info(f"🛡️ Placing stop-loss: {side.upper()} stop-market @ ${stop_price}")
        
        return await place_order(
            client=client,
            product_id=product_id,
            size=size,
            side=side,
            order_type=ORDER_TYPE_MARKET,
            stop_price=stop_price,
            stop_order_type="stop_loss_order",
            reduce_only=True
        )
    else:
        return await place_order(
            client=client,
            product_id=product_id,
            size=size,
            side=side,
            order_type=ORDER_TYPE_LIMIT,
            limit_price=stop_price,
            stop_price=stop_price,
            stop_order_type="stop_loss_order",
            reduce_only=True
        )


async def get_open_orders(client: DeltaExchangeClient, 
                         product_id: Optional[int] = None,
                         include_untriggered: bool = True) -> Optional[List[Dict[str, Any]]]:
    """
    Get all open orders, including untriggered stop orders.
    
    ✅ FIXED: Now retrieves both "open" AND "untriggered" orders
    
    Args:
        client: Delta Exchange client instance
        product_id: Optional product ID to filter
        include_untriggered: Include untriggered stop orders (default True)
    
    Returns:
        List of open/untriggered orders or None
    """
    try:
        all_orders = []
        
        # ✅ STEP 1: Get OPEN orders
        logger.info(f"🔍 [STEP 1] Fetching OPEN orders...")
        params = {"state": "open"}
        if product_id:
            params["product_id"] = product_id
        
        response = await client.get("/v2/orders", params)
        
        if response and response.get("success"):
            open_orders = response.get("result", [])
            logger.info(f"   Found {len(open_orders)} open orders")
            all_orders.extend(open_orders)
        else:
            logger.warning(f"⚠️ Failed to get open orders: {response}")
        
        # ✅ STEP 2: Get UNTRIGGERED stop orders
        if include_untriggered:
            logger.info(f"🔍 [STEP 2] Fetching UNTRIGGERED stop orders...")
            params = {"state": "untriggered"}
            if product_id:
                params["product_id"] = product_id
            
            response = await client.get("/v2/orders", params)
            
            if response and response.get("success"):
                untriggered_orders = response.get("result", [])
                logger.info(f"   Found {len(untriggered_orders)} untriggered orders")
                all_orders.extend(untriggered_orders)
            else:
                logger.warning(f"⚠️ Failed to get untriggered orders: {response}")
        
        logger.info(f"✅ Total orders retrieved: {len(all_orders)}")
        
        return all_orders if all_orders else []
        
    except Exception as e:
        logger.error(f"❌ Exception getting open orders: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None


async def cancel_order(client: DeltaExchangeClient, order_id: int) -> bool:
    """
    Cancel an order.
    ✅ Returns True if cancelled OR already gone (404 is success)
    """
    try:
        if isinstance(order_id, str):
            order_id = int(order_id)
        
        response = await client.delete(f"/v2/orders/{order_id}")
        
        if response is None:
            return True  # 404 - order already gone
        
        if isinstance(response, dict) and response.get("success"):
            return True
        
        if isinstance(response, dict):
            msg = response.get("message", "") or response.get("error", "")
            if "404" in msg or "not found" in msg.lower():
                return True
        
        return False
        
    except Exception as e:
        if "404" in str(e):
            return True
        logger.error(f"❌ Cancel error: {e}")
        return False


async def cancel_all_orders(client: DeltaExchangeClient, 
                           product_id: Optional[int] = None) -> int:
    """Cancel all open orders (optionally for specific product)."""
    try:
        orders = await get_open_orders(client, product_id)
        
        if not orders:
            logger.info("ℹ️ No open orders to cancel")
            return 0
        
        cancelled_count = 0
        
        for order in orders:
            order_id = order.get("id")
            if order_id and await cancel_order(client, order_id):
                cancelled_count += 1
        
        logger.info(f"✅ Cancelled {cancelled_count}/{len(orders)} orders")
        return cancelled_count
        
    except Exception as e:
        logger.error(f"❌ Exception cancelling all orders: {e}")
        return 0


async def get_order_by_id(client: DeltaExchangeClient, order_id: int) -> Optional[Dict[str, Any]]:
    """Get order details by ID."""
    try:
        response = await client.get(f"/v2/orders/{order_id}")
        
        if response and response.get("success"):
            order = response.get("result", {})
            return order
        
        logger.error(f"❌ Failed to get order {order_id}: {response}")
        return None
        
    except Exception as e:
        logger.error(f"❌ Exception getting order: {e}")
        return None


async def format_orders_display(orders: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Format orders for display in Telegram."""
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
                "stop_price": round(float(order.get("stop_price", 0)), 2) if order.get("stop_price") else None,
                "filled": order.get("unfilled_size", 0),
                "status": order.get("state", "").capitalize(),
                "reduce_only": order.get("reduce_only", False)
            }
            
            formatted.append(formatted_order)
            
        except Exception as e:
            logger.error(f"❌ Error formatting order: {e}")
            continue
    
    return formatted
    

async def check_stop_loss_filled(client: DeltaExchangeClient, 
                                 stop_loss_order_id: Optional[int],
                                 product_id: int) -> bool:
    """
    Check if stop-loss order was already filled/executed.
    
    ✅ Returns True if stop-loss is GONE (filled or cancelled)
    """
    try:
        if not stop_loss_order_id:
            return False
        
        if isinstance(stop_loss_order_id, str):
            try:
                stop_loss_order_id = int(stop_loss_order_id)
            except (ValueError, TypeError):
                return False
        
        # Try to get the order
        response = await client.get(f"/v2/orders/{stop_loss_order_id}")
        
        if response is None or not response.get("success"):
            # 404 - Order doesn't exist (filled or cancelled)
            return True
        
        order = response.get("result", {})
        order_state = order.get("state", "").lower()
        
        # If order is filled, closed, or cancelled - it's GONE
        if order_state in ["filled", "closed", "cancelled"]:
            logger.info(f"ℹ️ Stop-loss order {stop_loss_order_id} is {order_state}")
            return True
        
        # Order still exists (open or untriggered)
        logger.info(f"ℹ️ Stop-loss order {stop_loss_order_id} still {order_state}")
        return False
        
    except Exception as e:
        logger.warning(f"⚠️ Error checking SL status: {e}")
        return False
        
