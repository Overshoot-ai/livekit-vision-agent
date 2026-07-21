"""Real-time vision agent for LiveKit rooms, powered by Overshoot.

The agent joins a LiveKit room, watches the first video track it finds
(camera, screen share, or an RTSP bridge), sends it to a vision-language
model on Overshoot, and publishes structured JSON observations back into
the room on the `vision` text-stream topic.

Two ingest modes (OVERSHOOT_INGEST_MODE):
  frames  (default) — encode the latest frame as JPEG and send it inline
          with each request. Simplest; no extra moving parts.
  stream  — republish the video into an Overshoot ingest stream over
          WebRTC and reference it as ovs://streams/{id}?frame_index=-1.
          Requests carry no pixels at all; lowest per-request overhead.
"""

import asyncio
import base64
import io
import json
import logging
import os
import time

from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import AutoSubscribe, JobContext, WorkerOptions, cli
from openai import AsyncOpenAI
from PIL import Image

from overshoot import OvershootStreamBridge

load_dotenv(".env.local")
load_dotenv()

logger = logging.getLogger("vision-agent")

OVERSHOOT_API_BASE = os.environ.get("OVERSHOOT_API_BASE", "https://api.overshoot.ai/v1beta")
OVERSHOOT_MODEL = os.environ.get("OVERSHOOT_MODEL", "google/gemma-4-26B-A4B-it")
INGEST_MODE = os.environ.get("OVERSHOOT_INGEST_MODE", "frames")
VISION_FPS = float(os.environ.get("VISION_FPS", "2"))
VISION_PROMPT = os.environ.get("VISION_PROMPT", "Describe what is happening in the video.")
RESULT_TOPIC = "vision"

DEFAULT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "one sentence describing the scene"},
        "objects": {"type": "array", "items": {"type": "string"}},
        "activity": {"type": "string", "description": "what is currently happening"},
        "alert": {
            "type": ["string", "null"],
            "description": "anything unusual or noteworthy, null otherwise",
        },
    },
    "required": ["summary", "objects", "activity", "alert"],
}


def _load_schema() -> dict:
    raw = os.environ.get("VISION_SCHEMA")
    return json.loads(raw) if raw else DEFAULT_SCHEMA


def _system_prompt(schema: dict) -> str:
    # gemma models are much slower under strict json_schema constrained
    # decoding with vision input, so we use json_object mode and put the
    # schema in the prompt instead.
    return (
        "You are a real-time vision analyst watching a live video feed. "
        "Respond with a single JSON object matching this JSON schema, and nothing else:\n"
        f"{json.dumps(schema)}"
    )


def _frame_to_data_url(frame: rtc.VideoFrame, max_width: int = 1280) -> str:
    rgba = frame.convert(rtc.VideoBufferType.RGBA)
    img = Image.frombuffer("RGBA", (rgba.width, rgba.height), bytes(rgba.data)).convert("RGB")
    if img.width > max_width:
        img = img.resize((max_width, int(img.height * max_width / img.width)))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


class VisionAgent:
    def __init__(self, room: rtc.Room):
        self.room = room
        self.schema = _load_schema()
        self.latest_frame: rtc.VideoFrame | None = None
        self.bridge: OvershootStreamBridge | None = None
        # never use timeout=None here: a stalled request would hang the loop
        self.client = AsyncOpenAI(
            api_key=os.environ["OVERSHOOT_API_KEY"],
            base_url=OVERSHOOT_API_BASE,
            timeout=30.0,
            max_retries=1,
        )
        self._reader_task: asyncio.Task | None = None

    async def start(self) -> None:
        if INGEST_MODE == "stream":
            self.bridge = OvershootStreamBridge(api_key=os.environ["OVERSHOOT_API_KEY"])
            await self.bridge.start()

        self.room.on("track_subscribed", self._on_track_subscribed)
        for participant in self.room.remote_participants.values():
            for pub in participant.track_publications.values():
                if pub.track and pub.track.kind == rtc.TrackKind.KIND_VIDEO:
                    self._watch(pub.track)

        await self._analysis_loop()

    def _on_track_subscribed(self, track, publication, participant) -> None:
        if track.kind == rtc.TrackKind.KIND_VIDEO:
            logger.info("watching video track from %s", participant.identity)
            self._watch(track)

    def _watch(self, track: rtc.Track) -> None:
        if self._reader_task is not None and not self._reader_task.done():
            return  # already watching a track
        self._reader_task = asyncio.create_task(self._read_frames(track))

    async def _read_frames(self, track: rtc.Track) -> None:
        async for event in rtc.VideoStream(track):
            self.latest_frame = event.frame
            if self.bridge is not None:
                self.bridge.push(event.frame)
        self._reader_task = None

    async def _analysis_loop(self) -> None:
        interval = 1.0 / VISION_FPS
        while True:
            started = time.perf_counter()
            if self.latest_frame is not None:
                try:
                    await self._analyze_once()
                except Exception:
                    logger.exception("analysis failed")
            elapsed = time.perf_counter() - started
            await asyncio.sleep(max(0.0, interval - elapsed))

    async def _analyze_once(self) -> None:
        if self.bridge is not None:
            image_url = self.bridge.media_url
        else:
            image_url = _frame_to_data_url(self.latest_frame)

        started = time.perf_counter()
        response = await self.client.chat.completions.create(
            model=OVERSHOOT_MODEL,
            max_completion_tokens=512,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _system_prompt(self.schema)},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": VISION_PROMPT},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                },
            ],
        )
        latency_ms = round((time.perf_counter() - started) * 1000)

        try:
            result = json.loads(response.choices[0].message.content)
        except (json.JSONDecodeError, TypeError):
            logger.warning("model returned non-JSON output, skipping frame")
            return
        result["_overshoot"] = {"model": OVERSHOOT_MODEL, "latency_ms": latency_ms}
        await self.room.local_participant.send_text(json.dumps(result), topic=RESULT_TOPIC)
        logger.info("published result (%dms): %s", latency_ms, result.get("summary"))

    async def aclose(self) -> None:
        if self.bridge is not None:
            await self.bridge.aclose()


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect(auto_subscribe=AutoSubscribe.VIDEO_ONLY)
    logger.info("connected to room %s (mode=%s, model=%s)", ctx.room.name, INGEST_MODE, OVERSHOOT_MODEL)
    agent = VisionAgent(ctx.room)
    try:
        await agent.start()
    finally:
        await agent.aclose()


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
