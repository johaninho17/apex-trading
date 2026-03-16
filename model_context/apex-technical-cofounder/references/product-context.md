# Product Context

Use this reference when a code change depends on Apex's product goals or user model.

## Product

Apex is a high-velocity trading terminal for a pro-retail, automation-oriented trader. The product aims to expose institutional-style capabilities such as live market data, API-driven execution, automation hooks, and cross-market context without forcing the user into slow retail workflows.

## Target User

- Located in the United States, including restricted states such as California
- Understands EV, arbitrage, and market microstructure
- Wants faster execution and better tooling than typical retail platforms provide
- Cares about automation and data visibility, but still wants human control over final actions

## Core Problems

1. Fragmentation across multiple dashboards, brokers, and market sources
2. Retail latency while markets move faster than UI refresh cycles
3. Missing API access, delayed odds, and stale boards on constrained US platforms
4. Poor operator visibility into cross-market signals and execution opportunities

## Solution Themes

- Unify accounts and market views into a single operator dashboard
- Stream live data so the user can react immediately
- Use automation to scan, rank, and surface opportunities while keeping the operator in control
- Improve time-to-alpha by shortening the path from signal to executable order

## North Star

Time-to-alpha: the time between an actionable market move and the user's execution on a supported platform.

## Scope Guidance

- Build for the repo that exists today, not only for older product copy.
- Older docs mention Kalshi, Polymarket, DFS, and other convergence ideas; newer requests may narrow the scope to current Alpaca, crypto, and stock workflows.
- When product docs conflict, prefer the latest user request and the integrations that are actually present in the codebase.
