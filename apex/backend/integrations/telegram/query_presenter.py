"""Natural-language rendering for Telegram bot data queries."""

from __future__ import annotations

from typing import Any, Dict, List

from integrations.telegram import bot_knowledge


def format_money(value: Any) -> str:
    number = float(value or 0.0)
    sign = "-" if number < 0 else ""
    return f"{sign}${abs(number):,.2f}"


def format_pct(value: Any) -> str:
    number = float(value or 0.0)
    sign = "+" if number > 0 else ""
    return f"{sign}{number:.2f}%"


def _latest_strategy(bundle: Dict[str, Any]) -> str:
    for row in bundle.get("live_experience") or []:
        strategy = str(row.get("strategy_used") or row.get("opening_strategy") or "").strip()
        if strategy:
            return strategy
    return ""


def _config_subfocus(question: str) -> str:
    lower = str(question or "").lower()
    if "brain" in lower:
        return "brain"
    if "risk state" in lower or ("risk" in lower and "state" in lower):
        return "risk"
    if "governor" in lower or "pressure" in lower:
        return "governor"
    return "general"


def _follow_up_suggestions(plan: Dict[str, Any], question: str) -> List[str]:
    symbol = plan.get("symbol") or "this symbol"
    intent = plan.get("intent")
    if intent == "coin_card":
        return [f"how is {symbol} learning going", f"why is {symbol} blocked", f"show {symbol} news today"]
    if intent == "learning_card":
        return [f"how is the brain scoring {symbol}", f"show {symbol} decision traces", f"show {symbol} news today"]
    if intent == "brain_symbol":
        return [f"how is {symbol} learning going", f"why is {symbol} blocked", f"show raw evidence for {symbol}"]
    if intent == "tracked_status":
        return ["show oracle details", "show market breadth in news", "show latest reports"]
    if intent == "blocked_review":
        return [f"how is {symbol} learning going", f"show {symbol} decision traces", "show raw evidence"]
    if intent == "news_breadth":
        return ["show oracle details", "show tracked coins", "show latest reports"]
    if intent == "oracle_detail":
        return ["show market breadth in news", "show tracked coins", "show latest reports"]
    if intent == "position_status":
        return [f"why is {symbol} down", f"show {symbol} news today", f"show {symbol} entry scores"]
    if intent == "trade_history":
        return [f"why did we buy {symbol}", f"show raw evidence for {symbol}", f"what news affected {symbol}"]
    if intent == "config_status":
        subfocus = _config_subfocus(question)
        if subfocus == "brain":
            return ["what is my risk state", "what is my governor mode", "is the bot running"]
        if subfocus == "risk":
            return ["what is my current brain", "what is my governor mode", "show latest reports"]
        if subfocus == "governor":
            return ["what is my current brain", "what is my risk state", "show latest reports"]
        return ["what is my risk state", "what is my governor mode", "show latest reports"]
    if intent == "decision_review":
        return [
            f"show {symbol} decision traces" if symbol else "show recent decision traces",
            f"what news affected {symbol}" if symbol else "what news affected recent trades",
            "show raw evidence",
        ]
    if intent == "news_analysis":
        return [
            f"show raw evidence for {symbol}" if symbol else "show raw evidence",
            f"why are we still holding {symbol}" if symbol else "why are we still holding this position",
            "show latest reports",
        ]
    if intent == "event_edge_analysis":
        return ["show raw evidence", "which strategies lose the most money", "show last 10 losing trades"]
    return ["show raw evidence", "show latest reports", "what news affected this symbol"]


def _tracked_state_line(metrics: Dict[str, Any]) -> str:
    state = str(metrics.get("tracked_active_state", "idle") or "idle")
    reason = str(metrics.get("tracked_active_reason", "") or "")
    tier = str(metrics.get("tracked_monitor_tier", "eligible") or "eligible")
    saved = bool(metrics.get("tracked_saved_manual"))
    parts = [f"Tracked state is {state}"]
    if reason:
        parts.append(f"because {reason}")
    parts.append(f"with monitor tier {tier}")
    if saved:
        parts.append("and it is manually saved")
    return " ".join(parts) + "."


