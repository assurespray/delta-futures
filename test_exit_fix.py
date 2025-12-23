#!/usr/bin/env python3
"""
Test script to verify the exit logic fix.
This script simulates a Sirusu signal flip and tests the exit execution.
"""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Import the classes we need to test
from strategy.position_manager import PositionManager
from api.delta_client import DeltaExchangeClient

async def test_exit_logic():
    """Test the exit logic with a simulated Sirusu signal flip."""
    
    # Create a mock setup
    algo_setup = {
        "_id": "test_setup_123",
        "setup_name": "Test Setup",
        "asset": "BTC-USD",
        "lot_size": 1,
        "product_id": 12345,
        "current_position": "long",
        "stop_loss_order_id": "sl_order_678",
        "user_id": "test_user"
    }
    
    # Create PositionManager instance
    position_manager = PositionManager()
    
    # Mock the DeltaExchangeClient
    mock_client = MagicMock(spec=DeltaExchangeClient)
    
    # Mock the get_position_by_symbol to return an open position
    mock_client.get_position_by_symbol = AsyncMock(return_value={
        "size": 1,  # Open long position
        "entry_price": 50000.0
    })
    
    # Mock the get_open_orders to return a stop-loss order
    mock_client.get_open_orders = AsyncMock(return_value=[{
        "id": "sl_order_678",
        "stop_order_type": "stop_loss_order",
        "reduce_only": True,
        "state": "open",
        "side": "sell",
        "product_id": 12345
    }])
    
    # Mock the cancel_order to succeed
    mock_client.cancel_order = AsyncMock(return_value=True)
    
    # Mock the place_market_order to succeed
    mock_client.place_market_order = AsyncMock(return_value={
        "id": "exit_order_999",
        "state": "filled",
        "order_type": "market_order",
        "side": "sell",
        "size": 1,
        "average_fill_price": 51000.0,
        "reduce_only": True
    })
    
    # Mock database functions
    with patch('strategy.position_manager.update_algo_setup', new_callable=AsyncMock) as mock_update_setup, \
         patch('strategy.position_manager.get_open_activity_by_setup', new_callable=AsyncMock) as mock_get_activity, \
         patch('strategy.position_manager.update_algo_activity', new_callable=AsyncMock) as mock_update_activity, \
         patch('strategy.position_manager.create_order_record', new_callable=AsyncMock) as mock_create_order, \
         patch('strategy.position_manager.update_order_record', new_callable=AsyncMock) as mock_update_order, \
         patch('strategy.position_manager.get_db') as mock_get_db, \
         patch('strategy.position_manager.get_position_lock') as mock_get_lock, \
         patch('strategy.position_manager.release_position_lock', new_callable=AsyncMock) as mock_release_lock:
        
        # Mock activity data
        mock_get_activity.return_value = {
            "_id": "activity_123",
            "entry_price": 50000.0,
            "user_id": "test_user"
        }
        
        # Mock database
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db
        mock_db.positions.update_many = AsyncMock()
        
        # Mock position lock
        mock_get_lock.return_value = None
        
        # Test the exit execution
        result = await position_manager.execute_exit(
            client=mock_client,
            algo_setup=algo_setup,
            sirusu_signal_text="Sirusu flipped to downtrend"
        )
        
        # Verify the result
        if result:
            print("✅ Exit execution successful!")
            
            # Verify that cancel_order was called for the stop-loss
            mock_client.cancel_order.assert_called_once_with("sl_order_678")
            print("✅ Stop-loss cancellation called")
            
            # Verify that place_market_order was called for the exit
            mock_client.place_market_order.assert_called_once()
            print("✅ Market exit order placed")
            
            # Verify that database updates were called
            mock_update_setup.assert_called()
            mock_update_activity.assert_called()
            mock_create_order.assert_called()
            print("✅ Database updates performed")
            
            return True
        else:
            print("❌ Exit execution failed!")
            return False

async def test_already_closed_position():
    """Test the exit logic when position is already closed on exchange."""
    
    # Create a mock setup
    algo_setup = {
        "_id": "test_setup_456",
        "setup_name": "Test Setup 2",
        "asset": "ETH-USD",
        "lot_size": 2,
        "product_id": 67890,
        "current_position": "short",
        "stop_loss_order_id": "sl_order_111",
        "user_id": "test_user"
    }
    
    # Create PositionManager instance
    position_manager = PositionManager()
    
    # Mock the DeltaExchangeClient
    mock_client = MagicMock(spec=DeltaExchangeClient)
    
    # Mock the get_position_by_symbol to return a closed position
    mock_client.get_position_by_symbol = AsyncMock(return_value={
        "size": 0,  # Closed position
        "entry_price": 3000.0
    })
    
    # Mock database functions
    with patch('strategy.position_manager.update_algo_setup', new_callable=AsyncMock) as mock_update_setup, \
         patch('strategy.position_manager.get_open_activity_by_setup', new_callable=AsyncMock) as mock_get_activity, \
         patch('strategy.position_manager.update_algo_activity', new_callable=AsyncMock) as mock_update_activity, \
         patch('strategy.position_manager.get_db') as mock_get_db, \
         patch('strategy.position_manager.release_position_lock', new_callable=AsyncMock) as mock_release_lock:
        
        # Mock activity data
        mock_get_activity.return_value = {
            "_id": "activity_456",
            "entry_price": 3000.0,
            "user_id": "test_user"
        }
        
        # Mock database
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db
        mock_db.positions.update_many = AsyncMock()
        
        # Test the exit execution for already closed position
        result = await position_manager.execute_exit(
            client=mock_client,
            algo_setup=algo_setup,
            sirusu_signal_text="Sirusu flipped to uptrend"
        )
        
        # Verify the result
        if result:
            print("✅ Already closed position handled successfully!")
            
            # Verify that database sync was performed
            mock_update_setup.assert_called()
            mock_update_activity.assert_called()
            mock_db.positions.update_many.assert_called()
            print("✅ Database synchronization performed")
            
            return True
        else:
            print("❌ Handling of already closed position failed!")
            return False

async def main():
    """Run all tests."""
    print("🧪 Testing exit logic fix...")
    print("=" * 50)
    
    # Test 1: Normal exit execution
    print("\n1. Testing normal exit execution...")
    test1_result = await test_exit_logic()
    
    # Test 2: Already closed position
    print("\n2. Testing already closed position handling...")
    test2_result = await test_already_closed_position()
    
    print("\n" + "=" * 50)
    if test1_result and test2_result:
        print("🎉 All tests passed! Exit logic fix is working correctly.")
        return True
    else:
        print("❌ Some tests failed. Please check the implementation.")
        return False

if __name__ == "__main__":
    # Run the tests
    result = asyncio.run(main())
    
    if result:
        print("\n✅ Exit logic fix verification completed successfully!")
    else:
        print("\n❌ Exit logic fix verification failed!")