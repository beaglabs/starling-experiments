#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest

MODULE_PATH = pathlib.Path(__file__).with_name("daytona_agentic_bench.py")
SPEC = importlib.util.spec_from_file_location("daytona_agentic_bench", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
bench = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bench
SPEC.loader.exec_module(bench)


class DaytonaAgenticBenchTests(unittest.TestCase):
    def test_benchmark_registry_is_exact_first_suite(self) -> None:
        self.assertEqual(
            bench.BENCHMARKS,
            {
                "skillsbench": "benchflow/skillsbench",
                "harbor-index": "harbor-index/harbor-index-1.0",
                "tau3": "sierra-research/tau3-bench",
                "frontier-bench": "frontier-bench/frontier-bench",
            },
        )

    def test_oracle_command_has_no_model(self) -> None:
        spec = bench.make_spec(
            "oracle",
            "skillsbench",
            served_model="frozen",
            api_base="http://127.0.0.1:8000/v1",
            agent_a="terminus-2",
            agent_b=None,
            agent_c=None,
            max_turns=50,
        )
        command = bench.harbor_command(
            spec,
            concurrency=4,
            max_tasks=1,
            output_dir="/tmp/jobs",
        )
        self.assertIn("-a oracle", command)
        self.assertIn("-e daytona", command)
        self.assertIn("--n-tasks 1", command)
        self.assertNotIn("--max-tasks", command)
        self.assertNotIn(" -m ", command)

    def test_a_uses_frozen_model_and_local_api(self) -> None:
        spec = bench.make_spec(
            "a",
            "harbor-index",
            served_model="frozen",
            api_base="http://127.0.0.1:8000/v1",
            agent_a="terminus-2",
            agent_b=None,
            agent_c=None,
            max_turns=42,
        )
        command = bench.harbor_command(
            spec,
            concurrency=8,
            max_tasks=10,
            output_dir="/tmp/jobs",
        )
        self.assertIn("-m openai/frozen", command)
        self.assertIn("api_base=http://127.0.0.1:8000/v1", command)
        self.assertIn("temperature=0", command)
        self.assertIn("max_turns=42", command)
        self.assertIn("--n-tasks 10", command)

    def test_b_requires_explicit_starlings_agent(self) -> None:
        with self.assertRaisesRegex(ValueError, "condition B requires"):
            bench.make_spec(
                "b",
                "tau3",
                served_model="frozen",
                api_base="http://127.0.0.1:8000/v1",
                agent_a="terminus-2",
                agent_b=None,
                agent_c=None,
                max_turns=50,
            )

    def test_c_has_no_model(self) -> None:
        spec = bench.make_spec(
            "c",
            "frontier-bench",
            served_model="frozen",
            api_base="http://127.0.0.1:8000/v1",
            agent_a="terminus-2",
            agent_b=None,
            agent_c="benchmarks.harbor_agents:DeterministicStarlingsAgent",
            max_turns=50,
        )
        command = bench.harbor_command(
            spec,
            concurrency=8,
            max_tasks=None,
            output_dir="/tmp/jobs",
        )
        self.assertIn(
            "-a benchmarks.harbor_agents:DeterministicStarlingsAgent",
            command,
        )
        self.assertNotIn(" -m ", command)
        self.assertNotIn("--n-tasks", command)


if __name__ == "__main__":
    unittest.main()
