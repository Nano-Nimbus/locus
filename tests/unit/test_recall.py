"""Unit tests for locus.recall: frontmatter parser, FTS5 index, ranking, CLI (#50)."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import threading
import time
import unicodedata
from datetime import UTC, datetime
from pathlib import Path

import pytest

from locus.recall import RecallError, RecallIndex, recall, resolve_roots
from locus.recall.config import default_index_path, find_config, load_config_roots
from locus.recall.frontmatter import parse_mapping, split_frontmatter
from locus.recall.index import (
    best_line,
    build_match,
    extract_document,
    is_stale,
    iter_markdown,
    trust_tier,
)
from locus.recall.main import main
from locus.recall.output import HEADER, format_json, format_text, truncate_bytes

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
BUNDLE = FIXTURES / "okf-bundle"
PALACE = FIXTURES / "palace"


@pytest.fixture(autouse=True)
def cache_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep every index under the test's tmp dir, never in the real cache or a root."""
    cache = tmp_path / "xdg-cache"
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    monkeypatch.delenv("LOCUS_PALACE", raising=False)
    return cache


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# frontmatter.py
# ---------------------------------------------------------------------------

class TestFrontmatter:
    def test_no_frontmatter(self) -> None:
        fm, body = split_frontmatter("# Title\n\ntext\n")
        assert fm == {}
        assert body == "# Title\n\ntext\n"

    def test_unterminated_frontmatter_is_body(self) -> None:
        fm, body = split_frontmatter("---\ntitle: x\n\n# Heading\n")
        assert fm == {}
        assert body.startswith("---")

    def test_scalars_quotes_and_comments(self) -> None:
        fm, body = split_frontmatter(
            '---\ntitle: "Quoted: yes"\nstatus: stable # trailing\nempty:\nnum: 42\n---\nbody\n'
        )
        assert fm == {"title": "Quoted: yes", "status": "stable", "empty": "", "num": "42"}
        assert body == "body\n"

    def test_flow_list(self) -> None:
        fm, _ = split_frontmatter("---\ntags: [a, 'b c', \"d\"]\nnone: []\n---\n")
        assert fm["tags"] == ["a", "b c", "d"]
        assert fm["none"] == []

    def test_block_list_of_scalars(self) -> None:
        fm, _ = split_frontmatter("---\ntags:\n  - one\n  - two\n---\n")
        assert fm["tags"] == ["one", "two"]

    def test_block_list_of_mappings(self) -> None:
        text = (
            "---\nverified:\n  - by: human:gardener\n    at: 2026-05-02\n"
            "  - by: process:x/1\n    at: 2026-05-03\n---\n"
        )
        fm, _ = split_frontmatter(text)
        assert fm["verified"] == [
            {"by": "human:gardener", "at": "2026-05-02"},
            {"by": "process:x/1", "at": "2026-05-03"},
        ]

    def test_nested_mapping(self) -> None:
        fm, _ = split_frontmatter(
            "---\nmetadata:\n  type: project\n  tags: [pump]\ngenerated:\n  by: p\n  at: 2026-01-01\n---\n"
        )
        assert fm["metadata"] == {"type": "project", "tags": ["pump"]}
        assert fm["generated"] == {"by": "p", "at": "2026-01-01"}

    def test_crlf_and_bom(self) -> None:
        fm, body = split_frontmatter("﻿---\r\ntitle: t\r\n---\r\nbody\r\n")
        assert fm == {"title": "t"}
        assert body == "body\r\n"

    def test_list_item_with_url_is_scalar(self) -> None:
        assert parse_mapping("links:\n  - https://example.test/a\n") == {"links": ["https://example.test/a"]}

    def test_comment_lines_ignored(self) -> None:
        assert parse_mapping("# note\ntitle: t\n  # indented note\n") == {"title": "t"}


# ---------------------------------------------------------------------------
# index.py helpers
# ---------------------------------------------------------------------------

