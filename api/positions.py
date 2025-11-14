import logging
from typing import Dict, Any, Optional, List
from api.delta_client import DeltaExchangeClient

logger = logging.getLogger(__name__)

async def get_all_positions(client: DeltaExchangeClient) -> Optional[List[Dict[str, Any]]]:
    """
    Fetch all open positions across all tradable products/contracts.
    Queries by trading symbol (e.g. 'ADAUSD') to match what the Delta UI shows.
    Returns list of position dicts (only those with nonzero size).
    """
    try:
        # 1. Get all product symbols (tradable contracts) from /v2/products
        products_resp = await client.get("/v2/products")
        if not (products_resp and products_resp.get("success")):
            logger.error("Could not fetch products. No positions will be returned.")
            return []
        product_symbols = [
            prod.get("symbol") for prod in products_resp.get("result", [])
            if prod.get("symbol")
        ]
        logger.debug(f"Queried {len(product_symbols)} products from /v2/products.")

        # 2. For each product symbol, attempt to fetch open positions
        all_positions = []
        for symbol in product_symbols:
            try:
                logger.debug(f"Querying positions for {symbol} ...")
                pos_resp = await client.get("/v2/positions", params={"symbol": symbol})
                # If API returns a successful response and positions found
                if pos_resp and pos_resp.get("success"):
                    positions = pos_resp.get("result", [])
                    # Only include nonzero size positions
                    for p in positions:
                        try:
                            size = float(p.get("size", 0))
                        except Exception:
                            size = 0
                        if size != 0:
                            all_positions.append(p)
                            logger.debug(f"   Found open position: {symbol} (position_id={p.get('id')})")
                # else: skip
            except Exception as e:
                logger.debug(f"Error fetching positions for {symbol}: {e}")

        logger.info(f"Found {len(all_positions)} open positions across all products.")
        return all_positions
    except Exception as e:
        logger.error(f"Exception in get_all_positions: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return []

async def format_positions_display(positions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Format positions for display in Telegram (includes position_id).
    Each dict in list includes: position_id, symbol, size, side, entry/current/margin/pnl, etc.
    """
    formatted = []
    for pos in positions:
        try:
            product = pos.get("product", {})
            symbol = product.get("symbol", "Unknown")
            position_id = pos.get('id', 'N/A')
            size = float(pos.get("size", 0))
            if size == 0:
                continue

            entry_price = float(pos.get("entry_price", 0))
            current_price = float(pos.get("mark_price", 0))
            margin = float(pos.get("margin", 0))
            pnl = float(pos.get("unrealized_pnl", 0))
            pnl_percentage = float(pos.get("unrealized_pnl_percentage", 0))

            formatted_pos = {
                "position_id": position_id,
                "symbol": symbol,
                "size": size,
                "side": "Long" if size > 0 else "Short",
                "entry_price": round(entry_price, 5),
                "current_price": round(current_price, 5),
                "margin": round(margin, 2),
                "margin_inr": round(margin * 85, 2),  # Example FX rate; update if you fetch live rate
                "pnl": round(pnl, 2),
                "pnl_inr": round(pnl * 85, 2),
                "pnl_percentage": round(pnl_percentage, 2)
            }
            formatted.append(formatted_pos)
        except Exception as e:
            logger.error(f"Error formatting position: {e}")
            continue
    return formatted

# Example display in Telegram (for a markdown list, inside your bot handler)
async def get_and_display_positions(client: DeltaExchangeClient):
    positions = await get_all_positions(client)
    formatted = await format_positions_display(positions)
    if not formatted:
        return "❌ No open positions found."

    message = "📊 *Open Positions*\n\n"
    for pos in formatted:
        message += (
            f"*Position ID*: `{pos['position_id']}`\n"
            f"*Symbol*: `{pos['symbol']}` | *Side*: {pos['side']}\n"
            f"*Size*: {pos['size']}\n"
            f"*Entry*: ${pos['entry_price']} | *Mark*: ${pos['current_price']}\n"
            f"*Margin*: ${pos['margin']} (₹{pos['margin_inr']})\n"
            f"*PnL*: ${pos['pnl']} (₹{pos['pnl_inr']}) | *%*: {pos['pnl_percentage']}%\n"
            "-------------------------\n"
        )
    return message
  
