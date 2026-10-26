"""Trivial smoke test for the project skeleton."""

from whyledger import __version__


def test_package_version():
    assert __version__ == "0.1.0"
