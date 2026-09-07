"""Tests for the locus-security CLI (init-keys, sign-all, verify-all, rotate-keys)."""

from __future__ import annotations

from pathlib import Path

import pytest

from locus.security.keys import load_keystore
from locus.security.main import iter_signable_files, main
from locus.security.signing import verify_file

_CONFIG = 'version: "1"\nkey_store: ".security/keys/"\n'


@pytest.fixture()
def palace(tmp_path: Path) -> Path:
    (tmp_path / "locus-security.yaml").write_text(_CONFIG)
    (tmp_path / "INDEX.md").write_text("# Index\n")
    room = tmp_path / "global" / "toolchain"
    room.mkdir(parents=True)
    (room / "toolchain.md").write_text("# Toolchain\n\nuv for Python.\n")
    (room / "sessions").mkdir()
    (room / "sessions" / "2026-03-01.md").write_text("## Session\n")
    # Never signed: tooling dot-directories and the metrics pipeline output.
    (tmp_path / ".obsidian").mkdir()
    (tmp_path / ".obsidian" / "workspace.md").write_text("editor state\n")
    (tmp_path / "_metrics").mkdir()
    (tmp_path / "_metrics" / "report.md").write_text("# Audit\n")
    return tmp_path


def _keystore(palace: Path):
    return load_keystore(palace / ".security" / "keys")


class TestIterSignableFiles:
    def test_skips_dot_dirs_and_metrics(self, palace: Path) -> None:
        rels = {str(p.relative_to(palace)) for p in iter_signable_files(palace)}
        assert rels == {
            "INDEX.md",
            "global/toolchain/toolchain.md",
            "global/toolchain/sessions/2026-03-01.md",
        }


class TestInitKeys:
    def test_creates_active_keypair(self, palace: Path, capsys: pytest.CaptureFixture) -> None:
        assert main(["init-keys", "--palace", str(palace), "--key-id", "k1"]) == 0
        store = palace / ".security" / "keys"
        assert (store / "active.pem").exists()
        assert (store / "active.pub").exists()
        assert (store / "active.json").exists()
        assert _keystore(palace).active.key_id == "k1"
        assert "Generated keypair k1" in capsys.readouterr().out

    def test_refuses_overwrite_without_force(self, palace: Path, capsys: pytest.CaptureFixture) -> None:
        assert main(["init-keys", "--palace", str(palace), "--key-id", "k1"]) == 0
        assert main(["init-keys", "--palace", str(palace), "--key-id", "k2"]) == 1
        assert "already exists" in capsys.readouterr().err
        assert _keystore(palace).active.key_id == "k1"

    def test_force_overwrites(self, palace: Path) -> None:
        main(["init-keys", "--palace", str(palace), "--key-id", "k1"])
        assert main(["init-keys", "--palace", str(palace), "--key-id", "k2", "--force"]) == 0
        assert _keystore(palace).active.key_id == "k2"

    def test_expires_days_zero_means_never(self, palace: Path) -> None:
        assert main(["init-keys", "--palace", str(palace), "--expires-days", "0"]) == 0
        assert _keystore(palace).active.expires_at is None

    def test_requires_config(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        assert main(["init-keys", "--palace", str(tmp_path)]) == 1
        assert "locus-security.yaml" in capsys.readouterr().err

    def test_missing_palace_dir_is_usage_error(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit) as exc:
            main(["init-keys", "--palace", str(tmp_path / "nope")])
        assert exc.value.code == 2


class TestSignAll:
    def test_signs_every_markdown_file(self, palace: Path, capsys: pytest.CaptureFixture) -> None:
        main(["init-keys", "--palace", str(palace)])
        assert main(["sign-all", "--palace", str(palace)]) == 0
        assert "Signed 3 files" in capsys.readouterr().out
        keystore = _keystore(palace)
        for path in iter_signable_files(palace):
            assert (path.parent / ".sig" / f"{path.name}.sig").exists()
            assert verify_file(path, palace, keystore).trusted

    def test_skipped_dirs_get_no_sidecar(self, palace: Path) -> None:
        main(["init-keys", "--palace", str(palace)])
        main(["sign-all", "--palace", str(palace)])
        assert not (palace / ".obsidian" / ".sig").exists()
        assert not (palace / "_metrics" / ".sig").exists()

    def test_without_keys_reports_error(self, palace: Path, capsys: pytest.CaptureFixture) -> None:
        assert main(["sign-all", "--palace", str(palace)]) == 1
        assert "No active keypair" in capsys.readouterr().err


class TestVerifyAll:
    def test_all_valid_exits_zero(self, palace: Path, capsys: pytest.CaptureFixture) -> None:
        main(["init-keys", "--palace", str(palace)])
        main(["sign-all", "--palace", str(palace)])
        assert main(["verify-all", "--palace", str(palace)]) == 0
        assert "3/3 files verified" in capsys.readouterr().out

    def test_tampered_file_exits_one(self, palace: Path, capsys: pytest.CaptureFixture) -> None:
        main(["init-keys", "--palace", str(palace)])
        main(["sign-all", "--palace", str(palace)])
        (palace / "INDEX.md").write_text("# Index\n\ninjected\n")
        assert main(["verify-all", "--palace", str(palace)]) == 1
        out = capsys.readouterr().out
        assert "FAIL INDEX.md" in out
        assert "2/3 files verified" in out


class TestRotateKeys:
    def test_rotation_keeps_old_signatures_valid(self, palace: Path, capsys: pytest.CaptureFixture) -> None:
        main(["init-keys", "--palace", str(palace), "--key-id", "old-key"])
        main(["sign-all", "--palace", str(palace)])
        assert main(["rotate-keys", "--palace", str(palace)]) == 0
        assert "Rotated key old-key" in capsys.readouterr().out
        store = palace / ".security" / "keys"
        assert (store / "retired" / "old-key.pub").exists()
        assert not (store / "retired" / "old-key.pem").exists()
        assert _keystore(palace).active.key_id != "old-key"
        # Files signed by the retired key still verify.
        assert main(["verify-all", "--palace", str(palace)]) == 0
