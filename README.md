# Apex Trading Infrastructure

Welcome to the Apex Trading mono-repository. This project is a unified, automated trading workspace that combines three previously separate domains into a single, cohesive architecture:

1. **Alpaca (Stocks & Crypto)**: Features a high-speed execution engine, dynamic DCA sizing, and backtesting.
2. **Kalshi (Event Contracts)**: Integrates market browsing, automated event scalping, and copy-trading bots.
3. **Sports Betting (DFS)**: Provides slip optimizers, EV grinding, and arbitrage discovery across platforms like PrizePicks.

---

## 🏗 System Architecture & Intent

The primary intent of this project is to build an institutional-grade, fully automated trading platform that operates entirely on deterministic, machine-verifiable rules. 

We utilize a **FastAPI** backend to handle heavy mathematical lifting, data streams, and execution routing. The frontend is a modern **React + Vite** application offering complete observability over all active trading bots, positions, and live action feeds via WebSockets.

### The Deterministic PR Agent Loop

This repository enforces stringent risk management not just in trading logic, but in code deployment. 

We utilize a **Deterministic PR Agent Loop**. All code changes must pass through a Risk Policy Gate (`apex-policy.json`) before CI builds execute.

- **High-Risk Code** (Execution Engine, Bot logic) requires absolute proof via `harness-trading-simulation` scripts and autonomous Code Review validation.
- **Low-Risk Code** (UI tweaks) requires `browser-evidence-ui` passing.

If an autonomous coding AI or human writes buggy logic, the Review Agent flags it, halts the PR, and triggers a Remediation Agent to automatically submit a patch to the same branch.

---

## 📂 Repository Structure

* **`apex-policy.json`**: The central machine-readable contract defining what paths are high/low risk.
* **`.github/workflows/`**: The Preflight Gates, Remediation Agent loops, and Canonical Review triggers.
* **`apex/`**: The core application root. See `apex/README.md` for specific framework details, configuration syntax, and run instructions.

---

## 🚀 Getting Started

To launch the unified Apex platform locally:

```bash
cd apex/
chmod +x run.sh
./run.sh
```

For detailed backend and frontend environment requirements, and how to structure your `.env` secrets, please refer to the internal **[Apex README](apex/README.md)**.
