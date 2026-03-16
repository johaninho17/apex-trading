import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
ENV_FILE = BACKEND_DIR / '.env'
CONFIG_FILE = BACKEND_DIR / 'config.json'
TEMP_CONFIG = Path(tempfile.gettempdir()) / 'apex_drl_event_fusion_paper_smoke.json'

if ENV_FILE.exists():
    load_dotenv(ENV_FILE, override=True)

cfg = json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
crypto = cfg.setdefault('stocks', {}).setdefault('crypto', {})
crypto['active_brain'] = 'drl_event_fusion'
crypto['trading_mode'] = 'live'
crypto['account_mode'] = 'paper'
crypto['symbols'] = ['SOL/USD', 'ETH/USD', 'BTC/USD', 'LINK/USD', 'DOGE/USD', 'AVAX/USD']
crypto['max_total_trades_per_day'] = 1
crypto['max_trades_per_coin_per_day'] = 1
crypto['max_trades_per_hour'] = 1
crypto['trade_spacing_min'] = 999
crypto['min_signal_score'] = 68.0
crypto['short_term']['base_notional'] = 11.0
crypto['short_term']['breakout_notional'] = 11.0
crypto['long_term']['dca_notional'] = 11.0
crypto['long_term']['crossover_notional'] = 11.0
crypto['notional_pct_of_cash'] = 0.0
crypto['use_compounding_pct'] = 0.0
crypto['news'] = {
    'enabled': True,
    'watchlist_only': True,
    'binance_poll_sec': 10,
    'crypto_news_poll_sec': 120,
    'event_default_ttl_min': 180,
    'dedupe_window_min': 240,
    'sources': {'binance': True, 'cryptopanic': True},
}
crypto['brains'] = {
    'drl_event_fusion': {
        'enabled': True,
        'event_bonus_cap': 25.0,
        'macro_bonus_cap': 15.0,
        'hard_veto_enabled': True,
        'golden_trade_min_score': 90.0,
        'use_gemini_macro': True,
        'use_ollama_events': True,
    }
}
TEMP_CONFIG.write_text(json.dumps(cfg, indent=2) + '\n', encoding='utf-8')
os.environ['APEX_CONFIG_FILE'] = str(TEMP_CONFIG)
os.environ.setdefault('NUMBA_DISABLE_JIT', '1')

from services.crypto import bot as crypto_bot
from services.crypto import market_data, news_pipeline, store, strategy
from services.crypto.execution import close_crypto_position, place_crypto_order
from services.crypto.indicators import enrich_indicators, snapshot
from services.crypto.oracle import get_current_oracle_state, get_macro_score, refresh_oracle_score


def heading(title: str) -> None:
    print('\n' + '=' * 72)
    print(title)
    print('=' * 72)


def choose_test_symbol(cfg_symbols):
    positions = market_data.get_crypto_positions(mode='paper')
    held = {str(p.get('symbol', '')).upper() for p in positions if float(p.get('qty', 0.0) or 0.0) > 0}
    for symbol in cfg_symbols:
        if symbol.upper() not in held:
            return symbol, held
    return (cfg_symbols[0] if cfg_symbols else 'BTC/USD'), held


def wait_for_position(symbol: str, timeout_sec: int = 25):
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        positions = market_data.get_crypto_positions(mode='paper')
        for pos in positions:
            if str(pos.get('symbol', '')).upper() == symbol.upper() and float(pos.get('qty', 0.0) or 0.0) > 0:
                return pos
        time.sleep(2)
    return None


