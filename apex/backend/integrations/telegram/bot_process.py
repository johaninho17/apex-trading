"""
Apex Telegram Bot Process
==========================
The main listener loop that receives messages from Telegram, routes them
through the NLP parser, and executes commands against the live bot.

This runs as an asyncio background task within the FastAPI lifespan.

Commands supported:
  - status / portfolio         -> portfolio snapshot
  - positions                  -> current open positions
  - sell <coin> [half|all]     -> close/trim a position
  - close all                  -> close every open position
  - pause / stop bot           -> pauses crypto trading domain
  - resume / start bot         -> resumes crypto trading domain
"""

import asyncio
import logging
import os
from typing import Any, Dict

logger = logging.getLogger("apex.telegram.bot_process")

# â”€â”€ Security â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _get_credentials() -> tuple[str, int]:
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    raw_id = (os.getenv("TELEGRAM_CHAT_ID") or "0").strip()
    try:
        chat_id = int(raw_id)
    except ValueError:
        chat_id = 0
    return token, chat_id


def _is_configured() -> bool:
    token, chat_id = _get_credentials()
    return bool(token) and chat_id != 0


# â”€â”€ Action Handlers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _handle_status() -> str:
    """Fetch rich bot/account/governor status for Telegram."""
    try:
        from core.state import state as app_state
        from services.crypto.bot import _RUNTIME
        from services.crypto.market_data import get_account_summary, get_crypto_positions

        account = get_account_summary() or {}
        positions = [p for p in (get_crypto_positions() or []) if float(p.get("qty", 0) or 0) > 0]
        runtime = _RUNTIME.status() or {}
        last_status = dict(runtime.get("last_status") or {})
        risk_state = dict(runtime.get("risk_state") or {})
        governor = dict(runtime.get("activity_governor") or {})
        mode_effectiveness = dict(runtime.get("mode_effectiveness") or {})
        effective_risk_profile = dict(runtime.get("effective_risk_profile") or last_status.get("effective_risk_profile") or {})
        thresholds = dict(last_status.get("effective_thresholds") or {})
        governor_metrics = dict(governor.get("metrics") or {})
        governor_reasons = list(governor.get("reason_codes") or [])

        equity = float(account.get("equity", 0) or 0)
        cash = float(account.get("cash", 0) or 0)
        unrealized = sum(float(p.get("unrealized_pl", 0) or 0) for p in positions)
        paused = bool(app_state.is_domain_paused("crypto"))
        running = bool(runtime.get("running", False))
        status_label = "paused" if paused else ("running" if running else "stopped")
        trading_mode = str(last_status.get("trading_mode", "unknown") or "unknown")

        risk_label = str(risk_state.get("state", "normal") or "normal")
        risk_source = str(risk_state.get("source", "") or "")
        risk_reason = str(risk_state.get("reason", "") or "")

        mode = str(governor.get("mode", "normal") or "normal")
        pressure = float(governor.get("pressure_score", 0.0) or 0.0)
        verdict = str(mode_effectiveness.get("verdict", "insufficient") or "insufficient")
        eff = float(mode_effectiveness.get("effectiveness_score", 0.0) or 0.0)
        conf = float(mode_effectiveness.get("confidence_score", 0.0) or 0.0)

        market_regime = str(last_status.get("market_regime", "normal") or "normal")
        macro_risk = float(last_status.get("macro_risk_multiplier", 1.0) or 1.0)
        oracle_summary = str(last_status.get("oracle_summary", "") or "")

        feedback = dict(last_status.get("decision_feedback") or runtime.get("decision_feedback") or {})

        lines = [
            "Bot Status",
            f"State: {status_label} | Trading: {trading_mode}",
            f"Equity: ${equity:,.2f} | Cash: ${cash:,.2f} | Unrealized: {unrealized:+,.2f}",
            f"Open positions: {len(positions)} | Symbols watched: {len(last_status.get('symbols', []) or [])}",
            f"Risk state: {risk_label}" + (f" via {risk_source}" if risk_source else ""),
            f"Governor: {mode} | Pressure {pressure:+.1f} | Effectiveness {eff:+.1f} ({verdict}, conf {conf:.0f})",
        ]

        if thresholds:
            lines.append(
                "Thresholds: "
                + f"min_score {float(thresholds.get('min_signal_score', 0.0) or 0.0):.1f}, "
                + f"spacing {int(thresholds.get('trade_spacing_min', 0) or 0)}m, "
                + f"rsi_oversold {float(thresholds.get('rsi_oversold', 0.0) or 0.0):.1f}, "
                + f"breakout_vol {float(thresholds.get('breakout_volume_mult', 0.0) or 0.0):.2f}"
            )
        if effective_risk_profile:
            lines.append(
                "Sizing: "
                + f"hard_cap ${float(effective_risk_profile.get('effective_hard_cap_usd', 0.0) or 0.0):,.2f}, "
                + f"ai_ceiling ${float(effective_risk_profile.get('effective_ai_ceiling_usd', 0.0) or 0.0):,.2f}, "
                + f"max_exposure ${float(effective_risk_profile.get('max_exposure_usd', 0.0) or 0.0):,.2f}, "
                + f"capacity {int(effective_risk_profile.get('capacity_remaining', 0) or 0)}"
            )
        if governor_metrics:
            lines.append(
                f"Activity: signals_6h {int(governor_metrics.get('signals_6h', 0) or 0)}, blocked_6h {int(governor_metrics.get('blocked_traces_6h', 0) or 0)}, near_miss_6h {int(governor_metrics.get('near_miss_6h', 0) or 0)}, buys_24h {int(governor_metrics.get('buys_24h', 0) or 0)}"
            )
        if governor_reasons:
            lines.append("Governor flags: " + ", ".join(governor_reasons[:4]))

        lines.append(f"Macro: {market_regime} @ {macro_risk:.2f}")
        if oracle_summary:
            lines.append(f"Oracle: {oracle_summary[:180]}")
        if feedback:
            lines.append(
                f"Decision feedback: evaluated {int(feedback.get('evaluated', 0) or 0)}, recorded {int(feedback.get('recorded', 0) or 0)}, failed {int(feedback.get('failed', 0) or 0)}"
            )

        lines.append("")
        lines.append(f"The bot is {status_label} and trading mode is {trading_mode}.")
        risk_line = f"Risk state is {risk_label}"
        if risk_source:
            risk_line += f" via {risk_source}"
        if risk_reason:
            risk_line += f" because {risk_reason[:140]}"
        lines.append(risk_line + ".")
        lines.append(
            f"Governor is {mode} with pressure {pressure:+.1f}. Effectiveness is {eff:+.1f} and currently reads {verdict} with confidence {conf:.0f}."
        )
        if positions:
            top = sorted(positions, key=lambda row: float(row.get("unrealized_pl", 0) or 0))
            worst = top[0]
            best = top[-1]
            lines.append(
                f"Worst open position is {worst.get('symbol', '')} at {float(worst.get('unrealized_plpc', 0.0) or 0.0) * 100.0:+.2f}%. Best is {best.get('symbol', '')} at {float(best.get('unrealized_plpc', 0.0) or 0.0) * 100.0:+.2f}%."
            )
        else:
            lines.append("There are no open positions right now.")
        lines.append("Ask next: why are trades blocked, last 5 BTC trades, or what news affected a symbol.")
        return "\n".join(lines)
    except Exception as exc:
        return f"Error fetching status: {exc}"

