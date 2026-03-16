import asyncio
import logging
from datetime import datetime
from typing import Any, Awaitable, Dict, Optional

logger = logging.getLogger("apex.background")

_LOOP: Optional[asyncio.AbstractEventLoop] = None
_TASKS: Dict[str, asyncio.Task] = {}
_DELAYED_TASKS: Dict[str, asyncio.Task] = {}
_SERVICE_ERRORS: Dict[str, str] = {}


def bind_current_loop() -> None:
    global _LOOP
    _LOOP = asyncio.get_running_loop()


def _get_loop() -> asyncio.AbstractEventLoop:
    try:
        return _LOOP or asyncio.get_running_loop()
    except RuntimeError as exc:
        raise RuntimeError("Background services loop is not bound.") from exc


def _task_done(service_name: str, task: asyncio.Task) -> None:
    _TASKS.pop(service_name, None)
    try:
        _DELAYED_TASKS.pop(service_name, None)
        task.result()
        _SERVICE_ERRORS.pop(service_name, None)
    except asyncio.CancelledError:
        logger.info("Background service stopped: %s", service_name)
        _SERVICE_ERRORS.pop(service_name, None)
    except Exception as exc:
        _SERVICE_ERRORS[service_name] = str(exc)
        logger.warning("Background service failed: %s (%s)", service_name, exc)


async def _report_cron_loop() -> None:
    logger.info("AI Report Scheduler started in manual opt-in mode.")
    now = datetime.now()
    last_daily = now.strftime("%Y-%m-%d")
    last_weekly = now.strftime("%Y-W%W")
    last_monthly = now.strftime("%Y-%m")

    while True:
        await asyncio.sleep(60)
        try:
            now = datetime.now()
            today_key = now.strftime("%Y-%m-%d")
            week_key = now.strftime("%Y-W%W")
            month_key = now.strftime("%Y-%m")

            from services.crypto.report_generator import generate_report
            from services.crypto.self_tuner import generate_and_apply_tuning_patch

            loop = asyncio.get_running_loop()

            if last_daily != today_key:
                await loop.run_in_executor(None, generate_report, "daily")
                last_daily = today_key
                await loop.run_in_executor(None, generate_and_apply_tuning_patch)
                await asyncio.sleep(10)

            if last_weekly != week_key:
                await loop.run_in_executor(None, generate_report, "weekly")
                last_weekly = week_key
                await asyncio.sleep(10)

            if last_monthly != month_key:
                await loop.run_in_executor(None, generate_report, "monthly")
                last_monthly = month_key
        except Exception as exc:
            logger.error("Report cron error: %s", exc)
            logger.info("Backing off report scheduler for 300s.")
            await asyncio.sleep(300)


async def _ollama_keepalive_loop() -> None:
    import httpx

    from core.config_manager import get_config
    from core.ollama import ollama_url

    def _model_name() -> str:
        cfg = get_config().get("stocks", {}).get("crypto", {})
        return str(cfg.get("ollama_model", "qwen3:8b") or "qwen3:8b")

    async def _post_keepalive(keep_alive: int, *, warm: bool) -> Dict[str, Any]:
        model_name = _model_name()
        payload: Dict[str, Any] = {
            "model": model_name,
            "prompt": "ping" if warm else "",
            "stream": False,
            "keep_alive": keep_alive,
        }
        if warm:
            payload["options"] = {"num_predict": 1}
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.post(
                ollama_url("/api/generate"),
                json=payload,
            )
            response.raise_for_status()
        return {"model": model_name, "keep_alive": keep_alive, "warm": warm}

    async def _loaded_model_names() -> set[str]:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(ollama_url("/api/ps"))
            response.raise_for_status()
            payload = response.json()
        return {
            str(item.get("name", "") or "").strip()
            for item in (payload.get("models") or [])
            if str(item.get("name", "") or "").strip()
        }

    logger.info("Ollama Keepalive started (manual opt-in).")
    try:
        while True:
            try:
                model_name = _model_name()
                loaded_models = await _loaded_model_names()
                if model_name not in loaded_models:
                    payload = await _post_keepalive(-1, warm=True)
                    logger.info("Ollama model pinned for bot runtime: %s", payload["model"])
                else:
                    logger.debug("Ollama model already loaded for bot runtime: %s", model_name)
                _SERVICE_ERRORS.pop("ollama", None)
            except Exception as exc:
                _SERVICE_ERRORS["ollama"] = str(exc)
                logger.warning("Ollama keepalive failed: %s", exc)
            await asyncio.sleep(600)
    except asyncio.CancelledError:
        try:
            payload = await _post_keepalive(0, warm=False)
            logger.info("Ollama model unloaded: %s", payload["model"])
        except Exception as exc:
            logger.debug("Ollama unload failed: %s", exc)
        raise


