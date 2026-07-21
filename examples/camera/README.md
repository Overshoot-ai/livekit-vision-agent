# Webcam → vision agent

Analyze a live camera feed in real time.

1. Start the agent:

   ```bash
   uv run src/agent.py dev
   ```

2. Start the demo frontend and open http://localhost:8080:

   ```bash
   task frontend
   ```

3. Click **Share camera**. The agent joins the room, watches the track, and streams structured JSON observations back on the `vision` topic, you'll see them appear in the right panel with per-request latency.

Point the camera at things and change `VISION_PROMPT` in `.env.local` to steer what the agent looks for, e.g.:

```bash
VISION_PROMPT=Is anyone in frame? What are they holding?
```

Any LiveKit client can stand in for the demo page, the [Agents Playground](https://agents-playground.livekit.io) or your own app using the [LiveKit SDKs](https://docs.livekit.io/reference/).
