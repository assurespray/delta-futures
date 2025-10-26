"""Delta Exchange API authentication and signature generation."""
import hmac
import hashlib
import time
import logging
from typing import Dict

logger = logging.getLogger(__name__)


def generate_signature(method: str, endpoint: str, api_secret: str, 
                      query_string: str = "", body: str = "") -> tuple[str, str]:
    """
    Generate HMAC-SHA256 signature for Delta Exchange API.
    
    Args:
        method: HTTP method (GET, POST, DELETE)
        endpoint: API endpoint path
        api_secret: API secret key
        query_string: Query parameters (optional)
        body: Request body (optional)
    
    Returns:
        Tuple of (signature, timestamp)
    """
    try:
        timestamp = str(int(time.time()))
        message = method + timestamp + endpoint + query_string + body
        
        signature = hmac.new(
            api_secret.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        return signature, timestamp
        
    except Exception as e:
        logger.error(f"❌ Failed to generate signature: {e}")
        raise


def get_auth_headers(method: str, endpoint: str, api_key: str, api_secret: str,
                    query_string: str = "", body: str = "") -> Dict[str, str]:
    """
    Generate authentication headers for Delta Exchange API.
    
    Args:
        method: HTTP method
        endpoint: API endpoint path
        api_key: API key
        api_secret: API secret
        query_string: Query parameters (optional)
        body: Request body (optional)
    
    Returns:
        Dictionary of headers
    """
    signature, timestamp = generate_signature(method, endpoint, api_secret, query_string, body)
    
    headers = {
        'api-key': api_key,
        'signature': signature,
        'timestamp': timestamp,
        'User-Agent': 'DeltaTradingBot/1.0',
        'Content-Type': 'application/json'
    }
    
    return headers
                      
