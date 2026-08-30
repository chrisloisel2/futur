"""
W7 microstructure alpha hunt -- crossed-book-fixed L2 reconstruction.

Streams one venue/symbol/date raw JSONL book_events file and produces THREE
compact outputs (all tiny, on-change / event sampled, never full tick dumps):

  1. price_ticks   -- canonical best bid/ask series. Binance/OKX/Hyperliquid:
                       EXCLUSIVELY from the dedicated top-of-book stream
                       (bbo/bookTicker/bbo-tbt). Bybit: no dedicated stream
                       exists in this capture, so derived from the
                       reconstructed deep book's own best level, with an
                       explicit bid<ask sanity check -- crossed ticks are
                       DROPPED and counted (crossed_dropped in stats).
  2. deep_ticks    -- depth-within-5bps/25bps both sides from the deep L2
                       book, sampled on a 250ms grid (bounded output size).
  3. depletion_ev  -- best-level depletion events on the DEEP book (own best,
                       decoupled from the bbo tick rate -- this is the fix
                       for A3's degenerate churn feature), with churn count
                       accumulated since that price became deep-book-best.

Deep book state itself uses the same source_stream-role separation as
market_physics_v3/orderbook.py (BBO stream never touches the deep dict;
deep deltas ignored until a genuine snapshot bootstraps them) -- reused
logic, reimplemented with plain dicts for speed on 90M+ line files.
"""
from __future__ import annotations
import orjson
import sys
import os
import time

BBO_STREAMS = {"bbo", "bookTicker", "bbo-tbt"}
GRID_NS = 250_000_000  # 250ms


def stream_role(s):
    if s is None:
        return "unknown"
    return "bbo" if s in BBO_STREAMS else "deep"


def depth_within(levels_dict, is_bid, mid, distance_bps):
    # levels_dict: {price: qty}
    total = 0.0
    thresh = mid * distance_bps / 1e4
    if is_bid:
        lo = mid - thresh
        for p, q in levels_dict.items():
            if p >= lo:
                total += q
    else:
        hi = mid + thresh
        for p, q in levels_dict.items():
            if p <= hi:
                total += q
    return total


