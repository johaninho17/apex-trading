"""
Apex Telegram Notification Client
===================================
Pushes formatted trade alerts and status messages from the bot to your Telegram.
Usage:
    from integrations.telegram.telegram_client import send_trade_alert, send_message
"""

import logging
import os
from typing import Optional

logger = logging.getLogger("apex.telegram")

# ── Config ────────────────────────────────────────────────────────────────────
# Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in your .env file.
# TELEGRAM_CHAT_ID: Your personal Telegram user/chat ID (integer).
# To find your Chat ID: message your bot then visit
#   https://api.telegram.org/bot<TOKEN>/getUpdates
# ──────────────────────────────────────────────────────────────────────────────

def _get_config() -> tuple[str, int]:
    """Read token and chat_id from environment at call time (supports hot reload)."""
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    raw_id = (os.getenv("TELEGRAM_CHAT_ID") or "0").strip()
    try:
        chat_id = int(raw_id)
    except ValueError:
        chat_id = 0
    return token, chat_id


def _is_configured() -> bool:
    token, chat_id = _get_config()
    return bool(token) and chat_id != 0


# ── Core Send ─────────────────────────────────────────────────────────────────

def send_message(text: str, parse_mode: Optional[str] = "Markdown") -> bool:
    """
    Send a raw text message to the configured Telegram chat.
    Returns True on success, False on failure (never raises).
    Automatically retries without parse_mode if Telegram rejects entities.
    """
    if not _is_configured():
        logger.debug("Telegram not configured - skipping send_message.")
        return False

    import httpx

    token, chat_id = _get_config()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    timeout = httpx.Timeout(20.0, connect=10.0, read=20.0, write=10.0)

    for attempt in range(3):
        try:
            resp = httpx.post(url, json=payload, timeout=timeout)
            if not resp.is_success:
                body = (resp.text or "")[:200]
                if resp.status_code == 400 and "can't parse entities" in body.lower() and payload.get("parse_mode"):
                    logger.warning("[Telegram] sendMessage rejected entities, retrying as plain text.")
                    payload.pop("parse_mode", None)
                    continue
                logger.warning(f"[Telegram] sendMessage HTTP {resp.status_code}: {body}")
                return False
            return True
        except httpx.ReadTimeout as exc:
            logger.warning(f"[Telegram] send_message read timeout on attempt {attempt + 1}: {exc}")
        except Exception as exc:
            logger.warning(f"[Telegram] send_message failed on attempt {attempt + 1}: {exc}")
            return False

    return False

# ── Trade Notifications ────────────────────────────────────────────────────────



