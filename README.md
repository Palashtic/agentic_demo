# Agentic Demo:

Sales Call Transcript (text)
        │
        ▼
Agent 1 — Analyst
(OpenRouter → LLM, JSON enforced)
• Extracts pain points
• Buying intent
• Action items
        │  (structured JSON)
        ▼
Agent 2 — Researcher
(Deterministic code + KB)
• Reads local knowledge base
• Selects case study
• Selects pricing tier
        │  (grounded context)
        ▼
Agent 3 — Closer
(OpenRouter → LLM)
• Drafts follow-up email
• Suggests next steps
        │
        ▼
Final Output
• Structured insight
• Personalized email


## What this demonstrates

This project shows how to:
- Orchestrate multiple specialized agents
- Pass structured context between agents
- Keep agents small, debuggable, and composable
- Rapidly ship an AI-native workflow using modern tools

## 1) Start backend

```powershell
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn backend.app:app --reload --host 127.0.0.1 --port 8001
```

## 2) Open UI

- http://127.0.0.1:8001/

Then click **Analyze Transcript**.

## Environment

`.env` should contain:

```env
OPENROUTER_API_KEY=your_key_here
OPENROUTER_MODEL=openrouter/auto
```

You can change `OPENROUTER_MODEL` to a specific model on OpenRouter.

## Voice

If you want voice recording/upload to auto-transcribe into text, set:

```env
OPENAI_API_KEY=your_openai_key_here
OPENAI_STT_MODEL=whisper-1
```

Then the UI will call:
- `POST /stt/transcribe` with the audio file, and feed the transcript into Agent 1 automatically.

## API

- `GET http://127.0.0.1:8001/health`
- `POST http://127.0.0.1:8001/stt/transcribe` (audio file; requires `OPENAI_API_KEY`)
- `POST http://127.0.0.1:8001/agent/analyst/analyze` (multipart form: `file` and/or `transcript_text`)
- `POST http://127.0.0.1:8001/agent/researcher/match` (form: `key_pain_points` or `analyst_json`)
- `POST http://127.0.0.1:8001/agent/closer/email` (form: `analyst_json` and/or `research_output`)
