# RTSP camera → vision agent

Bridge an RTSP feed, IP camera, NVR, drone, CCTV, into the LiveKit room so the agent can analyze it.

LiveKit Ingress pulls HTTP/SRT sources but not RTSP, so this example decodes the stream locally with [PyAV](https://github.com/PyAV-Org/PyAV) and publishes it as a WebRTC track:

```bash
uv sync --extra rtsp
uv run examples/rtsp/publish_rtsp.py rtsp://user:pass@camera.local:554/stream
```

With the agent running (`uv run src/agent.py dev`), structured JSON observations for the camera feed are published to the room on the `vision` topic. Open the demo frontend (`task frontend`) to watch them live, or consume them from any LiveKit client.

**Alternative without this script:** if your source can push RTMP/WHIP (or you can run ffmpeg somewhere), use [LiveKit Ingress](https://docs.livekit.io/home/ingress/overview/) instead:

```bash
lk ingress create --type rtmp --room overshoot-vision-demo --identity rtsp-camera
ffmpeg -rtsp_transport tcp -i rtsp://camera.local:554/stream -c:v libx264 -preset veryfast -f flv <ingress-rtmp-url>
```
