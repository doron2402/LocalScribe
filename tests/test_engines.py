import pytest

from localscribe import engines


@pytest.fixture
def apple_silicon(monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("platform.machine", lambda: "arm64")


def _mlx(monkeypatch, present: bool):
    monkeypatch.setattr(engines, "mlx_available", lambda: present)


def test_auto_prefers_mlx_when_installed(monkeypatch):
    _mlx(monkeypatch, True)
    assert engines.resolve("auto") == "mlx"


def test_auto_falls_back_to_cpu(monkeypatch):
    _mlx(monkeypatch, False)
    assert engines.resolve("auto") == "faster-whisper"


def test_explicit_cpu_engine_works_without_mlx(monkeypatch):
    _mlx(monkeypatch, False)
    assert engines.resolve("faster-whisper") == "faster-whisper"


def test_mlx_off_apple_silicon_is_a_clear_error(monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.setattr("platform.machine", lambda: "x86_64")
    with pytest.raises(engines.EngineError, match="Apple Silicon"):
        engines.resolve("mlx")


def test_mlx_uninstalled_on_apple_silicon_says_how_to_install(monkeypatch, apple_silicon):
    monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
    with pytest.raises(engines.EngineError, match=r"\[mlx\]"):
        engines.resolve("mlx")


def test_unknown_engine_is_rejected(monkeypatch):
    _mlx(monkeypatch, False)
    with pytest.raises(engines.EngineError, match="Unknown engine"):
        engines.resolve("whisper.cpp")


def test_mlx_availability_needs_apple_silicon(monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("platform.machine", lambda: "x86_64")
    monkeypatch.setattr("importlib.util.find_spec", lambda name: object())
    assert engines.mlx_available() is False


@pytest.mark.parametrize(
    "name,repo",
    [
        ("large-v3-turbo", "mlx-community/whisper-large-v3-turbo"),
        ("base.en", "mlx-community/whisper-base.en-mlx"),
        ("medium.en", "mlx-community/whisper-medium.en-mlx"),
        ("mlx-community/custom-model", "mlx-community/custom-model"),
    ],
)
def test_mlx_repo_mapping(name, repo):
    assert engines.mlx_repo(name) == repo


def test_unmapped_model_suggests_a_way_out():
    with pytest.raises(engines.EngineError, match="Hugging Face repo"):
        engines.mlx_repo("distil-large-v3")


def test_mlx_output_is_normalized(monkeypatch):
    """MLX returns dicts; faster-whisper returns objects. Both become RawSegment."""
    fake = {
        "language": "en",
        "segments": [{
            "start": 0.0, "end": 1.5, "text": "  hello there  ",
            "words": [{"word": " hello", "start": 0.0, "end": 0.7, "probability": 0.9},
                      {"word": " there", "start": 0.7, "end": 1.5, "probability": 0.9}],
        }],
    }
    import sys
    import types
    mod = types.ModuleType("mlx_whisper.transcribe")
    mod.transcribe = lambda *a, **k: fake
    monkeypatch.setitem(sys.modules, "mlx_whisper", types.ModuleType("mlx_whisper"))
    monkeypatch.setitem(sys.modules, "mlx_whisper.transcribe", mod)

    segs, lang = engines._run_mlx([0.0] * 16, "base.en", "en", True, "int8")
    segs = list(segs)
    assert lang == "en"
    assert segs[0].text == "hello there"
    assert [w.word for w in segs[0].words] == [" hello", " there"]
    assert segs[0].words[1].end == 1.5
