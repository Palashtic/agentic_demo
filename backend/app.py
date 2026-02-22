import io
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib import error, request

from fastapi import FastAPI, File, Form, UploadFile
from fastapi import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import requests

from docx import Document
from pypdf import PdfReader

API_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENAI_AUDIO_TRANSCRIBE_URL = "https://api.openai.com/v1/audio/transcriptions"

MOCK_CASE_STUDIES = """\
Acme Logistics – 32% close rate improvement | keywords: slow follow-ups, crm hygiene, pipeline visibility
Beta Retail – 50% increase in customer retention | keywords: lead nurturing, follow-up automation, customer retention
Gamma SaaS – 28% faster sales cycle | keywords: reporting, forecasting, stakeholder alignment
Delta Healthcare – 20% increase in demos booked | keywords: inbound leads, routing, speed-to-lead
Epsilon Manufacturing – 35% reduction in manual work | keywords: data entry, integrations, workflow automation
"""

MOCK_PRICING = """\
Starter Plan | suitable for small teams with basic CRM needs and simple pipeline tracking
Growth Plan | suitable for mid-sized teams needing automation and reporting dashboards
Scale Plan | suitable for larger orgs needing advanced integrations, governance, and forecasting
"""

app = FastAPI(title="Agentic Demo API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def load_env() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


def _extract_openrouter_content(data: dict) -> str:
    message = data.get("choices", [{}])[0].get("message", {})
    content = message.get("content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
        return "".join(parts).strip()
    return ""


def _coerce_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    # Best-effort: grab the first {...} block if the model wrapped it.
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        raise ValueError("Model did not return JSON object.")
    parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("Model returned non-object JSON.")
    return parsed


def _read_pdf_bytes(pdf_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    pages = []
    for page in reader.pages:
        pages.append(page.extract_text() or "")
    return "\n".join(pages).strip()


def _read_docx_bytes(docx_bytes: bytes) -> str:
    doc = Document(io.BytesIO(docx_bytes))
    return "\n".join(p.text for p in doc.paragraphs).strip()


def _normalize_transcript(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    # Avoid runaway context; keep head+tail.
    if len(text) <= 40_000:
        return text
    head = text[:20_000]
    tail = text[-20_000:]
    return head + "\n\n[...TRUNCATED...]\n\n" + tail


def _ensure_mock_research_files(case_path: Path, pricing_path: Path) -> None:
    if not case_path.exists():
        case_path.write_text(MOCK_CASE_STUDIES, encoding="utf-8")
    if not pricing_path.exists():
        pricing_path.write_text(MOCK_PRICING, encoding="utf-8")


def _tokenize(text: str) -> set[str]:
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9\s\-]", " ", text)
    tokens = re.split(r"[\s\-]+", text)
    return {t for t in tokens if t and len(t) >= 3}


def _normalize_keywords(text: str) -> set[str]:
    parts = [p.strip().lower() for p in (text or "").split(",") if p.strip()]
    out: set[str] = set()
    for p in parts:
        out |= _tokenize(p)
    return out


def _parse_case_studies(lines: list[str]) -> list[dict[str, Any]]:
    studies: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        left, sep, right = line.partition("|")
        title = left.strip()
        keywords: set[str] = set()
        if sep:
            m = re.search(r"keywords\s*:\s*(.*)$", right.strip(), flags=re.IGNORECASE)
            if m:
                keywords = _normalize_keywords(m.group(1))
        studies.append({"title": title, "tokens": keywords})
    return studies


def _parse_pricing(lines: list[str]) -> list[dict[str, Any]]:
    tiers: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        name, sep, desc = line.partition("|")
        tier_name = name.strip()
        description = desc.strip() if sep else ""
        tiers.append({"tier": tier_name, "tokens": _tokenize(tier_name + " " + description)})
    return tiers


def _best_overlap(query_tokens: set[str], items: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    best = None
    best_score = -1
    for item in items:
        overlap = query_tokens & item.get(key, set())
        score = len(overlap)
        if score > best_score:
            best = item
            best_score = score
    return best


def researcher_match(key_pain_points: str) -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parent.parent
    case_path = repo_root / "case_studies.txt"
    pricing_path = repo_root / "pricing.txt"
    _ensure_mock_research_files(case_path, pricing_path)

    query_tokens = _tokenize(key_pain_points)

    studies = _parse_case_studies(case_path.read_text(encoding="utf-8").splitlines())
    tiers = _parse_pricing(pricing_path.read_text(encoding="utf-8").splitlines())

    best_case = _best_overlap(query_tokens, studies, "tokens")
    best_tier = _best_overlap(query_tokens, tiers, "tokens")

    selected_case = best_case["title"] if best_case else "unknown"
    selected_tier = best_tier["tier"] if best_tier else "unknown"

    return {
        "selected_case_study": selected_case,
        "pricing_tier": selected_tier,
        "justification": "Selected by keyword overlap between pain points, case study keywords, and pricing description.",
    }


def call_openrouter_analyst(
    api_key: str, model: str, transcript: str, fallback_model: str | None = None
) -> tuple[int, dict]:
    system_prompt = (
        "You are Agent 1 (Analyst), a sales analyst AI. "
        "Extract structured insights from the sales call transcript. "
        "Return valid JSON only. No markdown, no extra keys, no explanations."
    )
    user_prompt = (
        "Analyze the transcript and return a JSON object with exactly these keys:\n"
        "customer_company (string)\n"
        "decision_maker (string or 'unknown')\n"
        "key_pain_points (list of strings)\n"
        "buying_intent_score (number between 0 and 1)\n"
        "recommend_next_steps (list of strings)\n\n"
        "Rules:\n"
        "- If missing, set customer_company to 'unknown'\n"
        "- decision_maker must be a string or 'unknown'\n"
        "- buying_intent_score must be a JSON number between 0 and 1 (examples: 0, 0.25, 0.8, 1). Never output invalid numbers like 0.\n"
        "- key_pain_points and recommend_next_steps must be arrays of strings\n"
        "- Output must be a single JSON object only (no code fences, no extra text)\n\n"
        f"Transcript:\n{transcript}"
    )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 1200,
        "temperature": 0,
        # Best-effort: supported by OpenAI-style models; others may ignore.
        "response_format": {"type": "json_object"},
    }

    req = request.Request(
        url=API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "http://127.0.0.1:5173",
            "X-Title": "Agentic Demo - Analyst Agent",
        },
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            finish_reason = data.get("choices", [{}])[0].get("finish_reason")
            content = _extract_openrouter_content(data)
            try:
                parsed = _coerce_json_object(content)
            except Exception as parse_exc:
                # Optionally retry with a fallback model if configured.
                if fallback_model and fallback_model != model:
                    return call_openrouter_analyst(api_key, fallback_model, transcript, None)
                raise ValueError(
                    "Model did not return valid JSON object. "
                    f"finish_reason={finish_reason!r} content_head={content[:400]!r} content_tail={content[-200:]!r}"
                ) from parse_exc
            return resp.status, parsed
    except error.HTTPError as exc:
        raw_body = exc.read().decode("utf-8") if exc.fp else "{}"
        try:
            data = json.loads(raw_body)
        except json.JSONDecodeError:
            data = {"error": {"message": raw_body}}
        err_msg = data.get("error", {}).get("message", "Unknown API error")
        return exc.code, {"error": err_msg, "status": exc.code, "raw": data}
    except Exception as exc:
        return 500, {"error": str(exc), "status": 500}


def _as_string(value: Any, default: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def _as_list_of_strings(value: Any) -> list[str]:
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
        return out
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _as_score_0_1(value: Any) -> float:
    try:
        score = float(value)
    except Exception:
        score = 0.0
    if score < 0.0:
        return 0.0
    if score > 1.0:
        return 1.0
    return score


def normalize_analyst_output(obj: dict[str, Any]) -> dict[str, Any]:
    customer_company = _as_string(obj.get("customer_company"), "unknown")
    decision_maker = _as_string(obj.get("decision_maker"), "unknown")
    key_pain_points = _as_list_of_strings(obj.get("key_pain_points"))
    buying_intent_score = _as_score_0_1(obj.get("buying_intent_score"))
    recommend_next_steps = _as_list_of_strings(obj.get("recommend_next_steps"))

    return {
        "customer_company": customer_company,
        "decision_maker": decision_maker if decision_maker else "unknown",
        "key_pain_points": key_pain_points,
        "buying_intent_score": buying_intent_score,
        "recommend_next_steps": recommend_next_steps,
    }


def _parse_json_or_text(value: str) -> Any:
    value = (value or "").strip()
    if not value:
        return ""
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def call_openrouter_closer(api_key: str, model: str, analyst: Any, research: Any) -> tuple[int, dict]:
    case_study_title = ""
    pricing_tier = ""
    if isinstance(research, dict):
        case_study_title = _as_string(research.get("selected_case_study"), "")
        pricing_tier = _as_string(research.get("pricing_tier"), "")

    pain_points: list[str] = []
    decision_maker = "unknown"
    customer_company = "unknown"
    if isinstance(analyst, dict):
        pain_points = _as_list_of_strings(analyst.get("key_pain_points"))
        decision_maker = _as_string(analyst.get("decision_maker"), "unknown")
        customer_company = _as_string(analyst.get("customer_company"), "unknown")

    def safe_template() -> dict:
        subject = f"Next steps on {', '.join(pain_points[:1]) or 'your priorities'}"
        greeting = "Hi"
        if decision_maker != "unknown":
            greeting = f"Hi {decision_maker}"
        elif customer_company != "unknown":
            greeting = "Hi there"

        pain_sentence = ""
        if pain_points:
            pain_sentence = (
                "Thanks again for the time. From our call, it sounds like the main challenges are "
                + ", ".join(pain_points[:3])
                + ".\n\n"
            )

        case_sentence = ""
        if case_study_title:
            case_sentence = f"Relevant example: {case_study_title}.\n"

        pricing_sentence = ""
        if pricing_tier:
            pricing_sentence = f"Based on what you shared, I suspect the {pricing_tier} could be a fit.\n"

        next_steps = []
        if isinstance(analyst, dict):
            next_steps = _as_list_of_strings(analyst.get("recommend_next_steps"))
        next_steps = next_steps[:4] or [
            "Confirm the priority pain points and success criteria",
            "Review your current workflow/tools at a high level",
            "Align on timeline and stakeholders",
        ]

        bullets = "\n".join(f"- {s}" for s in next_steps[:4])

        body = (
            f"{greeting},\n\n"
            + pain_sentence
            + (case_sentence + pricing_sentence + "\n" if (case_sentence or pricing_sentence) else "")
            + "Next steps:\n"
            + bullets
            + "\n\n"
            + "Would a quick 15-minute follow-up work? I can do Tue 10:30am or Wed 2:00pm. "
            + "If someone else should join, feel free to loop them in.\n\n"
            + "Best,\n"
            + "India"
        )
        return {"subject": subject, "body": body}

    def is_compliant(subject: str, body: str) -> bool:
        if not subject or not body:
            return False
        # Must include next steps section + bullets.
        if "next steps" not in body.lower():
            return False
        if body.count("\n- ") + body.count("\r\n- ") < 2:
            return False
        # Must mention case study title if provided.
        if case_study_title and case_study_title not in body:
            return False
        # Must reference at least one pain point if available.
        if pain_points:
            if not any(pp.lower() in body.lower() for pp in pain_points[:3]):
                return False
        # Avoid obvious hallucinations for this demo.
        forbidden = ["attached", "attachment", "%", "reduced by", "increased by"]
        if any(f in body.lower() for f in forbidden):
            return False
        return True

    system_prompt = (
        "You are Agent 3 (Closer), a senior sales representative. "
        "You write concise, professional, high-conversion follow-up emails. "
        "Return valid JSON only with keys: subject, body. No markdown, no extra keys."
    )
    prompt_parts: list[str] = [
        "Using the information below, draft a professional follow-up email.\n\n",
        "Hard requirements for the email body:\n",
        "- Must reference 1-3 pain points naturally\n",
        "- Must include a 'Next steps:' section with 2-4 bullet points\n",
        "- Must propose a clear CTA (e.g., 15-min call) with 2 scheduling options\n",
    ]
    if case_study_title:
        prompt_parts.append(f"- Must mention this case study title verbatim: {case_study_title}\n")
    if pricing_tier:
        prompt_parts.append(f"- If appropriate, mention the recommended pricing tier: {pricing_tier}\n")
    prompt_parts.extend(
        [
            "\n",
            "The email should:\n",
            "- Reference the customer's pain points naturally\n",
            "- Mention the relevant case study\n",
            "- Propose a clear next step\n",
            "- Be friendly, concise, and professional\n",
            "- Include suggested next steps inside the email body\n\n",
            "Customer Analysis (Agent 1):\n",
            f"{json.dumps(analyst, ensure_ascii=False) if not isinstance(analyst, str) else analyst}\n\n",
            "Internal Context (Agent 2):\n",
            f"{json.dumps(research, ensure_ascii=False) if not isinstance(research, str) else research}\n\n",
            "Output JSON format:\n",
            "{\"subject\":\"...\",\"body\":\"...\"}\n",
        ]
    )
    user_prompt = "".join(prompt_parts)

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 600,
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }

    req = request.Request(
        url=API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "http://127.0.0.1:5173",
            "X-Title": "Agentic Demo - Closer Agent",
        },
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            content = _extract_openrouter_content(data)
            parsed = _coerce_json_object(content)
            subject = _as_string(parsed.get("subject"), "")
            body = _as_string(parsed.get("body"), "")
            if not is_compliant(subject, body):
                return resp.status, safe_template()
            return resp.status, {"subject": subject, "body": body}
    except error.HTTPError as exc:
        raw_body = exc.read().decode("utf-8") if exc.fp else "{}"
        try:
            data = json.loads(raw_body)
        except json.JSONDecodeError:
            data = {"error": {"message": raw_body}}
        err_msg = data.get("error", {}).get("message", "Unknown API error")
        return exc.code, {"error": err_msg, "status": exc.code, "raw": data}
    except Exception as exc:
        # Fall back to a safe template if the model call fails.
        return 200, safe_template()


@app.get("/health")
def health() -> dict:
    return {"ok": True}

@app.post("/stt/transcribe")
async def stt_transcribe(file: UploadFile = File(...)) -> dict:
    """
    Speech-to-text for audio uploads.
    Requires OPENAI_API_KEY in .env. Uses OPENAI_STT_MODEL (default whisper-1).
    """
    load_env()
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=400, detail="Missing OPENAI_API_KEY in .env for /stt/transcribe")

    model = os.environ.get("OPENAI_STT_MODEL", "whisper-1")
    blob = await file.read()
    if not blob:
        raise HTTPException(status_code=400, detail="Empty audio file")

    headers = {"Authorization": f"Bearer {api_key}"}
    files = {
        "file": (file.filename or "audio.webm", blob, file.content_type or "application/octet-stream")
    }
    data = {"model": model}

    try:
        resp = requests.post(
            OPENAI_AUDIO_TRANSCRIBE_URL,
            headers=headers,
            files=files,
            data=data,
            timeout=60,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"STT request failed: {exc}") from exc

    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    payload = resp.json()
    text = payload.get("text", "")
    return {"transcript_text": text}


@app.post("/agent/analyst/analyze")
async def analyst_analyze(
    transcript_text: str = Form(default=""),
    file: UploadFile | None = File(default=None),
) -> dict:
    load_env()
    api_key = os.environ.get("OPENROUTER_API_KEY")
    model = os.environ.get("OPENROUTER_MODEL", "openrouter/auto")
    fallback_model = os.environ.get("OPENROUTER_FALLBACK_MODEL")
    if not api_key:
        return {"error": "Missing OPENROUTER_API_KEY in .env"}

    extracted = ""
    if file is not None:
        filename = (file.filename or "").lower()
        blob = await file.read()
        if filename.endswith(".pdf"):
            extracted = _read_pdf_bytes(blob)
        elif filename.endswith(".docx"):
            extracted = _read_docx_bytes(blob)
        elif filename.endswith(".txt"):
            extracted = blob.decode("utf-8", errors="replace")
        else:
            return {"error": "Unsupported file type. Use PDF, DOCX, or TXT."}

    transcript = _normalize_transcript(extracted or transcript_text)
    if not transcript:
        return {"error": "No transcript provided."}

    status, payload = call_openrouter_analyst(api_key, model, transcript, fallback_model)
    if status >= 400:
        return payload
    if not isinstance(payload, dict):
        return {"error": "Model returned invalid JSON."}
    return normalize_analyst_output(payload)


@app.post("/agent/closer/email")
async def closer_email(
    analyst_json: str = Form(default=""),
    research_output: str = Form(default=""),
) -> dict:
    load_env()
    api_key = os.environ.get("OPENROUTER_API_KEY")
    model = os.environ.get("OPENROUTER_MODEL", "openrouter/auto")
    if not api_key:
        return {"error": "Missing OPENROUTER_API_KEY in .env"}

    analyst = _parse_json_or_text(analyst_json)
    research = _parse_json_or_text(research_output)
    if not analyst and not research:
        return {"error": "Provide analyst_json and/or research_output."}

    status, payload = call_openrouter_closer(api_key, model, analyst, research)
    if status >= 400:
        return payload
    return payload


@app.get("/agent/closer/email")
async def closer_email_get() -> dict:
    raise HTTPException(status_code=405, detail="Use POST with form fields: analyst_json, research_output")


@app.post("/agent/researcher/match")
async def researcher(
    analyst_json: str = Form(default=""),
    key_pain_points: str = Form(default=""),
) -> dict:
    # Allows either raw pain points text or Agent 1 JSON.
    parsed = _parse_json_or_text(analyst_json)
    if not key_pain_points and isinstance(parsed, dict):
        kpps = _as_list_of_strings(parsed.get("key_pain_points"))
        key_pain_points = ", ".join(kpps)

    if not key_pain_points.strip():
        return {"error": "Provide key_pain_points or analyst_json with key_pain_points."}

    return researcher_match(key_pain_points)


# Serve the React UI from the backend root. Mount this last so API routes win.
_FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if _FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")
