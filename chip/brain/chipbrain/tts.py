"""Text to speech. Every backend returns a WAV that POST /play accepts
(16-bit mono 22050 Hz) via ``audio.to_robot_wav``.

Backends (``[tts] backend``):
  piper  - piper-tts python package, or piper.exe, + a voice model (best voice)
  sapi   - Windows built-in voices; tries comtypes, then pyttsx3, then
           PowerShell System.Speech (no install at all)
  none   - silent (logs only)
  auto   - piper if a model is configured, else sapi on Windows, else none

To add a cloud voice later: subclass ``TTSBackend`` and return WAV bytes from
``synthesize``; register it in ``make_tts``.
"""

from __future__ import annotations

import io
import logging
import os
import subprocess
import sys
import tempfile
import threading
import wave
from pathlib import Path

from .audio import to_robot_wav
from .config import TtsCfg

log = logging.getLogger("chip.tts")


class TTSBackend:
    name = "base"

    def synthesize(self, text: str) -> bytes:
        """Return robot-format WAV bytes. Called from the TTS worker thread."""
        raise NotImplementedError

    def warm(self) -> None:
        pass


class NullTTS(TTSBackend):
    name = "none"

    def synthesize(self, text: str) -> bytes:
        return b""


class PiperTTS(TTSBackend):
    name = "piper"

    def __init__(self, model: Path, exe: str = "", gain: float = 1.0):
        self.model = Path(model)
        self.exe = exe
        self.gain = gain
        self._voice = None
        self._lock = threading.Lock()
        if not self.model.exists():
            raise FileNotFoundError(f"piper voice model not found: {self.model}")
        if not exe:
            from piper import PiperVoice  # lazy; raises ImportError if missing

            self._voice_cls = PiperVoice

    def warm(self) -> None:
        if not self.exe:
            with self._lock:
                if self._voice is None:
                    self._voice = self._voice_cls.load(str(self.model))

    def synthesize(self, text: str) -> bytes:
        if self.exe:
            return self._synth_exe(text)
        self.warm()
        bio = io.BytesIO()
        with wave.open(bio, "wb") as wf:
            if hasattr(self._voice, "synthesize_wav"):  # piper-tts >= 1.3
                self._voice.synthesize_wav(text, wf)
            else:  # piper-tts 1.2
                self._voice.synthesize(text, wf)
        return to_robot_wav(bio.getvalue(), self.gain)

    def _synth_exe(self, text: str) -> bytes:
        fd, out = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            subprocess.run(
                [self.exe, "--model", str(self.model), "--output_file", out],
                input=text.encode("utf-8"), capture_output=True, timeout=30, check=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return to_robot_wav(Path(out).read_bytes(), self.gain)
        finally:
            try:
                os.unlink(out)
            except OSError:
                pass


class SapiTTS(TTSBackend):
    """Windows SAPI voices. No model download."""

    name = "sapi"

    def __init__(self, voice: str = "", rate: int = 1, gain: float = 1.0):
        self.voice = voice
        self.rate = rate
        self.gain = gain
        self._method = None
        if sys.platform != "win32":
            raise RuntimeError("SAPI is Windows-only")

    def synthesize(self, text: str) -> bytes:
        fd, out = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            methods = [self._method] if self._method else [self._comtypes, self._pyttsx3, self._powershell]
            last = None
            for m in methods:
                try:
                    m(text, out)
                    self._method = m
                    return to_robot_wav(Path(out).read_bytes(), self.gain)
                except Exception as e:  # try the next way
                    last = e
                    log.debug("SAPI via %s failed: %s", m.__name__, e)
            raise RuntimeError(f"SAPI synthesis failed: {last}")
        finally:
            try:
                os.unlink(out)
            except OSError:
                pass

    def _comtypes(self, text: str, path: str) -> None:
        import comtypes  # lazy
        import comtypes.client

        comtypes.CoInitialize()
        try:
            voice = comtypes.client.CreateObject("SAPI.SpVoice")
            if self.voice:
                for tok in voice.GetVoices():
                    if self.voice.lower() in tok.GetDescription().lower():
                        voice.Voice = tok
                        break
            voice.Rate = self.rate
            stream = comtypes.client.CreateObject("SAPI.SpFileStream")
            fmt = comtypes.client.CreateObject("SAPI.SpAudioFormat")
            fmt.Type = 22  # SAFT22kHz16BitMono
            stream.Format = fmt
            stream.Open(path, 3)  # SSFMCreateForWrite
            voice.AudioOutputStream = stream
            voice.Speak(text)
            stream.Close()
        finally:
            comtypes.CoUninitialize()

    def _pyttsx3(self, text: str, path: str) -> None:
        import pyttsx3  # lazy

        engine = pyttsx3.init()
        if self.voice:
            for v in engine.getProperty("voices"):
                if self.voice.lower() in (v.name or "").lower():
                    engine.setProperty("voice", v.id)
        engine.save_to_file(text, path)
        engine.runAndWait()

    def _powershell(self, text: str, path: str) -> None:
        safe = text.replace("'", "''")
        voice = self.voice.replace("'", "''")
        script = (
            "Add-Type -AssemblyName System.Speech;"
            "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
            + (f"$v=$s.GetInstalledVoices()|?{{$_.VoiceInfo.Name -like '*{voice}*'}}|select -First 1;"
               "if($v){$s.SelectVoice($v.VoiceInfo.Name)};" if voice else "")
            + f"$s.Rate={int(self.rate)};"
            "$f=New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(22050,"
            "[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,"
            "[System.Speech.AudioFormat.AudioChannel]::Mono);"
            f"$s.SetOutputToWaveFile('{path}',$f);$s.Speak('{safe}');$s.Dispose()"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, timeout=30, check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )


def make_tts(cfg: TtsCfg, resolve) -> TTSBackend:
    want = cfg.backend
    if want in ("auto", "piper") and cfg.piper_model:
        try:
            t = PiperTTS(resolve(cfg.piper_model), cfg.piper_exe, cfg.volume)
            log.info("TTS: piper (%s)", Path(cfg.piper_model).name)
            return t
        except (ImportError, FileNotFoundError) as e:
            log.warning("TTS: piper unavailable (%s)", e)
    elif want == "piper":
        log.warning("TTS: backend=piper but [tts] piper_model is empty")
    if want in ("auto", "sapi", "piper"):
        try:
            t = SapiTTS(cfg.sapi_voice, cfg.sapi_rate, cfg.volume)
            log.info("TTS: Windows SAPI")
            return t
        except RuntimeError as e:
            log.warning("TTS: SAPI unavailable (%s)", e)
    log.warning("TTS: none - Chip will only log what it would say")
    return NullTTS()
