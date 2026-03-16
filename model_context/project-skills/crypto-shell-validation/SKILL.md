---
name: crypto-shell-validation
description: Use when validating the crypto terminal shell in WSL, including backend readiness, timing smoke, learning proof, and endpoint health after code changes.
---

# crypto-shell-validation

## Use this draft when
- WSL runtime changes land
- shell latency or cold-cache behavior needs proof
- learning or endpoint regressions need a focused validation pass

## Core workflow
- run backend timing smoke and endpoint smoke in WSL
- verify `learning`, `scanner`, `universe`, and `symbol snapshot` timings
- check DB-backed learning writes, not just passing tests
- record blockers like provider quota, missing keys, or cold cache separately from code regressions
