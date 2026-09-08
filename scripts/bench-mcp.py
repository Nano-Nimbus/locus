"""
bench-mcp.py — Live integration benchmark for locus-mcp server.

Spawns the MCP server via stdio and runs structured test cases across
all five tools. Measures latency, correctness, safety, and search precision.

Usage:
    uv run scripts/bench-mcp.py [--palace PATH] [--debug]
    uv run scripts/bench-mcp.py --version 0.8.0   # saves to docs/bench/v0.8.0.json
"""

import argparse
import asyncio
import datetime
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PALACE = Path(__file__).parent.parent / "tests" / "fixtures" / "palace"


# ---------------------------------------------------------------------------
# Test case definition
# ---------------------------------------------------------------------------

@dataclass
class Case:
    """A single MCP tool invocation with expected outcome."""
    id: str
    tool: str                          # memory_list | memory_read | memory_write | memory_search
    args: dict[str, Any]
    # Scoring: provide ONE of these
    expect_contains: str | None = None  # result must contain this string
    expect_blocked: bool = False        # expect an MCP error (safety guard)
    expect_lines: int | None = None     # write: result must report this many lines
    expect_hit_count: int | None = None # search: must return exactly N match blocks
    category: str = "general"          # used for per-category stats


CASES: list[Case] = [
    # -----------------------------------------------------------------------
    # Navigation — read-only traversal of the fixture palace
    # -----------------------------------------------------------------------
    Case("list-index",        "memory_list", {},
         expect_contains="homelab-iac", category="navigation"),
    Case("list-room",         "memory_list", {"path": "projects/homelab-iac"},
         expect_contains="homelab-iac.md", category="navigation"),
    Case("list-room-sessions","memory_list", {"path": "projects/homelab-iac"},
         expect_contains="sessions/", category="navigation"),
    Case("list-as-read",      "memory_list", {"path": "projects/homelab-iac/homelab-iac.md"},
         expect_contains="K3s", category="navigation"),
    Case("read-canonical",    "memory_read", {"path": "projects/homelab-iac/homelab-iac.md"},
         expect_contains="bpg/proxmox", category="navigation"),
    Case("read-gotchas",      "memory_read", {"path": "projects/homelab-iac/technical-gotchas.md"},
         expect_contains="MUTUALLY EXCLUSIVE", category="navigation"),
    Case("read-platform",     "memory_read", {"path": "projects/homelab-iac/platform-services.md"},
         expect_contains="FluxCD", category="navigation"),
    Case("read-session",      "memory_read", {"path": "projects/homelab-iac/sessions/2026-02-25.md"},
         expect_contains="ServiceMonitor", category="navigation"),

    # -----------------------------------------------------------------------
    # Safety — all write-guard types + traversal variants
    # -----------------------------------------------------------------------
    Case("guard-sessions",        "memory_write",
         {"path": "projects/homelab-iac/sessions/evil.md", "content": "x"},
         expect_blocked=True, category="safety"),
    Case("guard-metrics",         "memory_write",
         {"path": "_metrics/bad.json", "content": "{}"},
         expect_blocked=True, category="safety"),
    Case("guard-archived",        "memory_write",
         {"path": "archived/old.md", "content": "x"},
         expect_blocked=True, category="safety"),
    Case("guard-binary",          "memory_write",
         {"path": "scratch/evil.exe", "content": "x"},
         expect_blocked=True, category="safety"),
    Case("guard-traversal-read",  "memory_read",
         {"path": "../../etc/passwd"},
         expect_blocked=True, category="safety"),
    Case("guard-traversal-write", "memory_write",
         {"path": "../../evil.md", "content": "x"},
         expect_blocked=True, category="safety"),
    Case("guard-traversal-deep",  "memory_read",
         {"path": "projects/homelab-iac/../../.."},
         expect_blocked=True, category="safety"),
    Case("guard-sessions-nested", "memory_write",
         {"path": "projects/homelab-iac/sessions/injected.md", "content": "x"},
         expect_blocked=True, category="safety"),
    Case("guard-metrics-nested",  "memory_write",
         {"path": "projects/homelab-iac/_metrics/foo.json", "content": "{}"},
         expect_blocked=True, category="safety"),

    # -----------------------------------------------------------------------
    # Search — precision queries against known fixture content
    # -----------------------------------------------------------------------
    Case("search-k3s",          "memory_search", {"query": "K3s"},
         expect_contains="K3s", category="search"),
    # memory_search is a bag-of-words FTS5 query, so a no-match probe must not
    # contain real words ("real", "term") that the fixture happens to use.
    Case("search-no-match",     "memory_search", {"query": "xyzzyplugh"},
         expect_contains="No matches", category="search"),
    Case("search-scoped-room",  "memory_search",
         {"query": "Proxmox", "path": "projects/homelab-iac"},
         expect_contains="Proxmox", category="search"),
    Case("search-flux",         "memory_search", {"query": "FluxCD"},
         expect_contains="v2.7.5", category="search"),
    Case("search-ip",           "memory_search", {"query": "192.168.2.201"},
         expect_contains="MetalLB", category="search"),
    Case("search-session-term", "memory_search", {"query": "n8n"},
         expect_contains="Prometheus", category="search"),
    Case("search-pg-gotcha",    "memory_search", {"query": "pg_basebackup"},
         expect_contains="checkpoint", category="search"),
    Case("search-invalid-path", "memory_search",
         {"query": "K3s", "path": "no/such/path"},
         expect_contains="not found", category="search"),

    # -----------------------------------------------------------------------
    # Write + Fidelity — round-trip: write → read → search → list → overwrite
    # Order matters: fidelity cases depend on prior writes.
    # -----------------------------------------------------------------------
    Case("write-new",          "memory_write",
         {"path": "scratch/bench-test.md", "content": "# Bench\n\ntest content\n"},
         expect_lines=3, category="write"),
    Case("read-written",       "memory_read", {"path": "scratch/bench-test.md"},
         expect_contains="test content", category="fidelity"),
    Case("search-written",     "memory_search", {"query": "test content"},
         expect_contains="bench-test", category="fidelity"),
    Case("list-written-dir",   "memory_list", {"path": "scratch"},
         expect_contains="bench-test.md", category="fidelity"),
    Case("write-overwrite",    "memory_write",
         {"path": "scratch/bench-test.md", "content": "# Bench\n\noverwritten\n"},
         expect_lines=3, category="write"),
    Case("read-overwritten",   "memory_read", {"path": "scratch/bench-test.md"},
         expect_contains="overwritten", category="fidelity"),
    Case("write-nested",       "memory_write",
         {"path": "scratch/deep/nested/file.md", "content": "# Nested\n\ndeep content\n"},
         expect_lines=3, category="write"),
    Case("read-nested",        "memory_read", {"path": "scratch/deep/nested/file.md"},
         expect_contains="deep content", category="fidelity"),
    Case("write-yaml",         "memory_write",
         {"path": "scratch/config.yaml", "content": "key: value\nother: val\n"},
         expect_lines=2, category="write"),

    # -----------------------------------------------------------------------
    # Edge — path handling, tool behaviour boundaries
    # -----------------------------------------------------------------------
    Case("list-whitespace-path", "memory_list", {"path": "   "},
         expect_contains="homelab-iac", category="edge"),
    Case("read-directory",       "memory_read", {"path": "projects/homelab-iac"},
         expect_contains="memory_list", category="edge"),
    Case("read-nonexistent",     "memory_read",
         {"path": "projects/homelab-iac/does-not-exist.md"},
         expect_contains="not found", category="edge"),
    Case("list-nonexistent",     "memory_list", {"path": "projects/no-such-room"},
         expect_contains="not found", category="edge"),
    # Regex queries were a ripgrep feature; the FTS5 backend matches the literal
    # version string as a phrase instead.
    Case("search-version-literal", "memory_search",
         {"query": "v2.7.5"},
         expect_contains="v2.7.5", category="edge"),
    Case("search-scoped-file",   "memory_search",
         {"query": "Grafana", "path": "projects/homelab-iac/platform-services.md"},
         expect_contains="Grafana", category="edge"),

    # -----------------------------------------------------------------------
    # Batch — memory_batch: multi-file reads, inline errors, safety
    # -----------------------------------------------------------------------
    Case("batch-two-files",      "memory_batch",
         {"paths": ["INDEX.md", "projects/homelab-iac/homelab-iac.md"]},
         expect_contains="K3s", category="batch"),
    Case("batch-three-files",    "memory_batch",
         {"paths": ["INDEX.md",
                    "projects/homelab-iac/technical-gotchas.md",
                    "projects/homelab-iac/platform-services.md"]},
         expect_contains="MUTUALLY EXCLUSIVE", category="batch"),
    Case("batch-missing-inline", "memory_batch",
         {"paths": ["INDEX.md", "no/such/file.md"]},
         expect_contains="not found", category="batch"),
    Case("batch-dir-inline",     "memory_batch",
         {"paths": ["projects/homelab-iac"]},
         expect_contains="directory", category="batch"),
    Case("batch-traversal-inline", "memory_batch",
         {"paths": ["../../etc/passwd"]},
         expect_contains="Path error", category="batch"),
]


