"""Bridge an RTSP feed (IP camera, NVR, drone) into a LiveKit room.

LiveKit Ingress pulls from HTTP/SRT sources but not RTSP, so this script
decodes the RTSP stream locally with PyAV and publishes it as a WebRTC
video track. The vision agent then picks it up like any other track.

Usage:
    uv sync --extra rtsp
    uv run examples/rtsp/publish_rtsp.py rtsp://user:pass@camera.local:554/stream

Requires LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET in .env.local.
"""

import argparse
import asyncio
import logging
import os
import pathlib

import av
from dotenv import load_dotenv
from livekit import api, rtc

ROOT = pathlib.Path(__file__).parent.parent.parent
load_dotenv(ROOT / ".env.local")
load_dotenv(ROOT / ".env")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("rtsp-bridge")


def make_token(room: str) -> str:
    return (
        api.AccessToken()
        .with_identity("rtsp-bridge")
        .with_grants(api.VideoGrants(room_join=True, room=room))
        .to_jwt()
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="RTSP URL to bridge")
    parser.add_argument("--room", default=os.environ.get("DEMO_ROOM", "overshoot-vision-demo"))
    args = parser.parse_args()

    room = rtc.Room()
    await room.connect(os.environ["LIVEKIT_URL"], make_token(args.room))
    logger.info("connected to room %s", args.room)

    container = av.open(args.url, options={"rtsp_transport": "tcp"}, timeout=10.0)
    video = container.streams.video[0]
    width, height = video.codec_context.width, video.codec_context.height
    logger.info("rtsp source open: %dx%d", width, height)

    source = rtc.VideoSource(width, height)
    track = rtc.LocalVideoTrack.create_video_track("rtsp", source)
    await room.local_participant.publish_track(
        track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA)
    )

    loop = asyncio.get_running_loop()

    def decode() -> None:
        # a live RTSP source paces itself; just decode and push
        for frame in container.decode(video):
            img = frame.to_image().convert("RGBA")
            vf = rtc.VideoFrame(img.width, img.height, rtc.VideoBufferType.RGBA, img.tobytes())
            loop.call_soon_threadsafe(source.capture_frame, vf)

    try:
        await asyncio.to_thread(decode)
    finally:
        container.close()
        await room.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
