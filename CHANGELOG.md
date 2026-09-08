# Changelog

## Unreleased

- `docs`: rewrote `CLAUDE.md` to match the shipped module layout and CLI commands, and archived `SPECIFICATION.md` to `docs/history/SPECIFICATION.md` as superseded by `spec/` (#60).

### `locus lint` and `locus index`: OKF conformance and generated indexes

Two commands that make an OKF bundle, a palace, or a Claude Code memory
directory checkable in CI and keep its index files honest (#51).

**`locus lint`** checks [OKF v0.2](https://github.com/GoogleCloudPlatform/open-knowledge-format)
conformance plus the Locus palace conventions:

- OKF rules (`okf.*`): a parseable frontmatter block with a non-empty `type` on
  every non-reserved document, `index.md` frontmatter limited to a bundle-root
  `okf_version`, `log.md` date headings that are ISO 8601 and ordered newest
  first, `generated`/`verified`/`sources` entries that name an actor or a
  resource, and ISO 8601 timestamps. Unknown keys and unknown type values are
  never reported: section 4.1 requires consumers to tolerate both. `verified`
  is accepted as a list or as one bare mapping.
- Locus rules (`locus.*`): the size limits from `spec/size-limits.md` (soft
  limit warns, hard limit errors) and the room main-file rule from
  `spec/room-conventions.md`.
- `--check` exits non-zero on errors, `--strict` on warnings too. A palace
  carries no frontmatter by design, so its missing-type rules are warnings
  rather than a CI failure on the layout `spec/recall.md` describes.
- `--fix` adds `type` (from `--type-map DIR=TYPE` or `[lint.types]`),
  `generated.at` (from the file's first git commit), and `status: deprecated`
  (for `--archive-glob` paths). It never rewrites or deletes an existing key,
  never invents a `generated` block, and is idempotent: edits are textual
  insertions, not a YAML round trip, so a second run produces identical bytes.

**`locus index`** generates the three index shapes the conventions define:
an OKF `index.md` per directory in section 8 form, the 50-line palace
`INDEX.md` routing table, and a Claude Code `MEMORY.md` of one
`- [Title](file.md) - description` line per topic file. Output is
deterministic, so `--check` is a byte comparison; entries are sorted, the
palace consolidation date and author prose are preserved rather than
regenerated, and only index files are ever written. A palace over the 50-line
budget is an error pointing at sub-indices, not a truncated file.

Both are dispatched from `locus.cli` before the Agent SDK is imported, so a CI
job that only checks conformance never installs it. `.locus.toml` gains a
`[lint]` table (`roots`, `archive_globs`, `[lint.types]`) and falls back to
`[recall] roots`, so a project configured once for recall needs no second
configuration.

**Fixes found while building this:**

- Root classification is case-sensitive. macOS and Windows filesystems are not,
  so `Path("bundle/INDEX.md").is_file()` answered `True` for an OKF `index.md`
  and every bundle classified as a palace on a Mac, which would have replaced
  its `index.md` files with an `INDEX.md`.
- A palace with no rooms and a memory directory with no topic files generate
  nothing, rather than replacing a hand-written or bootstrapped index with an
  empty placeholder.

Docs: new `spec/lint-and-index.md`, README "Lint and index" section, 60 unit
tests in `tests/unit/test_conform.py` over new fixtures in
`tests/fixtures/conform/`.

### Palace bootstrap for explicit roots, and the missing `locus-security` CLI

Two bugs found while wiring Locus into a container deployment.

**Fixes:**

- `fix(mcp)`: `find_palace()` now bootstraps a skeleton `INDEX.md` (plus `global/` and
  `projects/` when the directory is empty) for palaces given via `--palace`, `LOCUS_PALACE`,
  or `./.locus`. Only the `~/.locus` fallback was bootstrapped before, so every container
  deployment, which always passes `--palace`, started without an index and `memory_list`
  answered "No INDEX.md found" until someone wrote one by hand (#52). Existing files are never
  modified, a read-only root is logged and skipped, and the Claude Code auto-memory bridge
  directory is deliberately left untouched.
- `fix(security)`: the `locus-security init-keys` / `sign-all` / `rotate-keys` commands
  documented in the README, `docs/security.md`, `docs/onboarding.md`,
  `templates/locus-security.yaml`, and the `load_keystore()` error message had no
  console-script entry point. Added `locus/security/main.py` and the `locus-security`
  script with `init-keys`, `sign-all`, `verify-all`, and `rotate-keys` (#53).

**Security review fixes** (all on the newly reachable key path):

- `fix(security)`: `rotate-keys` gave the new key the same default id as the key it was
  retiring (`locus-YYYY-MM-DD` for both), so a single rotation on the day the key was
  created silently broke every existing signature: the retired archive is named after the
  key id, so it was overwritten, and `KeyStore.find_by_id` resolves a shared id to the
  *active* key, so verification used the wrong public key. New ids are now suffixed
  (`locus-2026-03-01-2`) until unique within the store, and `init-keys` rejects an
  explicit `--key-id` that another key already uses.
- `fix(security)`: a missing, wrong, or unexpected `LOCUS_SIGNING_PASSPHRASE` raised an
  uncaught `TypeError` with a traceback. All three cases now report which one it is and
  exit 1, and no message ever echoes the passphrase.
- `fix(security)`: `sign-all` aborted on the first file that was not valid UTF-8, without
  naming it, leaving every later file unsigned. Each bad file is now named on stderr and
  skipped, the rest are still signed, and the command exits 1 so a partial run is not
  mistaken for a clean one.
- `fix(security)`: `--expires-days -5` fell through the `> 0` guard and meant "never
  expires". Negative values are now a usage error; only 0 means never.
- `fix(security)`: `sign-all` signed symlinked files whose target resolves outside the
  palace, stamping palace-trusted provenance onto content the palace does not own.
  Escaping symlinks are now named and skipped, and `verify-all` fails on them. Symlinks
  pointing inside the palace are unaffected.
- `fix(security)`: the key store and its `retired/` directory are created 0700 and every
  file in them 0600, set explicitly rather than inherited from the umask. Writes fsync
  before the rename and clean up the temp file if anything fails.
- `fix(security)`: `rotate-keys` reads the store back after writing and fails loudly if
  the new active key does not match what it just generated, instead of surfacing a
  half-written store later as unexplained signing failures. The retired public key is
  still archived before `active.pem` is overwritten.
- `fix(security)`: `verify-all` exited 0 on a palace with no signable files, so a gate
  pointed at the wrong root passed vacuously. It now reports that and exits 1.
- `feat(security)`: `rotate-keys` accepts `--expires-days`, matching `init-keys`.
- `fix(security)`: **the signature did not bind a file to its path.** `verify_file()`
  rebuilt the signed payload from the sidecar's own `rel_path`, so the signature only
  attested "some file had this hash". Copying a signed low-value note plus its sidecar
  over `INDEX.md` verified clean and exited 0, with no key material needed. Because
  `memory_read` uses the same call to decide `[TRUSTED]`, that promoted attacker-chosen
  content into the tier the agent reads first. The payload is now rebuilt from where the
  file actually is and a path mismatch fails verification. `palace_slug` is still taken
  from the sidecar so moving or remounting a palace does not invalidate every signature.
- `fix(security)`: `init-keys --force` overwrote the active key without archiving its
  public half, so every signature made with it failed with "key not found" and the key
  needed to check them was gone from disk. `--force` now retires the outgoing key exactly
  as `rotate-keys` does.
- `fix(security)`: `--key-id` was used verbatim as the retired archive filename, so
  `--key-id ../../../escaped` wrote key files outside the store and hid the id from the
  uniqueness check. Key ids are now validated against `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`.
- `fix(security)`: `load_keystore()` never checked that `active.pub` belongs to
  `active.pem`. The two are separate renames, so a crash mid-save left a store that signed
  happily and produced signatures nothing could verify. The public key is now derived from
  the loaded private key and compared.
- `fix(security)`: key expiry was never enforced anywhere. `KeyPair.is_expired` had no
  call sites, so an expired key kept signing and its signatures kept verifying, which is
  worse than no expiry because the CLI help implies the flag gates something. `sign-all`
  now refuses an expired key.
- `fix(security)`: a sidecar that parsed to a scalar or a list raised `AttributeError` and
  killed the whole `verify-all` run, leaving every later file unchecked. A non-UTF-8 body
  did the same via `UnicodeDecodeError`, which is a `ValueError` and so slipped past the
  `except OSError` meant to catch it. Both now fail one file and the run continues.
- `fix(security)`: `except (InvalidSignature, Exception)` reported a corrupt public key, a
  malformed base64 payload, and a genuine forgery identically, with a message that ended
  in a bare colon because `InvalidSignature` stringifies to nothing. Each case now has its
  own reason, and that reason is what the server shows the agent.
- `fix(security)`: `init-keys` checked only `active.pem` for an existing store, so the
  half-written state a crash leaves behind read as "no keys here" and was silently
  overwritten. All three files are checked.
- `fix(security)`: `--expires-days 100000000` overflowed `timedelta` and exited with a
  traceback; values above 36500 are a usage error.
- `fix(mcp)`: bootstrap no longer writes `INDEX.md` into a palace that holds signature
  sidecars. The two features in this PR contradicted each other: an unsigned index turned
  a clean `verify-all` into a failure, and with `verify_on_read` enabled the server would
  refuse to serve the file it had just written.

**Tests:** 325 pass, up from 290. 9 bootstrap cases in `test_mcp.py` (empty root, env var,
`./.locus`, non-empty root, existing index preserved, read-only empty root, read-only
non-empty root, signed palace, bridge untouched); 46 CLI cases in
`tests/unit/security/test_cli.py`, including the relocation attack on a signed file, a
double rotation on one day that checks signatures from all three key generations still
verify, `--force` keeping old signatures verifiable, a torn keystore, key id traversal,
expiry enforcement, malformed and unparseable sidecars, the three passphrase failure
modes, the non-UTF-8 skip on both `sign-all` and `verify-all`, negative and oversized
`--expires-days`, symlink escape, and key store permissions; `--version` coverage for
`locus-security`.

Note that three of these are pre-existing bugs in `locus/security/signing.py` and
`keys.py` rather than in code this PR wrote. They are fixed here because this PR is what
first makes that path reachable from a command line, and because the path-binding one is
also reachable through `memory_read` today.
### `locus recall`: ranked, stale-aware retrieval over markdown roots
Locus gains its first structure-aware behaviour: frontmatter now drives ranking, trust
tiers, and staleness instead of being inert to every tool.
**New:**
- `feat(recall)`: `locus recall QUERY` builds or refreshes a SQLite FTS5 index (standard
  library only, no PyYAML) over one or more roots given by repeated `--root DIR`, a
  `.locus.toml` `[recall] roots = [...]` in the project or a parent directory, or
  `LOCUS_PALACE`. A root is any tree of markdown files: a palace, an OKF bundle, a Claude
  Code memory directory. The index lives at `${XDG_CACHE_HOME:-~/.cache}/locus/<hash-of-roots>.sqlite`,
  never inside a root, falls back to memory when that location is not writable, and
  refreshes incrementally by mtime, then content hash (#50).
- `feat(recall)`: bm25 ranking with title and description weighted above the body and an
  exact-phrase boost; ties prefer human-reviewed files, then newer `modified`. Each hit
  carries title, path, `modified`, OKF trust tier (`unverified`, `machine-confirmed`,
  `human-reviewed`), and a `STALE` flag when `stale_after` has passed or `status` is
  `deprecated`. Flags: `-k`, `--budget` (text output never exceeds it), `--include journal`
  (`type: Journal` is skipped by default), `--type`, `--json`, `--refresh`, `--index`.
- `feat(recall)`: importable `locus.recall.recall()` and `RecallIndex`; `python -m
  locus.recall` works too. The `locus` console script now dispatches `recall` before
  importing the Agent SDK, so a per-prompt hook pays only for `sqlite3`.
- `feat(mcp)`: `memory_search` delegates to the same index, scoped by its `path`
  argument, so results are ranked as `spec/mcp-server.md` always claimed. Each hit shows
  the relative path, title, trust tier, `STALE`, modified date, and the best matching
  body line. ripgrep and the Python `re` scan remain only as the fallback for a Python
  build without FTS5.
**Docs:** new `spec/recall.md`; README "Recall" section; `spec/mcp-server.md` now describes
the real `memory_search` behaviour; synthetic `tests/fixtures/okf-bundle/`; two
`scripts/bench-mcp.py` search cases that assumed ripgrep regex or substring semantics were
rewritten for the FTS5 backend (45/45 still pass).
**Tests:** 58 new in `tests/unit/test_recall.py` (frontmatter parser, planted hit, stale
flag, journal exclusion, byte budget, multi-root attribution, incremental refresh,
tie-breaks, phrase boost, config, CLI, console-script dispatch) and 9 in `test_mcp.py`
(ranked order, trust tier and `STALE` in output, scope to a file, journal inclusion,
write-then-search, FTS5 fallback); 336 total.
**Review fixes:**
- `fix(recall)`: **two concurrent refreshes of one index crashed** with
  `UNIQUE constraint failed: docs.root, docs.path`. `refresh()` snapshotted the known
  rows outside a transaction and only took the write lock at the first `INSERT`, so the
  process that waited for the lock resumed against a database the other had already
  populated and re-inserted rows it thought were new. That is the designed workload: a
  per-prompt hook and the MCP server's `memory_search` share one cache path, so two
  sessions were enough. Six concurrent runs over 1240 files: 5 of 6 failed before, 0 of 6
  now. `refresh()` takes `BEGIN IMMEDIATE` before reading, and the lock timeout went from
  5s to 30s.
- `fix(recall)`: a corrupt index file was a permanent, unrecoverable crash.
  `PRAGMA journal_mode=WAL` on a non-database raises `sqlite3.DatabaseError`, the *parent*
  of `OperationalError`, so it escaped the guard entirely. Every later run failed the same
  way, `--refresh` could not help (the failure is in `_connect`, before any refresh logic),
  and the only fix was deleting a file named after a hash the user never sees. The file is
  now discarded and rebuilt once. This also closes the matching hole in `memory_search`,
  which raised instead of falling back to ripgrep.
- `fix(recall)`: the index could be written inside an indexed root, contradicting the
  module docstring and `spec/recall.md`. Three ways: `XDG_CACHE_HOME` set to a root, a
  relative `XDG_CACHE_HOME` (which also made the index path depend on the working
  directory, so the same roots mapped to different files from different shells), and
  `--index <root>/x.sqlite`. All three are handled now.
- `fix(recall)`: NFD-normalized query text matched nothing. `[^\W_]+` splits on combining
  marks, which are category Mn and so not `\w`, while the FTS5 tokenizer folds them, so a
  decomposed accented word tokenized to two fragments. macOS produces NFD for filenames
  and some paste paths. Queries are NFC-normalized.
- `fix(recall)`: nested roots indexed the same physical file twice under two
  `(root, path)` keys and returned it twice, each copy eating one of `k` and one slice of
  `--budget`. A root contained in another is dropped with a warning, in `resolve_roots`
  and in `RecallIndex` for callers that bypass it.
- `fix(recall)`: a budget large enough for the header but not for one hit emitted the bare
  `Recalled memory:` header, so a hook injected a promise of recalled memory followed by
  nothing. For the test bundle that was every budget from 17 to 219. It now emits nothing.
- `fix(recall)`: `search()` turned any query-time SQL failure into `[]`, making a damaged
  `docs_fts` table or a lock held past the timeout indistinguishable from an honest
  zero-hit search. It raises `RecallError` now, which is what the CLI reports and what
  makes the MCP server fall back to ripgrep.
- `fix(recall)`: symlinked sub-directories were silently skipped (`os.walk` defaults to
  `followlinks=False`), so symlinking a memory directory into a palace made those files
  unsearchable. They are followed, with a realpath cycle guard.
- `fix(recall)`: `is_stale()` compared `status` case-sensitively. Harmless through the
  index, which lowercases at extraction, but a trap for the exported helper.
- `docs(recall)`: `--budget` help says it is ignored with `--json`.
**Tests:** 349 total, up from 336. 58 in `tests/unit/test_recall.py` for the feature
(frontmatter parser, planted hit, stale flag, journal exclusion, byte budget, multi-root
attribution, incremental refresh, tie-breaks, phrase boost, config, CLI, console-script
dispatch), 9 in `test_mcp.py` (ranked order, trust tier and `STALE` in output, scope to a
file, journal inclusion, write-then-search, FTS5 fallback), plus 11 review regressions
(concurrent refresh, corrupt index, index inside a root, cache home inside a root,
relative cache home, NFD query, nested roots, symlinked subdirectory, symlink loop,
`is_stale` casing, query failure raising).
Three existing tests asserted less than they claimed and were tightened:
`test_budget_is_a_hard_cap` sampled five budgets and checked only that output started with
the header, so the two budgets that rendered a header and no hit passed (it now sweeps
every budget and requires a real hit); `test_index_lives_in_cache_not_root` covered only
the happy XDG path; and `test_journal_excluded_by_default` asserted on the `journal/` path
prefix while the code filters on the frontmatter `type` column.

---

### Recall follow-up: a busy index degrades instead of failing

Three defects a second review pass confirmed after #56 merged, plus the lint it flagged.

- `fix(recall)`: a concurrent writer holding the WAL lock made `recall()` raise
  `sqlite3.OperationalError: database is locked` once the timeout expired, so a prompt hook
  died rather than returning anything. Two processes share one index by design (a hook and
  the MCP server's `memory_search`), so losing the race is normal, not exceptional. The
  refresh is now skipped with a warning on stderr and the search runs against the index as
  it stands: slightly stale hits beat a traceback and no hits. The timeout moved into
  `LOCK_TIMEOUT_SECONDS` so it can be patched in tests instead of waiting 30 seconds.
- `fix(recall)`: `search()` caught `sqlite3.DatabaseError`, so an `InterfaceError` from a
  connection torn down underneath it still surfaced as a bare traceback. It catches
  `sqlite3.Error` now, the base of every sqlite exception class.
- `fix(recall)`: recovering from an unusable index deleted the file even when the user
  named it with `--index`. The derived cache file is disposable and is still rebuilt in
  place, but an explicit `--index` is the user's own file: recall degrades to an in-memory
  index and leaves it exactly as found, saying so in the warning.
- `style(recall)`: cleared the ruff findings on the recall module and its tests (`UP017`
  `datetime.UTC`, `I001` import order, `PYI034` `Self` return on `__enter__`, `RUF015`,
  `PLW1510`). `SIM905` is left alone deliberately.

**Tests:** 408, up from 405. The merged corrupt-index test only covered the explicit
`--index` path, which is exactly the path whose behaviour changed, so it is split into a
derived-cache case (rebuilt on disk) and an explicit case (left untouched, in memory),
plus a locked-index case and one that pins the widened exception class.

---

## v0.10.0 — 2026-03-14

Bumps version to include `--version` flag on all CLIs, skill sync tooling,
`locus-palace-init` skill, Gemini Code Assist config, and GitHub Copilot
review instructions merged in PR #47.

See the [v0.10.0 milestone](https://github.com/Nano-Nimbus/locus/pull/47) for full details.

---

## feat: --version flag, skill sync, and locus-palace-init — 2026-03-13

**CLI**

- `feat`: `--version` flag added to `locus-mcp`, `locus`, and `locus-audit`; reports
  the installed package version via `importlib.metadata` (always in sync with PyPI)

**Developer experience**

- `feat(Makefile)`: new root-level `Makefile` with `install-skills`, `install-skills-dry`,
  `install`, `test`, and `lint` targets
- `feat(scripts/install-skills.sh)`: idempotent skill sync from `skills/claude/` to
  `~/.claude/skills/`; supports `--dry-run` and `CLAUDE_SKILLS_DIR` env override

**New skill: `locus-palace-init`**

- `feat(skills)`: new `locus-palace-init` skill — bootstraps a structured memory palace
  from existing `~/.claude/projects/*/memory/MEMORY.md` auto-memory files; discovers
  projects, groups them (`infra` / `tools` / `data` / `docs` / `projects`) by tech-tag
  heuristics, and writes `INDEX.md` + one room per project with imported content
- Available for Claude, Codex, and Gemini agents

**Tests**

- 13 new unit tests (`test_cli_version.py`, `test_install_skills.py`); 269 total

---

## Documentation audit — 2026-03-13

Full review of all markdown files, spec docs, and the GitHub wiki for consistency
with the v0.9.0 implementation.

**Repo (`docs/`, `spec/`)**

- `fix(docs/architecture.md)`: MCP server section said "four tools" and the Mermaid
  diagram was missing `memory_batch` (added in v0.8.0)
- `fix(docs/onboarding.md)`: palace location priority list had 3 entries and was
  missing `LOCUS_PALACE` env var (priority 2) and the Claude Code auto-memory bridge
  (priority 4); corrected to the full 5-step order matching `find_palace()`
- `fix(spec/mcp-server.md)`: safety model write-blocked dirs section was missing
  `.sig/` and `.security/` (added in v0.9.0)

**Wiki (pushed directly)**

- `fix(Home.md)`: GitHub repo and CONTRIBUTING.md links still pointed to `EDKarlsson` org
- `fix(Installation.md)`: `git clone` URL still pointed to `EDKarlsson`; added
  `locus-security` to CLI commands table; added security skill install instructions
- `fix(Getting-Started.md)`: "four tools" → five; added `memory_batch` example
- `fix(MCP-Server-Configuration.md)`: "four tools" → five; added `memory_batch` to
  tool table; added `.sig/` and `.security/` to write-blocked dirs; added SSE
  transport section (`--transport sse`, `LOCUS_ALLOWED_HOSTS`)
- `fix(CLI-Reference.md)`: "four tools" → five; added `memory_batch`; fixed
  `locus-mcp` resolution order (was truncated to 3 steps); added `--transport`,
  `--security` flags; added `--security`, `--metrics-file`, `--json` to `locus`;
  added full `locus-security` CLI section

---

## Repository move — 2026-03-11

Locus has moved to the **Nano-Nimbus** GitHub organization.

- New repo: https://github.com/Nano-Nimbus/locus
- PyPI package name (`locus-mcp`) and MCP server name (`io.github.Nano-Nimbus/locus`) updated
- Docker images now published automatically to `ghcr.io/nano-nimbus/locus-mcp` on each tagged release
- Old GitHub URLs (`github.com/EDKarlsson/locus`) redirect automatically

---

## v0.9.0 — 2026-03-11

### Ed25519 Security System — prompt injection defense for AI agents

Adds a cryptographic trust layer that makes injected content in memory files
cryptographically distinguishable from operator-authorized content. Enabled
with `--security` on both the Agent SDK and MCP server.

**Architecture — 5-layer stack:**

```
[Operator] --signs--> [Memory files + System prompt]
                              |
               [SecurityMiddleware]  ← PreToolUse / PostToolUse hooks
                              |
            Tags output: [TRUSTED] | [DATA] | [CRITICAL-DATA]
                              |
                       [Agent LLM]  ← nonce embedded in signed system prompt
```

**New package: `locus/security/`**

- `keys.py` — Ed25519 keypair generation, PKCS8/AES-256-CBC storage, rotation.
  `locus-security init-keys --palace <path>` creates `.security/keys/active.pem`
  + `active.pub`. `rotate-keys` archives the current public key to `retired/` and
  generates a fresh active pair. The private key is never retained after rotation.
- `signing.py` — `sign_file()` / `verify_file()` using Ed25519 sidecar files
  (`.sig/<filename>.sig`, YAML). Canonical payload: `locus-sig-v1\n<palace_slug>\n<rel_path>\n<signed_at>\n<sha256_hex>`.
  Content normalised to LF + BOM-stripped before hashing. `sign_system_prompt()`
  embeds a verifiable `SECURITY CONTEXT` block in the agent system prompt.
- `nonce.py` — `generate_session_nonce()` (HMAC-SHA256 over 32-byte random seed,
  32-char URL-safe base64). Injected into system prompt once per session;
  exfiltration detected in every tool output unconditionally.
- `taint.py` — `TaintTracker` with sticky propagation: TRUSTED → AUDITED →
  TAINTED. `classify_content()` checks for injection patterns and nonce.
  Nonce detection always escalates to TAINTED regardless of signature status.
- `config.py` — `SecurityConfig` from `locus-security.yaml`. Three criticality
  levels: `CRITICAL` (block), `AUDITED` (tag + log), `PERMISSIVE` (pass-through).
  Fail-closed: missing config or keys at startup raises `FileNotFoundError` —
  never silently degrades to unsecured mode.
- `middleware.py` — `SecurityMiddleware` registering `PreToolUse` / `PostToolUse`
  hooks. Pre-hook verifies file signatures and blocks CRITICAL violations.
  Post-hook injects trust tags and scans unconditionally for nonce exfiltration.
  `post_write_hook()` auto-signs new files after every Write.
- `__init__.py` — Public surface: `build_security_context()`, `SecurityContext`,
  `AuditEntry`.

**CLI integration**

- `locus --security` — Agent SDK run with full middleware stack. Fails at startup
  if `locus-security.yaml` or keys are absent.
- `locus-mcp --security` — MCP server with `_SecurityVerifier` verifying every
  `memory_read`, `memory_list`, and `memory_batch` result; auto-signing every
  `memory_write`.
- `locus-security init-keys`, `locus-security rotate-keys`, `locus-security sign-all`
  — key management CLI.

**Safety guard updates (`mcp/palace.py`)**

- `.sig/` and `.security/` added to `_WRITE_BLOCKED_DIRS` — agents cannot
  overwrite or forge sidecar files or key material via MCP tools.

**Skills and templates**

- `skills/claude/locus-security/SKILL.md` — agent-facing conventions: trust tag
  recognition, nonce discipline, injection pattern recognition (fake System:
  blocks, embedded tool calls), write discipline (never launder `[DATA]` content
  verbatim), incident reporting to `_security/incidents/`.
- `templates/locus-security.yaml` — fully annotated configuration template.

**Docs**

- `docs/security.md` — comprehensive reference: threat model, five-layer stack
  flowchart, secured-read and auto-sign write sequence diagrams, key management,
  signature protocol, trust tag table, full config reference, and design decision
  rationale (Ed25519 vs HMAC, sidecar vs inline, per-session nonce, fail-closed,
  sticky taint).
- `docs/architecture.md` — added Security Layer section with Mermaid flowchart.
- `docs/onboarding.md` — added section 8 "Security (optional hardening)" with
  4-step setup guide.
- `docs/benchmarks.md` — updated with three-way comparison: v0.8.0, v0.9.0-base,
  v0.9.0-security. Security overhead is ≤ +1.4 ms per category.

**Performance (see `docs/benchmarks.md` for full analysis)**

- Baseline (no security): 45/45, avg 4.7 ms, p95 13.8 ms
- With `--security`: 45/45, avg 4.9 ms (+0.2 ms, +4%), p95 10.3 ms
- Highest overhead: `write` +1.4 ms (+38%) and `batch` +1.1 ms (+35%)
  — both from Ed25519 sign/verify per file. Safety guards are unaffected
  (+0.1 ms — rejections still short-circuit before crypto).

**Tests**

- `tests/unit/security/` — 5 test modules covering all layers:
  `test_config.py`, `test_keys.py`, `test_signing.py`, `test_nonce.py`,
  `test_taint.py`
- `tests/unit/security/test_review_fixes.py` — 13 regression tests for all
  P1/P2 review findings: fail-open removal, coverage gaps in `memory_list` /
  `memory_batch`, path-traversal anchoring, unconditional nonce detection,
  `embed_nonce` flag respected

**New dependency:** `cryptography>=41.0`, `pyyaml>=6.0`

**Post-review fixes (this session)**

- `fix(security/config)`: `auto_sign_writes` default changed `True` → `False`.
  Default-on auto-signing enables taint laundering: a compromised agent can write
  injected content to disk, which the system then signs as `[TRUSTED]`. Operators
  must now explicitly opt in (`signing.auto_sign_writes: true`).
- `fix(security/taint)`: Added `_session_tainted` one-way latch to `TaintTracker`.
  `mark_tainted()` is called on any nonce exfiltration event or when a TAINTED
  pending record is processed. Cannot be cleared — only a fresh session resets it.
- `fix(security/middleware)`: `post_write_hook` now gates on
  `session_tainted` — suppresses auto-signing if any TAINTED content was processed
  this session, regardless of the `auto_sign_writes` config flag.
- `refactor(mcp/palace)`: Extracted `_slug_from_path` to `locus/utils.py` as
  `slug_from_path()` (public). `palace.py` retains a `_slug_from_path` alias;
  `security/__init__.py` now imports from `locus.utils` (breaks the reverse
  dependency: `security` → `mcp`).
- `fix(security/middleware)`: Removed unused `classify_content` import.
- `fix(security/keys)`: Moved three local `import json` calls to module level.
- `fix(security/keys)`: Updated stale PKCS8 comment (previously described "raw"
  format; code correctly uses PKCS8).
- **Tests**: 5 new regression tests — `session_tainted` starts False, latches,
  cannot be cleared; `auto_sign_writes` defaults False; auto-sign suppressed when
  session is tainted. Total: 256 tests.

---

## v0.8.0 — 2026-03-03

### Auto-memory bridge + `memory_batch` tool

**Auto-memory bridge** — `locus-mcp` now detects Claude Code's auto-memory
directory automatically when started from a project directory with no explicit
`--palace` argument. It derives the Claude Code project slug by replacing `/`
with `-` in the CWD path, then checks `~/.claude/projects/<slug>/memory/`.
If that directory exists it becomes the palace root. Zero configuration needed.
Priority slot: `.locus/` > **auto-memory** > `~/.locus/`.

**`memory_batch` tool** — new MCP tool that reads up to 20 palace files in a
single call. Sections are joined by `---`, each headed by `## <path>`. Missing
files, directories, and path-traversal violations are noted inline (never
raised as exceptions), so partial results are always returned for valid calls.
Raises `ValueError` only for invalid arguments (more than 20 paths). Path
headers are sanitized to strip embedded newlines and prevent Markdown injection.
Designed for research agents that need several rooms at startup.

**Spec** — `spec/mcp-server.md` updated with `memory_batch` in the Tools table,
an "Auto-Memory Bridge" section (slug derivation rule + log signal), and a
"Simplify Integration Pattern" documenting the `code-patterns` palace room
workflow for persistent project-specific context.

- `feat(mcp/palace)`: `_slug_from_path`, `find_auto_memory`, updated `find_palace` priority
- `feat(mcp/server)`: `memory_batch` tool, `_MAX_BATCH_PATHS = 20`
- +15 unit tests (`TestFindAutoMemory`, `TestMemoryBatch`) — 201 tests total

---

## v0.7.1 — 2026-03-03

### Fix: allowed hosts for SSE reverse-proxy deployments

FastMCP 1.26.0 enables DNS rebinding protection by default, restricting allowed `Host`
headers to loopback (`127.0.0.1:*`, `localhost:*`, `[::1]:*`). Requests from Tailscale
or Kubernetes ingress (e.g. `locus.oryx-tegu.ts.net`, `locus.locus.svc.cluster.local`)
were rejected with `421 Misdirected Request`.

- `fix(mcp)`: Added `LOCUS_ALLOWED_HOSTS` env var — comma-separated hostnames (using
  FastMCP `:*` port wildcard) appended to the loopback defaults before SSE server start
- `fix(mcp)`: Bumped `mcp>=1.26.0` floor (`TransportSecuritySettings` was added in 1.26.0)
- +3 unit tests (`TestSseAllowedHosts`) — 186 tests total

---

## v0.7.0 — 2026-03-03

### SSE transport + Docker image for network deployments

Exposes `locus-mcp` as a network service (SSE transport) suitable for homelab K8s
clusters, n8n/Windmill automation, and any environment where multiple clients need a
shared palace over HTTP.

**CLI change**

- Added `--transport {stdio,sse}` flag to `locus-mcp`. Default is `stdio` (unchanged).
  SSE mode starts a uvicorn server instead of reading from stdin.

**Auth**

- `BearerAuthMiddleware` — raw ASGI middleware (not `BaseHTTPMiddleware` which buffers
  response bodies and breaks long-lived SSE streams). Set `LOCUS_API_KEY` to enable.
  Responses include `WWW-Authenticate: Bearer realm="locus-mcp"` on 401.

**Environment variables (SSE mode)**

| Variable | Default | Purpose |
|---|---|---|
| `FASTMCP_HOST` | `127.0.0.1` | Bind address — set to `0.0.0.0` for container deployments |
| `FASTMCP_PORT` | `8000` | Bind port |
| `LOCUS_API_KEY` | unset | Bearer token for auth (recommended) |

**Dockerfile**

```dockerfile
FROM python:3.12-slim
ARG LOCUS_MCP_VERSION="0.7.1"
RUN pip install --no-cache-dir "locus-mcp==${LOCUS_MCP_VERSION}"
ENTRYPOINT ["locus-mcp"]
```

Image published to `ghcr.io/edkarlsson/locus-mcp:0.7.1`.

**Dependencies**

- `uvicorn>=0.30` promoted from optional to core dependency

**Security fixes (from code review)**

- `fix(mcp/server)`: rg argument injection — added `"--"` separator before user query
  in `memory_search` subprocess args (prevents query starting with `--` from injecting
  ripgrep flags)
- `fix(mcp/main)`: `secrets.compare_digest()` for constant-time token comparison

---

## v0.6.2 — 2026-03-03

### Auto-bootstrap palace on first start

`find_palace()` now creates `~/.locus/` with `INDEX.md`, `global/`, and `projects/`
subdirectories when the default palace doesn't exist — instead of raising an error.
Explicit `--palace` and `LOCUS_PALACE` paths still raise if missing (those are
configuration errors, not first-run). Updated test: `test_no_palace_raises` →
`test_no_palace_bootstraps_home_locus`.

---

## v0.6.1 — 2026-03-02

### MCP Registry ownership tag

- Added `<!-- mcp-name: io.github.EDKarlsson/locus -->` to README.md (required by the
  Official MCP Registry PyPI ownership validation)
- Updated `server.json` with correct schema fields: `repository.source: "github"` and
  `packages[0].transport.type: "stdio"` — passes registry validation

---

## v0.6.0 — 2026-03-02

### Public Release — History squash, bug fixes, registry assets

**History**
- 31 commits squashed to 6 clean milestone commits (`feat(v0.1)` through `feat(v0.6)`)
- Personal local paths and private project references removed from `CLAUDE.md`,
  `SPECIFICATION.md`, and `spec/reference-analysis.md`

**Audit fixes**
- `fix(audit)`: stale-room check now requires no `_metrics/` activity in the last
  **90 days** (not just "no metrics ever") — `RoomSignals.has_recent_metrics` tracks
  this via `_is_recent()` on each run's `started_at` timestamp
- `fix(audit)`: `retrieval_depth_avg` is now computed from **Type A runs only**
  (`query_type == "A"`) per `spec/audit-algorithm.md`; non-benchmark runs no longer
  inflate the degraded signal
- +4 tests → 181 total

**MCP security**
- `fix(mcp)`: SEC-002 — `_MAX_READ_BYTES` (500 KB) bounds `memory_read` and
  `memory_list` file reads; `_MAX_WRITE_BYTES` (500 KB) rejects oversized writes
  before any file I/O; `_read_bounded()` centralises both read paths
- +2 tests → 183 total

**Registry**
- `smithery.yaml` — Smithery.ai stdio config: `uvx locus-mcp` with `palace` path
  as optional configSchema field
- `server.json` — Official MCP Registry format (`io.github.EDKarlsson/locus`,
  PyPI package `locus-mcp` v0.6.0); also used by Glama.ai
- For mcp.so: `npx mcp-index https://github.com/EDKarlsson/locus`
- **PyPI**: `locus-mcp` v0.6.0 published at https://pypi.org/project/locus-mcp/

**CI fix**
- `fix(ci)`: `publish.yml` environment name corrected to `uv` to match the PyPI
  OIDC trusted publisher config; tag re-pointed to the fixed commit before re-publish

---

### MCP Integration Benchmarks + Architecture Docs

Adds two live MCP benchmark scripts, a palace-vs-flat recall comparison harness,
architecture diagrams, and benchmark charts for the repo.

**New scripts (`scripts/`)**

- `bench-mcp.py` — 40-case integration benchmark covering all 4 MCP tools across
  navigation, safety, search, write, fidelity, and edge categories. Runs against
  a live `locus-mcp` subprocess. Key fix: FastMCP surfaces `ValueError` as
  `isError=True` tool results (not Python exceptions) — use `resp.isError`, not
  `try/except`, to detect guard rejections.
- `bench-compare.py` — 9-scenario recall benchmark comparing palace vs flat-palace.
  Measures lines loaded into context and answer recall per query. Results: palace
  loads 52% fewer lines on average; flat misses session-only queries entirely.
- `generate-charts.py` — Regenerates `docs/img/` SVG charts from benchmark data.
  Requires `matplotlib` (added to `[dev]` optional dependency group).

**New fixture (`tests/fixtures/flat-palace/`)**

Flat baseline palace: `INDEX.md` + `MEMORY.md` (184 lines, all content in one file).
Used as the comparison target for `bench-compare.py`.

**New docs (`docs/`)**

- `architecture.md` — Four Mermaid diagrams: palace structure with line counts,
  MCP server internals (main/server/palace modules), agent interfaces
  (Claude/SDK/Codex/Gemini), memory lifecycle (query → consolidate loop).
- `benchmarks.md` — Benchmark methodology, results summary, and embedded charts.
- `img/lines-comparison.svg` — Grouped bar chart: palace vs flat lines loaded per scenario.
- `img/latency-by-category.svg` — Horizontal bar chart: MCP avg latency by tool category.

**README** updated: Benchmarking section now references the two new scripts and
their summary results; Structure section reflects new scripts and docs layout.

---

## v0.5.0 — 2026-03-02

### MCP Server

Adds `locus-mcp` — a stdio MCP server exposing the memory palace to any
MCP-capable client (Claude Desktop, Cursor, Zed) without requiring skill files.

**New package: `locus/mcp/`**

- `palace.py` — palace root resolution (`--palace`, `LOCUS_PALACE`, `.locus/`, `~/.locus/`);
  path-traversal guard; write-blocked dir check at any depth; extension allowlist
- `server.py` — `FastMCP` instance with four tools:
  - `memory_list(path?)` — returns `INDEX.md` or lists a room directory
  - `memory_read(path)` — reads any file in the palace
  - `memory_write(path, content)` — atomic write (temp-rename); blocks writes to
    `_metrics/`, `sessions/`, `archived/` at any nesting depth
  - `memory_search(query, path?)` — full-text search via `rg --json`; Python `re` fallback
- `main.py` — `locus-mcp` CLI entry point

**Supporting files**

- `spec/mcp-server.md` — architecture, tool surface, safety model, client config examples
- `scripts/try-mcp.py` — stdio client smoke test (exercises all four tools)
- `tests/unit/test_mcp.py` — 41 tests covering palace utilities and all MCP tools
- `.mcp.json` (gitignored) — local dev config pointing at `tests/fixtures/palace`

**Notable fix**: `rg` text output format breaks on filenames containing `-`
(e.g., `technical-gotchas.md`). Switched to `rg --json` for unambiguous path parsing.

---

## v0.4.0 — 2026-03-02

### Self Evaluation

**#16 — Audit algorithm spec** (`spec/audit-algorithm.md`)
Room discovery by `<dir>/<dir>.md` pattern; four health statuses
(critical / degraded / stale / healthy); scoring thresholds for file size,
session count, retrieval depth, and feedback rates.

**#17 — Health report format** (`spec/health-report-format.md`)
Markdown + JSON sidecar at `_metrics/audit-YYYY-MM-DDTHHMMSSZ.{md,json}`;
`_metrics/_last-audit.txt` timestamp file.

**#18 — `locus-audit` implementation** (`locus/audit/`, `skills/claude/locus-audit/`)
Full Python package: `model.py`, `scanner.py`, `report.py`, `main.py`.
`locus-audit` CLI entry point. 43 unit tests.

**#19 — Inferred disagreement signal** (`locus/feedback/signals.py`)
`classify_message()` detects implicit fail/partial signals in user follow-ups.
Fail patterns always win over partial patterns at all confidence tiers.
Step 7 added to `skills/claude/locus/SKILL.md`. 58 unit tests.

---

## v0.3 — 2026-03-02

### Performance Metrics

**#13 — Metrics schema** (`spec/metrics-schema.md`, `locus/agent/metrics.py`)
Schema v1: `schema_version`, `query_type`, `agent{model, sdk_version}`,
`feedback`, `suggestions[]`. Default storage at `palace/_metrics/`.

**#14 — `locus-feedback` skill**
`/locus-feedback <pass|partial|fail> [note]` — records quality feedback on
the most recent `_metrics/*.json` file.

**#15 — Suggestion logic + tests**
`generate_suggestions()` in `RunMetrics`; thresholds by query type (A/B/C/D).
First pytest infrastructure: `tests/unit/test_metrics.py` (33 tests).

---

## v0.2.0 — 2026-03-02

### Core Palace

- Templates: `INDEX.md`, room main file, session log
- Skills: Claude Code (`skills/claude/`), Codex (`skills/codex/`), Gemini (`skills/gemini/`)
- Python Agent SDK: `locus.agent` package, `locus` CLI, metrics collector
- Benchmark: 15-query palace vs flat fixture; palace 87% pass / 0% fail,
  flat 73% pass / 13% fail

---

## v0.1.0 — 2026-03-02

### Foundation

- Conventions: INDEX format, room conventions, size limits, write modes
- Spec: `spec/index-format.md`, `spec/room-conventions.md`, `spec/size-limits.md`,
  `spec/write-modes.md`
- Benchmark fixtures: `tests/fixtures/palace/` and `tests/fixtures/flat/`
- README and SPECIFICATION.md
