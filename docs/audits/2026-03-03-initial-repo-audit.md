# Initial Repository Audit (March 3, 2026)

## Scope

Audit baseline from:

- `README.md`
- `CLAUDE.md`
- `SPECIFICATION.md` (since moved to [`docs/history/SPECIFICATION.md`](../history/SPECIFICATION.md), see #60)

Validation pass covered local tests, packaging/CLI smoke checks, CI workflow review, and spec-to-implementation alignment.

## Checks Executed

- `uv sync --extra dev && uv run pytest tests/unit/ -v --tb=short` -> 175 passed
- `uv build` -> sdist + wheel built successfully
- `uv run locus --help`, `uv run locus-mcp --help`, `uv run locus-audit --help` -> all CLI entry points load
- `.github/workflows/ci.yml` and `.github/workflows/publish.yml` reviewed
- `spec/*` vs `locus/audit/*` implementation reviewed

## Findings (Ordered by Severity)

### 1) High: stale-room scoring does not implement the spec's "last 90 days" rule

- Spec requires stale status when there are no metrics in the last 90 days (or no metrics at all): `spec/audit-algorithm.md:83`
- Current implementation only checks `retrieval_depth_avg is None`: `locus/audit/scanner.py:169-171`
- Effect: rooms with only old metrics never become stale, which diverges from documented behavior.

### 2) Medium: degraded retrieval-depth rule is not limited to Type A runs

- Spec defines `retrieval_depth_avg > 3.5` for **Type A** runs only: `spec/audit-algorithm.md:76`
- Current enrichment logic averages all touching runs regardless of `query_type`: `locus/audit/scanner.py:121-127`
- Degraded status then applies globally from that average: `locus/audit/scanner.py:160-162`
- Effect: Type B/C/D-heavy usage can incorrectly trigger degraded actions meant for Type A navigation quality.

### 3) Medium: unstructured directory action is missing from action-items output

- Spec says unstructured dirs should emit an action: `spec/audit-algorithm.md:127`
- Current code records `unstructured_dirs` in summary but never appends a corresponding action item: `locus/audit/main.py:47-55` and `locus/audit/main.py:58-81`
- Effect: report surfaces the metric but does not guide remediation.

### 4) Medium: audit report filename format diverges from documented format

- Spec format: `audit-YYYY-MM-DDTHHMMSSZ.*`: `spec/health-report-format.md:4` and `spec/health-report-format.md:149`
- Current timestamp stem generation outputs compact date and introduces a dot before `Z`: `locus/audit/report.py:151-152`
- Reproduction produced: `tests/fixtures/palace/_metrics/audit-20260303T041423.Z.md`
- Effect: tooling expecting the spec pattern may fail to match files.

### 5) Low: duplicate session-log filtering logic in `collect_room_signals`

- `logs` is computed twice; the first value is overwritten immediately: `locus/audit/scanner.py:71-77`
- Effect: no immediate functional break seen, but this is confusing and increases risk of future regression.

### 6) Low: dead helper with incorrect percentage branch

- `_fmt()` has a condition that can never be true (`"rate" in ""`) and appears unused: `locus/audit/report.py:38-45`
- Effect: currently low impact, but dead/incorrect utility code suggests missing test coverage around formatting helpers.

## Overall Health

- Build/test baseline is strong (all unit tests pass; packaging and CLI smoke checks pass).
- Primary risks are spec-implementation drift in the audit subsystem, not runtime breakage in core MCP/agent entry points.

## Recommended Next Actions

1. Align stale scoring with the 90-day metric recency requirement.
2. Restrict retrieval-depth degradation logic to Type A runs.
3. Emit an explicit unstructured-directory action item when `unstructured_dirs > 0`.
4. Fix audit filename timestamp formatting to match spec and add regression tests.
5. Remove duplicate/dead audit code paths and add focused tests for these edge cases.