async def run() -> None:
    cfg_live = crypto_bot.current_crypto_config()
    heading('DRL Event Fusion Paper Smoke Test')
    print('active_brain =', cfg_live.get('active_brain'))
    print('trading_mode =', cfg_live.get('trading_mode'))
    print('account_mode =', cfg_live.get('account_mode'))
    print('symbols      =', cfg_live.get('symbols'))

    account = market_data.get_account_summary(mode='paper')
    print('paper_equity =', account.get('equity'))
    print('paper_cash   =', account.get('cash'))

    symbol, held = choose_test_symbol(cfg_live.get('symbols', []))
    base_asset = symbol.split('/')[0]
    print('held_symbols =', sorted(held))
    print('test_symbol  =', symbol)

    heading('Feed Fetch')
    binance_items = news_pipeline.fetch_binance_announcements_sync(limit=5)
    cryptopanic_rate_limited = False
    try:
        cryptopanic_items = news_pipeline.fetch_cryptopanic_posts_sync(base_assets=[base_asset], limit=5)
    except Exception as exc:
        if isinstance(exc, getattr(news_pipeline, "_CryptoPanicRateLimited", tuple())):
            cryptopanic_rate_limited = True
            cryptopanic_items = []
        else:
            raise
    print('binance_count     =', len(binance_items))
    for item in binance_items[:3]:
        print('  BINANCE:', item.get('title', '')[:120])
    print('cryptopanic_rate_limited =', cryptopanic_rate_limited)
    print('cryptopanic_count =', len(cryptopanic_items))
    for item in cryptopanic_items[:3]:
        print('  CRYPTOPANIC:', item.get('title', '')[:120])

    heading('Macro Oracle')
    try:
        refreshed = refresh_oracle_score()
        print('oracle_refresh_score =', refreshed)
    except Exception as exc:
        print('oracle_refresh_error =', str(exc))
    oracle_state = get_current_oracle_state()
    macro_score = get_macro_score()
    print('macro_score   =', macro_score)
    print('market_regime =', oracle_state.get('market_regime'))
    print('summary       =', oracle_state.get('rationale_summary'))

    heading('Synthetic Event Seed')
    previous_state = store.get_symbol_event_state_sync(symbol)
    now_ms = int(time.time() * 1000)
    synthetic_item = {
        'source': 'manual',
        'title': f'{base_asset} ETF approval momentum accelerates institutional demand',
        'body': f'{base_asset} sees sustained institutional demand and positive ETF-related flows.',
        'base_asset': base_asset,
        'url': f'smoke://{base_asset.lower()}-etf',
    }
    parsed = news_pipeline.classify_news_item_with_ollama(synthetic_item, model_name=str(cfg_live.get('ollama_model', 'qwen3:8b')))
    synthetic_event = {
        'id': f'smoke:{base_asset}:{now_ms}',
        'source': 'manual',
        'symbol': symbol,
        'base_asset': base_asset,
        'event_type': str(parsed.get('event_type', 'headline')),
        'headline': synthetic_item['title'],
        'published_at': now_ms,
        'ttl_minutes': int(parsed.get('ttl_minutes', 180) or 180),
        'expires_at': now_ms + int(parsed.get('ttl_minutes', 180) or 180) * 60000,
        'sentiment': str(parsed.get('sentiment', 'neutral')),
        'impact_score': float(parsed.get('impact_score', 0.0) or 0.0),
        'confidence': float(parsed.get('confidence', 0.0) or 0.0),
        'trade_bias': str(parsed.get('trade_bias', 'watch_only')),
        'event_type': str(parsed.get('event_type', 'headline')),
    }
    synthetic_state = news_pipeline._compute_symbol_event_state(symbol, [synthetic_event], now_ms=now_ms)
    store.upsert_symbol_event_state_sync(symbol, synthetic_state)
    print('parsed_event =', json.dumps(parsed, indent=2))
    print('event_state  =', json.dumps(store.get_symbol_event_state_sync(symbol), indent=2))

    try:
        heading('Live Signal Evaluation')
        bars_15m = market_data.fetch_bars(symbol, timeframe='15Min', limit=360, mode='paper')
        bars_1m = market_data.fetch_bars(symbol, timeframe='1Min', limit=30, mode='paper')
        print('bars_15m =', len(bars_15m) if bars_15m is not None else 0)
        print('bars_1m  =', len(bars_1m) if bars_1m is not None else 0)
        signal = await strategy.evaluate_symbol(
            symbol=symbol,
            bars_15m=bars_15m,
            bars_1m=bars_1m,
            cfg=cfg_live,
            now_ms=now_ms,
            macro_risk_mult=float(macro_score or 1.0),
            position=None,
            live_equity=float(account.get('equity', 0.0) or 0.0),
            total_exposure=0.0,
        )
        if signal:
            print('live_signal =', json.dumps(signal, indent=2, default=str))
        else:
            print('live_signal = None')

        if not signal or str(signal.get('side', '')).lower() != 'buy':
            enriched_15m = enrich_indicators(bars_15m, fast_ma=int(cfg_live.get('long_term', {}).get('ma_fast', 50)), slow_ma=int(cfg_live.get('long_term', {}).get('ma_slow', 200)))
            enriched_1m = enrich_indicators(bars_1m, fast_ma=9, slow_ma=21)
            s_15m = snapshot(enriched_15m)
            s_1m = snapshot(enriched_1m)
            seeded = strategy._apply_drl_event_fusion(
                symbol=symbol,
                cfg=cfg_live,
                bars_15m=bars_15m,
                s_15m=s_15m,
                s_1m=s_1m,
                macro_risk_mult=float(macro_score or 1.0),
                candidates=[
                    strategy._candidate(
                        'ai_macro',
                        'buy',
                        74.0,
                        float(s_1m.get('close', 0.0) or 0.0),
                        11.0,
                        'Smoke fallback candidate routed through drl_event_fusion.',
                        {'smoke_test': True},
                    )
                ],
            )
            signal = seeded[0] if seeded else None
            print('seeded_signal =', json.dumps(signal, indent=2, default=str) if signal else 'None')

        if not signal or str(signal.get('side', '')).lower() != 'buy':
            raise RuntimeError('No usable buy signal available for paper execution.')

        signal['notional'] = 11.0
        signal.setdefault('meta', {})
        signal['meta'].setdefault('brain_mode', 'drl_event_fusion')
        signal['meta'].setdefault('opening_strategy', str(signal.get('strategy', 'ai_macro')).lower())

        heading('Paper Buy Order')
        order = place_crypto_order(symbol=symbol, side='buy', order_type='market', notional=11.0, mode='paper')
        trace_id = crypto_bot._record_decision_trace(
            symbol,
            str(signal['meta'].get('brain_mode', 'drl_event_fusion')),
            signal,
            submitted=True,
            extra={'smoke_test': True, 'order': order, 'macro_score': macro_score, 'event_state': synthetic_state},
        )
        print('order =', json.dumps(order, indent=2, default=str))
        print('decision_trace_id =', trace_id)

        heading('Position Check')
        opened_position = wait_for_position(symbol)
        print('opened_position =', json.dumps(opened_position, indent=2, default=str) if opened_position else 'None')

        heading('Paper Close')
        close_result = close_crypto_position(symbol, mode='paper')
        print('close_result =', json.dumps(close_result, indent=2, default=str))

        heading('Stored Evidence')
        traces = store.list_brain_decision_traces_sync(limit=3, symbol=symbol)
        for row in traces[:3]:
            print('trace:', json.dumps(row, indent=2, default=str))
        print('final_event_state =', json.dumps(store.get_symbol_event_state_sync(symbol), indent=2))

    finally:
        restore_state = previous_state or {
            'symbol': symbol,
            'net_event_score': 0.0,
            'event_bias': 'neutral',
            'hard_veto': False,
            'golden_trade_flag': False,
            'top_event_id': '',
            'top_event_type': '',
            'top_event_score': 0.0,
            'event_count_active': 0,
            'updated_at': int(time.time() * 1000),
        }
        store.upsert_symbol_event_state_sync(symbol, restore_state)


if __name__ == '__main__':
    asyncio.run(run())