def run(venue, symbol, date, root, out_dir):
    path = os.path.join(root, "data/market_physics_v3/raw/book_events",
                         "venue=%s" % venue, "symbol=%s" % symbol, "date=%s" % date, "events.jsonl")
    if not os.path.exists(path):
        return None

    bids = {}
    asks = {}
    deep_bootstrapped = False
    last_snap_key = {}

    bbo_bid_px = bbo_bid_qty = None
    bbo_ask_px = bbo_ask_qty = None

    deep_best_bid = None
    deep_best_ask = None
    churn_bid = 0
    churn_ask = 0
    bid_best_since_ns = None
    ask_best_since_ns = None

    price_ticks = []       # canonical price series
    deep_ticks = []        # periodic deep depth
    depletion_ev = []      # A3 depletion events

    last_price_bid = None
    last_price_ask = None
    last_grid_bucket = -1

    n_lines = 0
    n_unknown = 0
    n_ignored_unboot = 0
    n_bbo_events = 0
    n_bbo_crossed = 0
    n_bybit_crossed_dropped = 0
    n_bybit_price_ticks_attempted = 0

    is_bybit = (venue == "bybit")

    with open(path, "rb") as f:
        for line in f:
            n_lines += 1
            try:
                d = orjson.loads(line)
            except orjson.JSONDecodeError:
                # Tail of an in-progress write (e.g. still-live 08-29
                # capture caught mid-append). Read-only: stop cleanly at
                # the last complete line rather than erroring out.
                n_lines -= 1
                break
            role = stream_role(d.get("source_stream"))
            if role == "unknown":
                n_unknown += 1
                continue

            ts = d["event_ts_ns"]
            recv = d["receive_ts_ns"]
            side = d["side"]
            price = d["price"]
            qty = d["qty"]
            etype = d["event_type"]

            if role == "bbo":
                n_bbo_events += 1
                is_remove = (qty <= 0.0 or etype == "remove")
                if side == "bid":
                    bbo_bid_px = None if is_remove else price
                    bbo_bid_qty = None if is_remove else qty
                else:
                    bbo_ask_px = None if is_remove else price
                    bbo_ask_qty = None if is_remove else qty

                if bbo_bid_px is not None and bbo_ask_px is not None:
                    if bbo_bid_px >= bbo_ask_px:
                        n_bbo_crossed += 1
                    else:
                        if bbo_bid_px != last_price_bid or bbo_ask_px != last_price_ask:
                            price_ticks.append((ts, recv, bbo_bid_px, bbo_bid_qty, bbo_ask_px, bbo_ask_qty))
                            last_price_bid, last_price_ask = bbo_bid_px, bbo_ask_px
                continue

            # role == "deep"
            if etype == "snapshot":
                key = (d.get("sequence_id"), ts, recv)
                if last_snap_key.get(d.get("source_stream")) != key:
                    bids.clear()
                    asks.clear()
                    deep_best_bid = None
                    deep_best_ask = None
                    churn_bid = 0
                    churn_ask = 0
                    last_snap_key[d.get("source_stream")] = key
                deep_bootstrapped = True
            elif not deep_bootstrapped:
                n_ignored_unboot += 1
                continue

            is_remove = (qty <= 0.0 or etype == "remove")
            if side == "bid":
                if is_remove:
                    had = bids.pop(price, None)
                    if had is not None and price == deep_best_bid:
                        depletion_ev.append((ts, recv, "bid", price, churn_bid))
                        deep_best_bid = max(bids) if bids else None
                        churn_bid = 0
                        bid_best_since_ns = ts
                else:
                    bids[price] = qty
                    if deep_best_bid is None or price > deep_best_bid:
                        deep_best_bid = price
                        churn_bid = 0
                        bid_best_since_ns = ts
                    elif price == deep_best_bid:
                        churn_bid += 1
            else:
                if is_remove:
                    had = asks.pop(price, None)
                    if had is not None and price == deep_best_ask:
                        depletion_ev.append((ts, recv, "ask", price, churn_ask))
                        deep_best_ask = min(asks) if asks else None
                        churn_ask = 0
                        ask_best_since_ns = ts
                else:
                    asks[price] = qty
                    if deep_best_ask is None or price < deep_best_ask:
                        deep_best_ask = price
                        churn_ask = 0
                        ask_best_since_ns = ts
                    elif price == deep_best_ask:
                        churn_ask += 1

            # Bybit: no dedicated bbo stream -> derive canonical price from
            # deep book's own best, with explicit crossed-book sanity check.
            if is_bybit and deep_best_bid is not None and deep_best_ask is not None:
                n_bybit_price_ticks_attempted += 1
                if deep_best_bid >= deep_best_ask:
                    n_bybit_crossed_dropped += 1
                else:
                    if deep_best_bid != last_price_bid or deep_best_ask != last_price_ask:
                        price_ticks.append((ts, recv, deep_best_bid, bids.get(deep_best_bid),
                                             deep_best_ask, asks.get(deep_best_ask)))
                        last_price_bid, last_price_ask = deep_best_bid, deep_best_ask

            # Periodic deep depth sample on a 250ms grid (bounded output).
            bucket = ts // GRID_NS
            if bucket != last_grid_bucket and deep_best_bid is not None and deep_best_ask is not None and deep_best_bid < deep_best_ask:
                last_grid_bucket = bucket
                mid = 0.5 * (deep_best_bid + deep_best_ask)
                bid5 = depth_within(bids, True, mid, 5.0)
                ask5 = depth_within(asks, False, mid, 5.0)
                bid25 = depth_within(bids, True, mid, 25.0)
                ask25 = depth_within(asks, False, mid, 25.0)
                deep_ticks.append((ts, recv, deep_best_bid, deep_best_ask, bid5, ask5, bid25, ask25,
                                    len(bids), len(asks)))

    stats = dict(
        venue=venue, symbol=symbol, date=date, n_lines=n_lines,
        n_unknown_provenance=n_unknown, n_ignored_unbootstrapped=n_ignored_unboot,
        n_bbo_events=n_bbo_events, n_bbo_crossed=n_bbo_crossed,
        n_price_ticks=len(price_ticks), n_deep_ticks=len(deep_ticks),
        n_depletion_events=len(depletion_ev),
        n_bybit_price_ticks_attempted=n_bybit_price_ticks_attempted,
        n_bybit_crossed_dropped=n_bybit_crossed_dropped,
    )

    import pyarrow as pa
    import pyarrow.parquet as pq

    os.makedirs(out_dir, exist_ok=True)
    tag = "%s_%s_%s" % (venue, symbol, date)

    pt = pa.table({
        "ts_ns": [r[0] for r in price_ticks], "recv_ns": [r[1] for r in price_ticks],
        "bid_px": [r[2] for r in price_ticks], "bid_qty": [r[3] for r in price_ticks],
        "ask_px": [r[4] for r in price_ticks], "ask_qty": [r[5] for r in price_ticks],
    })
    pq.write_table(pt, os.path.join(out_dir, "price_ticks_%s.parquet" % tag), compression="zstd")

    dt = pa.table({
        "ts_ns": [r[0] for r in deep_ticks], "recv_ns": [r[1] for r in deep_ticks],
        "best_bid": [r[2] for r in deep_ticks], "best_ask": [r[3] for r in deep_ticks],
        "bid_depth5": [r[4] for r in deep_ticks], "ask_depth5": [r[5] for r in deep_ticks],
        "bid_depth25": [r[6] for r in deep_ticks], "ask_depth25": [r[7] for r in deep_ticks],
        "n_bid_levels": [r[8] for r in deep_ticks], "n_ask_levels": [r[9] for r in deep_ticks],
    })
    pq.write_table(dt, os.path.join(out_dir, "deep_ticks_%s.parquet" % tag), compression="zstd")

    de = pa.table({
        "ts_ns": [r[0] for r in depletion_ev], "recv_ns": [r[1] for r in depletion_ev],
        "side": [r[2] for r in depletion_ev], "price": [r[3] for r in depletion_ev],
        "churn": [r[4] for r in depletion_ev],
    })
    pq.write_table(de, os.path.join(out_dir, "depletion_%s.parquet" % tag), compression="zstd")

    return stats


if __name__ == "__main__":
    venue, symbol, date, root, out_dir = sys.argv[1:6]
    t0 = time.time()
    stats = run(venue, symbol, date, root, out_dir)
    stats["elapsed_s"] = round(time.time() - t0, 1)
    print(orjson.dumps(stats).decode())
