<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **delta-futures** (2712 symbols, 4951 relationships, 202 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `npx gitnexus analyze` in terminal first.

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `gitnexus_impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `gitnexus_detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `gitnexus_query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `gitnexus_context({name: "symbolName"})`.

## Never Do

- NEVER edit a function, class, or method without first running `gitnexus_impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `gitnexus_rename` which understands the call graph.
- NEVER commit changes without running `gitnexus_detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/delta-futures/context` | Codebase overview, check index freshness |
| `gitnexus://repo/delta-futures/clusters` | All functional areas |
| `gitnexus://repo/delta-futures/processes` | All execution flows |
| `gitnexus://repo/delta-futures/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->

---

# Delta Exchange API Reference

Critical rules learned from production debugging. Violating these causes silent order rejections or runtime errors.

## Order Types

### Opening a position (entry orders)

- Use `order_type="market_order"` with a `stop_price` for stop-market entries.
- Use `order_type="limit_order"` with a `stop_price` and `limit_price` for stop-limit entries.
- Delta Exchange only accepts `order_type` values `"market_order"` and `"limit_order"` — the strings `"stop_market_order"` and `"stop_limit_order"` are **rejected** by the API.
- Do **NOT** set `stop_order_type` — that field only exists for closing/protecting positions.
- Do **NOT** set `reduce_only=True` — entry orders open positions, not close them.

### Closing/protecting a position (stop-loss, take-profit)

- Use `order_type="limit_order"` or `order_type="market_order"`.
- Set `stop_order_type="stop_loss_order"` or `stop_order_type="take_profit_order"`.
- Set `reduce_only=True`.
- Set `stop_price` for the trigger level.

### `stop_order_type` allowed values

Only two values are accepted: `"stop_loss_order"` and `"take_profit_order"`.
Any other value (e.g., `"stop_order"`) is rejected by the API.

## `time_in_force` Rules

- Only send `time_in_force="gtc"` for `limit_order`.
- Do **NOT** send `time_in_force` for `market_order` — Delta rejects it and returns a misleading `post_only` validation error.

## Tickers API

- `GET /v2/tickers?symbol=X` **ignores the symbol parameter** and returns all 295+ instruments.
- You must iterate the full response array and exact-match on the `symbol` field.
- Tickers include `tags` (list), `top_tag` (string), and `oi_value_usd` (number).

## Client Lifecycle

- Never pass a `DeltaExchangeClient` to background tasks (`asyncio.create_task`). The caller's `finally` block will `close()` the client while the background task is still using it.
- Background tasks (e.g., journal fill-polling) must create their own client instance and close it in their own `finally` block.

## Relevant Constants (`config/constants.py`)

| Constant | Value | Notes |
|----------|-------|-------|
| `ORDER_TYPE_LIMIT` | `"limit_order"` | For limit entries and SL/TP with `stop_order_type` |
| `ORDER_TYPE_MARKET` | `"market_order"` | For market entries and SL/TP with `stop_order_type` |
| `ORDER_TYPE_STOP_MARKET` | `"stop_market_order"` | **NOT accepted by API** — use `ORDER_TYPE_MARKET` + `stop_price` instead |
| `ORDER_TYPE_STOP_LIMIT` | `"stop_limit_order"` | **NOT accepted by API** — use `ORDER_TYPE_LIMIT` + `stop_price` + `limit_price` instead |
| `PAPER_TRADE_TAKER_FEE` | `0.0005` | 0.05% taker fee for paper trade simulation |
| `BREAKOUT_PIP_OFFSET` | varies | Offset added/subtracted from breakout candle high/low |
| `CANDLE_CLOSE_BUFFER_SECONDS` | `5` | Buffer before candle boundary for close enforcement |
