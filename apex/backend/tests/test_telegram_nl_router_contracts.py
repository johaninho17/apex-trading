"""Contract tests for Telegram NLP routing and fallback behavior."""

import os
import sys
import unittest
from unittest.mock import patch

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from integrations.telegram import nl_router


class _DummyResponse:
    def __init__(self, content: str, status_code: int = 200):
        self._content = content
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return {"message": {"content": self._content}}


class TelegramNlRouterContracts(unittest.TestCase):
    def test_extract_json_dict_handles_fenced_json(self):
        parsed = nl_router._extract_json_dict('```json {"action":"chat","reply":"Hi"} ```')
        self.assertEqual(parsed["action"], "chat")
        self.assertEqual(parsed["reply"], "Hi")

    @patch("integrations.telegram.nl_router.httpx.post")
    @patch("integrations.telegram.nl_router.ollama_url", return_value="http://ollama.local/api/chat")
    @patch("core.config_manager.get_config")
    def test_parse_intent_uses_telegram_model_and_chat_endpoint(self, mock_get_config, mock_ollama_url, mock_post):
        mock_get_config.return_value = {
            "stocks": {"crypto": {"ollama_model": "apexbot:latest", "telegram_ollama_model": "qwen3:8b"}}
        }
        mock_post.return_value = _DummyResponse('{"action":"chat","reply":"Hi. How can I help?"}')

        parsed = nl_router.parse_intent("hi")

        self.assertEqual(parsed["action"], "chat")
        self.assertEqual(parsed["reply"], "Hi. How can I help?")
        kwargs = mock_post.call_args.kwargs
        self.assertEqual(mock_post.call_args.args[0], "http://ollama.local/api/chat")
        self.assertEqual(kwargs["json"]["model"], "qwen3:8b")
        self.assertEqual(kwargs["json"]["format"], "json")

    @patch("integrations.telegram.nl_router.httpx.post")
    @patch("core.config_manager.get_config")
    def test_parse_intent_normalizes_weak_chat_reply(self, mock_get_config, mock_post):
        mock_get_config.return_value = {"stocks": {"crypto": {"telegram_ollama_model": "qwen3:8b"}}}
        mock_post.return_value = _DummyResponse('{"action":"chat","reply":"hi"}')

        parsed = nl_router.parse_intent("hi")

        self.assertEqual(parsed, {"action": "chat", "reply": "Hi. How can I help with the bot?"})

    @patch("integrations.telegram.nl_router.httpx.post")
    @patch("core.config_manager.get_config")
    def test_parse_intent_falls_back_to_chat_when_model_returns_empty(self, mock_get_config, mock_post):
        mock_get_config.return_value = {"stocks": {"crypto": {"telegram_ollama_model": "qwen3:8b"}}}
        mock_post.return_value = _DummyResponse("")

        parsed = nl_router.parse_intent("hi")

        self.assertEqual(parsed, {"action": "chat", "reply": "Hi. How can I help with the bot?"})

    def test_parse_intent_fast_paths_status_phrase(self):
        parsed = nl_router.parse_intent("whats my status")
        self.assertEqual(parsed, {"action": "status"})

    def test_parse_intent_fast_paths_positions_phrase(self):
        parsed = nl_router.parse_intent("what am i holding right now")
        self.assertEqual(parsed, {"action": "positions"})

    @patch("integrations.telegram.nl_router.get_config")
    def test_parse_intent_fast_paths_symbol_question(self, mock_get_config):
        mock_get_config.return_value = {"stocks": {"crypto": {"symbols": ["ETH/USD", "SOL/USD"]}}}
        parsed = nl_router.parse_intent("what is my eth")
        self.assertEqual(parsed, {"action": "query", "question": "what is my eth"})

    def test_parse_intent_fast_paths_query(self):
        parsed = nl_router.parse_intent("why did we buy SOL yesterday")
        self.assertEqual(parsed, {"action": "query", "question": "why did we buy SOL yesterday"})

    def test_parse_intent_fast_paths_tracked_query(self):
        parsed = nl_router.parse_intent("show tracked coins")
        self.assertEqual(parsed, {"action": "query", "question": "show tracked coins"})

    @patch("integrations.telegram.nl_router.get_config")
    def test_parse_intent_treats_rsi_question_as_query_not_symbol(self, mock_get_config):
        mock_get_config.return_value = {"stocks": {"crypto": {"symbols": ["ETH/USD", "SOL/USD"]}}}
        parsed = nl_router.parse_intent("what is the rsi on eth")
        self.assertEqual(parsed, {"action": "query", "question": "what is the rsi on eth"})


if __name__ == "__main__":
    unittest.main()
