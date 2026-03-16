---
name: crypto-news-attribution
description: Use when changing crypto news ingestion, source mix, symbol attribution, or event-confidence rules for learning and reporting.
---

# crypto-news-attribution

## Use this draft when
- adding or replacing crypto news sources
- tightening symbol confidence rules
- debugging BTC bias or unattributed news rows

## Core workflow
- prefer deterministic alias and source rules before LLM inference
- store more news than you trust for scoring
- only let high-confidence symbol events affect `symbol_event_state`
- keep `fresh_no_active_events`, `stale`, and `unavailable` distinct
