import sys

import pytest

from xiangqi.engine.check import candidate_rank, find_candidates, guess_flavor


def _touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")
    return path


@pytest.mark.skipif(sys.platform == "win32", reason="示例文件名按 Linux / macOS 构造")
def test_find_candidates_orders_fastest_first(tmp_path, monkeypatch):
    monkeypatch.setattr("platform.machine", lambda: "x86_64")
    root = tmp_path / "Pikafish"
    for name in ("pikafish-avx2", "pikafish-vnni512", "pikafish-sse41-popcnt", "pikafish-bmi2"):
        _touch(root / name)
    _touch(root / "pikafish.nnue")
    _touch(root / "Windows" / "pikafish-avx2.exe")  # 其他系统的版本不应出现
    _touch(root / "README.md")
    names = [p.name for p in find_candidates(root)]
    assert names == ["pikafish-vnni512", "pikafish-bmi2", "pikafish-avx2", "pikafish-sse41-popcnt"]


def test_apple_silicon_preferred_on_arm(tmp_path, monkeypatch):
    monkeypatch.setattr("platform.machine", lambda: "arm64")
    assert candidate_rank(tmp_path / "pikafish-apple-silicon") < candidate_rank(
        tmp_path / "pikafish-avx2"
    )


def test_universal_build_first(tmp_path):
    assert candidate_rank(tmp_path / "pikafish") == 0


def test_guess_flavor(tmp_path):
    assert guess_flavor(tmp_path / "fairy-stockfish-largeboard") == "fairy-stockfish"
    assert guess_flavor(tmp_path / "pikafish-avx2") == "pikafish"


def test_toml_path_is_valid_toml_even_with_backslashes(tmp_path):
    import tomllib

    from xiangqi.engine.check import toml_path

    tricky = tmp_path / 'dir with "quote" and \\backslash' / "pikafish"
    value = tomllib.loads(f"path = {toml_path(tricky, None)}")["path"]
    assert value == tricky.resolve().as_posix()


def test_toml_path_relative_to_config(tmp_path):
    from xiangqi.engine.check import toml_path

    config = tmp_path / "config.toml"
    config.write_text("")
    assert toml_path(tmp_path / "engines" / "pikafish", config) == '"engines/pikafish"'
