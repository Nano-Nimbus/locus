"""Tests for packaged templates, `locus init`, and what the wheel ships.

The regression these guard is issue #62: `templates/` and `example-palace/`
were referenced by running code and by the README, but the wheel contained
neither, so every documented setup step was impossible from a PyPI install.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path
from unittest import mock

import pytest
import yaml

from locus.scaffold import (
    SECURITY_CONFIG_NAME,
    ScaffoldError,
    available_templates,
    copy_example_palace,
    init_main,
    scaffold_root,
    security_config_template,
    template_path,
    write_security_config,
)

REPO_ROOT = Path(__file__).parents[2]

# Files a wheel must carry for the documented commands to work at all.
REQUIRED_WHEEL_MEMBERS = (
    "locus/_scaffold/templates/locus-security.yaml",
    "locus/_scaffold/templates/INDEX.md",
    "locus/_scaffold/templates/room/room-name.md",
    "locus/_scaffold/example-palace/INDEX.md",
)


class TestScaffoldRoot:
    def test_holds_both_data_trees(self) -> None:
        root = scaffold_root()
        assert (root / "templates").is_dir()
        assert (root / "example-palace").is_dir()

    def test_security_template_is_valid_yaml(self) -> None:
        parsed = yaml.safe_load(security_config_template().read_text(encoding="utf-8"))
        assert parsed["version"] == "1"
        assert "boundaries" in parsed

    def test_unknown_template_raises(self) -> None:
        with pytest.raises(ScaffoldError, match="No packaged template"):
            template_path("templates/does-not-exist.yaml")


class TestWheelContents:
    """The regression test proper: build the wheel and look inside it.

    Asserting from the source tree proves nothing here, because the source
    tree has the data either way. Only the built artifact can show that an
    install ships it.
    """

    def test_pyproject_force_includes_the_data(self) -> None:
        config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        force_include = config["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
        assert force_include["templates"] == "locus/_scaffold/templates"
        assert force_include["example-palace"] == "locus/_scaffold/example-palace"

    @pytest.mark.skipif(shutil.which("uv") is None, reason="uv is needed to build the wheel")
    def test_built_wheel_carries_templates_and_example_palace(self, tmp_path: Path) -> None:
        result = subprocess.run(
            ["uv", "build", "--wheel", "--out-dir", str(tmp_path)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        wheels = list(tmp_path.glob("*.whl"))
        assert len(wheels) == 1, f"expected one wheel, got {wheels}"
        with zipfile.ZipFile(wheels[0]) as archive:
            names = set(archive.namelist())
        missing = [name for name in REQUIRED_WHEEL_MEMBERS if name not in names]
        assert not missing, f"wheel is missing packaged data: {missing}"


class TestCopyExamplePalace:
    def test_writes_the_documented_structure(self, tmp_path: Path) -> None:
        palace = tmp_path / "palace"
        written, skipped = copy_example_palace(palace)
        assert skipped == []
        relatives = {str(path) for path in written}
        assert "INDEX.md" in relatives
        assert "global/toolchain/toolchain.md" in relatives
        assert "projects/my-project/my-project.md" in relatives
        # Session directories are kept even though their .gitkeep marker is not.
        assert (palace / "global" / "toolchain" / "sessions").is_dir()
        assert not (palace / "global" / "toolchain" / "sessions" / ".gitkeep").exists()

    def test_never_overwrites_without_force(self, tmp_path: Path) -> None:
        palace = tmp_path / "palace"
        palace.mkdir()
        (palace / "INDEX.md").write_text("# Mine\n", encoding="utf-8")
        written, skipped = copy_example_palace(palace)
        assert Path("INDEX.md") in skipped
        assert Path("INDEX.md") not in written
        assert (palace / "INDEX.md").read_text(encoding="utf-8") == "# Mine\n"

    def test_force_overwrites(self, tmp_path: Path) -> None:
        palace = tmp_path / "palace"
        palace.mkdir()
        (palace / "INDEX.md").write_text("# Mine\n", encoding="utf-8")
        written, skipped = copy_example_palace(palace, force=True)
        assert Path("INDEX.md") in written
        assert skipped == []
        assert (palace / "INDEX.md").read_text(encoding="utf-8") != "# Mine\n"

    def test_refuses_a_symlinked_directory_component(self, tmp_path: Path) -> None:
        """A symlinked `global/` must not route writes outside the palace.

        `Path.mkdir`/`shutil.copyfile` both follow an existing symlinked
        directory the same way they would a real one, so without a guard
        this would write into `elsewhere/` instead of `palace/global/`.
        """
        palace = tmp_path / "palace"
        palace.mkdir()
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        (palace / "global").symlink_to(elsewhere, target_is_directory=True)
        with pytest.raises(ScaffoldError, match="symlink"):
            copy_example_palace(palace)
        assert list(elsewhere.rglob("*")) == []

    def test_refuses_a_dangling_symlink_at_a_file_target(self, tmp_path: Path) -> None:
        """`Path.exists()` is False for a dangling symlink, so without a guard
        a planted one would be written through even without `force`."""
        palace = tmp_path / "palace"
        palace.mkdir()
        outside = tmp_path / "outside.md"
        (palace / "INDEX.md").symlink_to(outside)
        with pytest.raises(ScaffoldError, match="symlink"):
            copy_example_palace(palace)
        assert not outside.exists()


class TestWriteSecurityConfig:
    def test_writes_the_packaged_template(self, tmp_path: Path) -> None:
        path, written = write_security_config(tmp_path)
        assert written is True
        assert path == tmp_path / SECURITY_CONFIG_NAME
        assert path.read_text(encoding="utf-8") == security_config_template().read_text(
            encoding="utf-8"
        )

    def test_keeps_an_existing_config(self, tmp_path: Path) -> None:
        target = tmp_path / SECURITY_CONFIG_NAME
        target.write_text('version: "1"\nkey_store: "custom/"\n', encoding="utf-8")
        path, written = write_security_config(tmp_path)
        assert written is False
        assert "custom/" in path.read_text(encoding="utf-8")

    def test_refuses_a_dangling_symlink_even_without_force(self, tmp_path: Path) -> None:
        """A dangling `locus-security.yaml` symlink reads as "does not exist",
        so without a guard this would write the packaged config through it to
        wherever the symlink points, silently, with no `--force` given."""
        outside = tmp_path / "outside.yaml"
        (tmp_path / SECURITY_CONFIG_NAME).symlink_to(outside)
        with pytest.raises(ScaffoldError, match="symlink"):
            write_security_config(tmp_path)
        assert not outside.exists()


class TestInitCommand:
    def test_creates_a_palace(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        palace = tmp_path / "palace"
        assert init_main([str(palace)]) == 0
        assert (palace / "INDEX.md").is_file()
        assert "wrote  INDEX.md" in capsys.readouterr().out

    def test_security_flag_writes_the_config(self, tmp_path: Path) -> None:
        palace = tmp_path / "palace"
        assert init_main([str(palace), "--security"]) == 0
        assert (palace / SECURITY_CONFIG_NAME).is_file()

    def test_second_run_changes_nothing(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        palace = tmp_path / "palace"
        assert init_main([str(palace)]) == 0
        (palace / "INDEX.md").write_text("# Mine\n", encoding="utf-8")
        capsys.readouterr()
        assert init_main([str(palace)]) == 0
        assert "Nothing written" in capsys.readouterr().out
        assert (palace / "INDEX.md").read_text(encoding="utf-8") == "# Mine\n"

    def test_show_prints_a_packaged_template(self, capsys: pytest.CaptureFixture) -> None:
        assert init_main(["--show", "templates/room/room-name.md"]) == 0
        assert "SIZE LIMIT" in capsys.readouterr().out

    def test_show_list_names_every_template(self, capsys: pytest.CaptureFixture) -> None:
        assert init_main(["--show", "list"]) == 0
        printed = capsys.readouterr().out.split()
        assert printed == available_templates()

    def test_show_error_does_not_crash_when_scaffold_root_itself_is_missing(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """The error handler names the available templates by calling
        `available_templates()` again, which raises the same `ScaffoldError`
        when the failure was `scaffold_root()` itself. That must degrade to a
        clean exit 1, not an unhandled second traceback."""
        with mock.patch(
            "locus.scaffold.scaffold_root",
            side_effect=ScaffoldError("packaged templates are missing"),
        ):
            assert init_main(["--show", "templates/INDEX.md"]) == 1
        err = capsys.readouterr().err
        assert "packaged templates are missing" in err
        assert "Available templates:" in err

    def test_show_refuses_to_escape_the_scaffold(self, capsys: pytest.CaptureFixture) -> None:
        """--show is a template printer, not an arbitrary file reader."""
        assert init_main(["--show", "../../../etc/passwd"]) == 1
        assert "Not a packaged template name" in capsys.readouterr().err

    def test_show_does_not_create_anything(self, tmp_path: Path) -> None:
        palace = tmp_path / "palace"
        assert init_main([str(palace), "--show", "templates/INDEX.md"]) == 0
        assert not palace.exists()

    def test_dispatched_by_the_locus_entry_point(self, tmp_path: Path) -> None:
        """`locus init` must not need the Agent SDK, like recall/lint/index."""
        palace = tmp_path / "palace"
        result = subprocess.run(
            [sys.executable, "-m", "locus.cli", "init", str(palace)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert (palace / "INDEX.md").is_file()