class TestExtraction:
    def test_trust_tiers(self) -> None:
        assert trust_tier(None) == "unverified"
        assert trust_tier([]) == "unverified"
        assert trust_tier([{"by": "process:x/1"}]) == "machine-confirmed"
        assert trust_tier([{"by": "process:x/1"}, {"by": "human:me"}]) == "human-reviewed"
        assert trust_tier("human:me") == "human-reviewed"

    def test_stale_rules(self) -> None:
        now = datetime(2026, 9, 7, tzinfo=UTC)
        assert is_stale("deprecated", "", now)
        assert is_stale("", "2026-01-01T00:00:00Z", now)
        assert is_stale("", "2026-01-01", now)
        assert not is_stale("", "2999-01-01", now)
        assert not is_stale("stable", "", now)
        assert not is_stale("", "not a date", now)

    def test_document_fields_from_frontmatter(self, tmp_path: Path) -> None:
        f = _write(tmp_path / "a.md", (BUNDLE / "irrigation-schedule.md").read_text())
        doc = extract_document(tmp_path, f, f.read_text(), f.stat().st_mtime)
        assert doc.title == "Irrigation schedule"
        assert doc.type == "Runbook"
        assert doc.tags == ["irrigation", "valves"]
        assert doc.modified == "2026-05-01T09:00:00Z"  # generated.at
        assert doc.tier == "human-reviewed"
        assert doc.status == "stable"
        assert "Valve chatter" in doc.body

    def test_document_fields_claude_style(self, tmp_path: Path) -> None:
        f = _write(tmp_path / "p.md", (BUNDLE / "pump-controller.md").read_text())
        doc = extract_document(tmp_path, f, f.read_text(), f.stat().st_mtime)
        assert doc.title == "project_pump-controller"
        assert doc.type == "project"
        assert doc.tags == ["pump", "firmware"]

    def test_document_without_frontmatter_uses_heading_and_mtime(self, tmp_path: Path) -> None:
        f = _write(tmp_path / "plain.md", "# Plain heading\n\nFirst paragraph here.\nStill it.\n\nSecond.\n")
        doc = extract_document(tmp_path, f, f.read_text(), 1_700_000_000.0)
        assert doc.title == "Plain heading"
        assert doc.modified.startswith("2023-11-14")
        assert doc.excerpt == "First paragraph here. Still it."
        assert doc.tier == "unverified"

    def test_build_match_phrase_then_bag_of_words(self) -> None:
        assert build_match("Why does the flux kustomization stall?") == (
            '"why does the flux kustomization stall" OR "flux" OR "kustomization" OR "stall"'
        )
        assert build_match("pg_basebackup") == '"pg basebackup" OR "pg" OR "basebackup"'
        assert build_match("WireGuard") == '"wireguard"'
        assert build_match("the and of") is None
        assert build_match('a "quoted" NEAR(x) term') == (
            '"a quoted near x term" OR "quoted" OR "near" OR "term"'
        )

    def test_best_line_prefers_the_line_with_most_terms(self) -> None:
        body = "# Heading\n\n- alpha only here\n- alpha and beta together\n- beta alone\n"
        assert best_line(body, ["alpha", "beta"]) == "- alpha and beta together"
        assert best_line(body, ["gamma"]) == ""
        assert best_line("x " * 300, ["x"]).endswith("...")

    def test_exact_phrase_outranks_scattered_tokens(self, tmp_path: Path) -> None:
        _write(tmp_path / "phrase.md", "# Services\n\nThe load balancer answers on 10.0.0.201 for every app.\n")
        _write(
            tmp_path / "scatter.md",
            "# Nodes\n\nnode-a 10.0.0.13, node-b 10.0.0.10, node-c 10.0.0.11, nas 10.0.0.161, vip 10.0.0.72\n",
        )
        hits = recall("10.0.0.201", roots=[tmp_path], k=2)
        assert hits[0].path == "phrase.md"


# ---------------------------------------------------------------------------
# recall() over the synthetic OKF bundle
# ---------------------------------------------------------------------------