def _news_context_line(event_state: Dict[str, Any]) -> str:
    if not event_state:
        return ""
    news_context_state = str(event_state.get("news_context_state", "") or "")
    event_bias = event_state.get("event_bias", "neutral")
    score = float(event_state.get("net_event_score", 0.0) or 0.0)
    if news_context_state == "unavailable":
        return f"News context is unavailable right now, so the event layer stays neutral by default at {score:+.1f} unless real events are cached."
    if news_context_state == "fresh_no_active_events":
        return f"Fresh news coverage found no active catalyst for this symbol, so event context is effectively neutral at {score:+.1f}."
    if news_context_state == "stale":
        return f"News context is stale, so the cached event bias of {event_bias} at {score:+.1f} should be treated cautiously."
    return f"Event context is {event_bias} with a cached event score of {score:+.1f}."


def _outcome_summary_lines(metrics: Dict[str, Any]) -> List[str]:
    by_horizon = dict(metrics.get("decision_outcomes_by_horizon") or {})
    if not by_horizon:
        return []
    lines = ["Outcome coverage by horizon:"]
    for horizon, bucket in sorted(by_horizon.items(), key=lambda item: item[0]):
        avg = bucket.get("avg_pnl_pct")
        avg_text = "n/a" if avg is None else format_pct(avg)
        lines.append(f"- {horizon}: {int(bucket.get('count', 0) or 0)} outcome(s), avg {avg_text}")
    return lines


