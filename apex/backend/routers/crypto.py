from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from core import background_services
from core.config_manager import update_config
from integrations.alpaca.runtime_config import save_trading_mode
from services.crypto import (
    current_crypto_config,
    flatten_all_positions,
    place_crypto_order,
    save_crypto_config,
    start_bot,
    stop_bot,
)
from services.crypto import store
from services.crypto.simulation import run_simulation, simulation_defaults
from services.crypto.terminal_data import (
    get_asset_catalog,
    get_activity_feed,
    get_brain_overview,
    get_crypto_health,
    get_equity_history_view,
    get_learning_overview,
    get_reports_overview,
    get_scanner,
    get_tracked_coins,
    get_symbol_brain,
    get_symbol_decisions,
    get_symbol_history,
    get_symbol_learning,
    get_symbol_news,
    get_symbol_overview,
    get_symbol_raw_context,
    get_symbol_reports,
    get_symbol_snapshot,
    get_symbol_trades,
    get_terminal_summary,
    get_universe_view,
)

router = APIRouter()
compat_router = APIRouter()

BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class CryptoOrderRequest(BaseModel):
    symbol: str
    side: str = "buy"
    order_type: str = "market"
    qty: Optional[float] = None
    notional: Optional[float] = None
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    time_in_force: str = "gtc"


class CryptoConfigUpdateRequest(BaseModel):
    updates: Dict[str, Any] = Field(default_factory=dict)


class TrackedSymbolRequest(BaseModel):
    symbol: str
    saved: bool = True


class CryptoSimulationRequest(BaseModel):
    scope: str = "single"
    symbols: list[str] = Field(default_factory=list)
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    initial_equity: float = 10000.0
    macro_score: float = 1.0
    slippage_pct: float = 0.05
    commission_pct: float = 0.0
    max_position_pct: float = 0.10
    limit: int = 8


def _success_status(payload: Dict[str, Any], *, fallback: str = "fresh") -> Dict[str, Any]:
    result = dict(payload or {})
    result.setdefault("status", fallback)
    return result