class TestRecallBundle:
    def test_planted_hit_is_top(self) -> None:
        hits = recall("why does zone seven valve chatter", roots=[BUNDLE])
        assert hits, "expected at least one hit"
        top = hits[0]
        assert top.path == "irrigation-schedule.md"
        assert top.root == str(BUNDLE)
        assert top.abs_path == str(BUNDLE / "irrigation-schedule.md")
        assert top.tier == "human-reviewed"
        assert top.stale is False
        assert top.summary == top.description
        assert top.modified == "2026-05-01T09:00:00Z"

    def test_journal_excluded_by_default(self) -> None:
        # The filter is on the frontmatter `type` column, not the path, so
        # assert on the type: a path-prefix assertion passes even if the
        # exclusion were keyed off the directory name instead.
        hits = recall("valve chatter solenoid", roots=[BUNDLE], k=10)
        assert all(h.type.lower() != "journal" for h in hits)
        assert all(not h.path.startswith("journal/") for h in hits)
        hits = recall("valve chatter solenoid", roots=[BUNDLE], k=10, include_journal=True)
        journal = [h for h in hits if h.path == "journal/2026-05-03.md"]
        assert journal and journal[0].type.lower() == "journal"

    def test_stale_after_in_the_past(self) -> None:
        hit = recall("frost seedlings fleece", roots=[BUNDLE])[0]
        assert hit.path == "frost-watch.md"
        assert hit.stale is True
        assert hit.tier == "unverified"

    def test_deprecated_status_is_stale(self) -> None:
        hit = recall("propane heater north house", roots=[BUNDLE])[0]
        assert hit.path == "heater-decision.md"
        assert hit.stale is True

    def test_future_stale_after_is_fresh(self) -> None:
        hit = recall("compost bay turning", roots=[BUNDLE])[0]
        assert hit.path == "compost-cadence.md"
        assert hit.stale is False

    def test_machine_confirmed_tier(self) -> None:
        hit = recall("soil moisture probe offset firmware", roots=[BUNDLE])[0]
        assert hit.path == "sensor-calibration.md"
        assert hit.tier == "machine-confirmed"
        assert hit.modified == "2026-06-15"  # frontmatter modified beats generated.at

    def test_type_filter(self) -> None:
        hits = recall("valve chatter", roots=[BUNDLE], k=10, include_journal=True, types=["journal"])
        assert [h.path for h in hits] == ["journal/2026-05-03.md"]

    def test_no_hits_for_unknown_term(self) -> None:
        assert recall("xyzzy_no_such_term", roots=[BUNDLE]) == []

    def test_k_limits_results(self) -> None:
        assert len(recall("zone seven", roots=[BUNDLE], k=1)) == 1

    def test_index_lives_in_cache_not_root(self, cache_home: Path) -> None:
        recall("valve", roots=[BUNDLE])
        assert list((cache_home / "locus").glob("*.sqlite"))
        assert not list(BUNDLE.rglob("*.sqlite"))
        assert default_index_path([BUNDLE]).parent == cache_home / "locus"

    def test_index_path_is_order_insensitive(self) -> None:
        assert default_index_path([BUNDLE, PALACE]) == default_index_path([PALACE, BUNDLE])
        assert default_index_path([BUNDLE]) != default_index_path([PALACE])


class TestMultiRoot:
    def test_hits_carry_their_root(self) -> None:
        hits = recall("keepalived health check deadlock", roots=[PALACE, BUNDLE], k=3)
        assert hits[0].root == str(PALACE)
        assert hits[0].path.endswith("technical-gotchas.md")
        hits = recall("zone seven valve chatter", roots=[PALACE, BUNDLE], k=3)
        assert hits[0].root == str(BUNDLE)
        assert hits[0].path == "irrigation-schedule.md"

    def test_both_roots_can_appear_in_one_result(self, tmp_path: Path) -> None:
        a = tmp_path / "a"
        b = tmp_path / "b"
        _write(a / "one.md", "# Solenoid spring\n\nSolenoid spring replacement.\n")
        _write(b / "two.md", "# Spring notes\n\nThe solenoid spring wears out.\n")
        roots = {h.root for h in recall("solenoid spring", roots=[a, b], k=5)}
        assert roots == {str(a), str(b)}


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

