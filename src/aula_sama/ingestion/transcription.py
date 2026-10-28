"""Transcripción de audio/video sin subtítulos con la API de OpenAI.

`gpt-transcribe` no devuelve timestamps, así que el audio se corta en silencios
en ventanas de ~90 s y cada ventana se transcribe por separado: el inicio/fin de
la ventana es el timestamp de su texto, suficiente para citar un minuto del video.
"""

import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from aula_sama.config import Settings, get_settings
from aula_sama.models import Segment

TARGET_WINDOW = 90.0  # segundos
MAX_WINDOW = 150.0
_SILENCE = re.compile(r"silence_(start|end): (-?[\d.]+)")
_DURATION = re.compile(r"Duration: (\d+):(\d+):([\d.]+)")


class TranscriptionError(Exception):
    pass


def download_audio(url: str, workdir: Path) -> Path:
    """Descarga solo la pista de audio de un video (YouTube u otros sitios soportados por yt-dlp)."""
    import yt_dlp

    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(workdir / "audio.%(ext)s"),
        "quiet": True,
        "noprogress": True,
        "noplaylist": True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return Path(ydl.prepare_filename(info))
    except yt_dlp.utils.DownloadError as exc:
        raise TranscriptionError(f"No se pudo descargar el audio: {exc}") from exc


def transcribe_file(path: Path, keywords: list[str] = (), settings: Settings | None = None) -> list[Segment]:
    settings = settings or get_settings()
    if not settings.openai_api_key:
        raise TranscriptionError("Falta OPENAI_API_KEY en .env para transcribir audio sin subtítulos.")
    from openai import OpenAI

    client = OpenAI(api_key=settings.openai_api_key)
    windows = _windows(*_probe(path))
    terms = _keywords([*settings.transcription_keywords, *keywords])

    def run(window: tuple[float, float]) -> Segment:
        start, end = window
        with tempfile.NamedTemporaryFile(suffix=".mp3") as piece:
            _ffmpeg("-ss", f"{start:.2f}", "-to", f"{end:.2f}", "-i", str(path),
                    "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k", "-y", piece.name)
            with open(piece.name, "rb") as f:
                result = client.audio.transcriptions.create(
                    model=settings.transcription_model,
                    file=f,
                    extra_body={"languages": ["es"], "keywords": terms},
                )
        return Segment(text=" ".join(result.text.split()), start=start, end=end)

    with ThreadPoolExecutor(max_workers=4) as pool:
        segments = list(pool.map(run, windows))
    segments = [s for s in segments if s.text]
    if not segments:
        raise TranscriptionError("La transcripción quedó vacía (¿el audio no tiene voz?).")
    return segments


def _probe(path: Path) -> tuple[float, list[float]]:
    """Duración total y puntos medios de los silencios detectados."""
    out = _ffmpeg("-i", str(path), "-vn", "-af", "silencedetect=noise=-30dB:d=0.4", "-f", "null", "-")
    m = _DURATION.search(out)
    if not m:
        raise TranscriptionError("No se pudo leer la duración del audio.")
    duration = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
    silences, start = [], None
    for kind, value in _SILENCE.findall(out):
        if kind == "start":
            start = float(value)
        elif start is not None:
            silences.append((start + float(value)) / 2)
            start = None
    return duration, silences


def _windows(duration: float, silences: list[float]) -> list[tuple[float, float]]:
    """Corta cerca de cada TARGET_WINDOW en el silencio más próximo, sin pasar de MAX_WINDOW."""
    cuts, last = [], 0.0
    while duration - last > MAX_WINDOW:
        goal = last + TARGET_WINDOW
        options = [s for s in silences if last + 20 < s <= last + MAX_WINDOW]
        cut = min(options, key=lambda s: abs(s - goal)) if options else goal
        cuts.append(cut)
        last = cut
    bounds = [0.0, *cuts, duration]
    return list(zip(bounds[:-1], bounds[1:]))


def _keywords(terms: list[str]) -> list[str]:
    seen, clean = set(), []
    for t in terms:
        t = re.sub(r"[<>\r\n]", " ", t).strip()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            clean.append(t)
    return clean


def _ffmpeg(*args: str) -> str:
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise TranscriptionError(f"ffmpeg falló: {proc.stderr[-500:]}")
    return proc.stderr
