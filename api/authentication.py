"""Authentication utilities for Delta Exchange API."""
import hmac
import hashlib
import time
import logging
import json

logger = logging.getLogger(__name__)


def generate_signature(method: str, endpoint: str, api_secret: str, 
                      query_string: str = "", body: str = "") -> tuple[str, str]:
    """
    Generate HMAC-SHA256 signature for Delta Exchange API.
    
    Args:
        method: HTTP method (GET, POST, DELETE)
        endpoint: API endpoint path
        api_secret: API secret key
        query_string: Query parameters (sorted alphabetically)
        body: Request body (MUST be compact JSON with no spaces)
    
    Returns:
        Tuple of (signature, timestamp)
    """
    try:
        timestamp = str(int(time.time()))
        
        # Build signature string: METHOD + TIMESTAMP + ENDPOINT + QUERY_STRING + BODY
        signature_string = method.upper() + timestamp + endpoint
        if query_string:
            signature_string += "?" + query_string
        signature_string += body
        
        # Generate HMAC-SHA256 signature
        signature = hmac.new(
            api_secret.encode('utf-8'),
            signature_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        logger.info(f"🔐 Signature data: {signature_string}")
        
        return signature, timestamp
        
    except Exception as e:
        logger.error(f"❌ Failed to generate signature: {e}")
        raise


def get_auth_headers(method: str, endpoint: str, api_key: str, api_secret: str,
                    query_string: str = "", body: str = "") -> dict:
    """
    Generate authentication headers for Delta Exchange API.
    
    Args:
        method: HTTP method
        endpoint: API endpoint
        api_key: API key
        api_secret: API secret
        query_string: Query parameters string
        body: Request body (compact JSON)
    
    Returns:
        Dictionary of headers
    """
    signature, timestamp = generate_signature(method, endpoint, api_secret, query_string, body)
    
    headers = {
        "api-key": api_key,
        "signature": signature,
        "timestamp": timestamp,
        "Content-Type": "application/json",
        "User-Agent": "DeltaFuturesBot/1.0"
    }
    
    return headers
                      