# ---------------------------------------------------------------------------
# Harness (do not modify below this line)
# ---------------------------------------------------------------------------

@dataclass
class Result:
    case: Case
    passed: bool
    latency_ms: float
    actual: str | None = None
    error: str | None = None


async def run_case(session: ClientSession, case: Case) -> Result:
    t0 = time.perf_counter()
    try:
        resp = await session.call_tool(case.tool, case.args)
        elapsed = (time.perf_counter() - t0) * 1000

        text = resp.content[0].text if resp.content else ""
        is_error = bool(resp.isError)

        if case.expect_blocked:
            return Result(case, passed=is_error, latency_ms=elapsed,
                          actual=text[:200],
                          error=None if is_error else "expected block, got success")

        if case.expect_contains is not None:
            passed = case.expect_contains in text
        elif case.expect_lines is not None:
            passed = f"{case.expect_lines} lines" in text
        elif case.expect_hit_count is not None:
            # Count "--" separators in rg output as hit blocks
            hit_blocks = text.count("\n--\n") + (1 if text.strip() else 0)
            passed = hit_blocks == case.expect_hit_count
        else:
            passed = True

        return Result(case, passed=passed, latency_ms=elapsed, actual=text[:200])

    except Exception as e:
        elapsed = (time.perf_counter() - t0) * 1000
        is_mcp_error = "not permitted" in str(e) or "traversal" in str(e).lower() or "blocked" in str(e).lower()
        if case.expect_blocked and is_mcp_error:
            return Result(case, passed=True, latency_ms=elapsed, error=str(e))
        return Result(case, passed=False, latency_ms=elapsed, error=str(e))


