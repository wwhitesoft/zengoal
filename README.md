# ZenGoal — Speak it. Ship it.

**Stanford x DeepMind Hackathon · July 19, 2026 · Built with Google Gemini**

Send a **voice memo** with your business idea. ZenGoal:

1. **Transcribes** it with Gemini (multimodal, no separate STT)
2. Distills it into **ONE clear goal**
3. A Gemini **planner** decomposes the goal into a pipeline of tasks
4. Specialized Gemini **agents** (Strategist, Designer, Copywriter, Developer, QA) execute them live on a kanban board
5. Minutes later: a **real, hosted deliverable** at a public preview URL — and the system asks for your approval, like a well-mannered employee.

This is a toy-scale rebuild of the autonomous loop that runs a real company every day:
[Codezen](https://codezen.ai) — an autonomous software development platform operating 14+ SaaS products in production.

## Stack

- **Google Gemini 3 Flash** — transcription, planning, and every worker agent
- Flask (single file, in-memory state) on **Google Cloud Run**
- Vanilla JS live board with in-browser voice recording (MediaRecorder)

## Run

```bash
pip install -r requirements.txt
GEMINI_API_KEY=... python main.py
```

## Deploy

```bash
gcloud run deploy zengoal --source . --region us-central1 --allow-unauthenticated \
  --set-env-vars GEMINI_API_KEY=...,GEMINI_MODEL=gemini-3-flash-preview
```

*We didn't teach AI to chat. We taught it to work — and we taught it manners.*
