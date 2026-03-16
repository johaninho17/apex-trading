---
name: telegram-intelligence
description: Use when extending Telegram NL retrieval, presenter output, coin cards, learning cards, oracle detail, or breadth-aware news answers.
---

# telegram-intelligence

## Use this draft when
- adding new Telegram intents
- improving query routing or presenters
- exposing local DB/runtime context without adding noisy commands

## Core workflow
- stay NL-first and compact
- answer from local DB/runtime/Ollama unless live news is explicitly requested
- prefer evidence-backed summaries over free-form speculation
- keep provider quota and cooldown state visible when relevant
