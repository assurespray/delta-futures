import asyncio
import logging
from typing import Dict, Any, Optional, List
from api.delta_client import DeltaExchangeClient

logger = logging.getLogger(__name__)

def get_float_or_na(pos, *keys):
    for key in keys:
        if key in pos and pos[key] not in [None, ""]:
            try:
                return float(pos[key])
            except Exception:
                continue
    return "N/A"

async def get_ticker_mark_price(client: DeltaExchangeClient, symbol: str) -> float:
    try:
        response = await client.get(f"/v2/tickers/{symbol}")
        if response and "result" in response:
            return float(response["result"].get("mark_price", 0))
        return 0.0
    except Exception as e:
        logger.warning(f"Could not fetch mark price for {symbol}: {e}")
        return 0.0

async def get_all_positions_for_assets(client: DeltaExchangeClient, assets: List[str] = None) -> Optional[List[Dict[str, Any]]]:
    if assets is None:
        assets = ["BTC", "ETH", "SOL", "MATIC", "AVAX", "ADA", "ALGO", "DOT", "NEAR", "ARB"]
    try:
        # Define the async fetcher for one asset
        async def fetch_positions(asset):
            try:
                logger.debug(f"Querying positions for {asset}...")
                response = await client.get("/v2/positions", params={"underlying_asset_symbol": asset})
                if response and response.get("success"):
                    positions = response.get("result", [])
                    active_positions = [
                        p for p in positions
                        if p.get("size") and abs(float(p.get("size", 0))) > 0
                    ]
                    if active_positions:
                        logger.debug(f"Found {len(active_positions)} positions for {asset}")
                    return active_positions
            except Exception as e:
                logger.debug(f"Error querying {asset}: {e}")
            return []

        # Launch all fetches in parallel
        all_results = await asyncio.gather(*(fetch_positions(asset) for asset in assets))
        # Flatten the results
        all_positions = [pos for sublist in all_results for pos in sublist]

        if all_positions:
            logger.info(f"Retrieved {len(all_positions)} total open positions")
            for pos in all_positions:
                symbol = pos.get("product", {}).get("symbol", "Unknown")
                size = pos.get("size", 0)
                logger.info(f"{symbol}: {size} contracts")
        else:
            logger.info("No open positions found")
        return all_positions
    except Exception as e:
        logger.error(f"Exception getting positions: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return []

async def format_positions_display(positions: List[Dict[str, Any]], client: DeltaExchangeClient) -> List[Dict[str, Any]]:
    formatted = []
    for pos in positions:
        try:
            symbol = (
                pos.get("product_symbol") or
                (pos.get("product", {}) or {}).get("symbol") or
                pos.get("symbol") or
                "Unknown"
            )
            size = float(pos.get("size", 0))
            entry_price = float(pos.get("entry_price", 0))
            if size == 0 or symbol == "Unknown":
                continue

            # Fetch mark price with fallback to entry price if not available
            mark_price = await get_ticker_mark_price(client, symbol)
            if not mark_price:
                mark_price = entry_price

            # Lot size logic (adapt as needed for other contracts)
            if "ETH" in symbol:
                lot_size = 0.01
            elif "BTC" in symbol:
                lot_size = 0.001
            else:
                lot_size = 1.0

            # Manual PnL calculation
            if size < 0:
                pnl = (entry_price - mark_price) * abs(size) * lot_size
            else:
                pnl = (mark_price - entry_price) * abs(size) * lot_size

            # PnL percent
            if entry_price != 0:
                pnl_percentage = (pnl / (entry_price * abs(size) * lot_size)) * 100
            else:
                pnl_percentage = 0.0

            # Margin (extract if present, else show "N/A")
            margin = get_float_or_na(pos, "margin")
            if isinstance(margin, float) and margin == 0:
                margin = "N/A"

            def safe_round(val, places):
                if isinstance(val, float):
                    return round(val, places)
                return val

            formatted_pos = {
                "symbol": symbol,
                "size": size,
                "side": "Long" if size > 0 else "Short",
                "entry_price": safe_round(entry_price, 5),
                "current_price": safe_round(mark_price, 5),
                "margin": safe_round(margin, 2),
                "margin_inr": safe_round(margin * 85 if isinstance(margin, float) else margin, 2),
                "pnl": round(pnl, 4),
                "pnl_inr": round(pnl * 85, 2),
                "pnl_percentage": round(pnl_percentage, 2)
            }
            formatted.append(formatted_pos)
        except Exception as e:
            logger.error(f"Error formatting position: {e}, original: {pos}")
            continue
    return formatted

async def display_positions_for_all_apis(credentials):
    message = "📊 *Open Positions Across All APIs*\n\n"
    total_positions = 0
    for cred in credentials:
        api_name = cred.get('api_name') or cred.get('api_label') or cred.get('apikey', '')[:6] + "..."
        api_key = cred.get('api_key') or cred.get('apikey') or cred.get('apiKey')
        api_secret = cred.get('api_secret') or cred.get('apisecret') or cred.get('apiSecret')
        logger.info(f"Credentials debug: {cred}")
        if not api_key or not api_secret:
            message += f"❌ Error fetching for {api_name}: missing API key or secret\n\n"
            continue
        try:
            client = DeltaExchangeClient(api_key, api_secret)
            positions = await get_all_positions_for_assets(client)
            formatted = await format_positions_display(positions, client)
            await client.close()
            message += f"=== Account: **{api_name}** ===\n"
            if not formatted:
                message += "No open positions.\n\n"
                continue
            for pos in formatted:
                entry_str = f"${pos['entry_price']}" if pos['entry_price'] != "N/A" else "N/A"
                mark_str = f"${pos['current_price']}" if pos['current_price'] != "N/A" else "N/A"
                margin_str = f"${pos['margin']}" if pos['margin'] != "N/A" else "N/A"
                margin_inr_str = f"(₹{pos['margin_inr']})" if pos['margin_inr'] != "N/A" else ""
                pnl_str = f"${pos['pnl']}" if pos['pnl'] != "N/A" else "N/A"
                pnl_inr_str = f"(₹{pos['pnl_inr']})" if pos['pnl_inr'] != "N/A" else ""
                pnl_pct_str = f"{pos['pnl_percentage']}%" if pos['pnl_percentage'] != "N/A" else "N/A"
                message += (
                    f"• {pos['symbol']} ({pos['side']}) | Size: {pos['size']}\n"
                    f"  Entry: {entry_str} | Mark: {mark_str}\n"
                    f"  Margin: {margin_str} {margin_inr_str}\n"
                    f"  PnL: {pnl_str} {pnl_inr_str} | %: {pnl_pct_str}\n"
                    "-------------------------\n"
                )
                total_positions += 1
            message += "\n"
        except Exception as e:
            message += f"❌ Error fetching for {api_name}: {str(e)[:40]}\n\n"
    if total_positions == 0:
        message += "ℹ️ No open positions across all accounts.\n"
    return message

async def get_position_by_symbol(client: DeltaExchangeClient, symbol: str, retry_count: int = 3) -> Optional[Dict[str, Any]]:
    import asyncio
    for attempt in range(retry_count):
        try:
            underlying_asset = symbol.replace("USD", "").replace("USDT", "")
            logger.info(f"Attempt {attempt + 1}: Querying positions for symbol='{symbol}', underlying_asset='{underlying_asset}'")
            response = await client.get("/v2/positions", params={"underlying_asset_symbol": underlying_asset})
            logger.info(f"API response for {underlying_asset}: {response}")

            if not response or not response.get("success"):
                logger.warning(f"No valid response for {underlying_asset}; retrying.")
                if attempt < retry_count - 1:
                    await asyncio.sleep(0.5)
                    continue
                return None

            positions = response.get("result", [])
            logger.info(f"Positions returned for {underlying_asset}: {positions}")

            for position in positions:
                # Delta returns 'product' dict with 'symbol'
                position_symbol = position.get("product_symbol") or position.get("product", {}).get("symbol", "")
                position_size = float(position.get("size", 0))
                logger.info(f"Checking position: symbol={position_symbol}, size={position_size}")
                # Symbol must match _and_ position size must be nonzero/open
                if position_symbol == symbol and abs(position_size) > 0:
                    logger.info(f"Match found: {position}")
                    return position

            logger.info(f"No matching open position found for symbol: {symbol} in attempt {attempt + 1}")

            if attempt < retry_count - 1:
                await asyncio.sleep(0.5)
                continue
            return None
        except Exception as e:
            logger.error(f"Exception for {symbol} (attempt {attempt + 1}): {e}")
            if attempt < retry_count - 1:
                await asyncio.sleep(0.5)
                continue
            return None
    logger.info(f"Finished all retries; no open position found for {symbol}")
    return None