def render_natural_response(question: str, bundle: Dict[str, Any]) -> str:
    plan = bundle.get("plan") or {}
    metrics = bundle.get("metrics") or {}
    symbol = plan.get("symbol") or "portfolio"
    macro = bundle.get("macro_consensus") or {}
    event_state = bundle.get("event_state") or {}
    reports = bundle.get("reports") or []
    strategy_name = _latest_strategy(bundle)
    strategy_context = bot_knowledge.STRATEGY_CONTEXT.get(strategy_name, "")

    lines: List[str] = []
    intent = plan.get("intent")

    if intent == "position_status":
        if metrics.get("position_open"):
            lines.append(f"You currently hold {symbol}.")
            qty = metrics.get("position_qty", 0.0)
            base = symbol.split("/")[0]
            lines.append(
                f"The position is {format_pct(metrics.get('unrealized_plpc', 0.0))} unrealized, about {format_money(metrics.get('unrealized_pl', 0.0))}. "
                f"You are holding {qty:.6f} {base} with an average entry around {format_money(metrics.get('avg_entry_price', 0.0))}, while current price is {format_money(metrics.get('current_price', 0.0))}."
            )
        else:
            lines.append(f"You do not currently hold {symbol}.")
        if strategy_name:
            lines.append(f"Recent context points to `{strategy_name}` as the relevant strategy for this symbol.")
        if event_state:
            lines.append(
                f"Event context is {event_state.get('event_bias', 'neutral')} with a cached event score of {float(event_state.get('net_event_score', 0.0) or 0.0):+.1f}."
            )
        if macro:
            lines.append(
                f"Macro context is {macro.get('market_regime', 'normal')} with a {float(macro.get('risk_multiplier', 1.0) or 1.0):.2f} risk multiplier."
            )
        if macro.get("summary"):
            lines.append(f"Macro summary: {str(macro.get('summary'))[:220]}")
    elif intent == "coin_card":
        lines.append(f"Coin card for {symbol}.")
        if metrics.get("tracked_active_state"):
            lines.append(_tracked_state_line(metrics))
        if metrics.get("position_open"):
            qty = metrics.get("position_qty", 0.0)
            base = symbol.split("/")[0]
            lines.append(
                f"You currently hold {qty:.6f} {base} with unrealized PnL of {format_money(metrics.get('unrealized_pl', 0.0))} ({format_pct(metrics.get('unrealized_plpc', 0.0))})."
            )
        else:
            lines.append(f"You do not currently hold {symbol}.")
        context_line = _news_context_line(event_state)
        if context_line:
            lines.append(context_line)
        traces = bundle.get("decision_traces") or []
        if traces:
            trace = traces[0]
            lines.append(
                f"Latest decision trace: `{trace.get('decision', '')}` with final score {float(trace.get('final_score', 0.0) or 0.0):.1f}. Submitted={bool(trace.get('submitted', 0))}."
            )
        outcomes = bundle.get("decision_outcomes") or []
        if outcomes:
            latest = outcomes[0]
            lines.append(
                f"Latest feedback: `{latest.get('outcome_label', 'unknown')}` at {str(metrics.get('latest_outcome_horizon', 'unknown'))} with PnL {format_pct(metrics.get('latest_outcome_pnl_pct', 0.0) or 0.0)}."
            )
        overlay = bundle.get("symbol_policy_overlay") or {}
        if overlay:
            lines.append(
                f"Current overlay is {float(overlay.get('score_delta', 0.0) or 0.0):+.2f} score delta with size multiplier {float(overlay.get('size_multiplier', 1.0) or 1.0):.2f} across {int(overlay.get('decision_samples', 0) or 0)} samples."
            )
        if macro:
            lines.append(
                f"Macro context is {macro.get('market_regime', 'normal')} at {float(macro.get('risk_multiplier', 1.0) or 1.0):.2f}."
            )
    elif intent == "learning_card":
        lines.append(f"Learning snapshot for {symbol}.")
        lines.append(
            f"I found {int(metrics.get('decision_outcome_count', 0) or 0)} recorded decision outcome(s) for this symbol."
        )
        lines.extend(_outcome_summary_lines(metrics))
        if metrics.get("overlay_decision_samples") is not None:
            lines.append(
                f"Policy overlay is {float(metrics.get('overlay_score_delta', 0.0) or 0.0):+.2f} with size multiplier {float(metrics.get('overlay_size_multiplier', 1.0) or 1.0):.2f} over {int(metrics.get('overlay_decision_samples', 0) or 0)} samples."
            )
        else:
            lines.append("There is no symbol policy overlay yet, which usually means sample size is still light.")
        if "brain_self_score" in metrics:
            lines.append(
                f"Latest brain self-score is {float(metrics.get('brain_self_score', 0.0) or 0.0):.1f} with confidence {float(metrics.get('brain_self_confidence', 0.0) or 0.0):.1f}."
            )
        lines.append(
            "Learning readiness is "
            + ("on track." if bool(metrics.get("learning_ready")) else "not mature yet; more evaluated outcomes are still needed.")
        )
    elif intent == "brain_symbol":
        lines.append(f"Brain view for {symbol}.")
        if metrics.get("decision_count"):
            lines.append(
                f"Recent trace count is {int(metrics.get('decision_count', 0) or 0)} with average final score {float(metrics.get('avg_decision_score', 0.0) or 0.0):.1f} and submitted ratio {float(metrics.get('submitted_ratio_pct', 0.0) or 0.0):.1f}%."
            )
        block_reason = str(metrics.get("top_block_reason", "") or "")
        if block_reason:
            lines.append(f"Top block reason lately is `{block_reason}`.")
        overlay = bundle.get("symbol_policy_overlay") or {}
        if overlay:
            lines.append(
                f"Overlay delta is {float(overlay.get('score_delta', 0.0) or 0.0):+.2f} with confidence boost {float(overlay.get('confidence_boost', 0.0) or 0.0):+.2f}."
            )
        if "brain_self_score" in metrics:
            lines.append(f"Latest overall brain self-score is {float(metrics.get('brain_self_score', 0.0) or 0.0):.1f}.")
        if macro:
            lines.append(f"Macro context is {macro.get('market_regime', 'normal')} at {float(macro.get('risk_multiplier', 1.0) or 1.0):.2f}.")
    elif intent == "tracked_status":
        counts = metrics.get("tracked_state_counts") or {}
        rows = bundle.get("tracked_symbols") or []
        lines.append(
            f"Tracked coins currently total {len(rows)}: active={int(counts.get('active', 0) or 0)}, recently_active={int(counts.get('recently_active', 0) or 0)}, saved={int(counts.get('saved', 0) or 0)}."
        )
        if rows:
            for row in rows[:10]:
                reason = str(row.get("active_reason", "") or "")
                reason_text = f" because {reason}" if reason else ""
                lines.append(
                    f"- {row.get('symbol', '')}: {row.get('active_state', 'idle')} / {row.get('monitor_tier', 'eligible')}{reason_text}"
                )
    elif intent == "blocked_review":
        lines.append(f"Blocked-trade review for {symbol}.")
        traces = bundle.get("decision_traces") or []
        blocked = next((row for row in traces if not bool(row.get("submitted", 0))), None)
        if blocked:
            lines.append(
                f"The latest blocked trace scored {float(blocked.get('final_score', 0.0) or 0.0):.1f} with reason `{blocked.get('block_reason', 'unknown')}`."
            )
        else:
            lines.append("I did not find a recent blocked trace for this symbol.")
        outcomes = bundle.get("decision_outcomes") or []
        if outcomes:
            latest = outcomes[0]
            lines.append(
                f"Latest saved outcome is `{latest.get('outcome_label', 'unknown')}` at {str(metrics.get('latest_outcome_horizon', 'unknown'))}, which helps judge whether the block was right."
            )
        context_line = _news_context_line(event_state)
        if context_line:
            lines.append(context_line)
        if macro:
            lines.append(f"Macro context is {macro.get('market_regime', 'normal')} at {float(macro.get('risk_multiplier', 1.0) or 1.0):.2f}.")
    elif intent == "trade_history":
        rows = bundle.get("live_experience") or []
        if rows:
            closed = [row for row in rows if row.get("outcome_pnl_pct") is not None]
            lines.append(f"Your last {len(rows)} saved trade{'s' if len(rows) != 1 else ''} for {symbol} are below.")
            if closed:
                pnls = [float(row.get("outcome_pnl_pct", 0.0) or 0.0) for row in closed]
                wins = [value for value in pnls if value > 0]
                lines.append(
                    f"Closed-trade summary: {len(wins)} winner{'s' if len(wins) != 1 else ''}, {len(closed) - len(wins)} loser{'s' if len(closed) - len(wins) != 1 else ''}, average PnL {format_pct(metrics.get('avg_pnl_pct', 0.0))}."
                )
            for row in rows[: min(5, len(rows))]:
                strategy = str(row.get("strategy_used") or row.get("opening_strategy") or "unknown")
                side = str(row.get("side") or "trade")
                entry = row.get("entry_price")
                pnl = row.get("outcome_pnl_pct")
                outcome = "open" if pnl is None else format_pct(pnl)
                lines.append(f"- {strategy}: {side} entry {format_money(entry)} | outcome {outcome}")
        else:
            lines.append(f"I could not find saved trade history for {symbol}.")
        if macro:
            lines.append(f"Macro context is {macro.get('market_regime', 'normal')} at {float(macro.get('risk_multiplier', 1.0) or 1.0):.2f}.")
    elif intent == "decision_review":
        lines.append(f"Here is the most relevant decision context I found for {symbol if symbol != 'portfolio' else 'your bot'}.")
        actions = bundle.get("actions") or []
        traces = bundle.get("decision_traces") or []
        if actions:
            action = actions[0]
            lines.append(
                f"The latest recorded action was `{action.get('action_type', 'action')}` on {action.get('symbol', symbol)} with reason: {str(action.get('reason') or 'no reason saved')[:180]}."
            )
        if traces:
            trace = traces[0]
            lines.append(
                f"The latest decision trace was `{trace.get('decision', '')}` with final score {float(trace.get('final_score', 0.0) or 0.0):.1f}. Submitted={bool(trace.get('submitted', 0))}."
            )
        if event_state:
            lines.append(
                f"At the event layer, bias is {event_state.get('event_bias', 'neutral')} with score {float(event_state.get('net_event_score', 0.0) or 0.0):+.1f}."
            )
        if macro:
            lines.append(
                f"Macro context is {macro.get('market_regime', 'normal')} at {float(macro.get('risk_multiplier', 1.0) or 1.0):.2f}."
            )
    elif intent == "config_status":
        runtime = bundle.get("runtime_status") or {}
        brain = str(metrics.get("active_brain") or plan.get("brain_mode") or "unknown")
        trading_mode = str(metrics.get("trading_mode") or "unknown")
        account_mode = str(metrics.get("account_mode") or "unknown")
        risk_state = str(metrics.get("risk_state") or "normal")
        risk_source = str(metrics.get("risk_source") or "")
        governor_mode = str(metrics.get("governor_mode") or "normal")
        pressure = float(metrics.get("pressure_score", 0.0) or 0.0)
        subfocus = _config_subfocus(question)
        if subfocus == "brain":
            lines.append(f"Your current brain is `{brain}`.")
            if trading_mode != "unknown" or account_mode != "unknown":
                lines.append(f"It is running in {trading_mode} mode on the {account_mode} account.")
        elif subfocus == "risk":
            risk_line = f"Your current risk state is {risk_state}"
            if risk_source:
                risk_line += f" via {risk_source}"
            lines.append(risk_line + ".")
        elif subfocus == "governor":
            lines.append(f"The activity governor is {governor_mode} with pressure {pressure:+.1f}.")
        else:
            lines.append(f"Your current brain is `{brain}`.")
            lines.append(f"Trading is running in {trading_mode} mode on the {account_mode} account.")
            risk_line = f"Risk state is {risk_state}"
            if risk_source:
                risk_line += f" via {risk_source}"
            lines.append(risk_line + ".")
            lines.append(f"The activity governor is {governor_mode} with pressure {pressure:+.1f}.")
            if runtime.get("running") is not None:
                lines.append(f"Runtime is {'running' if bool(runtime.get('running')) else 'stopped'}.")
    elif intent == "indicator_status":
        indicators = bundle.get("current_indicators") or {}
        if not symbol or symbol == "portfolio":
            lines.append("Ask for an indicator with a symbol, for example `what is ETH RSI`.")
        elif indicators:
            lines.append(f"Indicator snapshot for {symbol}:")
            if "rsi_15m" in indicators or "rsi_1m" in indicators:
                lines.append(
                    f"15m RSI is {float(indicators.get('rsi_15m', 50.0) or 50.0):.1f} and 1m RSI is {float(indicators.get('rsi_1m', 50.0) or 50.0):.1f}."
                )
            if "ema_fast_15m" in indicators and "ema_slow_15m" in indicators:
                relation = "above" if float(indicators.get('ema_fast_15m', 0.0) or 0.0) >= float(indicators.get('ema_slow_15m', 0.0) or 0.0) else "below"
                lines.append(
                    f"On 15m, the fast EMA is {relation} the slow EMA, which makes the short trend {indicators.get('trend_15m', 'mixed')}."
                )
            if "bb_upper_15m" in indicators and "bb_lower_15m" in indicators and "close_15m" in indicators:
                close = float(indicators.get('close_15m', 0.0) or 0.0)
                upper = float(indicators.get('bb_upper_15m', 0.0) or 0.0)
                lower = float(indicators.get('bb_lower_15m', 0.0) or 0.0)
                if close >= upper:
                    bb_state = "at or above the upper Bollinger band"
                elif close <= lower:
                    bb_state = "at or below the lower Bollinger band"
                else:
                    bb_state = "inside the Bollinger bands"
                lines.append(f"Price is currently {bb_state} on the 15m frame.")
        else:
            lines.append(f"I could not compute current indicators for {symbol} right now.")
        if event_state:
            lines.append(f"Event state is {event_state.get('event_bias', 'neutral')} at {float(event_state.get('net_event_score', 0.0) or 0.0):+.1f}.")
        if macro:
            lines.append(f"Macro context is {macro.get('market_regime', 'normal')} at {float(macro.get('risk_multiplier', 1.0) or 1.0):.2f}.")
    elif intent == "news_analysis":
        count = int(metrics.get("news_event_count", 0) or 0)
        lines.append(f"I found {count} relevant news item{'s' if count != 1 else ''} for {symbol}.")
        if count:
            lines.append(
                f"News tone is {int(metrics.get('positive_news_count', 0) or 0)} positive vs {int(metrics.get('negative_news_count', 0) or 0)} negative, with average impact {float(metrics.get('avg_news_impact', 0.0) or 0.0):.1f}."
            )
            top_headline = str(metrics.get("top_news_headline") or "").strip()
            if top_headline:
                lines.append(f"Top headline: {top_headline}")
        if metrics.get("symbol_tagged_news_count") or metrics.get("unattributed_news_count"):
            lines.append(
                f"Symbol attribution is {int(metrics.get('symbol_tagged_news_count', 0) or 0)} tagged vs {int(metrics.get('unattributed_news_count', 0) or 0)} unattributed item(s)."
            )
        if bundle.get("live_news"):
            live_meta = bundle.get("live_news_meta") or {}
            if bool(live_meta.get("from_cache")):
                lines.append("This answer used cached live-news results because the query cooldown is still active.")
            else:
                lines.append("This answer also included fresh live news from CryptoPanic and Binance because you asked about current or recent news.")
        elif bundle.get("live_news_meta"):
            live_meta = bundle.get("live_news_meta") or {}
            if live_meta.get("status") == "provider_limited":
                lines.append("Fresh live-news polling is currently limited by provider state, so this answer stayed on cached or stored news.")
        if event_state:
            context_line = _news_context_line(event_state)
            if context_line:
                lines.append(context_line)
    elif intent == "news_breadth":
        lines.append("Here is the current news-breadth view from local stored state.")
        lines.append(
            f"I found {int(metrics.get('news_event_count', 0) or 0)} recent stored news item(s), with {int(metrics.get('symbol_tagged_news_count', 0) or 0)} symbol-tagged and {int(metrics.get('unattributed_news_count', 0) or 0)} unattributed."
        )
        top_symbols = metrics.get("news_breadth_top_symbols") or []
        if top_symbols:
            lines.append("Top mentioned symbols: " + ", ".join(f"{symbol_name}={count}" for symbol_name, count in top_symbols[:6]))
        provider_status = metrics.get("news_provider_status") or {}
        if provider_status:
            lines.append("Provider health: " + ", ".join(f"{name}={status}" for name, status in provider_status.items()))
        if metrics.get("oracle_breadth_state"):
            lines.append(
                f"The oracle currently sees the market as `{metrics.get('oracle_breadth_state')}` with regime `{metrics.get('oracle_regime', metrics.get('macro_regime', 'normal'))}`."
            )
    elif intent == "oracle_detail":
        lines.append(
            f"Oracle score is {float(metrics.get('oracle_score', 1.0) or 1.0):.2f} in `{metrics.get('oracle_regime', 'normal')}` with breadth `{metrics.get('oracle_breadth_state', 'mixed')}`."
        )
        lines.append(f"Oracle age is {int(metrics.get('oracle_age_sec', 0) or 0)} seconds.")
        if metrics.get("oracle_drivers"):
            lines.append("Main drivers: " + ", ".join(str(item) for item in list(metrics.get("oracle_drivers") or [])[:3]))
        if metrics.get("oracle_mentioned_symbols"):
            lines.append("Mentioned symbols: " + ", ".join(str(item) for item in list(metrics.get("oracle_mentioned_symbols") or [])[:6]))
        if metrics.get("oracle_summary_source"):
            lines.append(f"Summary source is `{metrics.get('oracle_summary_source')}`.")
        if metrics.get("oracle_summary"):
            lines.append(f"Oracle summary: {str(metrics.get('oracle_summary') or '')[:220]}")
    elif intent == "event_edge_analysis":
        ranking = metrics.get("event_edge_ranking") or []
        if ranking:
            worst = ranking[0]
            lines.append(
                f"Your weakest saved event type is `{worst.get('event_type', 'unknown')}` with average PnL {format_pct(worst.get('avg_pnl_pct', 0.0))} across {int(worst.get('count', 0) or 0)} trade(s)."
            )
            if len(ranking) > 1:
                second = ranking[1]
                lines.append(
                    f"The next weakest is `{second.get('event_type', 'unknown')}` at {format_pct(second.get('avg_pnl_pct', 0.0))}."
                )
        else:
            lines.append("I do not have enough closed trade history yet to rank event types.")
    elif intent == "report_summary":
        if reports:
            latest = reports[0]
            lines.append(f"Latest {latest.get('report_type', 'report')}: {str(latest.get('content') or '')[:280]}")
        if macro:
            lines.append(f"Macro is {macro.get('market_regime', 'normal')} at {float(macro.get('risk_multiplier', 1.0) or 1.0):.2f}.")
    else:
        account = bundle.get("account_summary") or {}
        positions = bundle.get("current_positions") or []
        lines.append(
            f"Account equity is {format_money(account.get('equity', 0.0))} with cash {format_money(account.get('cash', 0.0))} and {len(positions)} open position(s)."
        )
        if macro:
            lines.append(f"Macro is {macro.get('market_regime', 'normal')} at {float(macro.get('risk_multiplier', 1.0) or 1.0):.2f}.")

    if strategy_context and intent in {"position_status", "decision_review"}:
        lines.append(f"Strategy context: {strategy_context}")

    if reports and intent not in {"report_summary", "config_status"}:
        lines.append(f"Latest report context: {str(reports[0].get('content') or '')[:180]}")

    suggestions = _follow_up_suggestions(plan, question)
    if suggestions:
        lines.append("Ask next:")
        lines.extend(f"- {item}" for item in suggestions[:3])

    return "\n".join(line for line in lines if line).strip()
