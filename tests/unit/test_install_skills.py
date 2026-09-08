"""Tests for scripts/install-skills.sh."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[2]
SCRIPT = REPO_ROOT / "scripts" / "install-skills.sh"
SKILLS_SRC = REPO_ROOT / "skills" / "claude"


@pytest.fixture()
def dst(tmp_path: Path) -> Path:
    return tmp_path / "skills"


def _run_script(dst: Path, extra_args: list[str] | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["CLAUDE_SKILLS_DIR"] = str(dst)
    return subprocess.run(
        ["bash", str(SCRIPT)] + (extra_args or []),
        capture_output=True,
        text=True,
        env=env,
    )


class TestInstallSkills:
    def test_script_exists_and_is_executable(self):
        assert SCRIPT.exists(), "scripts/install-skills.sh not found"

    def test_dry_run_prints_plan(self, dst: Path):
        result = _run_script(dst, ["--dry-run"])
        assert result.returncode == 0
        assert "[dry-run]" in result.stdout
        # Nothing should have been written
        assert not dst.exists() or not any(dst.iterdir())

    def test_dry_run_lists_all_skills(self, dst: Path):
        result = _run_script(dst, ["--dry-run"])
        expected_skills = [d.name for d in SKILLS_SRC.iterdir() if d.is_dir()]
        for skill in expected_skills:
            assert skill in result.stdout, f"skill '{skill}' missing from dry-run output"

    def test_install_copies_all_skills(self, dst: Path):
        result = _run_script(dst)
        assert result.returncode == 0
        expected = {d.name for d in SKILLS_SRC.iterdir() if d.is_dir()}
        installed = {p.name for p in dst.iterdir() if p.is_dir()}
        assert expected == installed

    def test_each_skill_has_skill_md(self, dst: Path):
        _run_script(dst)
        for skill_dir in dst.iterdir():
            skill_md = skill_dir / "SKILL.md"
            assert skill_md.exists(), f"{skill_dir.name}/SKILL.md not found after install"

    def test_idempotent_second_run(self, dst: Path):
        """Running install-skills twice should not error or duplicate files."""
        result1 = _run_script(dst)
        result2 = _run_script(dst)
        assert result1.returncode == 0
        assert result2.returncode == 0
        installed = list(dst.iterdir())
        # No duplicates — count should be the same as number of source skills
        expected_count = len([d for d in SKILLS_SRC.iterdir() if d.is_dir()])
        assert len(installed) == expected_count

    def test_reports_installed_count(self, dst: Path):
        result = _run_script(dst)
        expected_count = len([d for d in SKILLS_SRC.iterdir() if d.is_dir()])
        assert f"{expected_count} skill(s) installed" in result.stdout

    def test_custom_destination_via_env(self, tmp_path: Path):
        custom_dst = tmp_path / "custom_skills_dir"
        result = _run_script(custom_dst)
        assert result.returncode == 0
        assert custom_dst.exists()
        assert any(custom_dst.iterdir())


@pytest.fixture()
def fake_repo(tmp_path: Path) -> Path:
    """An isolated copy of the repository layout for the destructive cases.

    The guard tests below point the installer at directories it would
    ``rm -rf`` if the guard failed, so they must never run against the real
    checkout. ``locus/`` here stands in for the Python package that sits
    beside ``skills/`` and shares a name with the ``locus`` skill.
    """
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(SCRIPT, repo / "scripts" / SCRIPT.name)
    for name in ("locus", "locus-security"):
        skill = repo / "skills" / "claude" / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
    package = repo / "locus"
    package.mkdir()
    (package / "scaffold.py").write_text("# the real package\n", encoding="utf-8")
    return repo


def _run_guard(
    script: Path, dst: str | None, home: Path, cwd: Path, extra_args: list[str] | None = None
) -> subprocess.CompletedProcess:
    """Run the installer with HOME redirected to a throwaway directory.

    The default destination is ``$HOME/.claude/skills``, which on a developer
    machine is often a symlink into a tracked dotfiles repository, so no test
    may ever be allowed to reach the real one.
    """
    env = os.environ.copy()
    env["HOME"] = str(home)
    if dst is None:
        env.pop("CLAUDE_SKILLS_DIR", None)
    else:
        env["CLAUDE_SKILLS_DIR"] = dst
    return subprocess.run(
        ["bash", str(script)] + (extra_args or []),
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd),
        check=False,
    )


class TestDestinationGuard:
    """The installer ``rm -rf``s each destination skill directory before
    copying, so a wrong destination destroys data. The guard has been widened
    three separate times against real holes and nothing pinned any of them;
    these cases pin every class it is meant to refuse.
    """

    @pytest.fixture()
    def home(self, tmp_path: Path) -> Path:
        path = tmp_path / "home"
        path.mkdir()
        return path

    @pytest.fixture()
    def cwd(self, tmp_path: Path) -> Path:
        path = tmp_path / "cwd"
        path.mkdir()
        return path

    @pytest.mark.parametrize(
        "value",
        ["", " ", "\t", "relative/skills", ".", "./skills", "../skills"],
        ids=["empty", "space", "tab", "relative", "dot", "dot-slash", "dot-dot"],
    )
    def test_refuses_a_blank_or_relative_destination(
        self, value: str, fake_repo: Path, home: Path, cwd: Path
    ) -> None:
        """A relative destination would create stray content under whatever
        directory the script happened to be invoked from, and an empty one
        must not quietly fall back to the default: ``${VAR-default}`` rather
        than ``${VAR:-default}`` is what keeps an explicit empty value an
        error rather than a write to live user configuration.
        """
        result = _run_guard(fake_repo / "scripts" / SCRIPT.name, value, home, cwd)
        assert result.returncode == 1
        assert "must be a non-empty absolute path" in result.stderr
        assert list(cwd.iterdir()) == []
        assert not (home / ".claude").exists()

    @pytest.mark.parametrize(
        "value", ["/", "//", "///", "/tmp/.."], ids=["root", "double", "triple", "dotdot"]
    )
    def test_refuses_root_and_its_aliases(
        self, value: str, fake_repo: Path, home: Path, cwd: Path
    ) -> None:
        result = _run_guard(fake_repo / "scripts" / SCRIPT.name, value, home, cwd)
        assert result.returncode == 1
        assert "resolves to /" in result.stderr

    def test_refuses_a_symlink_to_root(
        self, fake_repo: Path, home: Path, cwd: Path, tmp_path: Path
    ) -> None:
        link = tmp_path / "rootlink"
        link.symlink_to("/", target_is_directory=True)
        result = _run_guard(fake_repo / "scripts" / SCRIPT.name, str(link), home, cwd)
        assert result.returncode == 1
        assert "resolves to /" in result.stderr

    @pytest.mark.parametrize("suffix", ["", "/locus", "/locus/../../claude"])
    def test_refuses_the_source_tree(
        self, suffix: str, fake_repo: Path, home: Path, cwd: Path
    ) -> None:
        target = f"{fake_repo / 'skills' / 'claude'}{suffix}"
        result = _run_guard(fake_repo / "scripts" / SCRIPT.name, target, home, cwd)
        assert result.returncode == 1
        assert "inside the source tree" in result.stderr

    def test_refuses_a_symlink_into_the_source_tree(
        self, fake_repo: Path, home: Path, cwd: Path, tmp_path: Path
    ) -> None:
        link = tmp_path / "srclink"
        link.symlink_to(fake_repo / "skills" / "claude", target_is_directory=True)
        result = _run_guard(fake_repo / "scripts" / SCRIPT.name, str(link), home, cwd)
        assert result.returncode == 1
        assert "inside the source tree" in result.stderr

    @pytest.mark.parametrize("via_symlink", [False, True])
    def test_refuses_a_destination_that_contains_the_source_tree(
        self, via_symlink: bool, fake_repo: Path, home: Path, cwd: Path, tmp_path: Path
    ) -> None:
        """Containment has to be checked in both directions.

        Installing to the repository root makes ``dst`` the sibling that
        shares a skill's name, so the per-skill ``rm -rf`` deletes the
        ``locus/`` package. Refusing only destinations *inside* the source
        tree let this through, and it exited 0 while doing it.
        """
        target = fake_repo
        if via_symlink:
            target = tmp_path / "ancestorlink"
            target.symlink_to(fake_repo, target_is_directory=True)
        result = _run_guard(fake_repo / "scripts" / SCRIPT.name, str(target), home, cwd)
        assert result.returncode == 1
        assert "contains the source tree" in result.stderr
        assert (fake_repo / "locus" / "scaffold.py").read_text(
            encoding="utf-8"
        ) == "# the real package\n"

    def test_a_refusal_leaves_no_directory_behind(
        self, fake_repo: Path, home: Path, cwd: Path
    ) -> None:
        """Canonicalizing requires the destination to exist, so the guard has
        to create it before it can judge it. A refusal must undo that.
        """
        stray = fake_repo / "skills" / "claude" / "stray" / "deep"
        result = _run_guard(fake_repo / "scripts" / SCRIPT.name, str(stray), home, cwd)
        assert result.returncode == 1
        assert not (fake_repo / "skills" / "claude" / "stray").exists()

    def test_a_refusal_keeps_directories_it_did_not_create(
        self, fake_repo: Path, home: Path, cwd: Path
    ) -> None:
        """Cleanup removes only what this run made.

        The obvious implementation, ``rmdir -p``, walks past the components
        this run created and takes pre-existing empty parents with it, so a
        refused destination under an existing empty directory would delete
        that directory too.
        """
        parent = fake_repo / "skills" / "claude" / "preexisting-empty"
        parent.mkdir()
        result = _run_guard(
            fake_repo / "scripts" / SCRIPT.name, str(parent / "new"), home, cwd
        )
        assert result.returncode == 1
        assert parent.is_dir(), "cleanup removed a directory it did not create"
        assert not (parent / "new").exists()

    def test_a_dry_run_never_reaches_the_filesystem(
        self, fake_repo: Path, home: Path, cwd: Path
    ) -> None:
        result = _run_guard(
            fake_repo / "scripts" / SCRIPT.name, str(fake_repo), home, cwd, ["--dry-run"]
        )
        assert result.returncode == 0
        assert (fake_repo / "locus" / "scaffold.py").exists()
