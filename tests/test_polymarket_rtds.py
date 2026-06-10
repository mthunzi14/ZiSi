import unittest
import time
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from infrastructure.websocket.polymarket_rtds_ingest import (
    PolymarketRTDSIngest,
    get_chainlink_price,
    get_chainlink_price_age,
    get_chainlink_candle_open,
    _chainlink_prices,
    _chainlink_candle_opens,
)

class TestPolymarketRTDSIngest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Clear caches before each test
        _chainlink_prices.clear()
        _chainlink_candle_opens.clear()

    async def test_process_message_valid(self):
        ingest = PolymarketRTDSIngest()
        envelope = {
            "topic": "crypto_prices_chainlink",
            "type": "update",
            "timestamp": int(time.time() * 1000),
            "payload": {
                "symbol": "btc/usd",
                "timestamp": int(time.time() * 1000),
                "value": 67234.50
            }
        }
        await ingest._process_message(envelope)

        price_data = await get_chainlink_price("BTC")
        self.assertIsNotNone(price_data)
        price, ts = price_data
        self.assertEqual(price, 67234.50)
        self.assertLess(time.time() - ts, 2.0)

        age = await get_chainlink_price_age("BTC")
        self.assertLess(age, 2.0)

    async def test_process_message_invalid_topic(self):
        ingest = PolymarketRTDSIngest()
        envelope = {
            "topic": "other_topic",
            "type": "update",
            "payload": {
                "symbol": "btc/usd",
                "value": 67000.0
            }
        }
        await ingest._process_message(envelope)
        price_data = await get_chainlink_price("BTC")
        self.assertIsNone(price_data)

    async def test_candle_opens_tracking(self):
        ingest = PolymarketRTDSIngest()
        now = time.time()
        
        envelope1 = {
            "topic": "crypto_prices_chainlink",
            "type": "update",
            "payload": {
                "symbol": "eth/usd",
                "value": 3500.0
            }
        }
        await ingest._process_message(envelope1)

        # Retrieve tracked candle open for 5m (300s)
        candle_start_5m = int(now // 300) * 300
        open_5m = await get_chainlink_candle_open("ETH", 300, candle_start_5m)
        self.assertEqual(open_5m, 3500.0)

        # Subsequent updates in the same candle should not change the open price
        envelope2 = {
            "topic": "crypto_prices_chainlink",
            "type": "update",
            "payload": {
                "symbol": "eth/usd",
                "value": 3510.0
            }
        }
        await ingest._process_message(envelope2)
        open_5m_again = await get_chainlink_candle_open("ETH", 300, candle_start_5m)
        self.assertEqual(open_5m_again, 3500.0)

    @patch("aiohttp.ClientSession.ws_connect")
    async def test_socket_loop_subscription(self, mock_ws_connect):
        mock_ws = AsyncMock()
        mock_ws.closed = False
        
        # Mock receiving one message and then exit
        mock_ws.__aiter__.return_value = [
            MagicMock(type=MagicMock(value=1), data='PONG'),  # Text message
            MagicMock(type=MagicMock(value=257))  # Close message (ends iteration)
        ]
        
        mock_ws_connect.return_value.__aenter__.return_value = mock_ws

        ingest = PolymarketRTDSIngest()
        ingest.start()
        
        # Let the task run for a brief moment
        await asyncio.sleep(0.1)
        
        # Verify subscribe message was sent
        self.assertTrue(mock_ws.send_json.called)
        sub_arg = mock_ws.send_json.call_args[0][0]
        self.assertEqual(sub_arg["action"], "subscribe")
        self.assertEqual(sub_arg["subscriptions"][0]["topic"], "crypto_prices_chainlink")

        ingest.stop()

if __name__ == "__main__":
    unittest.main()
