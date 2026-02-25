# HERB Enterprise Context

Hypothetical Enterprise Reference Benchmark data for memory/003 semantic search tests.

## Contents

- `employees.jsonl` — 530 employee records
- `products.jsonl` — 30 product records
- `customers.jsonl` — 120 customer records

## Regeneration

```bash
uv run python scripts/generate_herb_data.py
```

Uses seed 42 for deterministic output.
