---
name: tracked-coins-runtime
description: Use when changing saved, active, and recently-active tracked coin behavior, active-set stickiness, or scanner-vs-execution semantics.
---

# tracked-coins-runtime

## Use this draft when
- adjusting active coin stickiness or churn limits
- changing tracked coin UI or persistence
- verifying execution is not gated by saved coins

## Core workflow
- keep `saved` manual and persistent
- keep `active` and `recently_active` bot-managed
- preserve broad eligibility with tighter active monitoring
- validate tracked-state DB rows and UI state together