def _build_service_coro(service_name: str) -> Awaitable[Any]:
    if service_name == "oracle":
        from services.crypto.oracle import oracle_cron_loop
        return oracle_cron_loop()
    if service_name == "news":
        from services.crypto.news_pipeline import news_cron_loop
        return news_cron_loop()
    if service_name == "reports":
        return _report_cron_loop()
    if service_name == "telegram":
        from integrations.telegram.bot_process import start_telegram_bot
        return start_telegram_bot()
    if service_name == "ollama":
        return _ollama_keepalive_loop()
    if service_name == "ml":
        from services.crypto.ml.ml_cron import ml_cron_loop
        return ml_cron_loop()
    raise KeyError(f"Unknown background service: {service_name}")


def _service_running(service_name: str) -> bool:
    task = _TASKS.get(service_name)
    return bool(task and not task.done())


async def ensure_started(service_name: str) -> bool:
    if _service_running(service_name):
        return False
    _SERVICE_ERRORS.pop(service_name, None)
    loop = _get_loop()
    task = loop.create_task(_build_service_coro(service_name), name=f"apex:{service_name}")
    _TASKS[service_name] = task
    task.add_done_callback(lambda t, name=service_name: _task_done(name, t))
    logger.info("Background service started: %s", service_name)
    return True


async def ensure_started_delayed(service_name: str, delay_sec: int) -> bool:
    if _service_running(service_name):
        return False
    pending = _DELAYED_TASKS.get(service_name)
    if pending and not pending.done():
        return False

    async def _delayed() -> None:
        try:
            await asyncio.sleep(max(0, int(delay_sec)))
            await ensure_started(service_name)
        except asyncio.CancelledError:
            return

    loop = _get_loop()
    task = loop.create_task(_delayed(), name=f"apex:delay:{service_name}")
    _DELAYED_TASKS[service_name] = task
    task.add_done_callback(lambda t, name=service_name: _DELAYED_TASKS.pop(name, None))
    logger.info("Background service scheduled: %s in %ss", service_name, delay_sec)
    return True


async def stop_service(service_name: str) -> bool:
    stopped = False
    delayed = _DELAYED_TASKS.pop(service_name, None)
    if delayed and not delayed.done():
        delayed.cancel()
        stopped = True
    task = _TASKS.pop(service_name, None)
    if task and not task.done():
        task.cancel()
        stopped = True
        try:
            await task
        except asyncio.CancelledError:
            pass
    return stopped


async def shutdown_all_services() -> None:
    names = list(set(_TASKS.keys()) | set(_DELAYED_TASKS.keys()))
    for name in names:
        await stop_service(name)


def _startup_cfg() -> Dict[str, Any]:
    from core.config_manager import get_config
    return get_config().get("stocks", {}).get("crypto", {}).get("startup_services", {})


async def ensure_boot_services() -> None:
    cfg = _startup_cfg()
    async with asyncio.TaskGroup() as task_group:
        for service_name in ("oracle", "news", "reports", "telegram", "ollama"):
            if bool(cfg.get(f"start_{service_name}_on_backend_boot", False)):
                delay = max(0, int(cfg.get(f"{service_name}_backend_boot_delay_sec", 0) or 0))
                if delay > 0:
                    task_group.create_task(ensure_started_delayed(service_name, delay))
                else:
                    task_group.create_task(ensure_started(service_name))
        if bool(cfg.get("start_ml_on_backend_boot", False)):
            delay = int(cfg.get("ml_delayed_start_sec", 300) or 300)
            task_group.create_task(ensure_started_delayed("ml", delay))


async def ensure_crypto_runtime_services() -> Dict[str, bool]:
    from core.config_manager import get_config

    cfg = get_config().get("stocks", {}).get("crypto", {}).get("startup_services", {})
    started: Dict[str, bool] = {}
    if bool(cfg.get("start_oracle_with_bot", True)):
        started["oracle"] = await ensure_started("oracle")
    if bool(cfg.get("start_news_with_bot", True)):
        started["news"] = await ensure_started("news")
    if bool(cfg.get("start_reports_with_bot", True)):
        started["reports"] = await ensure_started("reports")
    if bool(cfg.get("start_ollama_with_bot", True)):
        started["ollama"] = await ensure_started("ollama")
    if bool(cfg.get("start_telegram_with_bot", False)):
        started["telegram"] = await ensure_started("telegram")
    if bool(cfg.get("start_ml_with_bot", False)):
        delay = int(cfg.get("ml_delayed_start_sec", 300) or 300)
        started["ml"] = await ensure_started_delayed("ml", delay)
    return started


async def stop_crypto_runtime_services() -> None:
    cfg = _startup_cfg()
    for service_name in ("oracle", "news", "reports", "ollama"):
        if not bool(cfg.get(f"start_{service_name}_on_backend_boot", False)):
            await stop_service(service_name)
    if bool(cfg.get("start_telegram_with_bot", False)) and not bool(cfg.get("start_telegram_on_backend_boot", False)):
        await stop_service("telegram")
    if bool(cfg.get("start_ml_with_bot", False)) and not bool(cfg.get("start_ml_on_backend_boot", False)):
        await stop_service("ml")


def service_status() -> Dict[str, Any]:
    return {
        "running": sorted([name for name, task in _TASKS.items() if task and not task.done()]),
        "scheduled": sorted([name for name, task in _DELAYED_TASKS.items() if task and not task.done()]),
        "errors": dict(_SERVICE_ERRORS),
    }
