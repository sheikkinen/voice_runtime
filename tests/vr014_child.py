"""Test-only spawn entry point for VR-014 speech_check tests.

The supervisor pickles ``functools.partial(entry, scenario)`` into a spawned
child. ``entry`` installs child-side fakes described by the picklable
``scenario`` dict, then calls the real step worker.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time
from typing import Any


def _rec(scenario: dict[str, Any], **event: Any) -> None:
    path = scenario.get("record")
    if path:
        event["pid"] = os.getpid()
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")


class FakeTts:
    def __init__(self, scenario: dict[str, Any], provider: str, behaviour: str):
        self.on_error = None
        self._sc = scenario
        self._p = provider
        self._b = behaviour
        _rec(scenario, event="construct", kind="tts", provider=provider)

    def speak(self, text, session, stop_event=None):
        _rec(self._sc, event="speak", provider=self._p, text=text)
        b = self._b
        n = int(self._sc.get("tts_bytes", 1600))
        if b == "log":
            logging.getLogger("voice_runtime.providers.fake").warning(
                "FAKE-PROVIDER-LOG"
            )
        if b == "permission":
            raise PermissionError("403 permission denied")
        if b == "block_coop":
            while not stop_event.is_set():
                time.sleep(0.01)
            _rec(self._sc, event="stop_event_seen", provider=self._p)
            return {"last_spoken": text, "interrupted": True}
        if b == "block_hard":
            time.sleep(60)
        if b == "ignore_term":
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            time.sleep(60)
        time.sleep(float(self._sc.get("tts_delay_s", 0.0)))
        for i in range(0, n, 4000):
            session.tap_agent(b"\x7f" * min(4000, n - i))
        if b == "on_error" and self.on_error:
            self.on_error("synth_failed")
        if b == "error_result":
            return {"last_spoken": text, "error": "synth_failed"}
        return {"last_spoken": text, "interrupted": False}


class FakeStt:
    def __init__(self, scenario: dict[str, Any], provider: str, behaviour: str):
        self.on_committed = None
        self.on_recognizing = None
        self.on_error = None
        self._sc = scenario
        self._p = provider
        self._b = behaviour
        self._task: asyncio.Task | None = None
        self.received = b""
        _rec(scenario, event="construct", kind="stt", provider=provider)

    def set_speaking(self, speaking: bool) -> None:
        pass

    async def start(self, inbound_queue) -> None:
        _rec(self._sc, event="start", provider=self._p)
        if self._b == "start_hang":
            await asyncio.Event().wait()
        self._task = asyncio.ensure_future(self._reader(inbound_queue))

    async def _reader(self, q) -> None:
        frames = 0
        while True:
            item = await q.get()
            if item is None:
                break
            self.received += item
            frames += 1
            if frames <= int(self._sc.get("recognizing", 0)) and self.on_recognizing:
                self.on_recognizing("")
        _rec(
            self._sc,
            event="received",
            provider=self._p,
            head=self.received[:64].hex(),
            n=len(self.received),
        )
        if self._b in ("none", "cancel_coop", "stop_block"):
            return
        gap = float(self._sc.get("commit_gap_s", 0.0))
        for i, text in enumerate(self._sc.get("commits", ["hei"])):
            if i and gap:
                await asyncio.sleep(gap)
            self.on_committed(text)
        if self._b == "on_error":
            self.on_error("stt_broke")

    async def stop(self) -> None:
        _rec(self._sc, event="stop", provider=self._p)
        if self._task:
            self._task.cancel()
        if self._b == "stop_raises":
            raise RuntimeError("stop failed")
        if self._b == "stop_block":
            time.sleep(60)


def _factory(scenario: dict[str, Any], kind: str, real, fake_cls):
    fakes = scenario.get(kind, {})

    def create(provider: str = "elevenlabs", **kwargs: Any):
        behaviour = fakes.get(provider)
        if behaviour == "mock":
            from voice_runtime.mock.tts import MockTts

            return MockTts()
        if behaviour == "import_error":
            raise ImportError("broken sdk")
        if behaviour == "syntax_error":
            raise SyntaxError("broken sdk")
        if behaviour:
            return fake_cls(scenario, provider, behaviour)
        return real(provider, **kwargs)

    return create


def _install(scenario: dict[str, Any]) -> None:
    import voice_runtime.stt as stt_mod
    import voice_runtime.tts as tts_mod
    from voice_runtime import speech_check as sc

    for name in scenario.get("unset", []):
        os.environ.pop(name, None)
    os.environ.update(scenario.get("env", {}))
    for name, value in scenario.get("timing", {}).items():
        setattr(sc, name, value)

    tts_mod.create_tts = _factory(scenario, "tts", tts_mod.create_tts, FakeTts)
    stt_mod.create_stt = _factory(scenario, "stt", stt_mod.create_stt, FakeStt)

    absent = set(scenario.get("absent", []))
    sc._find_spec = lambda name: None if name in absent else object()

    adc = scenario.get("adc", "ok")

    def adc_default() -> None:
        if adc == "missing":
            from google.auth.exceptions import DefaultCredentialsError

            raise DefaultCredentialsError("no ADC")
        if adc == "error":
            raise RuntimeError("metadata server exploded")

    sc._adc_default = adc_default

    patch = scenario.get("patch")
    if patch == "commit_raises":

        def committed(self, text):
            raise RuntimeError("callback broke")

        sc._SttCapture.committed = committed
    elif patch == "feed_raises":

        async def feed(*args, **kwargs):
            raise RuntimeError("feed broke")

        sc._feed = feed


def entry(
    scenario: dict[str, Any], spec: dict[str, Any], conn: Any, cancel: Any
) -> None:
    """Install fakes inside the child, then run the real step worker."""
    _install(scenario)
    from voice_runtime import speech_check as sc

    sc._child_main(spec, conn, cancel)