def _launch_script(script_name: str, success_message: str) -> Dict[str, Any]:
    script_path = os.path.join(os.path.dirname(BACKEND_ROOT), script_name)
    if not os.path.exists(script_path):
        raise HTTPException(status_code=404, detail=f"{script_name} not found in apex root")

    state_file = os.path.join(BACKEND_ROOT, "data", "runtime_state.json")
    try:
        if os.path.exists(state_file):
            with open(state_file, "r", encoding="utf-8") as handle:
                state = json.load(handle)
        else:
            state = {}
        state["last_ai_training_ms"] = int(time.time() * 1000)
        os.makedirs(os.path.dirname(state_file), exist_ok=True)
        with open(state_file, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2)
    except Exception:
        pass

    subprocess.Popen(
        ["bash", script_path],
        cwd=os.path.dirname(BACKEND_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return {"success": True, "message": success_message}


async def _set_trading_mode(mode: str) -> Dict[str, Any]:
    normalized = "live" if str(mode or "").strip().lower() == "live" else "paper"
    await asyncio.to_thread(save_trading_mode, normalized)
    await asyncio.to_thread(update_config, {"stocks": {"crypto": {"account_mode": normalized}}})
    return {"message": "Trading mode updated", "mode": normalized}


@router.get("/health")
async def crypto_health():
    return _success_status(await get_crypto_health(), fallback="fresh")


@router.get("/terminal/summary")
async def terminal_summary(prefer_cached: bool = True):
    return _success_status(await get_terminal_summary(prefer_cached=prefer_cached))


@router.get("/terminal/equity-history")
async def terminal_equity_history(since_ms: int = 0):
    return _success_status(await asyncio.to_thread(get_equity_history_view, since_ms))


@router.get("/terminal/performance")
async def terminal_performance():
    performance = await store.get_equity_performance()
    return {"status": "fresh", "performance": performance}


@router.get("/scanner")
async def scanner(limit: int = 10):
    return _success_status(await asyncio.to_thread(get_scanner, limit))


@router.get("/tracked")
async def tracked(limit: int = 80):
    return _success_status(await asyncio.to_thread(get_tracked_coins, limit))


@router.get("/assets")
async def assets(query: str = "", limit: int = 40, tradable_only: bool = True):
    return _success_status(await asyncio.to_thread(get_asset_catalog, query, limit, tradable_only))


@router.get("/universe")
async def universe(limit: int = 30):
    return _success_status(await asyncio.to_thread(get_universe_view, limit))


@router.get("/symbol/{symbol:path}/overview")
async def symbol_overview(symbol: str):
    return _success_status(await get_symbol_overview(symbol))


@router.get("/symbol/{symbol:path}/snapshot")
async def symbol_snapshot(symbol: str):
    return _success_status(await get_symbol_snapshot(symbol))


@router.get("/symbol/{symbol:path}/history")
async def symbol_history(symbol: str, range_key: str = Query("1D")):
    return _success_status(await asyncio.to_thread(get_symbol_history, symbol, range_key))


@router.get("/symbol/{symbol:path}/trades")
async def symbol_trades(symbol: str, limit: int = 25):
    return _success_status(await asyncio.to_thread(get_symbol_trades, symbol, limit))


@router.get("/symbol/{symbol:path}/decisions")
async def symbol_decisions(symbol: str, limit: int = 25):
    return _success_status(await asyncio.to_thread(get_symbol_decisions, symbol, limit))


@router.get("/symbol/{symbol:path}/learning")
async def symbol_learning(symbol: str):
    return _success_status(await asyncio.to_thread(get_symbol_learning, symbol))


@router.get("/symbol/{symbol:path}/brain")
async def symbol_brain(symbol: str):
    return _success_status(await asyncio.to_thread(get_symbol_brain, symbol))


@router.get("/symbol/{symbol:path}/news")
async def symbol_news(symbol: str, limit: int = 20, since_ms: int = 0):
    return _success_status(await asyncio.to_thread(get_symbol_news, symbol, limit, since_ms))


@router.get("/symbol/{symbol:path}/reports")
async def symbol_reports(symbol: str):
    return _success_status(await asyncio.to_thread(get_symbol_reports, symbol))


@router.get("/symbol/{symbol:path}/raw-context")
async def symbol_raw_context(symbol: str, ts: int = 0, window_ms: int = 3600000):
    return _success_status(await asyncio.to_thread(get_symbol_raw_context, symbol, ts, window_ms))


@router.get("/activity/feed")
async def activity_feed(
    limit: int = 100,
    before_ts: Optional[int] = None,
    status: str = "",
    symbol: str = "",
    search: str = "",
):
    return _success_status(
        await asyncio.to_thread(
            get_activity_feed,
            limit=limit,
            before_ts=before_ts,
            status=status,
            symbol=symbol,
            search=search,
        )
    )


@router.get("/activity/history")
async def activity_history(since_ms: int = 0, limit: int = 1000):
    history = await store.get_activity_history(since_ms=since_ms, limit=limit)
    return {"status": "fresh", "history": history}


@router.get("/reports")
async def reports(limit_per_type: int = 25):
    return _success_status(await asyncio.to_thread(get_reports_overview, limit_per_type))


@router.get("/learning/overview")
async def learning_overview():
    return _success_status(await asyncio.to_thread(get_learning_overview))


@router.get("/brain/overview")
async def brain_overview():
    return _success_status(await asyncio.to_thread(get_brain_overview))


@router.get("/simulation/config")
async def simulation_config():
    cfg = await asyncio.to_thread(current_crypto_config, False)
    account_mode = str(cfg.get("account_mode", "paper") or "paper")
    return _success_status(await asyncio.to_thread(simulation_defaults, account_mode))


@router.post("/simulation/run")
async def simulation_run(request: CryptoSimulationRequest):
    cfg = await asyncio.to_thread(current_crypto_config, False)
    account_mode = str(cfg.get("account_mode", "paper") or "paper")
    payload = await run_simulation(
        scope=request.scope,
        symbols=request.symbols,
        account_mode=account_mode,
        start_date=request.start_date,
        end_date=request.end_date,
        initial_equity=request.initial_equity,
        macro_score=request.macro_score,
        slippage_pct=request.slippage_pct,
        commission_pct=request.commission_pct,
        max_position_pct=request.max_position_pct,
        limit=request.limit,
    )
    return _success_status(payload)


@router.get("/bot/status")
async def bot_status():
    from services.crypto import get_bot_status

    return {"status": "fresh", **(await get_bot_status())}


@router.get("/bot/config")
async def bot_config():
    config = await asyncio.to_thread(current_crypto_config, False)
    return {"status": "fresh", "config": config}


@router.post("/bot/config")
async def bot_config_update(request: CryptoConfigUpdateRequest):
    config = await asyncio.to_thread(save_crypto_config, request.updates or {})
    return {"status": "fresh", "config": config}


@router.post("/tracked/save")
async def tracked_save(request: TrackedSymbolRequest):
    symbol = str(request.symbol or "").strip().upper()
    if not symbol:
        raise HTTPException(status_code=400, detail="symbol is required")
    cfg = await asyncio.to_thread(current_crypto_config, False)
    account_mode = str(cfg.get("account_mode", "paper") or "paper")
    row = await asyncio.to_thread(store.set_tracked_symbol_saved_sync, symbol, bool(request.saved), account_mode)
    return {"status": "fresh", "item": row}


@router.post("/bot/start")
async def bot_start():
    status = await asyncio.to_thread(start_bot)
    services = await background_services.ensure_crypto_runtime_services()
    return {"status": "fresh", "success": True, "runtime": status, "services": services}


@router.post("/bot/stop")
async def bot_stop():
    status = await asyncio.to_thread(stop_bot)
    await background_services.stop_crypto_runtime_services()
    return {"status": "fresh", "success": True, "runtime": status}


@router.post("/bot/train")
async def bot_train():
    return _launch_script("train.sh", "ML training pipeline initiated in the background.")


@router.post("/bot/finetune")
async def bot_finetune():
    return _launch_script("finetune.sh", "Fast fine-tune initiated in the background.")


@router.post("/bot/flatten")
async def bot_flatten():
    result = await asyncio.to_thread(flatten_all_positions)
    return {"status": "fresh", "success": True, "result": result}


@router.post("/order")
async def order(request: CryptoOrderRequest):
    order_result = await asyncio.to_thread(
        place_crypto_order,
        request.symbol,
        request.side,
        request.order_type,
        request.qty,
        request.notional,
        request.limit_price,
        request.stop_price,
        request.time_in_force,
    )
    await store.record_action(
        action_type="manual_order",
        symbol=request.symbol,
        side=request.side,
        qty=request.qty,
        notional=request.notional,
        price=order_result.get("filled_avg_price") or request.limit_price,
        status="success",
        reason="Manual crypto order submitted from UI.",
        payload=order_result,
    )
    return {"status": "fresh", "success": True, "order": order_result}


@router.post("/settings/trading-mode")
async def set_trading_mode(mode: str = Query("paper")):
    return await _set_trading_mode(mode)


@compat_router.get("/crypto/health")
async def compat_health():
    return await crypto_health()


@compat_router.get("/crypto/dashboard/summary")
async def compat_terminal_summary(prefer_cached: bool = True):
    return await terminal_summary(prefer_cached=prefer_cached)


@compat_router.get("/crypto/equity-history")
async def compat_equity_history(since_ms: int = 0):
    return await terminal_equity_history(since_ms=since_ms)


@compat_router.get("/crypto/performance")
async def compat_performance():
    return await terminal_performance()


@compat_router.get("/crypto/hub")
async def compat_hub(limit: int = 30):
    universe_payload = await universe(limit=limit)
    items = []
    for row in universe_payload.get("items", []):
        item = dict(row)
        item["bias"] = item.get("event_bias", "neutral")
        items.append(item)
    return {"top_coins": items, "status": universe_payload.get("status", "fresh")}


@compat_router.get("/crypto/actions")
async def compat_actions(
    limit: int = 100,
    before_ts: Optional[int] = None,
    status: str = "",
    symbol: str = "",
    search: str = "",
):
    return await activity_feed(limit=limit, before_ts=before_ts, status=status, symbol=symbol, search=search)


@compat_router.get("/crypto/activity-history")
async def compat_activity_history(since_ms: int = 0, limit: int = 1000):
    return await activity_history(since_ms=since_ms, limit=limit)


@compat_router.get("/crypto/bot/status")
async def compat_bot_status():
    return await bot_status()


@compat_router.get("/crypto/bot/config")
async def compat_bot_config():
    return await bot_config()


@compat_router.post("/crypto/bot/config")
async def compat_bot_config_update(request: CryptoConfigUpdateRequest):
    return await bot_config_update(request)


@compat_router.post("/crypto/bot/start")
async def compat_bot_start():
    return await bot_start()


@compat_router.post("/crypto/bot/stop")
async def compat_bot_stop():
    return await bot_stop()


@compat_router.post("/crypto/bot/train")
async def compat_bot_train():
    return await bot_train()


@compat_router.post("/crypto/bot/finetune")
async def compat_bot_finetune():
    return await bot_finetune()


@compat_router.post("/crypto/flatten")
async def compat_flatten():
    return await bot_flatten()


@compat_router.post("/crypto/order")
async def compat_order(request: CryptoOrderRequest):
    return await order(request)


@compat_router.get("/crypto/bot/reports")
async def compat_reports(limit_per_type: int = 25):
    return await reports(limit_per_type=limit_per_type)


@compat_router.get("/crypto/bot/reports/history")
async def compat_reports_history(limit: int = 25):
    return await reports(limit_per_type=limit)


@compat_router.get("/crypto/analytics/brain-status")
async def compat_brain_overview():
    return await brain_overview()


@compat_router.get("/crypto/ml-status")
async def compat_learning_overview():
    return await learning_overview()


@compat_router.get("/crypto/scanner")
async def compat_scanner(limit: int = 10):
    return await scanner(limit=limit)


@compat_router.get("/crypto/detail/symbol/overview")
async def compat_symbol_overview(symbol: str = Query(...)):
    return await symbol_overview(symbol=symbol)


@compat_router.get("/crypto/detail/symbol/snapshot")
async def compat_symbol_snapshot(symbol: str = Query(...)):
    return await symbol_snapshot(symbol=symbol)


@compat_router.get("/crypto/detail/symbol/history")
async def compat_symbol_history(symbol: str = Query(...), range_key: str = Query("1D")):
    return await symbol_history(symbol=symbol, range_key=range_key)


@compat_router.get("/crypto/detail/symbol/trades")
async def compat_symbol_trades(symbol: str = Query(...), limit: int = 25):
    return await symbol_trades(symbol=symbol, limit=limit)


@compat_router.get("/crypto/detail/symbol/scores")
async def compat_symbol_scores(symbol: str = Query(...), limit: int = 25):
    return await symbol_decisions(symbol=symbol, limit=limit)


@compat_router.get("/crypto/detail/symbol/news")
async def compat_symbol_news(symbol: str = Query(...), limit: int = 20, since_ms: int = 0):
    return await symbol_news(symbol=symbol, limit=limit, since_ms=since_ms)


@compat_router.get("/crypto/detail/symbol/risk")
async def compat_symbol_brain(symbol: str = Query(...)):
    return await symbol_brain(symbol=symbol)


@compat_router.get("/crypto/detail/report")
async def compat_report_detail(report_type: str = Query("hourly"), ts: int = 0):
    from services.crypto.report_generator import get_all_latest_reports, get_historical_reports

    report_type = str(report_type or "hourly").lower()
    if int(ts or 0) > 0:
        history = get_historical_reports(100).get(report_type, [])
        report = next((row for row in history if int(row.get("ts", 0) or 0) == int(ts)), None)
    else:
        report = (get_all_latest_reports() or {}).get(report_type)
    return {"report_type": report_type, "report": report}


@compat_router.get("/crypto/detail/context-window")
async def compat_raw_context(symbol: str = Query(""), ts: int = 0, window_ms: int = 3600000):
    return await symbol_raw_context(symbol=symbol, ts=ts, window_ms=window_ms)


@compat_router.post("/settings/trading-mode")
async def compat_set_trading_mode(mode: str = Query("paper")):
    return await _set_trading_mode(mode)
