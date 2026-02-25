"""Memory benchmark evaluation harness.

Compares Nexus memory against published baselines (Mem0, Zep, MemGPT)
on standardized datasets (LoCoMo, LongMemEval, TOFU).

Usage:
    python -m benchmarks.memory.run                    # All datasets
    python -m benchmarks.memory.run --dataset locomo   # Single dataset
    python -m benchmarks.memory.run --resume           # Resume from checkpoint
    python -m benchmarks.memory.run --report-only      # Regenerate report
"""
