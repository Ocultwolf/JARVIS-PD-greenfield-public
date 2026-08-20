import os

import pytest

from jarvis_pd.loop.application.fixture_atomic_write import (
    ConfirmedNoEffectError,
    FixtureAtomicWrite,
)


def test_atomic_write_is_verified_and_bounded(tmp_path) -> None:
    root = tmp_path / "root"
    (root / "result").mkdir(parents=True)
    tool = FixtureAtomicWrite(root)
    evidence = tool.execute("result/output.txt", b"safe", max_bytes=4)
    assert (root / "result/output.txt").read_bytes() == b"safe"
    assert evidence.resulting_hash == FixtureAtomicWrite.digest(b"safe")
    with pytest.raises(ConfirmedNoEffectError, match="SIZE"):
        tool.execute("result/large.txt", b"12345", max_bytes=4)


@pytest.mark.parametrize("path", ["/tmp/escape", "../escape", "result/../escape"])
def test_path_traversal_and_absolute_paths_are_denied(tmp_path, path) -> None:
    tool = FixtureAtomicWrite(tmp_path)
    with pytest.raises(ConfirmedNoEffectError, match="OPEN"):
        tool.execute(path, b"x", max_bytes=10)


def test_symlink_escape_is_denied_at_use_time(tmp_path) -> None:
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    os.symlink(outside, root / "link")
    tool = FixtureAtomicWrite(root)
    with pytest.raises(ConfirmedNoEffectError, match="OPEN"):
        tool.execute("link/escape.txt", b"x", max_bytes=10)
    assert not (outside / "escape.txt").exists()
