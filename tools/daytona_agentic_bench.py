#!/usr/bin/env python3
"""Run the Starlings agentic benchmark suite entirely on Daytona.

The local machine only needs Python + the Daytona SDK. This script creates one
GPU controller sandbox for vLLM + Harbor, while Harbor creates the benchmark
task sandboxes through Daytona.

Conditions:
  oracle  Harbor oracle; environment smoke only
  a       conventional frozen-model baseline (Terminus-2 by default)
  b       Starlings + the exact same frozen model (custom Harbor agent)
  c       deterministic Starlings; no neural model (custom Harbor agent)
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shlex
import sys
import time
from dataclasses import dataclass
from typing import Iterable, Sequence

BENCHMARKS = {
    "skillsbench": "benchflow/skillsbench",
    "harbor-index": "harbor-index/harbor-index-1.0",
    "tau3": "sierra-research/tau3-bench",
    "frontier-bench": "frontier-bench/frontier-bench",
}

DEFAULT_MODEL = "Qwen/Qwen2.5-Coder-1.5B-Instruct"
DEFAULT_SERVED_MODEL = "starlings-frozen-1.5b"
DEFAULT_VLLM_IMAGE = "vllm/vllm-openai:v0.22.1"
DEFAULT_ROOT = "/workspace/starling-agentic-bench"


@dataclass(frozen=True)
class RunSpec:
    condition: str
    benchmark: str
    dataset: str
    agent: str
    model: str | None
    agent_kwargs: tuple[str, ...]


def csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def join(parts: Iterable[str]) -> str:
    return shlex.join(list(parts))


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"{name} is required")
    return value


def validate_benchmarks(names: Sequence[str]) -> None:
    unknown = [name for name in names if name not in BENCHMARKS]
    if unknown:
        raise ValueError(
            f"unknown benchmark(s): {', '.join(unknown)}; "
            f"choose from {', '.join(BENCHMARKS)}"
        )


def make_spec(
    condition: str,
    benchmark: str,
    *,
    served_model: str,
    api_base: str,
    agent_a: str,
    agent_b: str | None,
    agent_c: str | None,
    max_turns: int,
) -> RunSpec:
    dataset = BENCHMARKS[benchmark]
    if condition == "oracle":
        return RunSpec(condition, benchmark, dataset, "oracle", None, ())
    if condition == "a":
        return RunSpec(
            condition,
            benchmark,
            dataset,
            agent_a,
            f"openai/{served_model}",
            (
                f"api_base={api_base}",
                "temperature=0",
                f"max_turns={max_turns}",
                "enable_summarize=false",
            ),
        )
    if condition == "b":
        if not agent_b:
            raise ValueError(
                "condition B requires --agent-b path.to.module:StarlingsAgent"
            )
        return RunSpec(
            condition,
            benchmark,
            dataset,
            agent_b,
            f"openai/{served_model}",
            (
                f"api_base={api_base}",
                "temperature=0",
                f"max_turns={max_turns}",
            ),
        )
    if condition == "c":
        if not agent_c:
            raise ValueError(
                "condition C requires "
                "--agent-c path.to.module:DeterministicStarlingsAgent"
            )
        return RunSpec(condition, benchmark, dataset, agent_c, None, ())
    raise ValueError(f"unknown condition: {condition}")


def harbor_command(
    spec: RunSpec,
    *,
    concurrency: int,
    max_tasks: int | None,
    output_dir: str,
) -> str:
    parts = [
        "harbor", "run",
        "-d", spec.dataset,
        "-a", spec.agent,
        "-e", "daytona",
        "-n", str(concurrency),
        "-o", output_dir,
    ]
    if spec.model:
        parts += ["-m", spec.model]
    if max_tasks is not None:
        parts += ["--n-tasks", str(max_tasks)]
    for kwarg in spec.agent_kwargs:
        parts += ["--ak", kwarg]
    return join(parts)


def gpu_types(names: Sequence[str]):
    from daytona import GpuType

    mapping = {
        "rtx-4090": GpuType.RTX_4090,
        "rtx-5090": GpuType.RTX_5090,
        "rtx-pro-6000": GpuType.RTX_PRO_6000,
        "h100": GpuType.H100,
        "h200": GpuType.H200,
    }
    unknown = [name for name in names if name not in mapping]
    if unknown:
        raise SystemExit(
            f"unknown GPU type(s): {', '.join(unknown)}; "
            f"choose from {', '.join(mapping)}"
        )
    return [mapping[name] for name in names]


def exec_checked(sandbox, command: str, *, cwd: str | None = None, timeout=900):
    print(f"+ {command}", flush=True)
    result = sandbox.process.exec(command, cwd=cwd, timeout=timeout)
    if result.result:
        print(result.result, end="" if result.result.endswith("\n") else "\n")
    if result.exit_code != 0:
        raise RuntimeError(f"exit {result.exit_code}: {command}")
    return result


def setup_controller(sandbox, args) -> None:
    root = shlex.quote(args.controller_root)
    exec_checked(
        sandbox,
        "apt-get update && apt-get install -y --no-install-recommends git curl "
        "ca-certificates && rm -rf /var/lib/apt/lists/*",
        timeout=900,
    )
    exec_checked(
        sandbox,
        "python -m venv /opt/harbor-venv && "
        "/opt/harbor-venv/bin/pip install --no-cache-dir -U 'harbor[daytona]' && "
        "ln -sf /opt/harbor-venv/bin/harbor /usr/local/bin/harbor",
        timeout=1200,
    )
    exec_checked(sandbox, f"mkdir -p {root}")

    repos = (
        (
            "starlings",
            "https://github.com/beaglabs/starlings.git",
            args.starlings_ref,
        ),
        (
            "starling-experiments",
            "https://github.com/beaglabs/starling-experiments.git",
            args.experiments_ref,
        ),
    )
    for name, url, ref in repos:
        path = f"{args.controller_root}/{name}"
        qpath, qref, qurl = map(shlex.quote, (path, ref, url))
        command = (
            f"if [ -d {qpath}/.git ]; then "
            f"git -C {qpath} fetch origin {qref} && "
            f"git -C {qpath} checkout {qref} && "
            f"git -C {qpath} reset --hard origin/{qref}; "
            f"else git clone --branch {qref} {qurl} {qpath}; fi"
        )
        exec_checked(sandbox, command, timeout=600)

    exec_checked(
        sandbox,
        "harbor --version && harbor dataset list | head -30",
        cwd=f"{args.controller_root}/starling-experiments",
        timeout=180,
    )


def ensure_vllm(sandbox, args) -> None:
    health = sandbox.process.exec(
        f"curl -fsS http://127.0.0.1:{args.port}/health", timeout=10
    )
    if health.exit_code == 0:
        print("vLLM already healthy", flush=True)
        return

    from daytona import SessionExecuteRequest

    session = f"vllm-{int(time.time())}"
    sandbox.process.create_session(session)
    command = join(
        (
            "vllm", "serve", args.model,
            "--port", str(args.port),
            "--served-model-name", args.served_model,
            "--gpu-memory-utilization", str(args.gpu_memory_utilization),
            "--max-model-len", str(args.max_model_len),
            "--enable-prefix-caching",
        )
    )
    response = sandbox.process.execute_session_command(
        session,
        SessionExecuteRequest(command=command, run_async=True),
    )
    print(f"vLLM session={session} cmd={response.cmd_id}", flush=True)

    deadline = time.monotonic() + args.model_boot_timeout
    previous = ""
    while time.monotonic() < deadline:
        health = sandbox.process.exec(
            f"curl -fsS http://127.0.0.1:{args.port}/health", timeout=10
        )
        if health.exit_code == 0:
            print("vLLM healthy", flush=True)
            return

        logs = sandbox.process.get_session_command_logs(session, response.cmd_id)
        output = logs.output or ""
        delta = output[len(previous):] if output.startswith(previous) else output
        if delta:
            print(delta, end="" if delta.endswith("\n") else "\n", flush=True)
        previous = output

        state = sandbox.process.get_session_command(session, response.cmd_id)
        if state.exit_code is not None:
            raise RuntimeError(f"vLLM exited with {state.exit_code}")
        time.sleep(5)

    raise TimeoutError("vLLM did not become healthy")


def run_async(sandbox, session: str, command: str) -> str:
    from daytona import SessionExecuteRequest

    sandbox.process.create_session(session)
    result = sandbox.process.execute_session_command(
        session,
        SessionExecuteRequest(command=command, run_async=True),
    )
    return result.cmd_id


def wait_async(sandbox, session: str, command_id: str, timeout: int) -> int:
    deadline = time.monotonic() + timeout
    previous = ""
    while time.monotonic() < deadline:
        state = sandbox.process.get_session_command(session, command_id)
        logs = sandbox.process.get_session_command_logs(session, command_id)
        output = logs.output or ""
        delta = output[len(previous):] if output.startswith(previous) else output
        if delta:
            print(delta, end="" if delta.endswith("\n") else "\n", flush=True)
        previous = output
        if state.exit_code is not None:
            return int(state.exit_code)
        time.sleep(5)
    raise TimeoutError(f"benchmark timed out: {session}/{command_id}")


def create_controller(daytona, args):
    from daytona import CreateSandboxFromImageParams, Image, Resources

    env = {
        "DAYTONA_API_KEY": require_env("DAYTONA_API_KEY"),
        "HARBOR_TELEMETRY": "off",
        "PYTHONUNBUFFERED": "1",
    }
    for optional in ("DAYTONA_API_URL", "HF_TOKEN"):
        if os.environ.get(optional):
            env[optional] = os.environ[optional]

    params = CreateSandboxFromImageParams(
        image=Image.base(args.vllm_image),
        resources=Resources(
            gpu=1,
            gpu_type=gpu_types(csv(args.gpu_types)),
        ),
        auto_stop_interval=0,
        ephemeral=True,
        env_vars=env,
        labels={"purpose": "starlings-agentic-benchmarks"},
    )
    print(f"creating GPU controller ({args.gpu_types}) ...", flush=True)
    return daytona.create(params, timeout=args.controller_create_timeout)


def run_suite(sandbox, args) -> list[dict[str, object]]:
    benchmarks = csv(args.benchmarks)
    conditions = csv(args.conditions)
    validate_benchmarks(benchmarks)

    unknown = [c for c in conditions if c not in {"oracle", "a", "b", "c"}]
    if unknown:
        raise ValueError("unknown condition(s): " + ", ".join(unknown))

    results: list[dict[str, object]] = []
    api_base = f"http://127.0.0.1:{args.port}/v1"

    for condition in conditions:
        if condition in {"a", "b"}:
            ensure_vllm(sandbox, args)

        for benchmark in benchmarks:
            spec = make_spec(
                condition,
                benchmark,
                served_model=args.served_model,
                api_base=api_base,
                agent_a=args.agent_a,
                agent_b=args.agent_b,
                agent_c=args.agent_c,
                max_turns=args.max_turns,
            )
            jobs = (
                f"{args.controller_root}/runs/{condition}/{benchmark}/jobs"
            )
            exec_checked(sandbox, f"mkdir -p {shlex.quote(jobs)}")
            command = harbor_command(
                spec,
                concurrency=args.concurrency,
                max_tasks=args.n_tasks,
                output_dir=jobs,
            )

            print(f"\n=== {benchmark} / {condition.upper()} ===", flush=True)
            session = (
                f"bench-{condition}-{benchmark}-{int(time.time())}"
                .replace("_", "-")
            )
            command_id = run_async(
                sandbox,
                session,
                f"cd {shlex.quote(args.controller_root + '/starling-experiments')} "
                f"&& {command}",
            )
            exit_code = wait_async(
                sandbox, session, command_id, args.benchmark_timeout
            )
            results.append(
                {
                    "benchmark": benchmark,
                    "dataset": spec.dataset,
                    "condition": condition,
                    "exit_code": exit_code,
                    "jobs": jobs,
                }
            )
            if exit_code and not args.keep_going:
                return results

    return results


def download_results(sandbox, args, results) -> pathlib.Path:
    manifest = json.dumps(results, indent=2, sort_keys=True).encode()
    manifest_path = f"{args.controller_root}/runs/manifest.json"
    sandbox.fs.upload_file(manifest, manifest_path)

    archive = f"{args.controller_root}/starlings-agentic-results.tar.gz"
    exec_checked(
        sandbox,
        f"tar -C {shlex.quote(args.controller_root)} "
        f"-czf {shlex.quote(archive)} runs",
        timeout=300,
    )
    local = pathlib.Path(args.output).expanduser().resolve()
    local.parent.mkdir(parents=True, exist_ok=True)
    sandbox.fs.download_file(archive, str(local), timeout=1800)
    return local


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Provision and run Starlings benchmarks on Daytona."
    )
    p.add_argument(
        "--benchmarks",
        default="skillsbench,harbor-index,tau3,frontier-bench",
    )
    p.add_argument(
        "--conditions",
        default="oracle",
        help="comma separated: oracle,a,b,c",
    )
    p.add_argument("--n-tasks", type=int, default=1)
    p.add_argument("--concurrency", type=int, default=8)
    p.add_argument("--max-turns", type=int, default=50)
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--served-model", default=DEFAULT_SERVED_MODEL)
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--max-model-len", type=int, default=32768)
    p.add_argument("--gpu-memory-utilization", type=float, default=0.85)
    p.add_argument("--vllm-image", default=DEFAULT_VLLM_IMAGE)
    p.add_argument(
        "--gpu-types",
        default="rtx-5090,rtx-pro-6000,h100",
    )
    p.add_argument("--controller-root", default=DEFAULT_ROOT)
    p.add_argument("--controller-id", help="reuse a Daytona GPU sandbox")
    p.add_argument("--starlings-ref", default="feat/murmurations")
    p.add_argument(
        "--experiments-ref",
        default="feat/daytona-agentic-benchmarks",
    )
    p.add_argument("--agent-a", default="terminus-2")
    p.add_argument("--agent-b")
    p.add_argument("--agent-c")
    p.add_argument("--controller-create-timeout", type=int, default=900)
    p.add_argument("--model-boot-timeout", type=int, default=900)
    p.add_argument("--benchmark-timeout", type=int, default=21600)
    p.add_argument(
        "--output",
        default="starlings-agentic-results.tar.gz",
    )
    p.add_argument("--keep-controller", action="store_true")
    p.add_argument("--keep-going", action="store_true")
    p.add_argument("--skip-setup", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    return p


def dry_run(args) -> int:
    benchmarks = csv(args.benchmarks)
    conditions = csv(args.conditions)
    validate_benchmarks(benchmarks)
    api_base = f"http://127.0.0.1:{args.port}/v1"
    for condition in conditions:
        for benchmark in benchmarks:
            spec = make_spec(
                condition,
                benchmark,
                served_model=args.served_model,
                api_base=api_base,
                agent_a=args.agent_a,
                agent_b=args.agent_b,
                agent_c=args.agent_c,
                max_turns=args.max_turns,
            )
            jobs = (
                f"{args.controller_root}/runs/{condition}/{benchmark}/jobs"
            )
            print(
                harbor_command(
                    spec,
                    concurrency=args.concurrency,
                    max_tasks=args.n_tasks,
                    output_dir=jobs,
                )
            )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.n_tasks is not None and args.n_tasks <= 0:
        raise SystemExit("--n-tasks must be > 0")
    if args.concurrency <= 0:
        raise SystemExit("--concurrency must be > 0")
    if args.max_turns <= 0:
        raise SystemExit("--max-turns must be > 0")
    if args.dry_run:
        return dry_run(args)

    require_env("DAYTONA_API_KEY")
    try:
        from daytona import Daytona
    except ImportError as exc:
        raise SystemExit(
            "install the Daytona SDK first: python3 -m pip install daytona"
        ) from exc

    daytona = Daytona()
    sandbox = None
    try:
        if args.controller_id:
            sandbox = daytona.get(args.controller_id)
            print(f"reusing controller_id={sandbox.id}", flush=True)
        else:
            sandbox = create_controller(daytona, args)
            print(f"controller_id={sandbox.id}", flush=True)

        if not args.skip_setup:
            setup_controller(sandbox, args)

        results = run_suite(sandbox, args)
        archive = download_results(sandbox, args, results)
        print(f"\nresults={archive}", flush=True)
        print(json.dumps(results, indent=2), flush=True)
        return 1 if any(r["exit_code"] for r in results) else 0
    finally:
        if sandbox is not None and args.keep_controller:
            print(f"controller retained: {sandbox.id}", flush=True)
        elif sandbox is not None:
            try:
                print(f"deleting controller: {sandbox.id}", flush=True)
                daytona.delete(sandbox)
            except Exception as exc:
                print(
                    f"warning: controller cleanup failed: {exc}",
                    file=sys.stderr,
                )


if __name__ == "__main__":
    raise SystemExit(main())
