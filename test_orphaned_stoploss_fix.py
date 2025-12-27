#!/usr/bin/env python3
"""
Test script to verify orphaned stop-loss order cancellation fix.

This test simulates the scenario where:
1. A position has an active stop-loss order
2. The bot executes a market exit
3. The stop-loss order should be properly cancelled to avoid orphaned orders
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


async def test_orphaned_stoploss_cancellation():
    """Test that stop-loss orders are properly cancelled during market exits."""
    
    print("🧪 Starting orphaned stop-loss cancellation test...")
    
    # Create position manager
    position_manager = PositionManager()
    
    # Mock the database and API calls
    mock_algo_setup = {
        '_id': 'test_setup_1',
        'setup_name': 'Test Setup',
        'asset': 'BTCUSDT',
        'timeframe': '5m',
        'api_id': 'test_api_1',
        'user_id': 'test_user_1',
        'current_position': 'long',
        'stop_loss_order_id': 'stop_loss_123',  # This is the stop-loss order that should be cancelled
        'product_id': 1001,
        'lot_size': 0.01,
        'additional_protection': True
    }
    
    # Mock the DeltaExchangeClient
    with patch('api.delta_client.DeltaExchangeClient') as mock_client_class, \
         patch('api.orders.get_order_status_by_id') as mock_get_order_status, \
         patch('api.orders.cancel_order') as mock_cancel_order, \
         patch('api.orders.get_open_orders') as mock_get_open_orders, \
         patch('api.orders.place_market_order') as mock_place_market_order, \
         patch('database.crud.update_order_record') as mock_update_order_record, \
         patch('database.crud.update_algo_setup') as mock_update_setup, \
         patch('database.crud.get_open_activity_by_setup') as mock_get_activity, \
         patch('database.crud.update_algo_activity') as mock_update_activity, \
         patch('database.crud.get_db') as mock_get_db, \
         patch('database.crud.release_position_lock') as mock_release_lock, \
         patch('api.positions.get_position_by_symbol') as mock_get_position:
        
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        
        # Set up mock responses
        mock_get_position.return_value = {
            'size': 0.01,  # Position is still open
            'entry_price': 50000.0
        }
        
        # Mock the order status - the stop-loss order is still active
        mock_get_order_status.return_value = 'open'
        
        # Mock the cancel order to succeed
        mock_cancel_order.return_value = True
        
        # Mock the market exit order
        mock_place_market_order.return_value = {
            'id': 'exit_order_456',
            'state': 'filled',
            'order_type': 'market_order',
            'side': 'sell',
            'size': 0.01,
            'average_fill_price': 51000.0,
            'reduce_only': True
        }
        
        # Mock open orders - include the stop-loss order
        mock_get_open_orders.return_value = [
            {
                'id': 'stop_loss_123',
                'symbol': 'BTCUSDT',
                'product_id': 1001,
                'stop_order_type': 'stop_loss_order',
                'reduce_only': True,
                'state': 'open',
                'side': 'sell',
                'size': 0.01
            }
        ]
        
        # Mock activity data
        mock_get_activity.return_value = {
            '_id': 'activity_789',
            'entry_price': 50000.0,
            'user_id': 'test_user_1'
        }
        
        try:
            # Execute the exit
            result = await position_manager.execute_exit(
                client=mock_client,
                algo_setup=mock_algo_setup,
                sirusu_signal_text='Sirusu downtrend'
            )
            
            # Verify that the exit was successful
            if result:
                print("✅ Exit execution succeeded")
            else:
                print("❌ Exit execution failed")
                return False
            
            # Verify that cancel_order was called for the stop-loss order
            cancel_calls = mock_cancel_order.call_args_list
            stop_loss_cancelled = False
            
            for call in cancel_calls:
                if call[0][1] == 'stop_loss_123':  # Check if the stop-loss order ID was passed
                    stop_loss_cancelled = True
                    break
            
            if stop_loss_cancelled:
                print("✅ SUCCESS: Stop-loss order was properly cancelled during exit")
            else:
                print("❌ FAILURE: Stop-loss order was not cancelled")
                return False
            
            # Verify that the order record was updated
            update_calls = mock_update_order_record.call_args_list
            order_updated = False
            
            for call in update_calls:
                if call[0][0] == 'stop_loss_123':
                    update_data = call[1]
                    if update_data.get('status') == 'cancelled':
                        order_updated = True
                        break
            
            if order_updated:
                print("✅ SUCCESS: Stop-loss order record was updated to cancelled")
            else:
                print("❌ FAILURE: Stop-loss order record was not updated")
                return False
            
            # Verify that the setup was cleaned up
            setup_update_calls = mock_update_setup.call_args_list
            setup_cleaned = False
            
            for call in setup_update_calls:
                if call[0][0] == 'test_setup_1':
                    update_data = call[1]
                    if (update_data.get('stop_loss_order_id') is None and
                        update_data.get('current_position') is None):
                        setup_cleaned = True
                        break
            
            if setup_cleaned:
                print("✅ SUCCESS: Algo setup was properly cleaned up")
            else:
                print("❌ FAILURE: Algo setup was not properly cleaned up")
                return False
            
            return True
            
        except Exception as e:
            print(f"❌ ERROR during test: {e}")
            import traceback
            traceback.print_exc()
            return False


async def test_stoploss_already_filled():
    """Test that the fix handles already-filled stop-loss orders correctly."""
    
    print("\n🧪 Starting already-filled stop-loss test...")
    
    # Create position manager
    position_manager = PositionManager()
    
    # Mock the database and API calls
    mock_algo_setup = {
        '_id': 'test_setup_2',
        'setup_name': 'Test Setup 2',
        'asset': 'ETHUSDT',
        'timeframe': '5m',
        'api_id': 'test_api_2',
        'user_id': 'test_user_2',
        'current_position': 'short',
        'stop_loss_order_id': 'stop_loss_456',
        'product_id': 1002,
        'lot_size': 0.1
    }
    
    # Mock the DeltaExchangeClient
    with patch('api.delta_client.DeltaExchangeClient') as mock_client_class, \
         patch('api.orders.get_order_status_by_id') as mock_get_order_status, \
         patch('api.orders.cancel_order') as mock_cancel_order, \
         patch('api.orders.get_open_orders') as mock_get_open_orders, \
         patch('api.orders.place_market_order') as mock_place_market_order, \
         patch('api.positions.get_position_by_symbol') as mock_get_position:
        
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        
        # Set up mock responses
        mock_get_position.return_value = {
            'size': -0.1,  # Short position is still open
            'entry_price': 3000.0
        }
        
        # Mock the order status - the stop-loss order is already filled
        mock_get_order_status.return_value = 'filled'
        
        # Mock the market exit order
        mock_place_market_order.return_value = {
            'id': 'exit_order_789',
            'state': 'filled',
            'order_type': 'market_order',
            'side': 'buy',
            'size': 0.1,
            'average_fill_price': 3050.0,
            'reduce_only': True
        }
        
        # Mock open orders - no active stop-loss orders
        mock_get_open_orders.return_value = []
        
        try:
            # Execute the exit
            result = await position_manager.execute_exit(
                client=mock_client,
                algo_setup=mock_algo_setup,
                sirusu_signal_text='Sirusu uptrend'
            )
            
            # Verify that the exit was successful
            if result:
                print("✅ Exit execution succeeded")
            else:
                print("❌ Exit execution failed")
                return False
            
            # Verify that cancel_order was NOT called for the already-filled stop-loss order
            cancel_calls = mock_cancel_order.call_args_list
            stop_loss_not_cancelled = True
            
            for call in cancel_calls:
                if call[0][1] == 'stop_loss_456':
                    stop_loss_not_cancelled = False
                    break
            
            if stop_loss_not_cancelled:
                print("✅ SUCCESS: Already-filled stop-loss order was not unnecessarily cancelled")
            else:
                print("❌ FAILURE: Already-filled stop-loss order was unnecessarily cancelled")
                return False
            
            return True
            
        except Exception as e:
            print(f"❌ ERROR during test: {e}")
            import traceback
            traceback.print_exc()
            return False


async def main():
    """Run all tests."""
    print("🚀 Running orphaned stop-loss order cancellation tests...\n")
    
    # Test 1: Stop-loss cancellation during market exit
    test1_result = await test_orphaned_stoploss_cancellation()
    
    # Test 2: Already-filled stop-loss handling
    test2_result = await test_stoploss_already_filled()
    
    print(f"\n📊 Test Results:")
    print(f"   Test 1 (Stop-loss cancellation): {'PASS' if test1_result else 'FAIL'}")
    print(f"   Test 2 (Already-filled handling): {'PASS' if test2_result else 'FAIL'}")
    
    if test1_result and test2_result:
        print("\n🎉 All tests passed! The orphaned stop-loss order fix is working correctly.")
    else:
        print("\n❌ Some tests failed. Please review the implementation.")


if __name__ == "__main__":
    asyncio.run(main())