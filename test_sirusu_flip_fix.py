#!/usr/bin/env python3
"""
Test script to verify Sirusu flip detection and pending order cancellation fix.

This test simulates the scenario where:
1. Perusu flip triggers a stop limit order for entry
2. Before entry happens, Sirusu flip occurs
3. The bot should cancel the pending entry order
"""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Import the classes we need to test
from services.algo_engine import AlgoEngine
from services.logger_bot import LoggerBot
from database.crud import save_indicator_cache


async def test_sirusu_flip_cancellation():
    """Test that pending orders are cancelled when Sirusu flips before entry."""
    
    print("🧪 Starting Sirusu flip cancellation test...")
    
    # Create a mock logger bot
    logger_bot = MagicMock(spec=LoggerBot)
    logger_bot.send_info = AsyncMock()
    logger_bot.send_error = AsyncMock()
    logger_bot.send_trade_entry = AsyncMock()
    logger_bot.send_trade_exit = AsyncMock()
    logger_bot.send_order_cancelled = AsyncMock()
    
    # Create algo engine
    engine = AlgoEngine(logger_bot)
    
    # Mock the database and API calls
    mock_algo_setup = {
        '_id': 'test_setup_1',
        'setup_name': 'Test Setup',
        'asset': 'BTCUSDT',
        'timeframe': '5m',
        'api_id': 'test_api_1',
        'user_id': 'test_user_1',
        'current_position': None,
        'pending_entry_order_id': 'pending_order_123',  # This is the pending order
        'pending_entry_side': 'long',
        'pending_entry_direction_signal': 1,  # Long position
        'product_id': 1001,
        'lot_size': 0.01,
        'additional_protection': True
    }
    
    # Mock indicator data - this simulates the Sirusu flip scenario
    mock_perusu_data = {
        'signal': 1,  # Uptrend
        'signal_text': 'Uptrend',
        'supertrend_value': 50000.0,
        'latest_close': 50100.0
    }
    
    mock_sirusu_data = {
        'signal': -1,  # Downtrend - this is the flip!
        'signal_text': 'Downtrend', 
        'supertrend_value': 49900.0
    }
    
    mock_indicator_result = {
        'perusu': mock_perusu_data,
        'sirusu': mock_sirusu_data,
        'latest_closed_candle': {
            'high': 50050.0,
            'low': 49950.0,
            'close': 50000.0
        }
    }
    
    # Mock the strategy to return our test data
    engine.strategy.calculate_indicators = AsyncMock(return_value=mock_indicator_result)
    
    # Mock the database functions
    with patch('database.crud.get_api_credential_by_id') as mock_get_cred, \
         patch('database.crud.get_indicator_cache') as mock_get_cache, \
         patch('database.crud.save_indicator_cache') as mock_save_cache, \
         patch('database.crud.update_algo_setup') as mock_update_setup, \
         patch('database.crud.get_algo_setup_by_id') as mock_get_setup, \
         patch('api.orders.is_order_gone') as mock_is_order_gone, \
         patch('api.orders.cancel_order') as mock_cancel_order:
        
        # Set up mock responses
        mock_get_cred.return_value = {
            'api_key': 'test_api_key',
            'api_secret': 'test_api_secret'
        }
        
        # This is the key part - we want to test the scenario where:
        # 1. There's a pending long order (pending_entry_direction_signal = 1)
        # 2. Sirusu flips to downtrend (signal = -1)
        # 3. The order monitor should detect this and cancel the order
        
        mock_get_cache.return_value = {
            'last_signal': 1,  # Previous Sirusu was uptrend
            'previous_signal': 1  # Previous Perusu was uptrend
        }
        
        mock_save_cache.return_value = True  # Indicate flip detected
        mock_get_setup.return_value = mock_algo_setup
        mock_is_order_gone.return_value = False  # Order is still pending
        mock_cancel_order.return_value = True  # Cancel succeeds
        
        # Mock the DeltaExchangeClient
        with patch('api.delta_client.DeltaExchangeClient') as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            
            try:
                # Process the algo setup
                await engine.process_algo_setup(mock_algo_setup)
                
                # Verify that cancel_order was called
                mock_cancel_order.assert_called_once_with(mock_client, 'pending_order_123')
                
                # Verify that the setup was updated to clear the pending order
                update_calls = mock_update_setup.call_args_list
                
                # Check if any of the update calls cleared the pending order
                cleared_pending_order = False
                for call in update_calls:
                    if call[0][0] == 'test_setup_1':
                        update_data = call[1]
                        if (update_data.get('pending_entry_order_id') is None and
                            update_data.get('pending_entry_side') is None):
                            cleared_pending_order = True
                            break
                
                if cleared_pending_order:
                    print("✅ SUCCESS: Pending order was properly cancelled and cleared from setup")
                    
                    # Verify notification was sent
                    logger_bot.send_order_cancelled.assert_called_once()
                    print("✅ SUCCESS: Order cancellation notification was sent")
                    
                    return True
                else:
                    print("❌ FAILURE: Pending order was not properly cleared from setup")
                    return False
                    
            except Exception as e:
                print(f"❌ ERROR during test: {e}")
                import traceback
                traceback.print_exc()
                return False