def _handle_performance() -> str:
    """Fetch time-series PNL updates and reply via Telegram."""
    try:
        from services.crypto.store import get_equity_performance_sync
        from integrations.telegram.telegram_client import send_performance_summary
        perf = get_equity_performance_sync()
        send_performance_summary(perf)
        return None
    except Exception as exc:
        return f"âŒ Error fetching performance: {exc}"

def _handle_positions() -> str:
    """Return open positions only."""
    try:
        from services.crypto.market_data import get_crypto_positions
        positions = [p for p in get_crypto_positions() if float(p.get("qty", 0) or 0) > 0]
        if not positions:
            return "ðŸ“­ No open positions right now."
        lines = ["ðŸ“‹ *Open Positions:*"]
        for p in positions:
            sym = p.get("symbol", "???")
            pl = float(p.get("unrealized_pl", 0) or 0)
            pl_pct = float(p.get("unrealized_plpc", 0) or 0) * 100
            qty = float(p.get("qty", 0) or 0)
            price = float(p.get("current_price", 0) or 0)
            sign = "+" if pl >= 0 else ""
            lines.append(
                f"  â€¢ `{sym}` â€” qty `{qty:.6f}` @ `${price:,.4f}` | P&L: `{sign}${pl:,.2f}` ({sign}{pl_pct:.2f}%)"
            )
        return "\n".join(lines)
    except Exception as exc:
        return f"âŒ Error fetching positions: {exc}"


