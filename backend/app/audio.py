"""Everything ffmpeg: validate the upload, convert it, find pauses, plan and cut parts.

ffmpeg runs via subprocess.run inside asyncio.to_thread so it never blocks the event loop
(and works on both Windows dev and Linux prod).
"""
import asyncio
import json
import re
import subprocess
from dataclasses import dataclass

# Gnani REST rejects > 30 s (measured). 28 s leaves margin for cut imprecision.
MAX_PART_SEC = 27.0
# Don't cut earlier than this inside a window: avoids lots of tiny parts.
MIN_PART_SEC = 10.0
# Pause detection, tuned on our Hinglish clip (real pauses were 0.57–0.81 s at -35 dB).
SILENCE_DB = -35
SILENCE_MIN_SEC = 0.3


class AudioError(Exception):
    """A problem with the file itself. `code` drives the UI, `message` is shown to the user."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Silence:
    start: float
    end: float

    @property
    def mid(self) -> float:
        return (self.start + self.end) / 2

    @property
    def length(self) -> float:
        return self.end - self.start


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")


async def probe(path: str) -> float:
    """Return duration in seconds. Raise AudioError if it isn't decodable audio."""
    result = await asyncio.to_thread(_run, [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration:stream=codec_type",
        "-of", "json", path,
    ])
    if result.returncode != 0:
        raise AudioError("DECODE_FAILED", "We couldn't read any audio in this file. It may be corrupted or not an audio file.")

    info = json.loads(result.stdout or "{}")
    has_audio = any(s.get("codec_type") == "audio" for s in info.get("streams", []))
    duration = float(info.get("format", {}).get("duration") or 0)
    if not has_audio or duration <= 0:
        raise AudioError("NO_AUDIO_STREAM", "This file has no audio track we can use.")
    return duration


async def normalize(src: str, dst_wav: str) -> None:
    """Any input (mp3/m4a/webm/mp4...) -> 16 kHz mono 16-bit WAV.
    Gnani converts to 16 kHz mono anyway; doing it once here makes cuts exact and uploads small."""
    result = await asyncio.to_thread(_run, [
        "ffmpeg", "-y", "-v", "error", "-i", src,
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", dst_wav,
    ])
    if result.returncode != 0:
        raise AudioError("DECODE_FAILED", "We couldn't decode this audio. It may be corrupted.")


_SIL_START = re.compile(r"silence_start: (-?[\d.]+)")
_SIL_END = re.compile(r"silence_end: ([\d.]+)")


async def detect_silences(wav: str) -> list[Silence]:
    """Run ffmpeg's silencedetect and parse the pauses it reports on stderr."""
    result = await asyncio.to_thread(_run, [
        "ffmpeg", "-hide_banner", "-nostats", "-i", wav,
        "-af", f"silencedetect=noise={SILENCE_DB}dB:d={SILENCE_MIN_SEC}",
        "-f", "null", "-",
    ])
    silences: list[Silence] = []
    start = None
    for line in result.stderr.splitlines():
        if (m := _SIL_START.search(line)):
            start = max(0.0, float(m.group(1)))
        elif (m := _SIL_END.search(line)) and start is not None:
            silences.append(Silence(start, float(m.group(1))))
            start = None
    return silences


def plan_chunks(duration: float, silences: list[Silence],
                max_len: float = MAX_PART_SEC, min_len: float = MIN_PART_SEC) -> list[tuple[float, float]]:
    """Pure function: decide where to cut.

    Walk through the file. For each part, look for pauses whose midpoint falls between
    min_len and max_len after the part's start. Cut in the LONGEST such pause (safest:
    least likely to be mid-word); on a tie, the later one (fewer parts).
    No pause in the window -> hard cut at max_len.
    Guarantees: parts are contiguous, cover [0, duration], and none exceeds max_len.
    """
    parts: list[tuple[float, float]] = []
    start = 0.0
    while duration - start > max_len:
        window_lo, window_hi = start + min_len, start + max_len
        candidates = [s for s in silences if window_lo <= s.mid <= window_hi]
        if candidates:
            best = max(candidates, key=lambda s: (s.length, s.mid))
            cut = best.mid
        else:
            cut = window_hi
        parts.append((round(start, 3), round(cut, 3)))
        start = cut
    parts.append((round(start, 3), round(duration, 3)))
    return parts


PAD_SEC = 0.3  # silence added before and after each part (ASR drops speech at clip edges)


async def cut(wav: str, dst: str, start: float, end: float) -> None:
    """Copy [start, end) of the 16 kHz WAV into its own file, padded with PAD_SEC of silence
    on both sides so speech at the very edge of a part isn't dropped by the ASR."""
    pad_ms = int(PAD_SEC * 1000)
    result = await asyncio.to_thread(_run, [
        "ffmpeg", "-y", "-v", "error",
        "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", wav,
        "-af", f"adelay={pad_ms},apad=pad_dur={PAD_SEC}",
        "-c:a", "pcm_s16le", dst,
    ])
    if result.returncode != 0:
        raise AudioError("CUT_FAILED", "We couldn't split this audio into parts.")