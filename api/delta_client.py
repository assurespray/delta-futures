"""Delta Exchange API client with rate limiting and retry logic."""
import asyncio
import logging
from typing import Dict, Any, Optional
import httpx
from config.settings import settings
from config.constants import REQUEST_RETRY_ATTEMPTS, REQUEST_RETRY_DELAY
from api.authentication import get_auth_headers

logger = logging.getLogger(__name__)


class DeltaExchangeClient:
    """Async client for Delta Exchange India API."""
    
    def __init__(self, api_key: str, api_secret: str):
        """
        Initialize Delta Exchange client.
        
        Args:
            api_key: Delta Exchange API key
            api_secret: Delta Exchange API secret
        """
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = settings.delta_api_base_url
        self.client = httpx.AsyncClient(timeout=30.0)
        self._rate_limit_lock = asyncio.Lock()
        self._last_request_time = 0
        self._min_request_interval = 0.1  # 10 requests per second max
    
    async def close(self):
        """Close HTTP client."""
        await self.client.aclose()
    
    async def _rate_limit(self):
        """Implement rate limiting."""
        async with self._rate_limit_lock:
            current_time = asyncio.get_event_loop().time()
            time_since_last = current_time - self._last_request_time
            
            if time_since_last < self._min_request_interval:
                await asyncio.sleep(self._min_request_interval - time_since_last)
            
            self._last_request_time = asyncio.get_event_loop().time()
    
    async def _request(self, method: str, endpoint: str, params: Optional[Dict] = None,
                      json_data: Optional[Dict] = None, retry: int = 0) -> Optional[Dict[str, Any]]:
        """
        Make authenticated request to Delta Exchange API.
    
        Args:
            method: HTTP method
            endpoint: API endpoint
            params: Query parameters
            json_data: JSON body data
            retry: Current retry attempt
    
        Returns:
            Response JSON or None on failure
        """
        await self._rate_limit()
    
        try:
            # Prepare request components
            query_string = ""
            if params:
                # Sort parameters alphabetically for consistent signature
                sorted_params = sorted(params.items())
                query_string = "&".join([f"{k}={v}" for k, v in sorted_params])
        
            body = ""
            if json_data:
                import json
                body = json.dumps(json_data, separators=(',', ':'))  # No spaces
        
            # Generate authentication headers
            headers = get_auth_headers(
                method=method.upper(),
                endpoint=endpoint,
                api_key=self.api_key,
                api_secret=self.api_secret,
                query_string=query_string,
                body=body
            )
        
            # Build full URL
            url = f"{self.base_url}{endpoint}"
            if query_string:
                url = f"{url}?{query_string}"
        
            # Make request WITHOUT params (already in URL)
            response = await self.client.request(
                method=method,
                url=url,
                json=json_data if json_data else None,
                headers=headers
            )
        
            # Check response
            if response.status_code == 200:
                return response.json()
        
            elif response.status_code == 429:  # Rate limit
                logger.warning(f"⚠️ Rate limit hit, retrying after delay...")
                await asyncio.sleep(2)
                if retry < REQUEST_RETRY_ATTEMPTS:
                    return await self._request(method, endpoint, params, json_data, retry + 1)
        
            else:
                logger.error(f"❌ API request failed: {response.status_code} - {response.text}")
                return None
    
        except httpx.TimeoutException:
            logger.error(f"❌ Request timeout for {endpoint}")
            if retry < REQUEST_RETRY_ATTEMPTS:
                await asyncio.sleep(REQUEST_RETRY_DELAY * (retry + 1))
                return await self._request(method, endpoint, params, json_data, retry + 1)
            return None
    
        except Exception as e:
            logger.error(f"❌ Request failed: {e}")
            if retry < REQUEST_RETRY_ATTEMPTS:
                await asyncio.sleep(REQUEST_RETRY_DELAY * (retry + 1))
                return await self._request(method, endpoint, params, json_data, retry + 1)
            return None
    
    async def get(self, endpoint: str, params: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
        """Make GET request."""
        return await self._request("GET", endpoint, params=params)
    
    async def post(self, endpoint: str, json_data: Dict) -> Optional[Dict[str, Any]]:
        """Make POST request."""
        return await self._request("POST", endpoint, json_data=json_data)
    
    async def delete(self, endpoint: str, params: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
        """Make DELETE request."""
        return await self._request("DELETE", endpoint, params=params)
      
