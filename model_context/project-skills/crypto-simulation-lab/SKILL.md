---
name: crypto-simulation-lab
description: Use when changing the crypto Simulation Lab, replay controls, backtest aggregation, or safeguard diagnostics for simulated trading.
---

# crypto-simulation-lab

## Use this draft when
- extending the crypto simulation page
- changing backtest controls or aggregation
- validating safeguard diagnostics in replay

## Core workflow
- reuse the existing crypto backtest engine before inventing a second simulator
- keep control labels honest about what the engine really supports
- surface equity curve, trade log, and safeguard diagnostics together
- compare replay behavior against live cooldown and spacing expectations
