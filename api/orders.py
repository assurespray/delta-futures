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
                            size: int, side: str,
                            reduce_only: bool = False) -> Optional[Dict[str, Any]]:
    return await place_order(client, product_id, size, side, ORDER_TYPE_MARKET,
                             reduce_only=reduce_only)

async def place_stop_market_entry_order(client: DeltaExchangeClient, product_id: int,
                                        size: int, side: str, 
                                        stop_price: float) -> Optional[Dict[str, Any]]:
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
    try:
        all_orders = []
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
        seen = set()
        unique_orders = []
        for order in all_orders:
            order_id = order.get("id")
            if order_id and order_id not in seen:
                seen.add(order_id)
                unique_orders.append(order)
        for order in unique_orders:
            label = None
            stop_order_type = order.get("stop_order_type")
            reduce_only = order.get("reduce_only")
            order_type = order.get("order_type")
            if reduce_only and stop_order_type == "stop_loss_order":
                label = "Bracket - SL"
            elif reduce_only and (stop_order_type == "take_profit_order" or order_type in ["limit_order", "market_order"]):
                label = "Bracket - TP"
            order["bracket_label"] = label
        logger.info(f"✅ Total orders retrieved: {len(all_orders)}")
        logger.info(f"✅ Unique orders after deduplication: {len(unique_orders)}")
        return unique_orders if unique_orders else []
    except Exception as e:
        logger.error(f"❌ Exception getting open orders: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None

async def cancel_order(client: DeltaExchangeClient, product_id: int, order_id: int) -> bool:
    """Cancel an order on Delta Exchange.
    
    Args:
        client: DeltaExchangeClient instance
        product_id: Product ID the order belongs to
        order_id: Order ID to cancel
    
    Returns:
        True if order was successfully cancelled, False otherwise
    """
    try:
        if isinstance(order_id, str):
            order_id = int(order_id)
        if isinstance(product_id, str):
            product_id = int(product_id)
        
        # Delta Exchange requires DELETE /v2/orders with JSON body containing id and product_id
        response = await client.delete("/v2/orders", json_data={
            "id": order_id,
            "product_id": product_id
        })
        
        if response is None:
            # None means non-200 status - cancellation FAILED
            logger.warning(f"⚠️ Cancel order {order_id} returned None (API error)")
            return False
        
        if isinstance(response, dict) and response.get("success"):
            logger.info(f"✅ Order {order_id} cancelled via API")
            return True
        
        # Log unexpected response
        logger.warning(f"⚠️ Unexpected cancel response for {order_id}: {response}")
        return False
        
    except Exception as e:
        logger.error(f"❌ Cancel error for order {order_id}: {e}")
        return False

async def cancel_all_orders(client: DeltaExchangeClient, 
                           product_id: Optional[int] = None) -> int:
    try:
        orders = await get_open_orders(client, product_id)
        if not orders:
            logger.info("ℹ️ No open orders to cancel")
            return 0
        cancelled_count = 0
        for order in orders:
            order_id = order.get("id")
            ord_product_id = order.get("product_id") or product_id
            if order_id and ord_product_id and await cancel_order(client, ord_product_id, order_id):
                cancelled_count += 1
        logger.info(f"✅ Cancelled {cancelled_count}/{len(orders)} orders")
        return cancelled_count
    except Exception as e:
        logger.error(f"❌ Exception cancelling all orders: {e}")
        return 0

# Legacy direct-by-id getter: use only for display/debug, NOT status detection!
async def get_order_by_id(client: DeltaExchangeClient, order_id: int) -> Optional[Dict[str, Any]]:
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
    formatted = []
    for order in orders:
        try:
            product = order.get("product", {})
            symbol = product.get("symbol", "Unknown")
            bracket_label = order.get("bracket_label")
            formatted_order = {
                "order_id": order.get("id"),
                "product_id": order.get("product_id"),
                "symbol": symbol,
                "side": order.get("side", "").capitalize(),
                "size": order.get("size", 0),
                "order_type": order.get("order_type", "").replace("_", " ").title(),
                "limit_price": round(float(order.get("limit_price", 0)), 2) if order.get("limit_price") else None,
                "stop_price": round(float(order.get("stop_price", 0)), 2) if order.get("stop_price") else None,
                "filled": order.get("unfilled_size", 0),
                "status": order.get("state", "").capitalize(),
                "reduce_only": order.get("reduce_only", False),
                "bracket_label": bracket_label
            }
            formatted.append(formatted_order)
        except Exception as e:
            logger.error(f"❌ Error formatting order: {e}")
            continue
    return formatted

# ---- Robust order state functions, only use these for tracking! ----

async def get_order_status_by_id(client, order_id: int, product_id: int) -> str:
    try:
        open_params = {"product_id": product_id, "state": "open"}
        open_resp = await client.get("/v2/orders", open_params)
        if open_resp and open_resp.get("success"):
            for order in open_resp["result"]:
                if str(order.get("id")) == str(order_id):
                    return order.get("state", "open").lower()
        untrig_params = {"product_id": product_id, "state": "untriggered"}
        untrig_resp = await client.get("/v2/orders", untrig_params)
        if untrig_resp and untrig_resp.get("success"):
            for order in untrig_resp["result"]:
                if str(order.get("id")) == str(order_id):
                    return order.get("state", "untriggered").lower()
    except Exception as e:
        logger.warning(f"Open order status check failed: {e}")
    try:
        hist_params = {"product_id": product_id, "page_size": 100}
        hist_resp = await client.get("/v2/orders/history", hist_params)
        if hist_resp and hist_resp.get("success"):
            for order in hist_resp["result"]:
                if str(order.get("id")) == str(order_id):
                    return order.get("state", "not_found").lower()
    except Exception as e:
        logger.warning(f"Order history status check failed: {e}")
    return "not_found"

async def is_order_gone(client, order_id, product_id):
    status = await get_order_status_by_id(client, order_id, product_id)
    logger.info(f"[STARTUP] get_order_status_by_id({order_id},{product_id}) -> {status}")
    terminal_states = {"filled", "cancelled", "rejected", "not_found", "closed"}
    return status in terminal_states

async def get_order_history(
    client: DeltaExchangeClient, 
    product_id: int, 
    page_size: int = 20,
    state: Optional[str] = None
) -> Optional[list]:
    """
    Get a user's order history for the specified product.

    :param client: DeltaExchangeClient instance (authenticated)
    :param product_id: Product ID of the symbol
    :param page_size: Result page size (default 20, max 100)
    :param state: Filter by state ('filled', 'cancelled', etc) or None for all
    :return: List of order dicts or None on error
    """
    params = {"product_id": product_id, "page_size": page_size}
    if state:
        params["state"] = state

    try:
        resp = await client.get("/v2/orders/history", params)
        if resp and resp.get("success", False):
            return resp.get("result", [])
        logger.warning(f"Order history fetch failed: {resp}")
        return None
    except Exception as e:
        logger.error(f"Exception getting order history: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None
        
# DEPRECATED: Never use for SL/entry checks! Only keep for backward compatibility if legacy code exists.
# async def check_stop_loss_filled(client: DeltaExchangeClient, stop_loss_order_id: Optional[int], product_id: int) -> bool:
#     raise NotImplementedError("Use `is_order_gone` instead of `check_stop_loss_filled` for stop-loss/execution status.")

