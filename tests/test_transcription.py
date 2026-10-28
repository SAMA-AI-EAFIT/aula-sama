import subprocess
from types import SimpleNamespace

import pytest

from aula_sama.config import Settings
from aula_sama.ingestion import transcription
from aula_sama.ingestion.transcription import MAX_WINDOW, _keywords, _windows


def test_windows_cut_at_nearest_silence():
    silences = [40.0, 88.0, 130.0, 185.0, 260.0, 300.0]
    windows = _windows(400.0, silences)
    assert windows[0] == (0.0, 88.0)
    assert windows[-1][1] == 400.0
    assert all(b - a <= MAX_WINDOW for a, b in windows)
    assert all(w1[1] == w2[0] for w1, w2 in zip(windows, windows[1:]))


def test_windows_without_silences_fall_back_to_fixed_cuts():
    windows = _windows(500.0, [])
    assert windows[0] == (0.0, 90.0)
    assert all(b - a <= MAX_WINDOW for a, b in windows)


def test_short_audio_is_single_window():
    assert _windows(120.0, [30.0]) == [(0.0, 120.0)]


def test_keywords_are_sanitized_and_deduplicated():
    assert _keywords(["SIATA", "siata", "  Río <Medellín>\n", ""]) == ["SIATA", "Río  Medellín"]


def test_transcribe_file_splits_audio_and_keeps_timestamps(tmp_path, monkeypatch):
    # 200 s: tono / 1 s de silencio / tono → debe cortar en el silencio cerca de 90 s
    audio = tmp_path / "talk.wav"
    subprocess.run(
        [transcription.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=89",
         "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=1", "-f", "lavfi", "-i", "sine=frequency=440:duration=110",
         "-filter_complex", "[0][1][2]concat=n=3:v=0:a=1", str(audio)],
        check=True,
    )
    calls = []

    class FakeTranscriptions:
        def create(self, model, file, extra_body):
            calls.append((model, extra_body))
            return SimpleNamespace(text=f"  ventana   {len(calls)} ")

    class FakeOpenAI:
        def __init__(self, api_key):
            self.audio = SimpleNamespace(transcriptions=FakeTranscriptions())

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)
    settings = Settings(_env_file=None, openai_api_key="test", transcription_keywords=["SIATA"])
    segments = transcription.transcribe_file(audio, ["Bello"], settings)

    assert len(segments) == 2
    assert segments[0].start == 0.0 and 88.5 < segments[0].end < 90.5
    assert segments[1].start == segments[0].end and segments[1].end == pytest.approx(200.0, abs=0.5)
    assert all(s.text.startswith("ventana") for s in segments)
    assert calls[0] == ("gpt-transcribe", {"languages": ["es"], "keywords": ["SIATA", "Bello"]})


def test_transcribe_requires_api_key(tmp_path):
    with pytest.raises(transcription.TranscriptionError, match="OPENAI_API_KEY"):
        transcription.transcribe_file(tmp_path / "x.mp3", settings=Settings(_env_file=None))
