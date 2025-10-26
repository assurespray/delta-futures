"""Account balance and wallet operations."""
import logging
from typing import Dict, Any, Optional, List
from api.delta_client import DeltaExchangeClient

logger = logging.getLogger(__name__)


async def get_wallet_balances(client: DeltaExchangeClient) -> Optional[List[Dict[str, Any]]]:
    """
    Get wallet balances for account.
    
    Args:
        client: Delta Exchange client instance
    
    Returns:
        List of wallet balances or None on failure
    """
    try:
        response = await client.get("/v2/wallet/balances")
        
        if response and response.get("success"):
            balances = response.get("result", [])
            logger.info(f"✅ Retrieved {len(balances)} wallet balances")
            return balances
        
        logger.error(f"❌ Failed to get wallet balances: {response}")
        return None
        
    except Exception as e:
        logger.error(f"❌ Exception getting wallet balances: {e}")
        return None


async def get_account_summary(client: DeltaExchangeClient) -> Optional[Dict[str, Any]]:
    """
    Get formatted account summary with total and available balance.
    
    Args:
        client: Delta Exchange client instance
    
    Returns:
        Dictionary with account summary or None
    """
    try:
        balances = await get_wallet_balances(client)
        
        if not balances:
            return None
        
        # Calculate totals
        total_balance = 0.0
        available_balance = 0.0
        locked_margin = 0.0
        
        for balance in balances:
            asset_id = balance.get("asset_id", "")
            balance_value = float(balance.get("balance", 0))
            available = float(balance.get("available_balance", 0))
            
            # Focus on USD balance for futures trading
            if asset_id == 1:  # USD
                total_balance = balance_value
                available_balance = available
                locked_margin = balance_value - available
                break
        
        summary = {
            "total_balance": round(total_balance, 2),
            "available_balance": round(available_balance, 2),
            "locked_margin": round(locked_margin, 2),
            "total_balance_inr": round(total_balance * 85, 2),
            "available_balance_inr": round(available_balance * 85, 2),
            "locked_margin_inr": round(locked_margin * 85, 2)
        }
        
        logger.info(f"✅ Account summary: Total=${total_balance}, Available=${available_balance}")
        return summary
        
    except Exception as e:
        logger.error(f"❌ Exception getting account summary: {e}")
        return None
      
