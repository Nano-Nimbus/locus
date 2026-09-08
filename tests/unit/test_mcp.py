"""Unit tests for locus.mcp — MCP server tools (#21, #22)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from locus.mcp.palace import (
    _slug_from_path,
    assert_writable,
    find_auto_memory,
    find_palace,
    safe_resolve,
)
from locus.mcp import server as mcp_server


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def palace(tmp_path: Path) -> Path:
    """A minimal valid palace tree."""
    (tmp_path / "INDEX.md").write_text("# Index\n\n| Room | Path |\n|---|---|\n| networking | global/networking |\n")
    (tmp_path / "global").mkdir()
    (tmp_path / "global" / "networking").mkdir()
    (tmp_path / "global" / "networking" / "networking.md").write_text(
        "# Networking\n\nWireGuard is a fast VPN.\n"
    )
    (tmp_path / "global" / "networking" / "sessions").mkdir()
    (tmp_path / "global" / "networking" / "sessions" / "2026-03-01.md").write_text("## Session\n")
    (tmp_path / "_metrics").mkdir()
    (tmp_path / "_metrics" / "2026-03-01T120000Z.json").write_text(
        json.dumps({"schema_version": "1", "task": "test", "palace_path": str(tmp_path)})
    )
    return tmp_path


@pytest.fixture(autouse=True)
def inject_palace(palace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the module-level server at the test palace and keep the search index in tmp."""
    mcp_server._palace_root = palace
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))


# ---------------------------------------------------------------------------
# palace.py — find_palace
# ---------------------------------------------------------------------------