def _handle_query(intent: Dict[str, Any]) -> str:
    """Answer a retrieval query over local bot data."""
    question = str(intent.get("question") or "").strip()
    if not question:
        return "Ask about trades, scores, reports, or news and I will query the bot history."
    try:
        from integrations.telegram.query_service import answer_query
        return answer_query(question)
    except Exception as exc:
        return f"Error running query: {exc}"


def _handle_close_all() -> str:
    """Close every open position."""
    try:
        from services.crypto.execution import close_all_crypto_positions
        result = close_all_crypto_positions()
        closed = result.get("closed", 0)
        failures = result.get("failures", [])
        msg = f"âœ… Closed *{closed}* position(s)."
        if failures:
            fail_syms = ", ".join(f["symbol"] for f in failures if "symbol" in f)
            msg += f"\nâš ï¸ Failed to close: {fail_syms}"
        return msg
    except Exception as exc:
        return f"âŒ Error closing positions: {exc}"


def _handle_sell(intent: Dict[str, Any]) -> str:
    """Sell a specific symbol, optionally a half or all."""
    symbol = str(intent.get("symbol", "")).strip().upper()
    amount = str(intent.get("amount", "all")).strip().lower()

    if not symbol or "/" not in symbol:
        return f"â“ I couldn't identify the symbol to sell. Please try again, e.g. *sell BTC/USD*."

    try:
        from services.crypto.market_data import get_crypto_positions
        from services.crypto.execution import place_crypto_order, close_crypto_position

        positions = get_crypto_positions()
        pos = next(
            (p for p in positions if p.get("symbol", "").upper() == symbol),
            None,
        )

        if not pos or float(pos.get("qty", 0) or 0) <= 0:
            return f"ðŸ“­ No open position found for `{symbol}`."

        qty_held = float(pos.get("qty", 0))

        if amount in ("all", ""):
            close_crypto_position(symbol)
            return f"âœ… Closing full position in `{symbol}`."
        elif amount == "half":
            qty_to_sell = round(qty_held / 2.0, 6)
            place_crypto_order(symbol=symbol, side="sell", qty=qty_to_sell)
            return f"âœ… Selling half of `{symbol}` ({qty_to_sell:.6f} units)."
        else:
            try:
                qty_to_sell = round(float(amount), 6)
            except ValueError:
                return f"â“ Couldn't parse amount `{amount}`. Use `all`, `half`, or a number."
            place_crypto_order(symbol=symbol, side="sell", qty=qty_to_sell)
            return f"âœ… Selling `{qty_to_sell:.6f}` units of `{symbol}`."

    except Exception as exc:
        return f"âŒ Sell failed for `{symbol}`: {exc}"


def _handle_pause() -> str:
    """Pause the crypto trading domain."""
    try:
        from core.state import state
        state.set_domain_paused("crypto", True)
        return "â¸ï¸ Trading paused. The bot will not place new orders until resumed."
    except Exception as exc:
        return f"âŒ Failed to pause: {exc}"


def _handle_resume() -> str:
    """Resume the crypto trading domain."""
    try:
        from core.state import state
        state.set_domain_paused("crypto", False)
        return "â–¶ï¸ Trading resumed. The bot is live again."
    except Exception as exc:
        return f"âŒ Failed to resume: {exc}"


