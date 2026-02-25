#!/usr/bin/env bash
# Downloads memory benchmark datasets for the evaluation harness.
#
# Usage:
#   ./scripts/download_memory_benchmarks.sh                    # default dir
#   ./scripts/download_memory_benchmarks.sh benchmarks/data    # custom dir
#   BENCHMARK_DIR=/custom/path ./scripts/download_memory_benchmarks.sh

set -euo pipefail

DATA_DIR="${1:-${BENCHMARK_DIR:-benchmarks/data}}"

echo "==> Downloading memory benchmark datasets to ${DATA_DIR}"
mkdir -p "${DATA_DIR}"

# LoCoMo (ACL 2024) — long-context memory evaluation
if [ ! -d "${DATA_DIR}/locomo" ]; then
    echo "--- Cloning LoCoMo..."
    git clone --depth 1 https://github.com/snap-research/locomo "${DATA_DIR}/locomo"
else
    echo "--- LoCoMo already present, skipping"
fi

# LongMemEval (ICLR 2025) — long-term memory evaluation
if [ ! -d "${DATA_DIR}/longmemeval" ]; then
    echo "--- Cloning LongMemEval..."
    git clone --depth 1 https://github.com/xiaowu0162/LongMemEval "${DATA_DIR}/longmemeval"
else
    echo "--- LongMemEval already present, skipping"
fi

# TOFU (selective forgetting) — download from HuggingFace
if [ ! -d "${DATA_DIR}/tofu" ]; then
    echo "--- Downloading TOFU from HuggingFace..."
    if python -c "import datasets" 2>/dev/null; then
        python -c "
from datasets import load_dataset
ds = load_dataset('locuslab/TOFU')
ds.save_to_disk('${DATA_DIR}/tofu')
print('TOFU downloaded successfully')
"
    else
        echo "    WARNING: 'datasets' package not installed. Install with: pip install datasets"
        echo "    Skipping TOFU download. Install and re-run to include TOFU benchmark."
    fi
else
    echo "--- TOFU already present, skipping"
fi

echo ""
echo "==> Done. Benchmark data in ${DATA_DIR}"
echo "    Run benchmarks: python -m benchmarks.memory.run --data-dir ${DATA_DIR}"