def print_report(results: list[Result]) -> None:
    from collections import defaultdict
    cats: dict[str, list[Result]] = defaultdict(list)
    for r in results:
        cats[r.case.category].append(r)

    print("\n" + "=" * 60)
    print("  LOCUS MCP BENCHMARK REPORT")
    print("=" * 60)

    all_latencies = [r.latency_ms for r in results]
    passed = sum(1 for r in results if r.passed)

    print(f"\nOverall: {passed}/{len(results)} passed "
          f"({100*passed//len(results)}%)")
    print(f"Latency: avg={sum(all_latencies)/len(all_latencies):.1f}ms  "
          f"p95={sorted(all_latencies)[int(0.95*len(all_latencies))]:.1f}ms  "
          f"max={max(all_latencies):.1f}ms")

    print("\nBy category:")
    for cat, rs in sorted(cats.items()):
        p = sum(1 for r in rs if r.passed)
        lats = [r.latency_ms for r in rs]
        print(f"  {cat:<14} {p}/{len(rs)}  avg {sum(lats)/len(lats):.1f}ms")

    failures = [r for r in results if not r.passed]
    if failures:
        print(f"\nFailures ({len(failures)}):")
        for r in failures:
            print(f"  [{r.case.id}] {r.error or ''}")
            if r.actual:
                print(f"    got: {r.actual[:100]!r}")

    print()


def build_report(results: list[Result], version: str | None = None) -> dict:
    from collections import defaultdict
    cats: dict[str, list[Result]] = defaultdict(list)
    for r in results:
        cats[r.case.category].append(r)
    all_lats = sorted(r.latency_ms for r in results)
    passed = sum(1 for r in results if r.passed)
    report: dict = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "overall": {
            "passed": passed,
            "total": len(results),
            "avg_ms": round(sum(all_lats) / len(all_lats), 1),
            "p95_ms": round(all_lats[int(0.95 * len(all_lats))], 1),
            "max_ms": round(max(all_lats), 1),
        },
        "categories": {
            cat: {
                "passed": sum(1 for r in rs if r.passed),
                "total": len(rs),
                "avg_ms": round(sum(r.latency_ms for r in rs) / len(rs), 1),
            }
            for cat, rs in sorted(cats.items())
        },
    }
    if version:
        report["version"] = version
    return report


async def run(palace: Path, debug: bool, json_out: Path | None = None,
              version: str | None = None, security: bool = False) -> None:
    srv_args = ["run", "-m", "locus.mcp.main", "--palace", str(palace)]
    if security:
        srv_args.append("--security")
    params = StdioServerParameters(
        command="uv",
        args=srv_args,
        env=None,
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            results: list[Result] = []
            for case in CASES:
                r = await run_case(session, case)
                status = "PASS" if r.passed else "FAIL"
                if debug or not r.passed:
                    print(f"  {status} [{case.id}] {r.latency_ms:.1f}ms")
                    if not r.passed:
                        print(f"       {r.error or r.actual!r}")
                else:
                    print(f"  {status} [{case.id}]")
                results.append(r)

            print_report(results)

            if json_out:
                json_out.parent.mkdir(parents=True, exist_ok=True)
                report = build_report(results, version)
                if security:
                    report["security"] = True
                json_out.write_text(json.dumps(report, indent=2))
                print(f"Results written to {json_out}")

            # Cleanup scratch
            import shutil
            scratch = palace / "scratch"
            if scratch.exists():
                shutil.rmtree(scratch)


BENCH_DIR = Path(__file__).parent.parent / "docs" / "bench"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--palace", default=str(PALACE))
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--security", action="store_true",
                        help="Start the MCP server with --security (requires locus-security.yaml + keys)")
    parser.add_argument("--version", metavar="X.Y.Z",
                        help="Tag results with this version and save to docs/bench/vX.Y.Z.json")
    parser.add_argument("--json-out", metavar="PATH",
                        help="Write results JSON to this path (overrides --version default)")
    args = parser.parse_args()

    json_out: Path | None = None
    if args.json_out:
        json_out = Path(args.json_out)
    elif args.version:
        json_out = BENCH_DIR / f"v{args.version}.json"

    asyncio.run(run(Path(args.palace), args.debug, json_out, args.version, args.security))


if __name__ == "__main__":
    main()