def _dispatch(intent: Dict[str, Any]) -> str:
    """Route a parsed intent dict to the right handler."""
    action = str(intent.get("action", "unknown")).lower()

    if action == "status":
        return _handle_status()
    elif action == "positions":
        return _handle_positions()
    elif action == "sell":
        return _handle_sell(intent)
    elif action == "close_all":
        return _handle_close_all()
    elif action == "performance":
        return _handle_performance()
    elif action == "query":
        return _handle_query(intent)
    elif action == "pause":
        return _handle_pause()
    elif action == "resume":
        return _handle_resume()
    elif action == "buy":
        return "âš ï¸ Manual buys via Telegram are disabled for safety. Use the dashboard."
    elif action == "chat":
        return str(intent.get("reply", "ðŸ¤– Hmm, I understood you, but my conversational core didn't generate a proper reply."))
    else:
        lines = [
            "\U0001f916 *Apex Bot \u2014 Commands*\n",
            "\U0001f4ca *Portfolio*",
            "  `status` \u2014 equity & full portfolio",
            "  `positions` \u2014 open positions",
            "  `performance` - 1h / 24h / 7d equity P&L",
            "  `why did we buy SOL` - query decisions/news/traces",
            "  `show last 5 BTC trades` - retrieve local history\n",
            "\U0001f4c8 *Trading*",
            "  `sell BTC` \u2014 close your BTC position",
            "  `sell half my ETH` \u2014 trim by 50%",
            "  `close all` \u2014 liquidate everything\n",
            "\u2699\ufe0f *Controls*",
            "  `pause` \u2014 halt new buys",
            "  `resume` \u2014 re-enable trading\n",
            "_You can use natural language too \u2014 I'll figure it out._",
        ]
        return "\n".join(lines)


# â”€â”€ Long-Poll Loop â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def _run_polling(token: str, allowed_chat_id: int) -> None:
    """
    Long-polling receive loop using raw httpx calls for maximum compatibility
    without requiring the full python-telegram-bot dispatcher machinery.
    """
    import httpx

    base_url = f"https://api.telegram.org/bot{token}"
    offset = 0
    logger.info(f"[TelegramBot] Long-poll loop started. Listening for chat_id={allowed_chat_id}.")

    timeout = httpx.Timeout(connect=10.0, read=45.0, write=10.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        while True:
            try:
                resp = await client.get(
                    f"{base_url}/getUpdates",
                    params={"offset": offset, "timeout": 30, "allowed_updates": ["message"]},
                )
                if not resp.is_success:
                    logger.warning(f"[TelegramBot] getUpdates HTTP {resp.status_code}. Backing off 5s.")
                    await asyncio.sleep(5)
                    continue

                updates = resp.json().get("result", [])

                for update in updates:
                    offset = update["update_id"] + 1
                    message = update.get("message") or {}
                    chat = message.get("chat") or {}
                    sender_id = int(chat.get("id", 0))
                    text = str(message.get("text") or "").strip()

                    # Security Gate: only respond to the whitelisted chat
                    if sender_id != allowed_chat_id:
                        logger.warning(f"[TelegramBot] Ignoring message from unauthorized chat_id={sender_id}")
                        continue

                    if not text:
                        continue

                    logger.info(f"[TelegramBot] Received: {text!r}")

                    # Parse intent via Ollama qwen3:8b
                    try:
                        from integrations.telegram.nl_router import parse_intent
                        intent = parse_intent(text)
                    except Exception as parse_exc:
                        logger.warning(f"[TelegramBot] NL parse error: {parse_exc}")
                        intent = {"action": "unknown"}

                    # Execute and reply
                    reply = _dispatch(intent)
                    if reply:  # some handlers send directly (e.g. portfolio summary)
                        try:
                            from integrations.telegram.telegram_client import send_message
                            send_message(reply)
                        except Exception as send_exc:
                            logger.warning(f"[TelegramBot] Reply send failed: {send_exc}")

            except asyncio.CancelledError:
                logger.info("[TelegramBot] Polling cancelled â€” shutting down.")
                return
            except httpx.ReadTimeout:
                logger.debug("[TelegramBot] getUpdates read timeout - retrying immediately.")
                continue
            except httpx.TimeoutException as exc:
                logger.warning(f"[TelegramBot] Poll timeout: {type(exc).__name__}: {exc!r}. Retrying in 3s.")
                await asyncio.sleep(3)
            except Exception as exc:
                logger.warning(f"[TelegramBot] Poll loop error: {type(exc).__name__}: {exc!r}. Retrying in 10s.")
                await asyncio.sleep(10)


# â”€â”€ Entry Point â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def start_telegram_bot() -> None:
    """
    Async entry point called from main.py lifespan.
    Gracefully skips startup if token/chat_id are not configured.
    """
    if not _is_configured():
        logger.info(
            "[TelegramBot] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set â€” Telegram integration disabled."
        )
        return

    token, chat_id = _get_credentials()
    try:
        await _run_polling(token, chat_id)
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.error(f"[TelegramBot] Fatal error: {exc}")



