"""CLI entry point: python -m benchmarks.memory.run

Usage:
    python -m benchmarks.memory.run                    # All datasets (resumes automatically)
    python -m benchmarks.memory.run --dataset locomo   # Single dataset
    python -m benchmarks.memory.run --fresh            # Clear checkpoints, start fresh
    python -m benchmarks.memory.run --report-only      # Regenerate report from checkpoints
    python -m benchmarks.memory.run --clear locomo     # Clear checkpoints for one dataset

Note: The harness automatically resumes from checkpoints. Use --fresh to start over.
"""

from __future__ import annotations

import argparse
import logging
import sys

from benchmarks.memory.config import BenchmarkConfig
from benchmarks.memory.runner import run_benchmark, run_report_only


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Nexus Memory Benchmark Evaluation Harness",
    )
    parser.add_argument(
        "--dataset",
        choices=["locomo", "longmemeval", "tofu"],
        help="Run only this dataset (default: all)",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Clear all checkpoints before running (start fresh)",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="Regenerate report from existing checkpoint data",
    )
    parser.add_argument(
        "--clear",
        metavar="DATASET",
        help="Clear checkpoints for a dataset before running",
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help="Override data directory (default: benchmarks/data)",
    )
    parser.add_argument(
        "--results-dir",
        default=None,
        help="Override results directory (default: benchmarks/results)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()

    # Configure logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Suppress noisy HTTP libraries unless verbose
    if not args.verbose:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)

    # Load config from env
    config = BenchmarkConfig.from_env()

    # Apply CLI overrides
    overrides: dict[str, object] = {}
    if args.dataset:
        overrides["datasets"] = (args.dataset,)
    if args.data_dir:
        overrides["data_dir"] = args.data_dir
    if args.results_dir:
        overrides["results_dir"] = args.results_dir

    if overrides:
        # Create new config with overrides (immutable dataclass)
        config = BenchmarkConfig(**{
            **{f.name: getattr(config, f.name) for f in config.__dataclass_fields__.values()},
            **overrides,
        })

    # Clear checkpoints if requested
    if args.clear or args.fresh:
        from benchmarks.memory.checkpoint import Checkpoint

        ckpt = Checkpoint(config.results_dir)
        if args.fresh:
            for ds in config.datasets:
                removed = ckpt.clear(ds)
                if removed:
                    logging.info("Cleared %d checkpoints for %s", removed, ds)
        if args.clear:
            removed = ckpt.clear(args.clear)
            logging.info("Cleared %d checkpoints for %s", removed, args.clear)

    # Run
    if args.report_only:
        results = run_report_only(config)
    else:
        results = run_benchmark(config)

    if not results:
        logging.warning("No benchmark results produced")
        return 1

    # Print summary to stdout
    print("\n" + "=" * 60)
    print("BENCHMARK RESULTS")
    print("=" * 60)
    for r in results:
        print(f"  {r.dataset:15s}  {r.accuracy:6.1f}%  ({r.correct}/{r.total_questions})")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
