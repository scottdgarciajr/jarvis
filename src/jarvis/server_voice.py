from __future__ import annotations

import asyncio
import logging
import re
import tempfile
import time
import traceback
import wave
from pathlib import Path

from jarvis.debug_log import DebugLog
from jarvis.events import Event, EventBus
from jarvis.service import AssistantService

log = logging.getLogger(__name__)
WAKE_ONLY = "__jarvis_wake_only__"
FOLLOWUP_TIMEOUT_SECONDS = 7.5
IGNORED_FOLLOWUPS = {"a", "an", "and", "but", "for", "it", "of", "the", "to", "with"}


class ServerMic:
    def __init__(
        self,
        service: AssistantService,
        events: EventBus,
        wake_phrase: str = "jarvis",
        sample_rate: int = 16000,
        seconds_per_chunk: float = 2.4,
        debug_log: DebugLog | None = None,
    ) -> None:
        self.service = service
        self.events = events
        self.wake_phrase = wake_phrase.lower()
        self.sample_rate = sample_rate
        self.seconds_per_chunk = seconds_per_chunk
        self.debug_log = debug_log or DebugLog(None, enabled=False)
        self.task: asyncio.Task | None = None
        self.last_error: str | None = None
        self.awaiting_command = False
        self.awaiting_until = 0.0
        self.last_transcript = ""

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    def status(self) -> dict:
        return {
            "running": self.running,
            "last_error": self.last_error,
            "awaiting_command": self.awaiting_command,
            "last_transcript": self.last_transcript,
        }

    async def start(self) -> dict:
        self._check_dependencies()
        if not self.running:
            self.last_error = None
            self.task = asyncio.create_task(self._loop())
        await self.events.publish(
            Event(
                "assistant_state",
                "idle",
                {"message": "Server microphone ready"},
            )
        )
        return self.status()

    async def stop(self) -> dict:
        if self.task is not None:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        self.task = None
        self.awaiting_command = False
        self.awaiting_until = 0.0
        await self.events.publish(
            Event(
                "assistant_state",
                "idle",
                {"message": "Server microphone stopped"},
            )
        )
        return self.status()

    def _check_dependencies(self) -> None:
        try:
            import faster_whisper  # noqa: F401
            import sounddevice  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "Server mic needs the voice-server extras: pip install -e .[voice-server]"
            ) from exc

    async def _loop(self) -> None:
        from faster_whisper import WhisperModel

        model = None
        while True:
            try:
                if model is None:
                    self.debug_log.write(
                        "server_mic_model",
                        source="server_mic",
                        outcome="loading",
                        model="Systran/faster-whisper-base.en",
                        device="cpu",
                        compute_type="int8",
                    )
                    model = await asyncio.to_thread(
                        WhisperModel,
                        "base.en",
                        device="cpu",
                        compute_type="int8",
                    )
                    self.debug_log.write(
                        "server_mic_model",
                        source="server_mic",
                        outcome="loaded",
                        model="Systran/faster-whisper-base.en",
                    )
                audio_path = await asyncio.to_thread(self._record_chunk)
                transcript = await asyncio.to_thread(self._transcribe, model, audio_path)
                self.last_transcript = transcript
                try:
                    audio_path.unlink()
                except OSError:
                    pass
                command = self._command_from_transcript(transcript)
                if command == WAKE_ONLY:
                    self.debug_log.write(
                        "server_mic_wake",
                        source="server_mic",
                        raw_transcript=transcript,
                        extracted_command=None,
                        outcome="awaiting_followup_command",
                    )
                    self.awaiting_command = True
                    self.awaiting_until = time.monotonic() + FOLLOWUP_TIMEOUT_SECONDS
                    await self.events.publish(
                        Event(
                            "assistant_state",
                            "listening",
                            {
                                "message": "Yes?",
                                "chime": True,
                                "say": "Yes?",
                                "say_state": "listening",
                                "after_say_state": "listening",
                                "after_say_message": "Listening...",
                            },
                        )
                    )
                    continue
                if self.awaiting_command and transcript:
                    if time.monotonic() <= self.awaiting_until:
                        command = self._clean_followup_command(transcript)
                        if self._is_usable_followup(command):
                            self.awaiting_command = False
                            self.awaiting_until = 0.0
                        else:
                            command = None
                    else:
                        self.awaiting_command = False
                        self.awaiting_until = 0.0
                        command = None
                        await self._publish_followup_timeout()
                elif self.awaiting_command and time.monotonic() > self.awaiting_until:
                    self.awaiting_command = False
                    self.awaiting_until = 0.0
                    await self._publish_followup_timeout()
                if command:
                    heard_after_wake = self._command_from_transcript(transcript)
                    self.debug_log.write(
                        "server_mic_command",
                        source="server_mic",
                        raw_transcript=transcript,
                        extracted_command=command,
                        heard_after_wake=(
                            heard_after_wake
                            if heard_after_wake and heard_after_wake != WAKE_ONLY
                            else None
                        ),
                        awaiting_followup=not self._has_wake_phrase(transcript),
                        outcome="started",
                    )
                    if self._has_wake_phrase(transcript):
                        await self.events.publish(
                            Event(
                                "assistant_state",
                                "listening",
                                {"message": "Listening...", "chime": True},
                            )
                        )
                    await self.events.publish(
                        Event(
                            "assistant_state",
                            "thinking",
                            {"message": "Heard server microphone"},
                        )
                    )
                    try:
                        reply, session_id = await self.service.handle(command)
                    except Exception as exc:
                        self.debug_log.write(
                            "server_mic_outcome",
                            source="server_mic",
                            raw_transcript=transcript,
                            extracted_command=command,
                            outcome="error",
                            error={"type": type(exc).__name__, "message": str(exc)},
                            assistant_trace=self.service.last_debug_trace,
                        )
                        raise
                    self.debug_log.write(
                        "server_mic_outcome",
                        source="server_mic",
                        raw_transcript=transcript,
                        extracted_command=command,
                        outcome="reply",
                        session_id=session_id,
                        reply=reply,
                        assistant_trace=self.service.last_debug_trace,
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = str(exc)
                self.debug_log.write(
                    "server_mic_loop",
                    source="server_mic",
                    outcome="error",
                    error={
                        "type": type(exc).__name__,
                        "message": str(exc),
                        "traceback": traceback.format_exc(),
                    },
                )
                log.warning("server microphone failed: %s", exc)
                await self.events.publish(
                    Event(
                        "assistant_state",
                        "error",
                        {"message": f"Server microphone issue: {exc}"},
                    )
                )
                await asyncio.sleep(2.0)

    def _record_chunk(self) -> Path:
        import sounddevice as sd

        frames = int(self.sample_rate * self.seconds_per_chunk)
        recording = sd.rec(
            frames,
            samplerate=self.sample_rate,
            channels=1,
            dtype="int16",
        )
        sd.wait()

        path = Path(tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name)
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(self.sample_rate)
            handle.writeframes(recording.tobytes())
        return path

    def _transcribe(self, model, audio_path: Path) -> str:
        segments, _info = model.transcribe(
            str(audio_path),
            beam_size=1,
            vad_filter=True,
        )
        return " ".join(segment.text for segment in segments).strip()

    def _command_from_transcript(self, transcript: str) -> str | None:
        text = transcript.strip()
        if not text:
            return None
        match = re.search(r"\b(?:hey\s+)?jarvis\b[,.!\s]*(.*)", text, re.I)
        if not match:
            return None
        command = match.group(1).strip()
        return command or WAKE_ONLY

    def _has_wake_phrase(self, transcript: str) -> bool:
        return bool(re.search(r"\b(?:hey\s+)?jarvis\b", transcript, re.I))

    def _clean_followup_command(self, transcript: str) -> str:
        text = transcript.strip()
        text = re.sub(r"^(?:yes|yeah|yep|okay|ok)[,.?!\s]+", "", text, flags=re.I)
        text = re.sub(r"^(?:listening)[,.?!\s]+", "", text, flags=re.I)
        return text.strip()

    def _is_usable_followup(self, command: str) -> bool:
        text = re.sub(r"[^a-z0-9\s]", "", command.lower()).strip()
        if not text or text in IGNORED_FOLLOWUPS:
            return False
        return bool(re.search(r"[a-z0-9]", text))

    async def _publish_followup_timeout(self) -> None:
        await self.events.publish(
            Event(
                "assistant_state",
                "idle",
                {"message": "Ready"},
            )
        )
