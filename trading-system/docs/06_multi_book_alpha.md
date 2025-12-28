# Multi-Book Alpha

Books: A (directional), B (convexity overlay), C (structural funding/basis/MM). Budgets separated; each book has independent caps.

Allocator merges proposals, applies per-book and cluster caps, drops low utility or costly targets, outputs TargetPositions and AllocatorDecision. No orders here.

Storage: Mongo caches for signals/states/alloc/targets/books_state/allocator_decision; S3 for artifacts/books/target_positions/*.parquet.

Causality: uses current Signal/Alloc/State/RiskState only; no future info.
