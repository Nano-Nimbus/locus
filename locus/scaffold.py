"""Packaged palace and configuration templates, and the ``locus init`` command.

``templates/`` and ``example-palace/`` live at the repository root, where
``CLAUDE.md``, ``CONTRIBUTING.md`` and the skills all cite them as the
canonical structural reference.  A wheel has no repository root, so
``pyproject.toml`` force-includes both trees under ``locus/_scaffold/`` at
build time and everything here reads that copy.  One source, two layouts:

* installed wheel: ``<site-packages>/locus/_scaffold/{templates,example-palace}``
* source checkout or editable install: ``<repo>/{templates,example-palace}``

Both roots hold the same two directories, so only :func:`scaffold_root`
knows which layout it is looking at.

The data is read as ordinary filesystem paths rather than through
``importlib.resources`` readers.  ``pip`` and ``uv`` always unpack a wheel,
so the files exist on disk; scaffolding copies whole directory trees, which
a ``Traversable`` cannot do without materialising them anyway.

Nothing here imports the Agent SDK, so ``locus.cli`` can dispatch
``locus init`` as cheaply as it dispatches ``recall``.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path

__all__ = [
    "SECURITY_CONFIG_NAME",
    "ScaffoldError",
    "available_templates",
    "copy_example_palace",
    "init_main",
    "scaffold_root",
    "security_config_template",
    "template_path",
    "write_security_config",
]

SECURITY_CONFIG_NAME = "locus-security.yaml"

# Directory markers that exist only to keep an empty directory in git.  The
# directory is still created; the marker file is not copied into a palace.
_VCS_MARKERS = {".gitkeep", ".gitignore"}

_PACKAGED_ROOT = Path(__file__).resolve().parent / "_scaffold"
_REPO_ROOT = Path(__file__).resolve().parents[1]

_DATA_DIRS = ("templates", "example-palace")


class ScaffoldError(Exception):
    """A user-facing scaffolding error: printed to stderr, exit status 1."""


def scaffold_root() -> Path:
    """Return the directory holding ``templates/`` and ``example-palace/``.

    Prefers the copy inside the installed package.  An editable install or a
    plain source checkout never runs the wheel build, so ``locus/_scaffold/``
    does not exist there and the repository root is used instead.
    """
    if all((_PACKAGED_ROOT / name).is_dir() for name in _DATA_DIRS):
        return _PACKAGED_ROOT
    if all((_REPO_ROOT / name).is_dir() for name in _DATA_DIRS):
        return _REPO_ROOT
    raise ScaffoldError(
        "Packaged templates are missing from this installation "
        f"(looked in {_PACKAGED_ROOT} and {_REPO_ROOT}). "
        "Reinstall locus-mcp, or report it at "
        "https://github.com/Nano-Nimbus/locus/issues"
    )


def template_path(relative: str) -> Path:
    """Return an absolute path to one packaged template file or directory.

    ``relative`` comes from ``locus init --show`` as well as from callers in
    this package, so it is confined to the scaffold tree: an absolute path or
    one containing ``..`` would otherwise turn a template printer into an
    arbitrary file reader.
    """
    root = scaffold_root()
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ScaffoldError(f"Not a packaged template name: {relative!r}")
    path = root / candidate
    if not path.exists() or root not in path.resolve().parents:
        raise ScaffoldError(f"No packaged template named {relative!r} at {path}")
    return path


def available_templates() -> list[str]:
    """Every packaged template file, as names ``--show`` accepts."""
    root = scaffold_root()
    return [
        str(path.relative_to(root))
        for path in sorted((root / "templates").rglob("*"))
        if path.is_file() and path.name not in _VCS_MARKERS
    ]


def security_config_template() -> Path:
    """Path to the annotated ``locus-security.yaml`` template."""
    return template_path(f"templates/{SECURITY_CONFIG_NAME}")


def _iter_tree(source: Path) -> Iterator[Path]:
    """Yield every file under ``source``, sorted, skipping VCS markers."""
    for path in sorted(source.rglob("*")):
        if path.is_file() and path.name not in _VCS_MARKERS:
            yield path


def _reject_symlink_escape(dest_root: Path, target: Path) -> None:
    """Refuse to write through a symlink under ``dest_root``.

    ``shutil.copyfile`` follows symlinks, and ``Path.exists()`` reports False
    for a dangling one. Without this check, a symlink planted at ``target``,
    or at any directory between ``dest_root`` and ``target``, would let a
    scaffold write land outside the palace even when the caller did not pass
    ``force``: a dangling symlink reads as "does not exist yet" and gets
    copied through, and an existing symlinked directory component gets
    followed by ``mkdir``/``copyfile`` the same way a real directory would.
    Writing into a tree that already has content there is exactly what this
    module promises never to do silently, so a symlink in the way is refused
    rather than followed.
    """
    if target.is_symlink():
        raise ScaffoldError(f"Refusing to write through a symlink at {target}")
    probe = dest_root
    for part in target.relative_to(dest_root).parts[:-1]:
        probe = probe / part
        if probe.is_symlink():
            raise ScaffoldError(f"Refusing to write through a symlink at {probe}")


def copy_example_palace(dest: Path, force: bool = False) -> tuple[list[Path], list[Path]]:
    """Copy the packaged example palace into ``dest``.

    Returns ``(written, skipped)`` as paths relative to ``dest``.  An existing
    file is never overwritten unless ``force`` is set: running this against a
    palace that already holds notes must not be able to destroy them.
    """
    source = template_path("example-palace")
    written: list[Path] = []
    skipped: list[Path] = []
    dest.mkdir(parents=True, exist_ok=True)
    for path in _iter_tree(source):
        relative = path.relative_to(source)
        target = dest / relative
        _reject_symlink_escape(dest, target)
        if target.exists() and not force:
            skipped.append(relative)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        written.append(relative)
    # Directories that hold only a VCS marker (sessions/) still belong in a
    # palace: they are where the next session log goes.
    for path in sorted(source.rglob("*")):
        if path.is_dir():
            target_dir = dest / path.relative_to(source)
            _reject_symlink_escape(dest, target_dir)
            target_dir.mkdir(parents=True, exist_ok=True)
    return written, skipped


def write_security_config(palace: Path, force: bool = False) -> tuple[Path, bool]:
    """Write ``locus-security.yaml`` into ``palace`` from the packaged template.

    Returns ``(path, written)``.  ``written`` is False when the file was
    already there and ``force`` was not given, because overwriting a tuned
    security policy is not something a command called ``init`` should do.
    """
    target = palace / SECURITY_CONFIG_NAME
    _reject_symlink_escape(palace, target)
    if target.exists() and not force:
        return target, False
    shutil.copyfile(security_config_template(), target)
    return target, True


def build_init_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="locus init",
        description=(
            "Create a palace from the packaged example: INDEX.md, a global room, "
            "and a project room. Existing files are kept unless --force is given."
        ),
    )
    parser.add_argument(
        "path",
        nargs="?",
        default="~/.locus",
        type=Path,
        help="Palace root to create (default: ~/.locus).",
    )
    parser.add_argument(
        "--show",
        metavar="TEMPLATE",
        help="Print one packaged template to stdout and exit, for example "
        "templates/room/room-name.md. Use --show list to see them all.",
    )
    parser.add_argument(
        "--security",
        action="store_true",
        help=f"Also write {SECURITY_CONFIG_NAME} for `locus-security init-keys`.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite files that already exist in the palace.",
    )
    return parser


def init_main(argv: list[str] | None = None) -> int:
    args = build_init_parser().parse_args(argv)
    if args.show:
        try:
            if args.show == "list":
                print("\n".join(available_templates()))
                return 0
            print(template_path(args.show).read_text(encoding="utf-8"), end="")
        except (ScaffoldError, OSError, UnicodeDecodeError) as exc:
            print(f"locus init: {exc}", file=sys.stderr)
            # available_templates() calls scaffold_root() again, which raises
            # the same ScaffoldError when the packaged data is what is
            # missing. Fall back instead of letting that escape as a second,
            # unhandled traceback on top of the message above.
            try:
                names = ", ".join(available_templates())
            except ScaffoldError:
                names = "none (see the error above)"
            print(f"Available templates: {names}", file=sys.stderr)
            return 1
        return 0

    dest = args.path.expanduser().resolve()
    try:
        written, skipped = copy_example_palace(dest, force=args.force)
        if args.security:
            config_path, config_written = write_security_config(dest, force=args.force)
            relative = config_path.relative_to(dest)
            (written if config_written else skipped).append(relative)
    except (ScaffoldError, OSError) as exc:
        print(f"locus init: {exc}", file=sys.stderr)
        return 1

    for relative in written:
        print(f"wrote  {relative}")
    for relative in skipped:
        print(f"kept   {relative} (already exists)")
    print(f"Palace ready at {dest}")
    if not written:
        print("Nothing written: every file was already there. Use --force to overwrite.")
    else:
        print(f"Next: edit {dest / 'INDEX.md'} to describe your palace.")
    return 0


def init_cli() -> None:
    sys.exit(init_main())


if __name__ == "__main__":
    init_cli()
