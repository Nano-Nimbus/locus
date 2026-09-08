"""Tests for the locus-security CLI (init-keys, sign-all, verify-all, rotate-keys)."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from locus.security.config import load_security_config
from locus.security.keys import load_keystore, unique_key_id, validate_key_id
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


class TestSignatureBindsToPath:
    def test_relocating_a_signed_file_with_its_sidecar_is_untrusted(self, palace: Path) -> None:
        """A signed file plus its sidecar must not verify at a different path.

        The signature only ever attested "some file had this hash", because
        verify_file rebuilt the payload from the sidecar's own rel_path. So
        copying a signed low-value note over INDEX.md, sidecar and all,
        verified clean and promoted attacker-chosen content into the tier
        memory_read serves first.
        """
        main(["init-keys", "--palace", str(palace)])
        main(["sign-all", "--palace", str(palace)])
        keystore = _keystore(palace)

        source = palace / "global" / "toolchain" / "toolchain.md"
        assert verify_file(source, palace, keystore).trusted

        (palace / "INDEX.md").write_bytes(source.read_bytes())
        (palace / ".sig").mkdir(exist_ok=True)
        (palace / ".sig" / "INDEX.md.sig").write_bytes(
            (source.parent / ".sig" / "toolchain.md.sig").read_bytes()
        )

        result = verify_file(palace / "INDEX.md", palace, keystore)
        assert not result.trusted
        assert "path mismatch" in result.reason
        assert main(["verify-all", "--palace", str(palace)]) == 1

    def test_a_file_still_verifies_where_it_was_signed(self, palace: Path) -> None:
        main(["init-keys", "--palace", str(palace)])
        assert main(["sign-all", "--palace", str(palace)]) == 0
        assert main(["verify-all", "--palace", str(palace)]) == 0


class TestMalformedSidecar:
    def test_scalar_sidecar_fails_one_file_not_the_run(
        self, palace: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """A sidecar that parses to a scalar used to raise AttributeError.

        That killed verify-all outright, so every file after it in sort order
        went unchecked while the command still looked like a normal failure.
        """
        main(["init-keys", "--palace", str(palace)])
        main(["sign-all", "--palace", str(palace)])
        (palace / ".sig" / "INDEX.md.sig").write_text("just a bare string\n")
        assert main(["verify-all", "--palace", str(palace)]) == 1
        out = capsys.readouterr().out
        assert "not a mapping" in out
        # The other two files were still verified.
        assert "2/3 files verified" in out

    def test_unparseable_yaml_fails_one_file(self, palace: Path, capsys: pytest.CaptureFixture) -> None:
        main(["init-keys", "--palace", str(palace)])
        main(["sign-all", "--palace", str(palace)])
        (palace / ".sig" / "INDEX.md.sig").write_text("key: [unclosed\n")
        assert main(["verify-all", "--palace", str(palace)]) == 1
        assert "2/3 files verified" in capsys.readouterr().out

    def test_non_utf8_body_fails_one_file(self, palace: Path, capsys: pytest.CaptureFixture) -> None:
        main(["init-keys", "--palace", str(palace)])
        main(["sign-all", "--palace", str(palace)])
        (palace / "INDEX.md").write_bytes(b"\xff\xfe binary\n")
        assert main(["verify-all", "--palace", str(palace)]) == 1
        out = capsys.readouterr().out
        assert "not valid UTF-8" in out
        assert "2/3 files verified" in out


class TestForceArchivesTheOldKey:
    def test_force_keeps_old_signatures_verifiable(self, palace: Path) -> None:
        """--force replaced the active key without archiving its public half.

        Every signature made with the old key then failed with "key not
        found", and the key needed to check them was gone from disk, so the
        state was unrecoverable.
        """
        main(["init-keys", "--palace", str(palace), "--key-id", "k1"])
        main(["sign-all", "--palace", str(palace)])
        assert main(["init-keys", "--palace", str(palace), "--key-id", "k2", "--force"]) == 0
        store = palace / ".security" / "keys"
        assert (store / "retired" / "k1.pub").exists()
        assert _keystore(palace).active.key_id == "k2"
        assert main(["verify-all", "--palace", str(palace)]) == 0

    def test_half_written_store_is_not_treated_as_empty(
        self, palace: Path, capsys: pytest.CaptureFixture
    ) -> None:
        main(["init-keys", "--palace", str(palace), "--key-id", "k1"])
        (palace / ".security" / "keys" / "active.pem").unlink()
        assert main(["init-keys", "--palace", str(palace), "--key-id", "k2"]) == 1
        assert "already exists" in capsys.readouterr().err


class TestTornKeystore:
    def test_mismatched_public_key_is_rejected(self, palace: Path, capsys: pytest.CaptureFixture) -> None:
        """active.pub and active.pem are separate renames, so they can diverge.

        Signing with a mismatched pair succeeded and produced signatures that
        nothing could ever verify.
        """
        store = palace / ".security" / "keys"
        main(["init-keys", "--palace", str(palace), "--key-id", "k1"])
        stale_pub = (store / "active.pub").read_bytes()
        main(["init-keys", "--palace", str(palace), "--key-id", "k2", "--force"])
        (store / "active.pub").write_bytes(stale_pub)

        assert main(["sign-all", "--palace", str(palace)]) == 1
        assert "is not the public key for" in capsys.readouterr().err


class TestKeyIdValidation:
    @pytest.mark.parametrize("bad", ["../../../escaped", "with/slash", "", "-leading", "a" * 65])
    def test_unsafe_ids_are_rejected(self, bad: str) -> None:
        with pytest.raises(ValueError):
            validate_key_id(bad)

    def test_traversing_key_id_is_a_usage_error(self, palace: Path, capsys: pytest.CaptureFixture) -> None:
        """--key-id ../../escaped used to write retired/*.pub outside the store."""
        with pytest.raises(SystemExit) as exc:
            main(["init-keys", "--palace", str(palace), "--key-id", "../../../escaped"])
        assert exc.value.code == 2
        assert "Invalid key id" in capsys.readouterr().err
        assert not (palace.parent / "escaped.pub").exists()

    def test_ordinary_ids_are_accepted(self) -> None:
        for good in ("k1", "locus-2026-03-01", "locus-2026-03-01-2", "team.signing_key"):
            assert validate_key_id(good) == good


class TestExpiryIsEnforced:
    def test_signing_with_an_expired_key_is_refused(
        self, palace: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """--expires-days was decorative: nothing ever read KeyPair.is_expired."""
        main(["init-keys", "--palace", str(palace), "--key-id", "k1"])
        meta = palace / ".security" / "keys" / "active.json"
        meta.write_text(
            json.dumps({"key_id": "k1", "created_at": "2000-01-01T00:00:00+00:00",
                        "expires_at": "2000-01-02T00:00:00+00:00"})
        )
        assert main(["sign-all", "--palace", str(palace)]) == 1
        err = capsys.readouterr().err
        assert "expired on 2000-01-02" in err
        assert not (palace / ".sig").exists()

    def test_huge_expiry_is_a_usage_error_not_an_overflow(
        self, palace: Path, capsys: pytest.CaptureFixture
    ) -> None:
        with pytest.raises(SystemExit) as exc:
            main(["init-keys", "--palace", str(palace), "--expires-days", "100000000"])
        assert exc.value.code == 2
        assert "at most" in capsys.readouterr().err


class TestInitConfig:
    """init-config replaces `cp templates/locus-security.yaml ...` (issue #62).

    The old instruction named a repository path, so it could not be followed
    from a PyPI install: there is no templates/ directory next to a wheel.
    """

    def test_writes_a_loadable_config(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        assert main(["init-config", "--palace", str(tmp_path)]) == 0
        config = load_security_config(tmp_path)
        assert config is not None
        assert config.key_store_path == (tmp_path / ".security/keys").resolve()
        assert "Wrote" in capsys.readouterr().out

    def test_keeps_an_existing_config(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        target = tmp_path / "locus-security.yaml"
        target.write_text('version: "1"\nkey_store: "custom/keys/"\n')
        assert main(["init-config", "--palace", str(tmp_path)]) == 0
        assert "custom/keys/" in target.read_text()
        assert "left unchanged" in capsys.readouterr().out

    def test_force_overwrites(self, tmp_path: Path) -> None:
        target = tmp_path / "locus-security.yaml"
        target.write_text('version: "1"\nkey_store: "custom/keys/"\n')
        assert main(["init-config", "--palace", str(tmp_path), "--force"]) == 0
        assert "custom/keys/" not in target.read_text()

    def test_init_keys_then_sign_all_works_end_to_end(self, tmp_path: Path) -> None:
        (tmp_path / "INDEX.md").write_text("# Index\n")
        assert main(["init-config", "--palace", str(tmp_path)]) == 0
        assert main(["init-keys", "--palace", str(tmp_path), "--key-id", "k1"]) == 0
        assert main(["sign-all", "--palace", str(tmp_path)]) == 0
        assert main(["verify-all", "--palace", str(tmp_path)]) == 0

    def test_missing_config_error_names_a_command_that_exists(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        assert main(["sign-all", "--palace", str(tmp_path)]) == 1
        err = capsys.readouterr().err
        assert "locus-security init-config --palace" in err
        assert "templates/" not in err
