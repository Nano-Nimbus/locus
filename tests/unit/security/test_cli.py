"""Tests for the locus-security CLI (init-keys, sign-all, verify-all, rotate-keys)."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from locus.security.keys import load_keystore, unique_key_id
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


class TestExpiresDays:
    def test_negative_is_rejected(self, palace: Path, capsys: pytest.CaptureFixture) -> None:
        # -5 used to fall through the "> 0" guard and silently mean "never".
        with pytest.raises(SystemExit) as exc:
            main(["init-keys", "--palace", str(palace), "--expires-days", "-5"])
        assert exc.value.code == 2
        assert "must be 0" in capsys.readouterr().err
        assert not (palace / ".security" / "keys" / "active.pem").exists()

    def test_positive_sets_an_expiry(self, palace: Path) -> None:
        assert main(["init-keys", "--palace", str(palace), "--expires-days", "30"]) == 0
        assert _keystore(palace).active.expires_at is not None


class TestUniqueKeyId:
    def test_suffixes_only_on_collision(self) -> None:
        assert unique_key_id("locus-2026-03-01", set()) == "locus-2026-03-01"
        taken = {"locus-2026-03-01", "locus-2026-03-01-2"}
        assert unique_key_id("locus-2026-03-01", taken) == "locus-2026-03-01-3"

    def test_init_keys_rejects_a_reused_explicit_id(
        self, palace: Path, capsys: pytest.CaptureFixture
    ) -> None:
        main(["init-keys", "--palace", str(palace), "--key-id", "k1"])
        main(["rotate-keys", "--palace", str(palace)])
        assert main(["init-keys", "--palace", str(palace), "--key-id", "k1", "--force"]) == 1
        assert "already used by another key" in capsys.readouterr().err


class TestRotateTwiceInOneDay:
    def test_both_old_keys_still_verify(self, palace: Path) -> None:
        """Two rotations on one day must not collide on the default key id.

        Both rotations default to ``locus-YYYY-MM-DD``: the second archive
        used to overwrite the first, and ``find_by_id`` resolved the shared id
        to the active key, so everything signed with either retired key failed.
        """
        assert main(["init-keys", "--palace", str(palace)]) == 0
        assert main(["sign-all", "--palace", str(palace)]) == 0
        first_id = _keystore(palace).active.key_id

        assert main(["rotate-keys", "--palace", str(palace)]) == 0
        second_id = _keystore(palace).active.key_id
        (palace / "after-first.md").write_text("# after first rotation\n")
        assert main(["sign-all", "--palace", str(palace)]) == 0

        assert main(["rotate-keys", "--palace", str(palace)]) == 0
        third_id = _keystore(palace).active.key_id
        (palace / "after-second.md").write_text("# after second rotation\n")
        assert main(["sign-all", "--palace", str(palace)]) == 0

        assert len({first_id, second_id, third_id}) == 3
        store = palace / ".security" / "keys"
        assert (store / "retired" / f"{first_id}.pub").exists()
        assert (store / "retired" / f"{second_id}.pub").exists()

        keystore = _keystore(palace)
        assert {kp.key_id for kp in keystore.retired} == {first_id, second_id}
        # Signatures from every generation still verify.
        assert main(["verify-all", "--palace", str(palace)]) == 0


class TestPassphraseErrors:
    def test_missing_passphrase_reports_cleanly(
        self, palace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.setenv("LOCUS_SIGNING_PASSPHRASE", "correct horse")
        assert main(["init-keys", "--palace", str(palace)]) == 0
        monkeypatch.delenv("LOCUS_SIGNING_PASSPHRASE")
        # Used to escape as an uncaught TypeError with a traceback.
        assert main(["sign-all", "--palace", str(palace)]) == 1
        err = capsys.readouterr().err
        assert "LOCUS_SIGNING_PASSPHRASE is not set" in err
        assert "correct horse" not in err

    def test_wrong_passphrase_reports_cleanly(
        self, palace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.setenv("LOCUS_SIGNING_PASSPHRASE", "correct horse")
        main(["init-keys", "--palace", str(palace)])
        monkeypatch.setenv("LOCUS_SIGNING_PASSPHRASE", "battery staple")
        assert main(["sign-all", "--palace", str(palace)]) == 1
        err = capsys.readouterr().err
        assert "does not match" in err
        assert "battery staple" not in err

    def test_passphrase_set_on_a_plaintext_key(
        self, palace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        main(["init-keys", "--palace", str(palace)])
        monkeypatch.setenv("LOCUS_SIGNING_PASSPHRASE", "unexpected")
        assert main(["sign-all", "--palace", str(palace)]) == 1
        assert "is not encrypted" in capsys.readouterr().err


class TestUnreadableFiles:
    def test_non_utf8_file_is_named_and_skipped(
        self, palace: Path, capsys: pytest.CaptureFixture
    ) -> None:
        main(["init-keys", "--palace", str(palace)])
        (palace / "binary.md").write_bytes(b"\xff\xfe not text\n")
        assert main(["sign-all", "--palace", str(palace)]) == 1
        captured = capsys.readouterr()
        assert "skipped binary.md: not valid UTF-8" in captured.err
        # Every other file is still signed: one bad file used to abort the run.
        assert "Signed 3 files" in captured.out
        assert (palace / "INDEX.md").parent.joinpath(".sig", "INDEX.md.sig").exists()
        assert not (palace / ".sig" / "binary.md.sig").exists()


class TestSymlinkEscape:
    @pytest.fixture()
    def outside_file(self, tmp_path_factory: pytest.TempPathFactory) -> Path:
        outside = tmp_path_factory.mktemp("outside")
        target = outside / "not-ours.md"
        target.write_text("# content the palace does not own\n")
        return target

    def test_escaping_symlink_is_never_signed(
        self, palace: Path, outside_file: Path, capsys: pytest.CaptureFixture
    ) -> None:
        main(["init-keys", "--palace", str(palace)])
        (palace / "smuggled.md").symlink_to(outside_file)
        assert main(["sign-all", "--palace", str(palace)]) == 1
        assert "smuggled.md: symlink resolves outside the palace" in capsys.readouterr().err
        assert not (palace / ".sig" / "smuggled.md.sig").exists()
        assert not (outside_file.parent / ".sig").exists()

    def test_verify_all_fails_on_an_escaping_symlink(
        self, palace: Path, outside_file: Path, capsys: pytest.CaptureFixture
    ) -> None:
        main(["init-keys", "--palace", str(palace)])
        main(["sign-all", "--palace", str(palace)])
        (palace / "smuggled.md").symlink_to(outside_file)
        assert main(["verify-all", "--palace", str(palace)]) == 1
        assert "FAIL smuggled.md" in capsys.readouterr().err

    def test_symlink_inside_the_palace_is_still_signed(self, palace: Path) -> None:
        main(["init-keys", "--palace", str(palace)])
        (palace / "alias.md").symlink_to(palace / "INDEX.md")
        assert main(["sign-all", "--palace", str(palace)]) == 0
        assert (palace / ".sig" / "alias.md.sig").exists()

    def test_broken_symlink_is_skipped_silently(self, palace: Path) -> None:
        main(["init-keys", "--palace", str(palace)])
        (palace / "dangling.md").symlink_to(palace / "gone.md")
        assert main(["sign-all", "--palace", str(palace)]) == 0


class TestVerifyAllEmptyPalace:
    def test_no_signable_files_is_not_a_silent_pass(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        (tmp_path / "locus-security.yaml").write_text(_CONFIG)
        main(["init-keys", "--palace", str(tmp_path)])
        assert main(["verify-all", "--palace", str(tmp_path)]) == 1
        assert "no signable files found" in capsys.readouterr().err


class TestKeyPermissions:
    def test_key_store_is_user_only(self, palace: Path) -> None:
        main(["init-keys", "--palace", str(palace)])
        store = palace / ".security" / "keys"
        assert stat.S_IMODE(store.stat().st_mode) == 0o700
        for name in ("active.pem", "active.pub", "active.json"):
            assert stat.S_IMODE((store / name).stat().st_mode) == 0o600

    def test_retired_dir_is_user_only_after_rotation(self, palace: Path) -> None:
        main(["init-keys", "--palace", str(palace)])
        main(["rotate-keys", "--palace", str(palace)])
        retired = palace / ".security" / "keys" / "retired"
        assert stat.S_IMODE(retired.stat().st_mode) == 0o700
