# DFS Direct Feed Outreach Plan

Status: `pending_external_approval`

## Objective
Obtain approved, read-only line availability feeds for:
- PrizePicks
- Underdog

Until then, Apex enforces Sleeper-compatible slip building.

## Required feed fields
- `player_name`
- `market`
- `line`
- `side` (`over`/`under`)
- `status` (`open`/`closed`/`suspended`)
- `event_id` and `start_time`
- `last_updated_at`

## Operational requirements to request
- Auth model (API key / OAuth / signed requests)
- Rate limits and burst policy
- Data retention/storage terms
- Caching policy
- Allowed user-facing display constraints
- Environment support (sandbox vs production)

## Internal implementation gates
1. Legal approval received and documented.
2. Provider contract/ToS review completed.
3. Feed smoke test endpoint returns deterministic fixtures.
4. Scanner row flags switched from compatibility to direct verification.
5. Slip builder enables provider-specific mode only after (1)-(4).

