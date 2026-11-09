"""
System audio ("what you hear") recording — the PC's output, not the mic.

Linux:   PulseAudio/PipeWire monitor of the default output device, via `parec`
         (ships with pipewire-pulse / pulseaudio-utils).
Windows: WASAPI loopback of the default output device, via pyaudiowpatch.
macOS:   not supported — macOS has no built-in loopback; it needs a virtual
         device such as BlackHole.

Recordings are 16-bit PCM WAV files in a private temp location. Callers own
the returned file and should delete it (see `discard`) once it is used.
"""

from __future__ import annotations

import io
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave
from typing import List, Optional

logger = logging.getLogger(__name__)

_RATE_LINUX = 16000  # speech-quality; keeps uploads small


def _temp_dir() -> str:
    return os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()


def _new_temp(suffix: str) -> str:
    fd, path = tempfile.mkstemp(prefix="hindsight-audio-", suffix=suffix, dir=_temp_dir())
    os.close(fd)
    return path


def discard(paths) -> None:
    for p in ([paths] if isinstance(paths, str) else paths or []):
        try:
            os.unlink(p)
        except OSError:
            pass


class SystemAudioRecorder:
    def __init__(self):
        self._backend = None
        self._started_at: Optional[float] = None

    @property
    def is_recording(self) -> bool:
        return self._backend is not None

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._started_at if self._started_at else 0.0

    def start(self) -> None:
        if self._backend is not None:
            return
        if sys.platform.startswith("linux"):
            backend = _ParecBackend()
        elif sys.platform == "win32":
            backend = _WasapiLoopbackBackend()
        else:
            raise RuntimeError(
                "Recording PC audio is not supported on this OS "
                "(macOS needs a loopback device such as BlackHole)"
            )
        backend.start()
        self._backend = backend
        self._started_at = time.monotonic()
        logger.info("System audio recording started")

    def stop(self) -> Optional[str]:
        """Stop and return the WAV path, or None if nothing was captured."""
        backend, self._backend = self._backend, None
        if backend is None:
            return None
        duration = self.elapsed
        self._started_at = None
        path = backend.stop()
        logger.info(f"System audio recording stopped ({duration:.1f}s) -> {path}")
        return path

    def cancel(self) -> None:
        discard(self.stop())


class _ParecBackend:
    def __init__(self):
        if not shutil.which("parec"):
            raise RuntimeError("Recording PC audio needs 'parec' (pipewire-pulse or pulseaudio-utils)")
        self._raw = _new_temp(".raw")
        self._proc: Optional[subprocess.Popen] = None

    def start(self) -> None:
        with open(self._raw, "wb") as out:
            # @DEFAULT_MONITOR@ = monitor of the current default output (what you hear)
            self._proc = subprocess.Popen(
                ["parec", "--device=@DEFAULT_MONITOR@", "--format=s16le",
                 f"--rate={_RATE_LINUX}", "--channels=1", "--raw",
                 # small buffer, so stopping loses at most ~50 ms of audio
                 "--latency-msec=50"],
                stdout=out, stderr=subprocess.PIPE,
            )
        time.sleep(0.15)
        if self._proc.poll() is not None:
            err = self._proc.stderr.read().decode(errors="replace").strip()
            discard(self._raw)
            raise RuntimeError(f"parec failed to start: {err or 'unknown error'}")

    def stop(self) -> Optional[str]:
        proc = self._proc
        if proc and proc.poll() is None:
            import signal
            proc.send_signal(signal.SIGINT)  # parec flushes buffered audio on SIGINT
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        try:
            with open(self._raw, "rb") as f:
                pcm = f.read()
        finally:
            discard(self._raw)
        pcm = pcm[: len(pcm) - (len(pcm) % 2)]
        if not pcm:
            return None
        path = _new_temp(".wav")
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(_RATE_LINUX)
            w.writeframes(pcm)
        return path


class _WasapiLoopbackBackend:
    def __init__(self):
        try:
            import pyaudiowpatch as pyaudio
        except ImportError as e:
            raise RuntimeError("Recording PC audio on Windows needs 'pyaudiowpatch'") from e
        self._pa_mod = pyaudio
        self._pa = pyaudio.PyAudio()
        wasapi = self._pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        speakers = self._pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
        if not speakers.get("isLoopbackDevice"):
            for lb in self._pa.get_loopback_device_info_generator():
                if speakers["name"] in lb["name"]:
                    speakers = lb
                    break
            else:
                self._pa.terminate()
                raise RuntimeError("No WASAPI loopback device for the default speakers")
        self._dev = speakers
        self._channels = int(speakers["maxInputChannels"]) or 2
        self._rate = int(speakers["defaultSampleRate"])
        self._frames: List[bytes] = []
        self._lock = threading.Lock()
        self._stream = None

    def _callback(self, in_data, frame_count, time_info, status):
        with self._lock:
            self._frames.append(in_data)
        return (None, self._pa_mod.paContinue)

    def start(self) -> None:
        self._stream = self._pa.open(
            format=self._pa_mod.paInt16, channels=self._channels, rate=self._rate,
            input=True, input_device_index=self._dev["index"],
            frames_per_buffer=1024, stream_callback=self._callback,
        )
        self._stream.start_stream()

    def stop(self) -> Optional[str]:
        try:
            if self._stream is not None:
                self._stream.stop_stream()
                self._stream.close()
        finally:
            self._pa.terminate()
        with self._lock:
            pcm = b"".join(self._frames)
        if not pcm:
            return None
        path = _new_temp(".wav")
        with wave.open(path, "wb") as w:
            w.setnchannels(self._channels)
            w.setsampwidth(2)
            w.setframerate(self._rate)
            w.writeframes(pcm)
        return path


def split_wav(path: str, max_seconds: float = 25.0, skip_silence: bool = True) -> List[bytes]:
    """Split a WAV into in-memory WAV chunks of at most max_seconds, dropping
    silent chunks (nothing to transcribe, and ASR models hallucinate on silence)."""
    import numpy as np

    chunks: List[bytes] = []
    with wave.open(path, "rb") as src:
        params = src.getparams()
        per_chunk = int(params.framerate * max_seconds)
        while True:
            frames = src.readframes(per_chunk)
            if not frames:
                break
            if skip_silence and params.sampwidth == 2:
                samples = np.frombuffer(frames, dtype="<i2").astype(np.float32)
                rms = float(np.sqrt(np.mean(samples ** 2))) if samples.size else 0.0
                if rms < 60:  # ~ -55 dBFS
                    continue
            buf = io.BytesIO()
            with wave.open(buf, "wb") as dst:
                dst.setnchannels(params.nchannels)
                dst.setsampwidth(params.sampwidth)
                dst.setframerate(params.framerate)
                dst.writeframes(frames)
            chunks.append(buf.getvalue())
    return chunks
