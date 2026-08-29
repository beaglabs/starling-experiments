#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

cargo build --release >&2
bin="./target/release/starlings-stage7c-p2panda"

profiles=(theta37 theta51 theta93 novel_first)
topologies=(ring grid)
seeds=(0 1 2)

first=1
for topology in "${topologies[@]}"; do
  for profile in "${profiles[@]}"; do
    for seed in "${seeds[@]}"; do
      args=(
        --profile "$profile"
        --nodes 8
        --facts 32
        --topology "$topology"
        --redundancy 2
        --bandwidth 2
        --seed "$seed"
      )

      echo "[suite] G=$topology profile=$profile seed=$seed" >&2

      if [[ "$first" -eq 1 ]]; then
        "$bin" "${args[@]}"
        first=0
      else
        "$bin" "${args[@]}" --no-header
      fi
    done
  done
done
