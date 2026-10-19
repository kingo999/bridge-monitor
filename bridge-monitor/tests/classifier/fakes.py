"""A deterministic in-process stand-in for the Ollama endpoints the classifier uses."""

import json
import time

import httpx

from bridge.classifier.client import OllamaClassifier
from contracts.classifier import GenerationProfile

MODEL = "gemma4:12b"
DIGEST = "4eb23ef187e2c5462566d6a1d3bbbc2f1346d0b4327cbb66d58fffbcc9b2b05c"
ENDPOINT = "http://127.0.0.1:11435"


def reply_json(route="paid", category="architecture", reason="Needs paid supervision."):
    return json.dumps({"category": category, "route": route, "reason": reason})


class _Stream(httpx.SyncByteStream):
    def __init__(self, frames, pause_after_first=0.0):
        self.frames = frames
        self.pause = pause_after_first
        self.closed = False

    def __iter__(self):
        for index, frame in enumerate(self.frames):
            yield (json.dumps(frame) + "\n").encode()
            if index == 0 and self.pause:
                end = time.monotonic() + self.pause
                while time.monotonic() < end and not self.closed:
                    time.sleep(0.02)

    def close(self):
        self.closed = True


class FakeOllama:
    def __init__(self, text=None, *, mode="ok", digest=DIGEST, model=MODEL, size=8_000, vram=8_000,
                 resident=True, pause=0.0):
        self.text = reply_json() if text is None else text
        self.mode, self.digest, self.model = mode, digest, model
        self.size, self.vram, self.resident, self.pause = size, vram, resident, pause
        self.requests: list[httpx.Request] = []
        self.chat_bodies: list[dict] = []

    @property
    def chat_calls(self) -> int:
        return len(self.chat_bodies)

    def frames(self):
        model = "other-model:1b" if self.mode == "wrong_model" else self.model
        chunks = [self.text[i:i + 24] for i in range(0, len(self.text), 24)] or [""]
        frames = [{"model": model, "message": {"role": "assistant", "content": c}, "done": False}
                  for c in chunks]
        if self.mode == "thinking":
            frames.insert(0, {"model": model, "message": {"thinking": "hmm"}, "done": False})
        if self.mode == "error_frame":
            return frames[:1] + [{"error": "model runner crashed"}]
        if self.mode in ("no_terminal", "slow"):
            return frames
        terminal = {"model": model, "message": {"role": "assistant", "content": ""}, "done": True,
                    "done_reason": "length" if self.mode == "length" else "stop",
                    "total_duration": 1_500_000_000, "load_duration": 40_000_000,
                    "prompt_eval_count": 700, "prompt_eval_duration": 400_000_000,
                    "eval_count": 40, "eval_duration": 800_000_000}
        if self.mode == "no_usage":
            for key in ("prompt_eval_count", "eval_count"):
                terminal.pop(key)
        return frames + [terminal]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if self.mode == "down":
            raise httpx.ConnectError("refused", request=request)
        if path == "/api/version":
            return httpx.Response(200, json={"version": "0.30.9"})
        if path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": self.model, "digest": self.digest}]})
        if path == "/api/ps":
            rows = [{"name": self.model, "digest": self.digest, "size": self.size,
                     "size_vram": self.vram, "context_length": 8192}] if self.resident else []
            return httpx.Response(200, json={"models": rows})
        if path == "/api/chat":
            self.chat_bodies.append(json.loads(request.content))
            if self.mode == "http500":
                return httpx.Response(500, json={"error": "boom"})
            return httpx.Response(200, stream=_Stream(self.frames(), self.pause))
        return httpx.Response(404)

    def classifier(self, **overrides) -> OllamaClassifier:
        options = dict(endpoint=ENDPOINT, model=MODEL, expected_digest=DIGEST,
                       profile=GenerationProfile(), request_timeout=5.0)
        options.update(overrides)
        client = httpx.Client(transport=httpx.MockTransport(self.handler))
        return OllamaClassifier(client=client, **options)
