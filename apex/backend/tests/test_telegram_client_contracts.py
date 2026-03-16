import os
import sys
import unittest
from unittest.mock import patch

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from integrations.telegram import telegram_client


class _DummyResponse:
    def __init__(self, status_code: int, text: str, is_success: bool):
        self.status_code = status_code
        self.text = text
        self.is_success = is_success


class TelegramClientContracts(unittest.TestCase):
    @patch('integrations.telegram.telegram_client._get_config', return_value=('token', 12345))
    @patch('integrations.telegram.telegram_client._is_configured', return_value=True)
    def test_send_message_retries_without_parse_mode_on_entity_error(self, _mock_configured, _mock_config):
        first = _DummyResponse(400, "{\"ok\":false,\"error_code\":400,\"description\":\"Bad Request: can't parse entities\"}", False)
        second = _DummyResponse(200, '{"ok":true}', True)

        sent_payloads = []

        def _fake_post(_url, json=None, timeout=None):
            sent_payloads.append(dict(json or {}))
            return first if len(sent_payloads) == 1 else second

        with patch('httpx.post', side_effect=_fake_post):
            ok = telegram_client.send_message('Query plan: intent=position_status, symbol=ETH/USD')

        self.assertTrue(ok)
        self.assertEqual(sent_payloads[0].get('parse_mode'), 'Markdown')
        self.assertNotIn('parse_mode', sent_payloads[1])

    @patch('integrations.telegram.telegram_client.send_message')
    def test_send_report_chunks_long_content(self, mock_send_message):
        mock_send_message.return_value = True
        content = 'A' * 7200

        ok = telegram_client.send_report('daily', content, grade={'grade': 'B', 'score': 82})

        self.assertTrue(ok)
        self.assertGreaterEqual(mock_send_message.call_count, 3)
        first_text = mock_send_message.call_args_list[0].args[0]
        self.assertIn('Report: Daily | Grade B (82)', first_text)
        self.assertEqual(mock_send_message.call_args_list[0].kwargs.get('parse_mode'), None)


if __name__ == '__main__':
    unittest.main()
