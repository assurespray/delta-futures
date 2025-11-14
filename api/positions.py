import logging
from typing import Dict, Any, Optional, List
from api.delta_client import DeltaExchangeClient

logger = logging.getLogger(__name__)

async def get_all_positions(client: DeltaExchangeClient) -> Optional[List[Dict[str, Any]]]:
    """
    Fetch all open positions across all tradable products/contracts for a single account.
    """
    try:
        products_resp = await client.get("/v2/products")
        if not (products_resp and products_resp.get("success")):
            logger.error("Could not fetch products. No positions will be returned.")
            return []
        product_symbols = [
            prod.get("symbol") for prod in products_resp.get("result", [])
            if prod.get("symbol")
        ]
        logger.debug(f"Queried {len(product_symbols)} products from /v2/products.")

        all_positions = []
        for symbol in product_symbols:
            try:
                pos_resp = await client.get("/v2/positions", params={"symbol": symbol})
                if pos_resp and pos_resp.get("success"):
                    positions = pos_resp.get("result", [])
                    for p in positions:
                        try:
                            size = float(p.get("size", 0))
                        except Exception:
                            size = 0
                        if size != 0:
                            all_positions.append(p)
                            logger.debug(f"   Found open position: {symbol} (position_id={p.get('id')})")
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
    Format positions for display (includes position_id).
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
                "margin_inr": round(margin * 85, 2),
                "pnl": round(pnl, 2),
                "pnl_inr": round(pnl * 85, 2),
                "pnl_percentage": round(pnl_percentage, 2)
            }
            formatted.append(formatted_pos)
        except Exception as e:
            logger.error(f"Error formatting position: {e}")
            continue
    return formatted

async def display_positions_for_all_apis(credentials: List[Dict[str, Any]]) -> str:
    """
    Fetch and display all open positions for a list of API credential dicts.
    """
    message = "📊 *Open Positions Across All APIs*\n\n"
    total_positions = 0
    for cred in credentials:
        api_name = cred.get('api_name') or cred.get('api_label') or cred.get('api_key', '')[:6] + "..."
        try:
            client = DeltaExchangeClient(cred['api_key'], cred['api_secret'])
            positions = await get_all_positions(client)
            await client.close()
            formatted = await format_positions_display(positions)
            message += f"=== Account: **{api_name}** ===\n"
            if not formatted:
                message += "No open positions.\n\n"
                continue
            for pos in formatted:
                message += (
                    f"• ID: `{pos['position_id']}` | {pos['symbol']} ({pos['side']})\n"
                    f"  Size: {pos['size']} | Entry: ${pos['entry_price']} | Mark: ${pos['current_price']}\n"
                    f"  Margin: ${pos['margin']} (₹{pos['margin_inr']})\n"
                    f"  PnL: ${pos['pnl']} (₹{pos['pnl_inr']}) | %: {pos['pnl_percentage']}%\n"
                    "-------------------------\n"
                )
                total_positions += 1
            message += "\n"
        except Exception as e:
            message += f"❌ Error fetching for {api_name}: {str(e)[:40]}\n\n"
    if total_positions == 0:
        message += "ℹ️ No open positions across all accounts.\n"
    return message
        