def _chunk_text(text: str, limit: int = 3500) -> list[str]:
    raw = str(text or "").strip()
    if not raw:
        return []
    chunks: list[str] = []
    remaining = raw
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit)
        if split_at < max(500, limit // 3):
            split_at = remaining.rfind(" ", 0, limit)
        if split_at < max(500, limit // 3):
            split_at = limit
        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks


def send_report(report_type: str, content: str, grade: Optional[dict] = None) -> bool:
    """Send a full Gemini report to Telegram, chunked for message limits."""
    report_label = str(report_type or "report").strip().capitalize()
    grade_text = ""
    if isinstance(grade, dict) and grade.get("grade"):
        score = grade.get("score")
        if score is None:
            grade_text = f" | Grade {grade.get('grade')}"
        else:
            grade_text = f" | Grade {grade.get('grade')} ({score})"
    header = f"Report: {report_label}{grade_text}\n\n"
    chunks = _chunk_text(str(content or ""), limit=3500)
    if not chunks:
        return send_message(header + "No content.", parse_mode=None)
    ok = True
    total = len(chunks)
    for idx, chunk in enumerate(chunks, start=1):
        prefix = header if idx == 1 else f"Report: {report_label} ({idx}/{total})\n\n"
        ok = send_message(prefix + chunk, parse_mode=None) and ok
    return ok

def send_trade_alert(
    side: str,
    symbol: str,
    notional: float,
    price: float,
    strategy: str = "",
    score: Optional[float] = None,
    status: str = "executed",
    reason: str = "",
) -> bool:
    """Push a formatted trade execution alert."""
    side_upper = side.upper()
    emoji = "🟢" if side_upper == "BUY" else "🔴"
    lines = [
        f"{emoji} *{side_upper} {status.upper()}*",
        f"*Symbol:* `{symbol}`",
        f"*Notional:* `${notional:,.2f}`",
        f"*Price:* `${price:,.4f}`",
    ]
    if strategy:
        lines.append(f"*Strategy:* `{strategy}`")
    if score is not None:
        lines.append(f"*Signal Score:* `{score:.1f}/100`")
    if reason:
        # Wrap the reason block nicely
        lines.append(f"\n*Reason/Context:*\n{reason}")
    return send_message("\n".join(lines))


def send_position_closed(
    symbol: str,
    pnl_usd: float,
    pnl_pct: float,
    reason: str = "",
) -> bool:
    """Push a formatted position-close notification."""
    emoji = "✅" if pnl_usd >= 0 else "⛔"
    sign = "+" if pnl_usd >= 0 else ""
    lines = [
        f"{emoji} *POSITION CLOSED*",
        f"*Symbol:* `{symbol}`",
        f"*P&L:* `{sign}${pnl_usd:,.2f}` ({sign}{pnl_pct:.2f}%)",
    ]
    if reason:
        lines.append(f"*Reason:* {reason}")
    return send_message("\n".join(lines))


def send_risk_halt(reason: str) -> bool:
    """Push a critical risk-halt notification."""
    msg = f"🚨 *RISK HALT TRIGGERED*\n{reason}"
    return send_message(msg)


def send_bot_status(running: bool, detail: str = "") -> bool:
    """Push a bot start/stop notification."""
    emoji = "▶️" if running else "⏹️"
    state_str = "STARTED" if running else "STOPPED"
    msg = f"{emoji} *Bot {state_str}*"
    if detail:
        msg += f"\n{detail}"
    return send_message(msg)


def send_portfolio_summary(
    equity: float,
    cash: float,
    positions: list,
) -> bool:
    """Push a formatted portfolio snapshot as a reply to status queries."""
    total_pnl = sum(float(p.get("unrealized_pl", 0) or 0) for p in positions)
    pnl_sign = "+" if total_pnl >= 0 else ""
    lines = [
        "📊 *Portfolio Snapshot*",
        f"*Equity:* `${equity:,.2f}`",
        f"*Cash:* `${cash:,.2f}`",
        f"*Unrealized P&L:* `{pnl_sign}${total_pnl:,.2f}`",
        f"*Open Positions:* `{len(positions)}`",
    ]
    if positions:
        lines.append("\n*Positions:*")
        for p in positions:
            sym = p.get("symbol", "???")
            pl = float(p.get("unrealized_pl", 0) or 0)
            pl_pct = float(p.get("unrealized_plpc", 0) or 0) * 100
            sign = "+" if pl >= 0 else ""
            lines.append(f"  • `{sym}` — {sign}${pl:,.2f} ({sign}{pl_pct:.2f}%)")
    return send_message("\n".join(lines))

def send_performance_summary(perf: dict) -> bool:
    """Push a formatted time-series PNL snapshot."""
    if not perf:
        return send_message("📭 Wait a bit longer, no equity history recorded yet.")
        
    def _fmt(block: dict) -> str:
        if not block: return "N/A"
        usd = float(block.get("usd", 0.0))
        pct = float(block.get("pct", 0.0))
        sign = "+" if usd >= 0 else ""
        return f"{sign}${usd:,.2f} ({sign}{pct:.2f}%)"

    lines = [
        "📈 *Equity Performance*",
        f"*Current Equity:* `${perf.get('current', 0.0):,.2f}`",
        "",
        f"*1h:* `{_fmt(perf.get('1h', {}))}`",
        f"*1d:* `{_fmt(perf.get('24h', {}))}`",
        f"*1w:* `{_fmt(perf.get('7d', {}))}`",
    ]
    return send_message("\n".join(lines))


def send_oracle_update(
    score: float,
    regime: str,
    summary: str = "",
    coins: Optional[list] = None,
) -> bool:
    """Push an oracle refresh alert with current macro sentiment."""
    regime_emoji = {
        "bullish": "🟢", "bearish": "🔴", "volatile": "🟡",
        "ranging": "⚪", "normal": "🔵",
    }.get(regime.lower(), "🔵")
    lines = [
        f"{regime_emoji} *Oracle Refreshed*",
        f"*Macro Score:* `{score:.2f}x` | *Regime:* `{regime}`",
    ]
    if summary:
        lines.append(f"*Summary:* {summary[:200]}")
    if coins:
        coin_str = ", ".join([f"`{c}`" for c in coins[:5]])
        lines.append(f"*Watching:* {coin_str}")
    return send_message("\n".join(lines))


def send_equity_guard_alert(
    guard_type: str,  # "soft" or "hard"
    pct_1h: float,
    equity: float,
    halt_min: int = 0,
) -> bool:
    """Push an equity drawdown guard alert."""
    if guard_type == "hard":
        lines = [
            f"🚨 *EQUITY GUARD — HARD HALT*",
            f"*Equity dropped:* `{pct_1h:.2f}%` in the last hour",
            f"*Current Equity:* `${equity:,.2f}`",
            f"*Action:* Bot soft-halted for `{halt_min} minutes`. Sells still active.",
        ]
    else:
        lines = [
            f"⚠️ *EQUITY GUARD — Soft Alert*",
            f"*Equity dropped:* `{pct_1h:.2f}%` in the last hour",
            f"*Current Equity:* `${equity:,.2f}`",
            f"*Action:* Signal floor raised +12pts, notional halved.",
        ]
    return send_message("\n".join(lines))


def send_frequency_block(
    reason: str,  # "hourly_cap" or "trade_spacing"
    symbol: str = "",
    detail: str = "",
) -> bool:
    """Push a frequency control block notification."""
    if reason == "hourly_cap":
        msg = f"⏳ *Hourly Trade Cap Reached* — bot is waiting for the next window to open."
    elif reason == "trade_spacing":
        msg = f"⏱ *Trade Spacing Active* for `{symbol}` — {detail}"
    else:
        msg = f"🚫 *Frequency Block*: {detail}"
    return send_message(msg)


def send_daily_digest(equity: float, pnl_usd: float, pnl_pct: float, trades: int) -> bool:
    """Push a brief daily equity digest (called once per 24h cycle reset)."""
    sign = "+" if pnl_usd >= 0 else ""
    emoji = "📈" if pnl_usd >= 0 else "📉"
    lines = [
        f"{emoji} *Daily Digest*",
        f"*Equity:* `${equity:,.2f}`",
        f"*24h P&L:* `{sign}${pnl_usd:,.2f}` ({sign}{pnl_pct:.2f}%)",
        f"*Total Trades Today:* `{trades}`",
    ]
    return send_message("\n".join(lines))