async def test_sirusu_flip_no_cancellation_needed():
    """Test that orders are NOT cancelled when Sirusu doesn't flip."""
    
    print("\n🧪 Starting no-cancellation test...")
    
    # Create a mock logger bot
    logger_bot = MagicMock(spec=LoggerBot)
    logger_bot.send_info = AsyncMock()
    logger_bot.send_error = AsyncMock()
    
    # Create algo engine
    engine = AlgoEngine(logger_bot)
    
    # Mock the database and API calls
    mock_algo_setup = {
        '_id': 'test_setup_2',
        'setup_name': 'Test Setup 2',
        'asset': 'ETHUSDT',
        'timeframe': '5m',
        'api_id': 'test_api_2',
        'user_id': 'test_user_2',
        'current_position': None,
        'pending_entry_order_id': 'pending_order_456',
        'pending_entry_side': 'long',
        'pending_entry_direction_signal': 1,  # Long position
        'product_id': 1002,
        'lot_size': 0.1
    }
    
    # Mock indicator data - NO Sirusu flip in this case
    mock_perusu_data = {
        'signal': 1,  # Uptrend
        'signal_text': 'Uptrend',
        'supertrend_value': 3000.0,
        'latest_close': 3010.0
    }
    
    mock_sirusu_data = {
        'signal': 1,  # Still uptrend - NO flip!
        'signal_text': 'Uptrend',
        'supertrend_value': 2990.0
    }
    
    mock_indicator_result = {
        'perusu': mock_perusu_data,
        'sirusu': mock_sirusu_data,
        'latest_closed_candle': {
            'high': 3005.0,
            'low': 2995.0,
            'close': 3000.0
        }
    }
    
    # Mock the strategy to return our test data
    engine.strategy.calculate_indicators = AsyncMock(return_value=mock_indicator_result)
    
    # Mock the database functions
    with patch('database.crud.get_api_credential_by_id') as mock_get_cred, \
         patch('database.crud.get_indicator_cache') as mock_get_cache, \
         patch('database.crud.save_indicator_cache') as mock_save_cache, \
         patch('database.crud.update_algo_setup') as mock_update_setup, \
         patch('database.crud.get_algo_setup_by_id') as mock_get_setup, \
         patch('api.orders.is_order_gone') as mock_is_order_gone, \
         patch('api.orders.cancel_order') as mock_cancel_order:
        
        # Set up mock responses
        mock_get_cred.return_value = {
            'api_key': 'test_api_key',
            'api_secret': 'test_api_secret'
        }
        
        mock_get_cache.return_value = {
            'last_signal': 1,  # Previous Sirusu was uptrend
            'previous_signal': 1  # Previous Perusu was uptrend
        }
        
        mock_save_cache.return_value = False  # No flip detected
        mock_get_setup.return_value = mock_algo_setup
        mock_is_order_gone.return_value = False  # Order is still pending
        mock_cancel_order.return_value = True
        
        # Mock the DeltaExchangeClient
        with patch('api.delta_client.DeltaExchangeClient') as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            
            try:
                # Process the algo setup
                await engine.process_algo_setup(mock_algo_setup)
                
                # Verify that cancel_order was NOT called (no flip, so no cancellation)
                mock_cancel_order.assert_not_called()
                
                print("✅ SUCCESS: Order was not cancelled when no Sirusu flip occurred")
                return True
                
            except Exception as e:
                print(f"❌ ERROR during test: {e}")
                import traceback
                traceback.print_exc()
                return False


async def main():
    """Run all tests."""
    print("🚀 Running Sirusu flip cancellation tests...\n")
    
    # Test 1: Sirusu flip should cancel pending order
    test1_result = await test_sirusu_flip_cancellation()
    
    # Test 2: No Sirusu flip should not cancel order
    test2_result = await test_sirusu_flip_no_cancellation_needed()
    
    print(f"\n📊 Test Results:")
    print(f"   Test 1 (Sirusu flip cancellation): {'PASS' if test1_result else 'FAIL'}")
    print(f"   Test 2 (No cancellation needed): {'PASS' if test2_result else 'FAIL'}")
    
    if test1_result and test2_result:
        print("\n🎉 All tests passed! The Sirusu flip detection and cancellation fix is working correctly.")
    else:
        print("\n❌ Some tests failed. Please review the implementation.")


if __name__ == "__main__":
    asyncio.run(main())