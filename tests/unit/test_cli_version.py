"""Tests for --version flag on all three Locus CLI entry points."""

from __future__ import annotations

import importlib.metadata
import subprocess
import sys

import pytest

_VERSION = importlib.metadata.version("locus-mcp")


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m"] + args,
        capture_output=True,
        text=True,
    )


class TestVersionFlag:
    def test_locus_mcp_version(self):
        result = subprocess.run(
            ["locus-mcp", "--version"], capture_output=True, text=True
        )
        assert result.returncode == 0
        assert f"locus-mcp {_VERSION}" in result.stdout

    def test_locus_version(self):
        result = subprocess.run(
            ["locus", "--version"], capture_output=True, text=True
        )
        assert result.returncode == 0
        assert f"locus {_VERSION}" in result.stdout

    def test_locus_audit_version(self):
        result = subprocess.run(
            ["locus-audit", "--version"], capture_output=True, text=True
        )
        assert result.returncode == 0
        assert f"locus-audit {_VERSION}" in result.stdout

    def test_locus_security_version(self):
        result = subprocess.run(
            ["locus-security", "--version"], capture_output=True, text=True
        )
        assert result.returncode == 0
        assert f"locus-security {_VERSION}" in result.stdout

    def test_version_matches_package_metadata(self):
        """Version reported by CLI must match the installed package metadata."""
        result = subprocess.run(
            ["locus-mcp", "--version"], capture_output=True, text=True
        )
        assert _VERSION in result.stdout

    def test_version_exits_zero(self):
        for cmd in [["locus-mcp", "--version"], ["locus-audit", "--version"], ["locus-security", "--version"]]:
            result = subprocess.run(cmd, capture_output=True, text=True)
            assert result.returncode == 0, f"{cmd[0]} --version exited {result.returncode}"