class TestRanking:
    def test_title_match_outranks_body_match(self, tmp_path: Path) -> None:
        _write(tmp_path / "title.md", "# Widget calibration\n\nShort note.\n")
        _write(
            tmp_path / "body.md",
            "# Unrelated\n\n" + "widget calibration is mentioned in the body. " * 5,
        )
        hits = recall("widget calibration", roots=[tmp_path], k=2)
        assert [h.path for h in hits] == ["title.md", "body.md"]

    def test_tie_prefers_human_reviewed(self, tmp_path: Path) -> None:
        body = "# Same title\n\nalpha beta gamma\n"
        _write(tmp_path / "machine.md", "---\nverified:\n  - by: process:x/1\n---\n" + body)
        _write(tmp_path / "human.md", "---\nverified:\n  - by: human:me\n---\n" + body)
        _write(tmp_path / "nobody.md", body)
        hits = recall("alpha beta", roots=[tmp_path], k=3)
        assert [h.path for h in hits] == ["human.md", "machine.md", "nobody.md"]

    def test_tie_then_prefers_newer_modified(self, tmp_path: Path) -> None:
        body = "# Same title\n\nalpha beta gamma\n"
        _write(tmp_path / "old.md", "---\nmodified: 2026-01-01\n---\n" + body)
        _write(tmp_path / "new.md", "---\nmodified: 2026-06-01\n---\n" + body)
        hits = recall("alpha beta", roots=[tmp_path], k=2)
        assert [h.path for h in hits] == ["new.md", "old.md"]


# ---------------------------------------------------------------------------
# Incremental refresh
# ---------------------------------------------------------------------------

class TestIncremental:
    def test_refresh_lifecycle(self, tmp_path: Path) -> None:
        root = tmp_path / "root"
        a = _write(root / "a.md", "# A\n\nfirst\n")
        b = _write(root / "b.md", "# B\n\nsecond\n")
        with RecallIndex([root]) as index:
            stats = index.refresh()
            assert (stats.added, stats.updated, stats.removed, stats.unchanged) == (2, 0, 0, 0)

            stats = index.refresh()
            assert (stats.changed, stats.unchanged) == (0, 2)

            # Touch without changing content: mtime differs, hash does not.
            os.utime(b, (time.time() + 60, time.time() + 60))
            stats = index.refresh()
            assert (stats.changed, stats.unchanged) == (0, 2)

            a.write_text("# A\n\nfirst, now with zebra\n")
            os.utime(a, (time.time() + 120, time.time() + 120))
            stats = index.refresh()
            assert (stats.added, stats.updated, stats.removed) == (0, 1, 0)
            assert index.search("zebra")[0].path == "a.md"

            b.unlink()
            _write(root / "c.md", "# C\n\nthird\n")
            stats = index.refresh()
            assert (stats.added, stats.updated, stats.removed) == (1, 0, 1)
            assert index.search("second") == []

            stats = index.refresh(force=True)
            assert stats.added == 2

    def test_skips_dot_dirs_and_metrics(self, tmp_path: Path) -> None:
        _write(tmp_path / "keep.md", "# Keep\n\nzebra\n")
        _write(tmp_path / ".git" / "note.md", "# Hidden\n\nzebra\n")
        _write(tmp_path / "_metrics" / "report.md", "# Metrics\n\nzebra\n")
        assert [h.path for h in recall("zebra", roots=[tmp_path], k=10)] == ["keep.md"]

    def test_unwritable_cache_falls_back_to_memory(self, tmp_path: Path) -> None:
        _write(tmp_path / "root" / "a.md", "# A\n\nzebra\n")
        bad = tmp_path / "file-not-dir"
        bad.write_text("x")
        with RecallIndex([tmp_path / "root"], index_path=bad / "index.sqlite") as index:
            assert index.in_memory is True
            index.refresh()
            assert index.search("zebra")[0].path == "a.md"


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

