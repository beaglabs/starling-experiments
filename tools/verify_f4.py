#!/usr/bin/env python3
"""F4 heterogeneous Gemma operator evidence gate."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from contextlib import contextmanager

ROOT = pathlib.Path(__file__).resolve().parents[1]
RAW = ROOT / "trials" / "f4-raw.tsv"
REPLAY = ROOT / "trials" / "f4-heterogeneous.tsv"
MODEL_NAME = "gemma-4-E2B-it-Q4_K_M.gguf"
EXPECTED_RUNS = 48


def run(
    *args: str,
    capture: bool = False,
    timeout_s: int | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    print("+", " ".join(args), file=sys.stderr)
    return subprocess.run(
        args,
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE if capture else None,
        timeout=timeout_s,
        env=env,
    )


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def probe(base_url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(
            f"{base_url.rstrip('/')}/v1/models",
            timeout=timeout,
        ) as response:
            data = json.loads(response.read().decode())
        return bool(data.get("data"))
    except (
        urllib.error.URLError,
        urllib.error.HTTPError,
        TimeoutError,
        ValueError,
    ):
        return False


def resolve_model() -> pathlib.Path:
    explicit = os.environ.get("F4_MODEL_PATH")
    candidates = []
    if explicit:
        candidates.append(pathlib.Path(explicit).expanduser())
    candidates.extend(
        [
            ROOT / "models" / MODEL_NAME,
            pathlib.Path("~/Downloads/models").expanduser() / MODEL_NAME,
        ]
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    raise SystemExit(
        "Gemma weights not found. Set F4_MODEL_PATH or place/link "
        f"{MODEL_NAME} under models/ or ~/Downloads/models/."
    )


@contextmanager
def server():
    external = os.environ.get("F4_BASE_URL")
    if external:
        if not probe(external, timeout=5):
            raise SystemExit(f"F4_BASE_URL is not serving /v1/models: {external}")
        yield external
        return

    llama_server = shutil.which("llama-server")
    if llama_server is None:
        raise SystemExit(
            "llama-server is required on PATH (Homebrew llama.cpp is expected)"
        )
    model = resolve_model()
    port = int(os.environ.get("F4_PORT", "18087"))
    base_url = f"http://127.0.0.1:{port}"
    log_path = ROOT / "trials" / "f4-llama-server.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"+ start llama-server model={model} base_url={base_url}", file=sys.stderr)
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            [
                llama_server,
                "-m",
                str(model),
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "-c",
                "4096",
            ],
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise SystemExit(
                        f"llama-server exited during startup; inspect {log_path}"
                    )
                if probe(base_url):
                    break
                time.sleep(0.5)
            else:
                raise SystemExit(
                    f"llama-server did not become ready; inspect {log_path}"
                )
            yield base_url
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def parse_replay(data: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(data.decode()), delimiter="\t"))


def as_int(row: dict[str, str], field: str) -> int:
    return int(row[field], 10)


def main() -> int:
    if shutil.which("zig") is None:
        raise SystemExit("zig 0.16.0 is required on PATH")
    zig = run("zig", "version", capture=True).stdout.decode().strip()
    if zig != "0.16.0":
        raise SystemExit(f"zig 0.16.0 is required; found {zig}")

    run("python3", "tools/f4_gemma_runner.py", "--self-test")
    run("python3", "tools/f4_gemma_runner.py", "--dry-run")
    run("zig", "build", "test")

    with server() as base_url:
        run(
            "python3",
            "tools/f4_gemma_runner.py",
            "--base-url",
            base_url,
            "--output",
            str(RAW),
            "--environments",
            "2",
            "--sampling-seeds",
            "2",
            "--max-rounds",
            "5",
            timeout_s=7200,
        )

    first = run(
        "zig",
        "build",
        "-Doptimize=ReleaseFast",
        "run-f4",
        "--",
        str(RAW),
        capture=True,
        timeout_s=300,
    ).stdout
    second = run(
        "zig",
        "build",
        "-Doptimize=ReleaseFast",
        "run-f4",
        "--",
        str(RAW),
        capture=True,
        timeout_s=300,
    ).stdout

    if first != second:
        raise SystemExit("F4 Zig replay is not byte-identical on the same raw evidence")

    rows = parse_replay(first)
    if len(rows) != EXPECTED_RUNS:
        raise SystemExit(f"expected {EXPECTED_RUNS} F4 runs; found {len(rows)}")

    if {row["population"] for row in rows} != {
        "deterministic_only",
        "mixed",
        "model_only",
    }:
        raise SystemExit("F4 population axis is incomplete")
    if {row["topology"] for row in rows} != {"ring", "grid"}:
        raise SystemExit("F4 topology axis is incomplete")
    if {row["mode"] for row in rows} != {
        "typed_unconstrained",
        "cfg_constrained",
    }:
        raise SystemExit("F4 decoding-arm axis is incomplete")
    if {row["environment_seed"] for row in rows} != {"0", "1"}:
        raise SystemExit("F4 environment-seed axis is incomplete")
    if {row["sampling_seed"] for row in rows} != {"0", "1"}:
        raise SystemExit("F4 sampling-seed axis is incomplete")

    hard_failures: list[str] = []
    limitations: list[str] = []

    for row in rows:
        label = (
            f"{row['population']}/{row['topology']}/"
            f"env{row['environment_seed']}/sampling{row['sampling_seed']}/"
            f"{row['mode']}"
        )
        if row["budget_compliant"] != "yes":
            hard_failures.append(f"{label}: exceeded frozen round budget")
        if row["population"] == "deterministic_only":
            if as_int(row, "model_calls") != 0:
                hard_failures.append(f"{label}: deterministic arm made model calls")
            if as_int(row, "deterministic_calls") == 0:
                hard_failures.append(f"{label}: deterministic arm made no local calls")
            if row["success"] != "yes":
                hard_failures.append(f"{label}: deterministic baseline failed")
        elif row["population"] == "model_only":
            if as_int(row, "deterministic_calls") != 0:
                hard_failures.append(f"{label}: model-only arm used deterministic operator")
            if as_int(row, "model_calls") == 0:
                hard_failures.append(f"{label}: model-only arm made no model calls")
        else:
            if as_int(row, "model_calls") == 0 or as_int(row, "deterministic_calls") == 0:
                hard_failures.append(f"{label}: mixed arm did not exercise both operator classes")

    if hard_failures:
        for failure in hard_failures:
            print(f"HARD FAIL: {failure}", file=sys.stderr)
        return 1

    backend_errors = sum(as_int(row, "backend_errors") for row in rows)
    if backend_errors:
        limitations.append(f"model backend errors: {backend_errors}")

    mixed = [row for row in rows if row["population"] == "mixed"]
    mixed_successes = sum(row["success"] == "yes" for row in mixed)
    if mixed_successes == 0:
        limitations.append("mixed deterministic+Gemma collective never converged")

    model_only = [row for row in rows if row["population"] == "model_only"]
    model_protocol_actions = sum(
        as_int(row, "protocol_actions") for row in model_only
    )
    if model_protocol_actions == 0:
        limitations.append("model-only diagnostic produced no valid protocol actions")

    REPLAY.write_bytes(first)
    meta = pathlib.Path(str(RAW) + ".meta.json")
    if not meta.exists():
        raise SystemExit("F4 metadata sidecar is missing")

    print(f"F4 raw bytes: {RAW.stat().st_size}")
    print(f"F4 raw sha256: {sha256(RAW)}")
    print(f"F4 replay bytes: {len(first)}")
    print(f"F4 replay sha256: {hashlib.sha256(first).hexdigest()}")
    print(f"F4 metadata sha256: {sha256(meta)}")
    print("byte_identical_zig_replay: yes")
    print(f"runs: {len(rows)}")
    print(f"backend_errors: {backend_errors}")

    grouped: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[
            (row["population"], row["topology"], row["mode"])
        ].append(row)

    for key in sorted(grouped):
        group = grouped[key]
        successes = sum(row["success"] == "yes" for row in group)
        model_calls = sum(as_int(row, "model_calls") for row in group)
        invalid = sum(as_int(row, "invalid_actions") for row in group)
        semantic = sum(as_int(row, "semantic_violations") for row in group)
        communication = sum(as_int(row, "communication_units") for row in group)
        useful = sum(as_int(row, "useful") for row in group)
        duplicate = sum(as_int(row, "duplicate") for row in group)
        print(
            f"aggregate population={key[0]} topology={key[1]} mode={key[2]} "
            f"successes={successes}/{len(group)} model_calls={model_calls} "
            f"invalid={invalid} semantic_rejections={semantic} "
            f"communication={communication} useful={useful} duplicate={duplicate}"
        )

    diversity: dict[
        tuple[str, str, str, str],
        set[str],
    ] = defaultdict(set)
    for row in rows:
        diversity[
            (
                row["population"],
                row["topology"],
                row["environment_seed"],
                row["mode"],
            )
        ].add(row["trajectory_hash"])

    for key in sorted(diversity):
        print(
            f"trajectory_multiplicity population={key[0]} topology={key[1]} "
            f"env={key[2]} mode={key[3]} unique={len(diversity[key])}/2"
        )

    if limitations:
        print("F4 limitation reasons:")
        for reason in limitations:
            print(f"  - {reason}")
        print(
            "F4 LIMITATION: heterogeneous operator interface validated, "
            "but live-model evidence has the limitations above"
        )
    else:
        print(
            "F4 PASS: heterogeneous deterministic+Gemma collectives executed "
            "through the neutral protocol interface with complete replay evidence"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