class TestFindPalace:
    def test_explicit_path(self, tmp_path: Path) -> None:
        result = find_palace(str(tmp_path))
        assert result == tmp_path.resolve()

    def test_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LOCUS_PALACE", str(tmp_path))
        result = find_palace()
        assert result == tmp_path.resolve()

    def test_cwd_locus(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        locus_dir = tmp_path / ".locus"
        locus_dir.mkdir()
        monkeypatch.chdir(tmp_path)
        result = find_palace()
        assert result == locus_dir.resolve()

    def test_missing_path_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="does not exist"):
            find_palace(str(tmp_path / "nonexistent"))

    def test_no_palace_bootstraps_home_locus(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("LOCUS_PALACE", raising=False)
        # Mock home so ~/.locus doesn't exist yet.
        monkeypatch.setenv("HOME", str(tmp_path))
        result = find_palace()
        assert result == (tmp_path / ".locus").resolve()
        assert (tmp_path / ".locus" / "INDEX.md").exists()
        assert (tmp_path / ".locus" / "global").is_dir()
        assert (tmp_path / ".locus" / "projects").is_dir()

    # Explicit roots used to skip bootstrapping entirely (#52).

    def test_explicit_empty_dir_gets_full_skeleton(self, tmp_path: Path) -> None:
        empty = tmp_path / "palace"
        empty.mkdir()
        assert find_palace(str(empty)) == empty.resolve()
        assert (empty / "INDEX.md").is_file()
        assert "# Memory Palace" in (empty / "INDEX.md").read_text()
        assert (empty / "global").is_dir()
        assert (empty / "projects").is_dir()

    def test_env_var_empty_dir_gets_skeleton(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        empty = tmp_path / "palace"
        empty.mkdir()
        monkeypatch.setenv("LOCUS_PALACE", str(empty))
        assert find_palace() == empty.resolve()
        assert (empty / "INDEX.md").is_file()

    def test_cwd_locus_empty_dir_gets_skeleton(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        locus_dir = tmp_path / ".locus"
        locus_dir.mkdir()
        monkeypatch.delenv("LOCUS_PALACE", raising=False)
        monkeypatch.chdir(tmp_path)
        find_palace()
        assert (locus_dir / "INDEX.md").is_file()

    def test_explicit_nonempty_dir_gets_index_only(self, tmp_path: Path) -> None:
        root = tmp_path / "palace"
        root.mkdir()
        (root / "notes.md").write_text("# Notes\n")
        find_palace(str(root))
        assert (root / "INDEX.md").is_file()
        # An existing layout is respected: no skeleton directories added.
        assert not (root / "global").exists()
        assert not (root / "projects").exists()
        assert (root / "notes.md").read_text() == "# Notes\n"

    def test_explicit_dir_with_index_is_untouched(self, tmp_path: Path) -> None:
        root = tmp_path / "palace"
        root.mkdir()
        (root / "INDEX.md").write_text("# Mine\n")
        find_palace(str(root))
        assert (root / "INDEX.md").read_text() == "# Mine\n"
        assert not (root / "global").exists()

    @pytest.mark.skipif(
        not hasattr(os, "geteuid") or os.geteuid() == 0,
        reason="root ignores directory permissions",
    )
    def test_explicit_readonly_dir_does_not_fail_startup(self, tmp_path: Path) -> None:
        root = tmp_path / "palace"
        root.mkdir()
        root.chmod(0o500)
        try:
            assert find_palace(str(root)) == root.resolve()
            assert not (root / "INDEX.md").exists()
        finally:
            root.chmod(0o700)

    @pytest.mark.skipif(
        not hasattr(os, "geteuid") or os.geteuid() == 0,
        reason="root ignores directory permissions",
    )
    def test_readonly_non_empty_dir_does_not_fail_startup(self, tmp_path: Path) -> None:
        # The empty-directory case fails at the first mkdir inside
        # _bootstrap_palace. A non-empty read-only root (a mounted checkout,
        # the case the docstring describes) gets all the way to the
        # _ensure_index write, which is the guard that actually matters.
        root = tmp_path / "palace"
        root.mkdir()
        (root / "notes.md").write_text("# Notes\n")
        root.chmod(0o500)
        try:
            assert find_palace(str(root)) == root.resolve()
            assert not (root / "INDEX.md").exists()
        finally:
            root.chmod(0o700)

    def test_signed_palace_is_not_given_an_index(self, tmp_path: Path) -> None:
        # An unsigned INDEX.md dropped into a signed palace turns a clean
        # locus-security verify-all into a failure, and with verify_on_read on
        # the server would refuse to serve the file it just wrote.
        root = tmp_path / "palace"
        (root / "global" / ".sig").mkdir(parents=True)
        (root / "global" / "notes.md").write_text("# Notes\n")
        (root / "global" / ".sig" / "notes.md.sig").write_text("protocol: locus-sig-v1\n")
        assert find_palace(str(root)) == root.resolve()
        assert not (root / "INDEX.md").exists()

    def test_auto_memory_dir_is_not_bootstrapped(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # The bridge directory belongs to Claude Code and already has MEMORY.md
        # as its entry point; Locus must not write an INDEX.md into it.
        fake_home = tmp_path / "home"
        fake_cwd = tmp_path / "projects" / "myrepo"
        fake_cwd.mkdir(parents=True)
        auto_mem = fake_home / ".claude" / "projects" / _slug_from_path(fake_cwd) / "memory"
        auto_mem.mkdir(parents=True)
        (auto_mem / "MEMORY.md").write_text("# Memory\n")
        monkeypatch.setenv("HOME", str(fake_home))
        monkeypatch.delenv("LOCUS_PALACE", raising=False)
        monkeypatch.chdir(fake_cwd)
        assert find_palace() == auto_mem.resolve()
        assert not (auto_mem / "INDEX.md").exists()


# ---------------------------------------------------------------------------
# palace.py — safe_resolve
# ---------------------------------------------------------------------------

class TestSafeResolve:
    def test_valid_relative_path(self, palace: Path) -> None:
        result = safe_resolve(palace, "global/networking/networking.md")
        assert result == palace / "global" / "networking" / "networking.md"

    def test_strips_leading_slash(self, palace: Path) -> None:
        result = safe_resolve(palace, "/global/networking/networking.md")
        assert result == palace / "global" / "networking" / "networking.md"

    def test_traversal_rejected(self, palace: Path) -> None:
        with pytest.raises(ValueError, match="escapes the palace root"):
            safe_resolve(palace, "../../etc/passwd")

    def test_root_itself_ok(self, palace: Path) -> None:
        result = safe_resolve(palace, "")
        assert result == palace


# ---------------------------------------------------------------------------
# palace.py — assert_writable
# ---------------------------------------------------------------------------

class TestAssertWritable:
    def test_metrics_blocked(self, palace: Path) -> None:
        target = palace / "_metrics" / "something.json"
        with pytest.raises(ValueError, match="_metrics"):
            assert_writable(palace, target)

    def test_sessions_blocked(self, palace: Path) -> None:
        target = palace / "global" / "networking" / "sessions" / "2026-03-01.md"
        # sessions/ is nested under a room — still blocked
        with pytest.raises(ValueError, match="sessions"):
            assert_writable(palace, target)

    def test_valid_md_allowed(self, palace: Path) -> None:
        target = palace / "global" / "networking" / "networking.md"
        assert_writable(palace, target)  # should not raise

    def test_binary_extension_blocked(self, palace: Path) -> None:
        target = palace / "global" / "networking" / "data.bin"
        with pytest.raises(ValueError, match="Extension"):
            assert_writable(palace, target)

    def test_yaml_allowed(self, palace: Path) -> None:
        target = palace / "global" / "config.yaml"
        assert_writable(palace, target)  # should not raise


# ---------------------------------------------------------------------------
# memory_list
# ---------------------------------------------------------------------------

class TestMemoryList:
    def test_no_path_returns_index(self) -> None:
        result = mcp_server.memory_list()
        assert "Index" in result
        assert "networking" in result

    def test_empty_string_returns_index(self) -> None:
        result = mcp_server.memory_list("")
        assert "Index" in result

    def test_room_path_lists_files(self) -> None:
        result = mcp_server.memory_list("global/networking")
        assert "networking.md" in result

    def test_missing_path_returns_message(self) -> None:
        result = mcp_server.memory_list("does/not/exist")
        assert "not found" in result.lower()

    def test_file_path_returns_contents(self) -> None:
        result = mcp_server.memory_list("global/networking/networking.md")
        assert "WireGuard" in result

    def test_traversal_raises(self) -> None:
        with pytest.raises(ValueError, match="escapes"):
            mcp_server.memory_list("../../etc")


# ---------------------------------------------------------------------------
# memory_read
# ---------------------------------------------------------------------------

class TestMemoryRead:
    def test_reads_existing_file(self) -> None:
        result = mcp_server.memory_read("global/networking/networking.md")
        assert "WireGuard" in result

    def test_missing_file_returns_message(self) -> None:
        result = mcp_server.memory_read("does/not/exist.md")
        assert "not found" in result.lower()

    def test_directory_returns_hint(self) -> None:
        result = mcp_server.memory_read("global/networking")
        assert "directory" in result.lower()

    def test_reads_index(self) -> None:
        result = mcp_server.memory_read("INDEX.md")
        assert "Index" in result

    def test_traversal_raises(self) -> None:
        with pytest.raises(ValueError, match="escapes"):
            mcp_server.memory_read("../../etc/passwd")

    def test_read_oversized_file_returns_error(self, palace: Path) -> None:
        big = palace / "global" / "networking" / "big.md"
        big.write_bytes(b"x" * (mcp_server._MAX_READ_BYTES + 1))
        result = mcp_server.memory_read("global/networking/big.md")
        assert "too large" in result.lower()


# ---------------------------------------------------------------------------
# memory_write
# ---------------------------------------------------------------------------

class TestMemoryWrite:
    def test_writes_new_file(self, palace: Path) -> None:
        result = mcp_server.memory_write("global/networking/notes.md", "# Notes\n\nHello.\n")
        assert "Written" in result
        written = (palace / "global" / "networking" / "notes.md").read_text()
        assert "Hello." in written

    def test_creates_parent_dirs(self, palace: Path) -> None:
        mcp_server.memory_write("global/new-room/new-room.md", "# New Room\n")
        assert (palace / "global" / "new-room" / "new-room.md").exists()

    def test_overwrites_existing(self, palace: Path) -> None:
        mcp_server.memory_write("global/networking/networking.md", "# Updated\n")
        content = (palace / "global" / "networking" / "networking.md").read_text()
        assert "Updated" in content
        assert "WireGuard" not in content

    def test_metrics_blocked(self) -> None:
        with pytest.raises(ValueError, match="_metrics"):
            mcp_server.memory_write("_metrics/foo.json", "{}")

    def test_binary_extension_blocked(self) -> None:
        with pytest.raises(ValueError, match="Extension"):
            mcp_server.memory_write("global/data.exe", "bad")

    def test_traversal_blocked(self) -> None:
        with pytest.raises(ValueError, match="escapes"):
            mcp_server.memory_write("../../evil.md", "evil")

    def test_line_count_in_result(self) -> None:
        result = mcp_server.memory_write("global/test.md", "line1\nline2\nline3\n")
        assert "3" in result

    def test_sessions_blocked(self) -> None:
        with pytest.raises(ValueError, match="sessions"):
            mcp_server.memory_write(
                "global/networking/sessions/2026-03-02.md", "## Session\n"
            )

    def test_write_oversized_content_blocked(self) -> None:
        oversized = "x" * (mcp_server._MAX_WRITE_BYTES + 1)
        with pytest.raises(ValueError, match="too large"):
            mcp_server.memory_write("global/big.md", oversized)


# ---------------------------------------------------------------------------
# memory_search
# ---------------------------------------------------------------------------

class TestMemorySearch:
    def test_finds_pattern(self) -> None:
        result = mcp_server.memory_search("WireGuard")
        assert "networking.md" in result
        assert "WireGuard" in result

    def test_no_match_returns_message(self) -> None:
        result = mcp_server.memory_search("xyzzy_not_found")
        assert "No matches" in result

    def test_scoped_to_path(self) -> None:
        result = mcp_server.memory_search("Index", path="global/networking")
        # INDEX.md is at root, not inside networking/ — should not appear
        assert "INDEX.md" not in result

    def test_invalid_path_returns_message(self) -> None:
        result = mcp_server.memory_search("anything", path="no/such/path")
        assert "not found" in result.lower()

    def test_traversal_in_path_raises(self) -> None:
        with pytest.raises(ValueError, match="escapes"):
            mcp_server.memory_search("anything", path="../../etc")

    def test_case_insensitive_python_fallback(self, palace: Path) -> None:
        result = mcp_server._search_python("wireguard", palace, palace)
        assert "networking.md" in result

    def test_rg_output_uses_relative_paths(self, palace: Path) -> None:
        # rg JSON formatter should strip the palace root from paths
        result = mcp_server.memory_search("WireGuard")
        assert str(palace) not in result
        assert "networking.md" in result

    def test_python_query_treated_as_literal_not_regex(self, palace: Path) -> None:
        # Queries are escaped before compiling so regex-special chars are treated as literals.
        # "[invalid" would raise re.error if compiled raw; with escaping it is a valid literal.
        result = mcp_server._search_python("[invalid", palace, palace)
        assert "Invalid search pattern" not in result
        assert "No matches" in result

    def test_python_query_too_long_returns_error(self, palace: Path) -> None:
        result = mcp_server._search_python("x" * 201, palace, palace)
        assert "Query too long" in result


class TestMemorySearchRanked:
    """memory_search is backed by the locus.recall FTS5 index (#50)."""

    def test_title_match_ranks_first(self, palace: Path) -> None:
        (palace / "global" / "networking" / "wireguard-setup.md").write_text(
            "# WireGuard setup\n\nPeer config steps.\n"
        )
        result = mcp_server.memory_search("WireGuard")
        lines = result.splitlines()
        assert lines[0].startswith("1. global/networking/wireguard-setup.md")
        assert lines[2].startswith("2. global/networking/networking.md")

    def test_hit_shows_tier_title_and_snippet(self) -> None:
        result = mcp_server.memory_search("WireGuard")
        assert "[unverified]" in result
        assert "Networking" in result
        assert "WireGuard is a fast VPN." in result

    def test_stale_flag_from_frontmatter(self, palace: Path) -> None:
        (palace / "global" / "old-vpn.md").write_text(
            "---\ntitle: Old VPN\nstatus: deprecated\nverified:\n  - by: human:me\n---\n"
            "# Old VPN\n\nOpenVPN was replaced by WireGuard.\n"
        )
        result = mcp_server.memory_search("OpenVPN")
        assert "global/old-vpn.md  [human-reviewed, STALE]" in result

    def test_journal_files_are_included(self, palace: Path) -> None:
        (palace / "global" / "journal.md").write_text(
            "---\ntype: Journal\n---\n# Journal\n\nTried the quantum tunnel today.\n"
        )
        assert "global/journal.md" in mcp_server.memory_search("quantum tunnel")

    def test_scoped_to_single_file(self) -> None:
        result = mcp_server.memory_search("WireGuard", path="global/networking/networking.md")
        assert "networking.md" in result
        result = mcp_server.memory_search("WireGuard", path="INDEX.md")
        assert "No matches" in result

    def test_query_of_only_stopwords_is_no_match(self) -> None:
        assert "No matches" in mcp_server.memory_search("the and of")

    def test_query_too_long(self) -> None:
        assert "Query too long" in mcp_server.memory_search("x" * 201)

    def test_write_is_visible_to_next_search(self) -> None:
        mcp_server.memory_write("global/fresh.md", "# Fresh\n\nA brand new ostrich fact.\n")
        assert "global/fresh.md" in mcp_server.memory_search("ostrich")

    def test_fallback_when_fts5_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(mcp_server, "fts5_available", lambda: False)
        result = mcp_server.memory_search("WireGuard")
        # rg / Python fallback: grep-style ``path:line:text`` lines, not the ranked list.
        assert "networking.md:" in result
        assert not result.startswith("1. ")


# ---------------------------------------------------------------------------
# CLI — SSE allowed_hosts construction
# ---------------------------------------------------------------------------

class TestSseAllowedHosts:
    """Verify LOCUS_ALLOWED_HOSTS is merged with loopback defaults."""

    def _build_allowed_hosts(self, env_value: str) -> list[str]:
        """Replicate the allowed_hosts logic from cli() without starting a server."""
        import os
        default = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
        extra = [h.strip() for h in env_value.split(",") if h.strip()]
        return default + extra

    def test_loopback_always_present_when_env_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("LOCUS_ALLOWED_HOSTS", raising=False)
        hosts = self._build_allowed_hosts("")
        assert "127.0.0.1:*" in hosts
        assert "localhost:*" in hosts
        assert "[::1]:*" in hosts

    def test_extra_hosts_appended(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LOCUS_ALLOWED_HOSTS", "myhost.ts.net,svc.cluster.local:*")
        hosts = self._build_allowed_hosts("myhost.ts.net,svc.cluster.local:*")
        assert "myhost.ts.net" in hosts
        assert "svc.cluster.local:*" in hosts
        # loopback still present
        assert "127.0.0.1:*" in hosts

    def test_whitespace_and_empty_entries_stripped(self) -> None:
        hosts = self._build_allowed_hosts(" host-a , , host-b ")
        assert "host-a" in hosts
        assert "host-b" in hosts
        # empty string not included
        assert "" not in hosts


# ---------------------------------------------------------------------------
# palace.py — auto-memory bridge
# ---------------------------------------------------------------------------

class TestFindAutoMemory:
    def test_slug_derivation(self) -> None:
        slug = _slug_from_path(Path("/home/user/proj"))
        assert slug == "-home-user-proj"

    def test_slug_derivation_nested(self) -> None:
        slug = _slug_from_path(Path("/home/alice/projects/locus"))
        assert slug == "-home-alice-projects-locus"

    def test_auto_memory_found(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_home = tmp_path / "home"
        fake_cwd = tmp_path / "projects" / "myrepo"
        fake_cwd.mkdir(parents=True)

        slug = _slug_from_path(fake_cwd)
        auto_mem = fake_home / ".claude" / "projects" / slug / "memory"
        auto_mem.mkdir(parents=True)

        monkeypatch.setenv("HOME", str(fake_home))
        monkeypatch.delenv("LOCUS_PALACE", raising=False)
        monkeypatch.chdir(fake_cwd)

        result = find_palace()
        assert result == auto_mem.resolve()

    def test_auto_memory_missing_falls_back_to_home_locus(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        fake_cwd = tmp_path / "projects" / "myrepo"
        fake_cwd.mkdir(parents=True)

        # No auto-memory directory created → should fall back to ~/.locus bootstrap
        monkeypatch.setenv("HOME", str(fake_home))
        monkeypatch.delenv("LOCUS_PALACE", raising=False)
        monkeypatch.chdir(fake_cwd)

        result = find_palace()
        assert result == (fake_home / ".locus").resolve()

    def test_auto_memory_env_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # LOCUS_PALACE takes priority — auto-memory should be skipped
        palace_dir = tmp_path / "my-palace"
        palace_dir.mkdir()

        monkeypatch.setenv("LOCUS_PALACE", str(palace_dir))
        result = find_palace()
        assert result == palace_dir.resolve()

    def test_find_auto_memory_returns_none_when_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        result = find_auto_memory(cwd=tmp_path / "some" / "project")
        assert result is None

    def test_cwd_locus_precedes_auto_memory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_home = tmp_path / "home"
        fake_cwd = tmp_path / "projects" / "myrepo"
        fake_cwd.mkdir(parents=True)

        # .locus/ in CWD takes priority over auto-memory
        cwd_locus = fake_cwd / ".locus"
        cwd_locus.mkdir()

        # Also create the auto-memory directory — should NOT be selected
        slug = _slug_from_path(fake_cwd)
        auto_mem = fake_home / ".claude" / "projects" / slug / "memory"
        auto_mem.mkdir(parents=True)

        monkeypatch.setenv("HOME", str(fake_home))
        monkeypatch.delenv("LOCUS_PALACE", raising=False)
        monkeypatch.chdir(fake_cwd)

        result = find_palace()
        assert result == cwd_locus.resolve()


# ---------------------------------------------------------------------------
# memory_batch
# ---------------------------------------------------------------------------

class TestMemoryBatch:
    def test_reads_multiple_files(self) -> None:
        result = mcp_server.memory_batch(["INDEX.md", "global/networking/networking.md"])
        assert "## INDEX.md" in result
        assert "## global/networking/networking.md" in result
        assert "Index" in result
        assert "WireGuard" in result

    def test_sections_separated_by_divider(self) -> None:
        result = mcp_server.memory_batch(["INDEX.md", "global/networking/networking.md"])
        assert "---" in result

    def test_missing_file_inline(self) -> None:
        result = mcp_server.memory_batch(["INDEX.md", "does/not/exist.md"])
        assert "Index" in result
        assert "not found" in result.lower()

    def test_directory_inline(self) -> None:
        result = mcp_server.memory_batch(["INDEX.md", "global/networking"])
        assert "Index" in result
        assert "directory" in result.lower()

    def test_traversal_inline(self) -> None:
        result = mcp_server.memory_batch(["../../etc/passwd"])
        assert "escapes" in result.lower() or "path error" in result.lower()

    def test_limit_enforced(self) -> None:
        paths = [f"file{i}.md" for i in range(21)]
        with pytest.raises(ValueError, match="20"):
            mcp_server.memory_batch(paths)

    def test_empty_list_returns_empty_string(self) -> None:
        result = mcp_server.memory_batch([])
        assert result == ""

    def test_newline_in_path_sanitized_in_header(self) -> None:
        # Embedded newlines in a path must not produce a standalone fake section header.
        # After sanitization "\n" → " ", the output contains a single line:
        #   "## INDEX.md  ## INJECTED"
        # — not a standalone "## INJECTED" on its own line.
        result = mcp_server.memory_batch(["INDEX.md\n\n## INJECTED"])
        lines = result.splitlines()
        standalone_headers = [l for l in lines if l.startswith("## ")]
        # Exactly one section header — not two
        assert len(standalone_headers) == 1
        # Newlines collapsed to spaces — whole thing on a single header line
        assert standalone_headers[0] == "## INDEX.md  ## INJECTED"
