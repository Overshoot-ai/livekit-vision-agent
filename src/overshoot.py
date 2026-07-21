"""Bridge a video feed into an Overshoot ingest stream.

Overshoot ingests live video over WebRTC: `POST /streams` returns a LiveKit
publish URL + token minted by Overshoot. Frames pushed there can then be
referenced in chat completions as `ovs://streams/{id}?frame_index=-1` (latest
frame) instead of re-uploading a base64 image on every request.

Stream leases last 300s, so a keepalive is posted every 120s. The server only
holds the most recent frames, and `capture_frame` doesn't buffer, a heartbeat
re-pushes the last frame at 8 fps so `frame_index=-1` never goes stale.
"""

import asyncio
import logging
import os

import httpx
from livekit import rtc
from PIL import Image

logger = logging.getLogger("overshoot-bridge")

DEFAULT_BASE_URL = os.environ.get("OVERSHOOT_API_BASE", "https://api.overshoot.ai/v1beta")
KEEPALIVE_INTERVAL = 120.0
HEARTBEAT_FPS = 8.0


class OvershootStreamBridge:
    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        width: int = 1280,
        height: int = 720,
    ):
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30.0,
        )
        self.width = width
        self.height = height
        self.stream_id: str | None = None
        self._publish_url: str | None = None
        self._publish_token: str | None = None
        self._room: rtc.Room | None = None
        self._source: rtc.VideoSource | None = None
        self._last_frame: rtc.VideoFrame | None = None
        self._tasks: list[asyncio.Task] = []

    @property
    def media_url(self) -> str:
        return f"ovs://streams/{self.stream_id}?frame_index=-1"

    async def start(self) -> None:
        resp = await self._http.post("/streams", json={})
        resp.raise_for_status()
        data = resp.json()
        self.stream_id = data["id"]
        self._publish_url = data["publish"]["url"]
        self._publish_token = data["publish"]["token"]

        self._room = rtc.Room()
        await self._room.connect(self._publish_url, self._publish_token)
        self._source = rtc.VideoSource(self.width, self.height)
        track = rtc.LocalVideoTrack.create_video_track("agent", self._source)
        await self._room.local_participant.publish_track(
            track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA)
        )
        self._tasks = [
            asyncio.create_task(self._keepalive_loop()),
            asyncio.create_task(self._heartbeat_loop()),
        ]
        logger.info("publishing to Overshoot stream %s", self.stream_id)

    def push(self, frame: rtc.VideoFrame) -> None:
        """Queue the latest room frame for publishing to Overshoot."""
        rgba = frame.convert(rtc.VideoBufferType.RGBA)
        if (rgba.width, rgba.height) != (self.width, self.height):
            img = Image.frombuffer("RGBA", (rgba.width, rgba.height), bytes(rgba.data))
            img = img.resize((self.width, self.height))
            rgba = rtc.VideoFrame(
                self.width, self.height, rtc.VideoBufferType.RGBA, img.tobytes()
            )
        self._last_frame = rgba

    async def _heartbeat_loop(self) -> None:
        while True:
            if self._last_frame is not None and self._source is not None:
                self._source.capture_frame(self._last_frame)
            await asyncio.sleep(1.0 / HEARTBEAT_FPS)

    async def _keepalive_loop(self) -> None:
        while True:
            await asyncio.sleep(KEEPALIVE_INTERVAL)
            try:
                resp = await self._http.post(f"/streams/{self.stream_id}/keepalive", json={})
                resp.raise_for_status()
                # fresh token, used if the publish connection needs to reconnect
                self._publish_token = resp.json()["publish"]["token"]
            except Exception:
                logger.exception("stream keepalive failed")

    async def aclose(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._room is not None:
            await self._room.disconnect()
        if self.stream_id is not None:
            try:
                await self._http.delete(f"/streams/{self.stream_id}")
            except Exception:
                pass  # streams auto-expire
        await self._http.aclose()
