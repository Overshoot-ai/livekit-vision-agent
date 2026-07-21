# Screen share → vision agent

Point the agent at a screen instead of a camera. Useful for UI monitoring, dashboard watching, or a copilot that knows what you are looking at.

1. Start the agent:

   ```bash
   uv run src/agent.py dev
   ```

2. Start the demo frontend and open http://localhost:8080:

   ```bash
   task frontend
   ```

3. Click **Share screen** and pick a window or display. Structured JSON observations stream back on the `vision` topic.

Useful prompts for screen content (`VISION_PROMPT` in `.env.local`):

```bash
VISION_PROMPT=Name the application on screen and summarize what the user is doing.
VISION_PROMPT=Watch this dashboard. Report any metric that looks anomalous in "alert".
```

For screen-heavy workloads, also try a smaller `VISION_FPS` like `0.5`. Screens change less often than cameras, and each analysis covers more of the UI text anyway.