class TestOutput:
    def test_text_format(self) -> None:
        hits = recall("frost seedlings", roots=[BUNDLE], k=1)
        text = format_text(hits)
        assert text.startswith("Recalled memory:\n1. [STALE] Frost watch (unverified, ")
        assert str(BUNDLE / "frost-watch.md") in text
        assert "Cover the seedlings" in text

    def test_text_empty_without_hits(self) -> None:
        assert format_text([]) == ""

    def test_budget_is_a_hard_cap(self) -> None:
        """Every budget, not a sampled few, and each rendering must hold a real hit.

        The old version sampled five budgets and asserted only
        ``startswith("Recalled memory:")``, so budgets 60 and 150, which
        rendered the bare header and no hit at all, passed. It would have
        passed against a format_text that never emitted a hit.
        """
        hits = recall("zone seven valve chatter solenoid", roots=[BUNDLE], k=5, include_journal=True)
        assert len(hits) >= 2
        full = format_text(hits, budget=100_000)
        for budget in range(len(full.encode("utf-8")) + 50):
            text = format_text(hits, budget=budget)
            assert len(text.encode("utf-8")) <= budget
            if text:
                assert text.startswith("Recalled memory:")
                # Never a header on its own: a hook must not promise recalled
                # memory and then show none.
                assert text != HEADER
                assert "\n1. " in text
        assert format_text(hits, budget=10) == ""

    def test_nothing_is_emitted_below_the_first_renderable_budget(self) -> None:
        """Output goes straight from "" to a real hit, with no header-only step.

        Every budget from the header length up to the first one that can fit a
        hit used to emit the 17-byte header alone.
        """
        hits = recall("zone seven valve chatter", roots=[BUNDLE], k=3)
        rendered = [b for b in range(400) if format_text(hits, budget=b)]
        assert rendered, "no budget under 400 renders a hit"
        first = rendered[0]
        assert first > len(HEADER.encode("utf-8"))
        assert all(format_text(hits, budget=b) == "" for b in range(first))
        assert format_text(hits, budget=first) != HEADER

    def test_budget_truncates_last_summary(self) -> None:
        hits = recall("zone seven valve chatter", roots=[BUNDLE], k=1)
        full = format_text(hits, budget=4096)
        short = format_text(hits, budget=len(full.encode("utf-8")) - 10)
        assert short.endswith("...\n")
        assert len(short.encode("utf-8")) <= len(full.encode("utf-8")) - 10

    def test_truncate_bytes_respects_multibyte(self) -> None:
        text = "héllo wörld " * 10
        out = truncate_bytes(text, 20)
        assert len(out.encode("utf-8")) <= 20
        assert out.endswith("...")

    def test_json_is_a_list_with_fields(self) -> None:
        data = json.loads(format_json(recall("valve chatter", roots=[BUNDLE], k=1)))
        assert isinstance(data, list) and len(data) == 1
        item = data[0]
        for key in ("title", "path", "abs_path", "root", "modified", "tier", "stale", "summary", "score"):
            assert key in item
        assert json.loads(format_json([])) == []


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class TestConfig:
    def test_explicit_roots_win(self, tmp_path: Path) -> None:
        assert resolve_roots([str(BUNDLE)], cwd=tmp_path) == [BUNDLE]

    def test_missing_root_is_an_error(self, tmp_path: Path) -> None:
        with pytest.raises(RecallError, match="not a directory"):
            resolve_roots([str(tmp_path / "nope")], cwd=tmp_path)

    def test_locus_toml_in_parent(self, tmp_path: Path) -> None:
        (tmp_path / "docs").mkdir()
        (tmp_path / ".locus.toml").write_text(f'[recall]\nroots = ["docs", "{BUNDLE.as_posix()}"]\n')
        nested = tmp_path / "src" / "deep"
        nested.mkdir(parents=True)
        assert find_config(nested) == tmp_path / ".locus.toml"
        assert resolve_roots(None, cwd=nested) == [(tmp_path / "docs").resolve(), BUNDLE]

    def test_locus_toml_validation(self, tmp_path: Path) -> None:
        cfg = tmp_path / ".locus.toml"
        cfg.write_text("[recall]\nroots = []\n")
        with pytest.raises(RecallError, match="non-empty list"):
            load_config_roots(cfg)
        cfg.write_text("[other]\nx = 1\n")
        with pytest.raises(RecallError, match=r"no \[recall\] table"):
            load_config_roots(cfg)
        cfg.write_text("not toml [[[")
        with pytest.raises(RecallError, match="Cannot read"):
            load_config_roots(cfg)

    def test_env_fallback_and_no_roots(self, tmp_path: Path) -> None:
        assert resolve_roots(None, cwd=tmp_path, env={"LOCUS_PALACE": str(BUNDLE)}) == [BUNDLE]
        with pytest.raises(RecallError, match="No roots configured"):
            resolve_roots(None, cwd=tmp_path, env={})


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class TestCli:
    def test_text_output(self, capsys: pytest.CaptureFixture) -> None:
        assert main(["--root", str(BUNDLE), "why", "does", "zone", "seven", "valve", "chatter"]) == 0
        out = capsys.readouterr().out
        assert out.startswith("Recalled memory:\n1. Irrigation schedule (human-reviewed, 2026-05-01)\n")
        assert str(BUNDLE / "irrigation-schedule.md") in out
        assert len(out.encode("utf-8")) <= 4096

    def test_json_output(self, capsys: pytest.CaptureFixture) -> None:
        assert main(["--root", str(BUNDLE), "--json", "-k", "2", "valve chatter"]) == 0
        data = json.loads(capsys.readouterr().out)
        assert next(d["path"] for d in data) == "irrigation-schedule.md"
        assert len(data) <= 2

    def test_budget_flag(self, capsys: pytest.CaptureFixture) -> None:
        assert main(["--root", str(BUNDLE), "--budget", "400", "-k", "3", "valve chatter zone"]) == 0
        out = capsys.readouterr().out
        assert 0 < len(out.encode("utf-8")) <= 400
        assert "\n1. " in out

    def test_budget_too_small_for_a_hit_prints_nothing(self, capsys: pytest.CaptureFixture) -> None:
        # 120 bytes fits the header but not one hit; it used to print the
        # header alone.
        assert main(["--root", str(BUNDLE), "--budget", "120", "-k", "3", "valve chatter zone"]) == 0
        assert capsys.readouterr().out == ""

    def test_include_journal_and_type(self, capsys: pytest.CaptureFixture) -> None:
        main(["--root", str(BUNDLE), "--json", "-k", "10", "valve chatter"])
        assert "journal/2026-05-03.md" not in capsys.readouterr().out
        main(["--root", str(BUNDLE), "--json", "-k", "10", "--include", "journal", "valve chatter"])
        assert "journal/2026-05-03.md" in capsys.readouterr().out
        main(["--root", str(BUNDLE), "--json", "-k", "10", "--type", "Gotcha", "seedlings valve"])
        assert [d["path"] for d in json.loads(capsys.readouterr().out)] == ["frost-watch.md"]

    def test_no_hits_prints_nothing_and_exits_zero(self, capsys: pytest.CaptureFixture) -> None:
        assert main(["--root", str(BUNDLE), "xyzzy_nothing"]) == 0
        assert capsys.readouterr().out == ""
        assert main(["--root", str(BUNDLE), "--json", "xyzzy_nothing"]) == 0
        assert capsys.readouterr().out.strip() == "[]"

    def test_no_roots_is_exit_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.chdir(tmp_path)
        assert main(["anything"]) == 1
        assert "No roots configured" in capsys.readouterr().err

    def test_locus_toml_from_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        (tmp_path / ".locus.toml").write_text(f'[recall]\nroots = ["{BUNDLE.as_posix()}"]\n')
        monkeypatch.chdir(tmp_path)
        assert main(["--json", "valve chatter"]) == 0
        assert json.loads(capsys.readouterr().out)[0]["path"] == "irrigation-schedule.md"

    def test_refresh_and_index_flags(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        index = tmp_path / "custom" / "idx.sqlite"
        assert main(["--root", str(BUNDLE), "--index", str(index), "--refresh", "valve"]) == 0
        assert index.exists()
        assert "Recalled memory:" in capsys.readouterr().out

    def test_console_script_dispatches_recall(self) -> None:
        # The `locus` script must reach recall without the Agent SDK in the way.
        result = subprocess.run(  # noqa: PLW1510 - the return code is the assertion
            ["locus", "recall", "--root", str(BUNDLE), "--json", "valve", "chatter"],
            capture_output=True,
            text=True,
            env={**os.environ, "XDG_CACHE_HOME": os.environ["XDG_CACHE_HOME"]},
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)[0]["path"] == "irrigation-schedule.md"

    def test_module_entry_point(self) -> None:
        result = subprocess.run(  # noqa: PLW1510 - the return code is the assertion
            ["python", "-m", "locus.recall", "--root", str(BUNDLE), "--json", "valve"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)


class TestReviewRegressions:
    """Regressions from the security and correctness review of this PR."""

    def test_concurrent_refresh_does_not_collide(self, tmp_path: Path, cache_home: Path) -> None:
        """Two refreshes of one index used to fail on the (root, path) unique index.

        refresh() snapshotted `known` outside a transaction and only took the
        write lock at the first INSERT, so the process that waited for the lock
        resumed with a stale snapshot and re-inserted rows the other had
        already written. That is the designed workload: a prompt hook and the
        MCP server's memory_search share one cache path.
        """
        root = tmp_path / "root"
        root.mkdir()
        for i in range(60):
            _write(root / f"doc{i}.md", f"# Doc {i}\n\nconcurrent marker text {i}\n")

        index_path = cache_home / "shared.sqlite"
        errors: list[BaseException] = []

        def refresh_once() -> None:
            try:
                with RecallIndex([root], index_path=index_path) as index:
                    index.refresh()
            except BaseException as exc:  # noqa: BLE001 - recorded and re-raised below
                errors.append(exc)

        threads = [threading.Thread(target=refresh_once) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert not errors, f"concurrent refresh failed: {errors!r}"
        with RecallIndex([root], index_path=index_path) as index:
            assert len(index.search("concurrent marker", k=100)) > 0

    def test_corrupt_cache_index_is_rebuilt_not_a_permanent_crash(
        self, tmp_path: Path, cache_home: Path
    ) -> None:
        """"file is not a database" is a DatabaseError, the parent of OperationalError.

        It escaped the guard entirely, so every later run crashed identically
        and --refresh could not help: the failure happens in _connect, before
        any refresh logic, on a file named after a hash the user never sees,
        so a prompt hook failed on every prompt.
        """
        root = tmp_path / "root"
        _write(root / "note.md", "# Note\n\nrecoverable marker\n")
        assert [h.path for h in recall("recoverable marker", roots=[root])] == ["note.md"]

        derived = default_index_path([root.resolve()])
        assert derived.is_relative_to(cache_home)
        derived.write_bytes(b"definitely not a sqlite database")

        # The derived cache file is disposable, so it is thrown away and
        # rebuilt on disk rather than degrading to memory.
        with RecallIndex([root]) as index:
            index.refresh()
            assert index.in_memory is False
            assert [h.path for h in index.search("recoverable marker", k=5)] == ["note.md"]
        assert derived.exists()

    def test_corrupt_explicit_index_is_left_alone(
        self, tmp_path: Path, cache_home: Path
    ) -> None:
        """An --index the user named is their file, not ours to delete.

        It still must not crash every run, so recall degrades to an in-memory
        index and leaves the file exactly as it found it.
        """
        root = tmp_path / "root"
        _write(root / "note.md", "# Note\n\nrecoverable marker\n")
        index_path = cache_home / "mine.sqlite"
        index_path.parent.mkdir(parents=True, exist_ok=True)
        junk = b"definitely not a sqlite database"
        index_path.write_bytes(junk)

        with RecallIndex([root], index_path=index_path) as index:
            index.refresh()
            assert index.in_memory is True
            assert [h.path for h in index.search("recoverable marker", k=5)] == ["note.md"]
        assert index_path.read_bytes() == junk

    def test_locked_index_still_searches_what_is_there(
        self,
        tmp_path: Path,
        cache_home: Path,
        capsys: pytest.CaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A refresh that cannot get the write lock must not fail the whole call.

        Two processes share one index by design, so one can lose the race and
        time out. Slightly stale hits beat a traceback and no hits at all.
        """
        # Real waits are 30s; no test should sit through one.
        monkeypatch.setattr("locus.recall.index.LOCK_TIMEOUT_SECONDS", 0.2)
        root = tmp_path / "root"
        _write(root / "note.md", "# Note\n\nlockable marker\n")
        index_path = cache_home / "locked.sqlite"
        assert [h.path for h in recall("lockable marker", roots=[root], index_path=index_path)] == [
            "note.md"
        ]

        # Hold the write lock from a second connection, as a concurrent
        # refresh in another process would.
        holder = sqlite3.connect(index_path, timeout=0.1)
        holder.execute("BEGIN IMMEDIATE")
        try:
            _write(root / "later.md", "# Later\n\nlockable marker too\n")
            hits = recall("lockable marker", roots=[root], index_path=index_path, k=5)
        finally:
            holder.rollback()
            holder.close()

        # The already-indexed document is still found; the one added while the
        # lock was held is simply not indexed yet.
        assert [h.path for h in hits] == ["note.md"]
        assert "index busy" in capsys.readouterr().err

    def test_search_reports_every_sqlite_error_class(
        self, tmp_path: Path, cache_home: Path
    ) -> None:
        """search() caught DatabaseError; an InterfaceError still looked like zero hits."""
        root = tmp_path / "root"
        _write(root / "note.md", "# Note\n\ninterface marker\n")
        index = RecallIndex([root], index_path=cache_home / "closed.sqlite")
        index.refresh()
        assert index.search("interface marker", k=5)
        index._con.close()
        with pytest.raises(RecallError, match="Index query failed"):
            index.search("interface marker", k=5)

    def test_index_may_not_live_inside_a_root(self, tmp_path: Path, cache_home: Path) -> None:
        root = tmp_path / "root"
        _write(root / "note.md", "# Note\n\nmarker\n")
        with pytest.raises(RecallError, match="inside the indexed root"):
            RecallIndex([root], index_path=root / "idx.sqlite")

    def test_cache_home_inside_a_root_is_not_used(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / "root"
        root.mkdir()
        monkeypatch.setenv("XDG_CACHE_HOME", str(root))
        assert not default_index_path([root]).is_relative_to(root)

    def test_relative_cache_home_is_ignored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A relative XDG_CACHE_HOME made the index path depend on the working
        # directory, so the same roots mapped to different index files.
        monkeypatch.setenv("XDG_CACHE_HOME", ".")
        path = default_index_path([BUNDLE])
        assert path.is_absolute()
        assert path.parent == (Path.home() / ".cache" / "locus").resolve()

    def test_decomposed_query_matches_composed_text(self, tmp_path: Path, cache_home: Path) -> None:
        """NFD input split mid-word, because a combining mark is not \\w.

        macOS hands out NFD filenames and some paste paths, so an accented
        query silently matched nothing while its NFC twin matched.
        """
        root = tmp_path / "root"
        _write(root / "pump.md", "# Pumpe\n\nDie p\u00f6mpe braucht Wartung.\n")
        nfc = "p\u00f6mpe"
        nfd = unicodedata.normalize("NFD", nfc)
        assert nfc != nfd
        assert build_match(nfd) == build_match(nfc)
        assert [h.path for h in recall(nfd, roots=[root], k=5)] == ["pump.md"]

    def test_nested_roots_do_not_duplicate_hits(self, tmp_path: Path, cache_home: Path) -> None:
        outer = tmp_path / "outer"
        inner = outer / "inner"
        _write(inner / "note.md", "# Note\n\nduplicated marker text\n")
        roots = resolve_roots([str(outer), str(inner)])
        assert roots == [outer.resolve()]
        hits = recall("duplicated marker", roots=[outer, inner], k=10)
        assert [h.abs_path for h in hits] == [str(inner.resolve() / "note.md")]

    def test_symlinked_subdirectory_is_indexed(self, tmp_path: Path, cache_home: Path) -> None:
        root = tmp_path / "root"
        elsewhere = tmp_path / "elsewhere"
        _write(root / "top.md", "# Top\n\nlinked marker at the top\n")
        _write(elsewhere / "deep.md", "# Deep\n\nlinked marker further down\n")
        (root / "sub").symlink_to(elsewhere, target_is_directory=True)
        found = {p.relative_to(root).as_posix() for p in iter_markdown(root)}
        assert found == {"top.md", "sub/deep.md"}
        assert {h.path for h in recall("linked marker", roots=[root], k=10)} == {
            "top.md",
            "sub/deep.md",
        }

    def test_symlink_loop_terminates(self, tmp_path: Path) -> None:
        root = tmp_path / "root"
        _write(root / "note.md", "# Note\n\nloop marker\n")
        (root / "self").symlink_to(root, target_is_directory=True)
        assert {p.relative_to(root).as_posix() for p in iter_markdown(root)} == {"note.md"}

    def test_is_stale_ignores_status_case(self) -> None:
        assert is_stale("Deprecated", "") is True
        assert is_stale("DEPRECATED", "") is True
        assert is_stale(" deprecated ", "") is True
        assert is_stale("active", "") is False

    def test_query_failure_raises_instead_of_looking_empty(
        self, tmp_path: Path, cache_home: Path
    ) -> None:
        """A damaged docs_fts used to return [], indistinguishable from no hits."""
        root = tmp_path / "root"
        _write(root / "note.md", "# Note\n\nsearchable marker\n")
        with RecallIndex([root], index_path=cache_home / "broken.sqlite") as index:
            index.refresh()
            assert index.search("searchable marker", k=5)
            index._con.execute("DROP TABLE docs_fts")
            with pytest.raises(RecallError, match="Index query failed"):
                index.search("searchable marker", k=5)
