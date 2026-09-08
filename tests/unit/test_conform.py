"""Tests for ``locus lint`` and ``locus index``.

The fixtures under ``tests/fixtures/conform/`` are the contract: ``clean-bundle``
lints clean and its indexes never drift, ``broken-bundle`` carries exactly one
instance of each rule, and ``memory-root`` is a Claude Code auto-memory
directory.  Anything that mutates a tree copies it into ``tmp_path`` first, so
the checked-in fixtures stay the reference for the non-mutating tests.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from locus.conform import (
    ConformConfig,
    ConformError,
    fix_text,
    generate_root,
    lint_root,
    load_doc,
    root_kind,
    summarize,
    write,
)
from locus.conform.config import load_config, parse_type_map
from locus.conform.fix import GitDates, insert_generated_at, insert_key
from locus.conform.generate import IndexError_, render_memory_index, render_palace_index
from locus.conform.lint import failed
from locus.conform.main import index_main, lint_main

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CONFORM = FIXTURES / "conform"
CLEAN = CONFORM / "clean-bundle"
BROKEN = CONFORM / "broken-bundle"
MEMORY = CONFORM / "memory-root"
PALACE = FIXTURES / "palace"
ARCHIVE_CONFIG = ConformConfig(archive_globs=["archive/*"])


def rules(violations) -> set[str]:
    return {violation.rule for violation in violations}


def by_name(violations, name: str) -> list:
    return [v for v in violations if v.path.name == name]


def copy(source: Path, tmp_path: Path) -> Path:
    destination = tmp_path / source.name
    shutil.copytree(source, destination)
    return destination


class TestRootKind:
    def test_classifies_each_shape(self) -> None:
        assert root_kind(CLEAN) == "okf"
        assert root_kind(MEMORY) == "memory"
        assert root_kind(PALACE) == "palace"

    def test_okf_index_does_not_read_as_a_palace_index(self, tmp_path: Path) -> None:
        # On a case-insensitive filesystem (macOS, Windows) a plain is_file()
        # test answers True for INDEX.md when only index.md exists.
        root = tmp_path / "bundle"
        root.mkdir()
        (root / "index.md").write_text("# Bundle\n")
        (root / "note.md").write_text("---\ntype: Reference\n---\n# Note\n")
        assert root_kind(root) == "okf"

    def test_one_memory_file_does_not_make_a_memory_root(self) -> None:
        # okf-bundle holds a single Claude Code style file among OKF concepts.
        assert root_kind(FIXTURES / "okf-bundle") == "okf"


class TestLintConformance:
    def test_clean_bundle_has_no_violations(self) -> None:
        assert lint_root(CLEAN, ConformConfig()) == []

    def test_broken_bundle_reports_every_rule(self) -> None:
        found = rules(lint_root(BROKEN, ARCHIVE_CONFIG))
        assert found == {
            "okf.frontmatter-missing",
            "okf.frontmatter-unparseable",
            "okf.type-missing",
            "okf.generated-by",
            "okf.timestamp",
            "okf.verified-actor",
            "okf.sources-resource",
            "okf.index-frontmatter",
            "okf.index-entry",
            "okf.log-frontmatter",
            "okf.log-heading",
            "okf.log-order",
            "locus.archive-status",
        }

    def test_unterminated_frontmatter_is_not_reported_as_missing(self) -> None:
        found = rules(by_name(lint_root(BROKEN, ARCHIVE_CONFIG), "unterminated.md"))
        assert found == {"okf.frontmatter-unparseable"}

    def test_metadata_type_does_not_satisfy_okf_type(self) -> None:
        violation = next(iter(by_name(lint_root(BROKEN, ARCHIVE_CONFIG), "no-type.md")))
        assert violation.rule == "okf.type-missing"
        assert "metadata.type" in violation.message

    def test_unknown_keys_and_unknown_types_are_tolerated(self, tmp_path: Path) -> None:
        # OKF: consumers MUST NOT reject unrecognised fields or unknown types.
        root = tmp_path / "bundle"
        root.mkdir()
        (root / "odd.md").write_text(
            "---\ntype: SomethingNobodyDefined\nwidget_count: 4\nnested:\n  a: b\n---\n# Odd\n"
        )
        assert lint_root(root, ConformConfig()) == []

    def test_bare_verified_mapping_is_tolerated(self, tmp_path: Path) -> None:
        root = tmp_path / "bundle"
        root.mkdir()
        (root / "a.md").write_text(
            "---\ntype: Reference\nverified:\n  by: human:dank\n  at: 2026-05-01T00:00:00Z\n---\n"
        )
        (root / "b.md").write_text(
            "---\ntype: Reference\nverified:\n  - by: human:dank\n    at: 2026-05-01T00:00:00Z\n---\n"
        )
        assert lint_root(root, ConformConfig()) == []

    def test_empty_frontmatter_block_is_a_type_violation_not_a_missing_one(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "bundle"
        root.mkdir()
        (root / "empty.md").write_text("---\n---\n# Empty\n")
        assert rules(lint_root(root, ConformConfig())) == {"okf.type-missing"}

    def test_index_frontmatter_allowed_only_at_the_bundle_root(self, tmp_path: Path) -> None:
        root = copy(CLEAN, tmp_path)
        (root / "runbooks" / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Runbooks\n')
        found = by_name(lint_root(root, ConformConfig()), "index.md")
        assert [v.rule for v in found] == ["okf.index-frontmatter"]
        assert "outside a bundle root" in found[0].message


class TestLintPalaceRules:
    def test_palace_frontmatter_gaps_are_warnings_not_errors(self) -> None:
        violations = lint_root(PALACE, ConformConfig())
        assert violations
        assert all(v.severity == "warning" for v in violations)
        assert not failed(violations)
        assert failed(violations, strict=True)

    def test_hard_size_limit_is_an_error_and_soft_is_a_warning(self, tmp_path: Path) -> None:
        root = tmp_path / "palace"
        (root / "projects" / "big").mkdir(parents=True)
        (root / "global").mkdir()
        (root / "INDEX.md").write_text("# P\n\nScope.\n")
        main = root / "projects" / "big" / "big.md"

        main.write_text("# Big\n" + "line\n" * 160)
        soft = [v for v in lint_root(root, ConformConfig()) if v.rule == "locus.size-limit"]
        assert [v.severity for v in soft] == ["warning"]
        assert "150-line soft limit" in soft[0].message

        main.write_text("# Big\n" + "line\n" * 250)
        hard = [v for v in lint_root(root, ConformConfig()) if v.rule == "locus.size-limit"]
        assert [v.severity for v in hard] == ["error"]
        assert "200-line hard limit" in hard[0].message

    def test_index_and_memory_limits_apply_outside_a_palace(self, tmp_path: Path) -> None:
        root = tmp_path / "memory"
        root.mkdir()
        (root / "MEMORY.md").write_text("# Memory Index\n" + "- entry\n" * 220)
        violations = [v for v in lint_root(root, ConformConfig()) if v.rule == "locus.size-limit"]
        assert [v.severity for v in violations] == ["error"]

    def test_session_logs_are_exempt_from_size_limits(self, tmp_path: Path) -> None:
        root = tmp_path / "palace"
        sessions = root / "projects" / "big" / "sessions"
        sessions.mkdir(parents=True)
        (root / "INDEX.md").write_text("# P\n\nScope.\n")
        (root / "projects" / "big" / "big.md").write_text("# Big\n")
        (sessions / "2026-05-01.md").write_text("# Session\n" + "line\n" * 500)
        assert not [v for v in lint_root(root, ConformConfig()) if v.rule == "locus.size-limit"]

    def test_room_without_a_main_file(self, tmp_path: Path) -> None:
        root = tmp_path / "palace"
        room = root / "projects" / "orphan"
        room.mkdir(parents=True)
        (root / "INDEX.md").write_text("# P\n\nScope.\n")
        (room / "some-notes.md").write_text("# Notes\n")
        violations = [v for v in lint_root(root, ConformConfig()) if v.rule == "locus.room-main-file"]
        assert len(violations) == 1
        assert "orphan.md" in violations[0].message

        (room / "orphan.md").write_text("# Orphan\n")
        assert not [v for v in lint_root(root, ConformConfig()) if v.rule == "locus.room-main-file"]

    def test_readme_satisfies_the_room_main_file_rule(self, tmp_path: Path) -> None:
        root = tmp_path / "palace"
        room = root / "global" / "toolchain"
        room.mkdir(parents=True)
        (root / "INDEX.md").write_text("# P\n\nScope.\n")
        (room / "README.md").write_text("# Toolchain\n")
        (room / "extra.md").write_text("# Extra\n")
        assert not [v for v in lint_root(root, ConformConfig()) if v.rule == "locus.room-main-file"]


class TestConfig:
    def test_type_map_matches_deepest_first_then_by_bare_name(self, tmp_path: Path) -> None:
        config = ConformConfig(
            types={".": "Reference", "runbooks": "Runbook", "docs/analysis": "Analysis"}
        )
        root = tmp_path
        assert config.type_for(root, root / "a.md") == "Reference"
        assert config.type_for(root, root / "runbooks" / "a.md") == "Runbook"
        assert config.type_for(root, root / "deep" / "runbooks" / "a.md") == "Runbook"
        assert config.type_for(root, root / "docs" / "analysis" / "a.md") == "Analysis"

    def test_parse_type_map_rejects_a_missing_type(self) -> None:
        assert parse_type_map(["runbooks=Runbook", "docs/=Reference"]) == {
            "runbooks": "Runbook",
            "docs": "Reference",
        }
        with pytest.raises(ConformError, match="DIR=TYPE"):
            parse_type_map(["runbooks"])

    def test_lint_table_is_read_from_locus_toml(self, tmp_path: Path, monkeypatch) -> None:
        (tmp_path / ".locus.toml").write_text(
            '[lint]\narchive_globs = ["old/*"]\n\n[lint.types]\n"." = "Reference"\n'
        )
        monkeypatch.chdir(tmp_path)
        config = load_config(cwd=tmp_path)
        assert config.types == {".": "Reference"}
        assert config.archive_globs == ["old/*"]
        assert config.is_archived(tmp_path, tmp_path / "old" / "deep" / "a.md")

    def test_lint_roots_fall_back_to_the_recall_table(self, tmp_path: Path) -> None:
        from locus.recall.config import load_config_roots

        (tmp_path / "docs").mkdir()
        config_file = tmp_path / ".locus.toml"
        config_file.write_text('[lint]\n\n[lint.types]\n"." = "Reference"\n\n[recall]\nroots = ["docs"]\n')
        assert load_config_roots(config_file, ("lint", "recall")) == [tmp_path / "docs"]


class TestFix:
    def test_adds_only_inferable_fields(self, tmp_path: Path) -> None:
        root = copy(BROKEN, tmp_path)
        config = ConformConfig(types={".": "Reference"}, archive_globs=["archive/*"])
        before = rules(lint_root(root, config))
        assert {"okf.frontmatter-missing", "okf.type-missing", "locus.archive-status"} <= before

        from locus.conform import fix_root

        fix_root(root, config)
        after = rules(lint_root(root, config))
        assert "okf.frontmatter-missing" not in after
        assert "okf.type-missing" not in after
        assert "locus.archive-status" not in after
        # Nothing inferable about these, so they survive --fix untouched.
        assert {"okf.generated-by", "okf.timestamp", "okf.log-order"} <= after

    def test_fix_is_idempotent(self, tmp_path: Path) -> None:
        from locus.conform import fix_root

        root = copy(BROKEN, tmp_path)
        config = ConformConfig(types={".": "Reference"}, archive_globs=["archive/*"])
        fix_root(root, config)
        first = {p: p.read_text() for p in sorted(root.rglob("*.md"))}
        assert fix_root(root, config) == []
        assert {p: p.read_text() for p in sorted(root.rglob("*.md"))} == first

    def test_fix_never_rewrites_an_existing_key(self, tmp_path: Path) -> None:
        root = tmp_path / "bundle"
        (root / "archive").mkdir(parents=True)
        target = root / "archive" / "kept.md"
        target.write_text("---\ntype: Decision\nstatus: draft\n---\n# Kept\n")
        config = ConformConfig(types={".": "Reference"}, archive_globs=["archive/*"])
        assert fix_text(load_doc(root, target), config, GitDates()) is None
        violation = next(v for v in lint_root(root, config) if v.rule == "locus.archive-status")
        assert violation.severity == "warning"
        assert violation.fix is None

    def test_insert_key_creates_a_block_when_there_is_none(self) -> None:
        assert insert_key("# Title\n", "type", "Reference") == "---\ntype: Reference\n---\n\n# Title\n"

    def test_insert_key_lands_before_the_closing_marker(self) -> None:
        text = "---\ntitle: A\n# a comment\n---\nbody\n"
        assert insert_key(text, "type", "Reference") == (
            "---\ntitle: A\n# a comment\ntype: Reference\n---\nbody\n"
        )

    def test_insert_generated_at_handles_block_and_flow_mappings(self) -> None:
        block = "---\ngenerated:\n  by: human:dank\ntitle: A\n---\n"
        assert insert_generated_at(block, "2026-05-01T00:00:00Z") == (
            "---\ngenerated:\n  by: human:dank\n  at: 2026-05-01T00:00:00Z\ntitle: A\n---\n"
        )
        flow = "---\ngenerated: { by: human:dank }\n---\n"
        assert insert_generated_at(flow, "2026-05-01T00:00:00Z") == (
            "---\ngenerated: { by: human:dank, at: 2026-05-01T00:00:00Z }\n---\n"
        )
        assert insert_generated_at("---\ngenerated: a-scalar\n---\n", "x") is None
        assert insert_generated_at("# no frontmatter\n", "x") is None

    def test_generated_at_comes_from_the_first_git_commit(self, tmp_path: Path) -> None:
        root = tmp_path / "repo"
        root.mkdir()
        target = root / "note.md"
        target.write_text("---\ntype: Reference\ngenerated:\n  by: human:dank\n---\n# Note\n")

        def git(*args: str) -> None:
            subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)

        git("init", "-q")
        git("config", "user.email", "test@example.invalid")
        git("config", "user.name", "Test")
        git("add", "note.md")
        subprocess.run(
            ["git", "-C", str(root), "commit", "-q", "-m", "add", "--date", "2026-05-01T00:00:00Z"],
            check=True,
            capture_output=True,
            env={"GIT_COMMITTER_DATE": "2026-05-01T00:00:00Z", "PATH": "/usr/bin:/bin:/usr/local/bin"},
        )

        updated = fix_text(load_doc(root, target), ConformConfig(), GitDates())
        assert updated is not None
        assert "at: 2026-05-01T00:00:00" in updated

    def test_no_generated_block_is_never_invented(self, tmp_path: Path) -> None:
        # generated.by cannot be inferred, so a bare generated.at would only
        # trade one violation for another.
        root = tmp_path / "bundle"
        root.mkdir()
        target = root / "a.md"
        target.write_text("---\ntype: Reference\n---\n# A\n")
        assert fix_text(load_doc(root, target), ConformConfig(), GitDates()) is None
        assert "okf.generated-at" not in rules(lint_root(root, ConformConfig()))


class TestIndexGeneration:
    def test_clean_bundle_indexes_do_not_drift(self) -> None:
        assert [item.status for item in generate_root(CLEAN)] == ["ok", "ok"]

    def test_generation_is_deterministic(self, tmp_path: Path) -> None:
        root = copy(BROKEN, tmp_path)
        first = {item.path: item.content for item in generate_root(root)}
        second = {item.path: item.content for item in generate_root(root)}
        assert first == second

    def test_writing_then_regenerating_reports_no_drift(self, tmp_path: Path) -> None:
        root = copy(BROKEN, tmp_path)
        write(generate_root(root))
        assert all(item.status == "ok" for item in generate_root(root))

    def test_drift_is_detected_after_an_edit(self, tmp_path: Path) -> None:
        root = copy(CLEAN, tmp_path)
        (root / "index.md").write_text("# Tampered\n")
        statuses = {item.path: item.status for item in generate_root(root)}
        assert statuses[root / "index.md"] == "drift"
        assert statuses[root / "runbooks" / "index.md"] == "ok"

    def test_bundle_root_carries_okf_version_and_nested_indexes_do_not(self, tmp_path: Path) -> None:
        root = copy(CLEAN, tmp_path)
        generated = {item.path: item.content for item in generate_root(root)}
        assert generated[root / "index.md"].startswith('---\nokf_version: "0.2"\n---\n')
        assert not generated[root / "runbooks" / "index.md"].startswith("---")

    def test_entries_use_the_okf_section_8_form(self) -> None:
        content = {i.path: i.content for i in generate_root(CLEAN)}[CLEAN / "index.md"]
        assert "* [Greenhouse overview](greenhouse.md) - What the greenhouse bundle covers." in content
        assert "* [Runbooks](runbooks/)" in content

    def test_reserved_names_are_never_listed_as_entries(self, tmp_path: Path) -> None:
        root = copy(CLEAN, tmp_path)
        (root / "MEMORY.md").write_text("# Memory Index\n")
        content = {i.path: i.content for i in generate_root(root)}[root / "index.md"]
        for reserved in ("index.md", "log.md", "MEMORY.md", "INDEX.md"):
            assert f"]({reserved})" not in content

    def test_non_index_files_are_never_touched(self, tmp_path: Path) -> None:
        root = copy(BROKEN, tmp_path)
        before = {
            path: path.read_text()
            for path in sorted(root.rglob("*.md"))
            if path.name not in {"index.md", "INDEX.md", "MEMORY.md"}
        }
        write(generate_root(root))
        after = {path: path.read_text() for path in before}
        assert after == before


class TestPalaceIndex:
    def test_routing_table_and_preserved_prose(self) -> None:
        content = render_palace_index(PALACE)
        assert content.startswith("# Homelab Palace (Benchmark Fixture)\n")
        assert "## Project Rooms" in content
        assert "| `homelab-iac` |" in content
        assert "| `homelab-iac` | Proxmox homelab" in content
        assert "_Last consolidated: 2026-02-25_" in content
        assert "NAVIGATION:" in content

    def test_stays_within_the_fifty_line_budget(self) -> None:
        assert len(render_palace_index(PALACE).splitlines()) <= 50

    def test_over_budget_palace_is_an_error_not_a_truncation(self, tmp_path: Path) -> None:
        root = tmp_path / "palace"
        (root / "projects").mkdir(parents=True)
        (root / "INDEX.md").write_text("# Big\n\nScope.\n")
        for number in range(60):
            room = root / "projects" / f"room-{number:02d}"
            room.mkdir()
            (room / f"room-{number:02d}.md").write_text(f"# Room {number}\n\nA room.\n")
        with pytest.raises(IndexError_, match="over the 50-line limit"):
            render_palace_index(root)

    def test_empty_palace_index_is_left_alone(self, tmp_path: Path) -> None:
        root = tmp_path / "palace"
        root.mkdir()
        (root / "INDEX.md").write_text("# Hand written\n\nDo not clobber me.\n")
        assert generate_root(root) == []

    def test_pipes_in_a_description_do_not_break_the_table(self, tmp_path: Path) -> None:
        root = tmp_path / "palace"
        room = root / "projects" / "piped"
        room.mkdir(parents=True)
        (root / "INDEX.md").write_text("# P\n\nScope.\n")
        (room / "piped.md").write_text("# Piped\n\nUse a | b to split the stream.\n")
        row = [line for line in render_palace_index(root).splitlines() if line.startswith("| `piped`")]
        assert row[0].replace("\\|", "").count("|") == 4
        assert "a \\| b" in row[0]


class TestMemoryIndex:
    def test_one_line_per_file_in_the_expected_form(self) -> None:
        content = render_memory_index(MEMORY)
        assert content.startswith("# Memory Index\n")
        assert (
            "- [project_valve-chatter](project_valve-chatter.md) - Zone seven valve chatter "
            "is a tired solenoid spring, not a controller fault." in content
        )
        entries = [line for line in content.splitlines() if line.startswith("- [")]
        assert len(entries) == 3

    def test_grouped_by_category_and_sorted(self) -> None:
        content = render_memory_index(MEMORY)
        assert "## Gotcha" in content and "## Workflow" in content
        assert content.index("## Gotcha") < content.index("## Workflow")
        gotchas = content.split("## Gotcha")[1].split("## Workflow")[0]
        assert gotchas.index("project_heat-pump") < gotchas.index("project_valve-chatter")

    def test_uncategorised_files_fall_into_a_flat_list(self, tmp_path: Path) -> None:
        root = tmp_path / "memory"
        root.mkdir()
        (root / "a.md").write_text("---\nname: a\ndescription: First.\n---\n# A\n")
        (root / "b.md").write_text("---\nname: b\ndescription: Second.\n---\n# B\n")
        content = render_memory_index(root)
        assert "##" not in content
        assert content.splitlines()[2:] == ["- [a](a.md) - First.", "- [b](b.md) - Second."]

    def test_descriptions_are_collapsed_to_one_line(self, tmp_path: Path) -> None:
        # A multi-line entry would break the union merge that lets two agents
        # append to the same index without conflicting.
        root = tmp_path / "memory"
        root.mkdir()
        (root / "a.md").write_text('---\nname: a\ndescription: "one"\n---\n# A\n\nTwo\nlines\n')
        (root / "b.md").write_text("---\nname: b\n---\n# B\n\nWrapped\nover\ntwo lines.\n")
        entries = [line for line in render_memory_index(root).splitlines() if line.startswith("- [")]
        assert entries == ["- [a](a.md) - one", "- [b](b.md) - Wrapped over two lines."]

    def test_empty_memory_root_is_left_alone(self, tmp_path: Path) -> None:
        root = tmp_path / "memory"
        root.mkdir()
        (root / "MEMORY.md").write_text("# Memory Index\n\nHand written.\n")
        assert generate_root(root) == []


class TestCli:
    def test_lint_reports_but_exits_zero_without_check(self, tmp_path: Path, capsys) -> None:
        assert lint_main(["--root", str(BROKEN), "--archive-glob", "archive/*"]) == 0
        assert "okf.type-missing" in capsys.readouterr().out

    def test_lint_check_fails_on_errors_and_passes_on_a_clean_bundle(self, capsys) -> None:
        assert lint_main(["--root", str(BROKEN), "--check"]) == 1
        capsys.readouterr()
        assert lint_main(["--root", str(CLEAN), "--check"]) == 0
        assert "clean: no violations" in capsys.readouterr().out

    def test_lint_check_ignores_warnings_unless_strict(self, capsys) -> None:
        assert lint_main(["--root", str(PALACE), "--check"]) == 0
        capsys.readouterr()
        assert lint_main(["--root", str(PALACE), "--check", "--strict"]) == 1

    def test_lint_json_report(self, capsys) -> None:
        assert lint_main(["--root", str(BROKEN), "--json", "--archive-glob", "archive/*"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["summary"]["errors"] > 0
        assert payload["summary"]["fixable"] == 1
        assert {v["rule"] for v in payload["violations"]}
        assert all("severity" in v and "fixable" in v for v in payload["violations"])

    def test_lint_fix_reports_what_it_changed(self, tmp_path: Path, capsys) -> None:
        root = copy(BROKEN, tmp_path)
        code = lint_main(["--root", str(root), "--fix", "--type-map", ".=Reference"])
        assert code == 0
        out = capsys.readouterr().out
        assert "fixed  no-frontmatter.md" in out
        assert "okf.type-missing" not in out

    def test_index_writes_then_reports_clean(self, tmp_path: Path, capsys) -> None:
        root = copy(BROKEN, tmp_path)
        assert index_main(["--root", str(root)]) == 0
        assert "wrote" in capsys.readouterr().out
        assert index_main(["--root", str(root), "--check"]) == 0
        assert "drift" not in capsys.readouterr().out

    def test_index_check_detects_drift_and_writes_nothing(self, tmp_path: Path, capsys) -> None:
        root = copy(CLEAN, tmp_path)
        target = root / "index.md"
        target.write_text("# Tampered\n")
        assert index_main(["--root", str(root), "--check"]) == 1
        assert "drift" in capsys.readouterr().out
        assert target.read_text() == "# Tampered\n"

    def test_index_kind_override(self, tmp_path: Path, capsys) -> None:
        root = copy(MEMORY, tmp_path)
        assert index_main(["--root", str(root), "--kind", "okf"]) == 0
        assert (root / "index.md").is_file()
        assert not (root / "MEMORY.md").is_file()

    def test_index_json_report(self, tmp_path: Path, capsys) -> None:
        root = copy(CLEAN, tmp_path)
        assert index_main(["--root", str(root), "--check", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["summary"] == {"total": 2, "drifted": 0}

    def test_over_budget_index_is_reported_not_written(self, tmp_path: Path, capsys) -> None:
        root = tmp_path / "palace"
        (root / "projects").mkdir(parents=True)
        (root / "INDEX.md").write_text("# Big\n\nScope.\n")
        for number in range(60):
            room = root / "projects" / f"room-{number:02d}"
            room.mkdir()
            (room / f"room-{number:02d}.md").write_text(f"# Room {number}\n\nA room.\n")
        assert index_main(["--root", str(root)]) == 1
        assert "50-line limit" in capsys.readouterr().err
        assert (root / "INDEX.md").read_text() == "# Big\n\nScope.\n"

    def test_missing_roots_is_a_clean_error(self, tmp_path: Path, monkeypatch, capsys) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("LOCUS_PALACE", raising=False)
        assert lint_main([]) == 1
        assert "No roots configured" in capsys.readouterr().err


class TestCliDispatch:
    def test_lint_and_index_dispatch_without_the_agent_sdk(self) -> None:
        # A CI job that only checks conformance must not need the SDK, so both
        # subcommands are dispatched from locus.cli before it is imported.
        from locus.cli import _SUBCOMMANDS

        assert set(_SUBCOMMANDS) == {"recall", "lint", "index"}
        for module_name, function_name in _SUBCOMMANDS.values():
            module = __import__(module_name, fromlist=[function_name])
            assert callable(getattr(module, function_name))


class TestSummary:
    def test_counts_by_severity_and_fixability(self) -> None:
        counts = summarize(lint_root(BROKEN, ARCHIVE_CONFIG))
        assert counts["total"] == counts["errors"] + counts["warnings"]
        assert counts["fixable"] == 1
