import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib import error, request


API_URL = "https://openrouter.ai/api/v1/chat/completions"

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


def load_env(env_path: Path) -> None:
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


def ensure_mock_files(case_studies_path: Path, pricing_path: Path) -> None:
    if not case_studies_path.exists():
        case_studies_path.write_text(MOCK_CASE_STUDIES, encoding="utf-8")
    if not pricing_path.exists():
        pricing_path.write_text(MOCK_PRICING, encoding="utf-8")


def tokenize(text: str) -> set[str]:
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9\s\-]", " ", text)
    tokens = re.split(r"[\s\-]+", text)
    return {t for t in tokens if t and len(t) >= 3}


def normalize_keywords(text: str) -> set[str]:
    # Allows comma-separated keyword phrases; also breaks into tokens for overlap.
    parts = [p.strip().lower() for p in (text or "").split(",") if p.strip()]
    out: set[str] = set()
    for p in parts:
        out |= tokenize(p)
    return out


def parse_case_studies(lines: list[str]) -> list[dict[str, Any]]:
    studies: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # "<title> | keywords: a, b, c"
        left, sep, right = line.partition("|")
        title = left.strip()
        keywords = set()
        if sep:
            m = re.search(r"keywords\s*:\s*(.*)$", right.strip(), flags=re.IGNORECASE)
            if m:
                keywords = normalize_keywords(m.group(1))
        studies.append({"title": title, "keywords": keywords, "raw": line})
    return studies


def parse_pricing(lines: list[str]) -> list[dict[str, Any]]:
    tiers: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        name, sep, desc = line.partition("|")
        tier_name = name.strip()
        description = desc.strip() if sep else ""
        tiers.append(
            {
                "tier": tier_name,
                "description": description,
                "tokens": tokenize(tier_name + " " + description),
                "raw": line,
            }
        )
    return tiers


def best_overlap_match(query_tokens: set[str], items: list[dict[str, Any]], tokens_key: str) -> dict[str, Any] | None:
    best = None
    best_score = -1
    best_overlap: set[str] = set()

    for item in items:
        item_tokens = item.get(tokens_key, set())
        overlap = query_tokens & item_tokens
        score = len(overlap)
        if score > best_score:
            best = item
            best_score = score
            best_overlap = overlap

    if best is None:
        return None
    best = dict(best)
    best["_overlap_tokens"] = sorted(best_overlap)
    best["_overlap_score"] = best_score
    return best


def pick_case_study_and_pricing(key_pain_points: str, case_studies_path: Path, pricing_path: Path) -> dict[str, Any]:
    ensure_mock_files(case_studies_path, pricing_path)

    case_lines = case_studies_path.read_text(encoding="utf-8").splitlines()
    pricing_lines = pricing_path.read_text(encoding="utf-8").splitlines()

    studies = parse_case_studies(case_lines)
    tiers = parse_pricing(pricing_lines)

    query_tokens = tokenize(key_pain_points)

    # For case studies, match against keywords tokens.
    case_items = [{"title": s["title"], "tokens": s["keywords"], "raw": s["raw"]} for s in studies]
    best_case = best_overlap_match(query_tokens, case_items, "tokens")

    # For pricing, match against tier name + description tokens.
    best_tier = best_overlap_match(query_tokens, tiers, "tokens")

    selected_case = best_case["title"] if best_case else "unknown"
    selected_tier = best_tier["tier"] if best_tier else "unknown"

    case_overlap = best_case.get("_overlap_tokens", []) if best_case else []
    tier_overlap = best_tier.get("_overlap_tokens", []) if best_tier else []

    justification_parts = []
    if best_case:
        justification_parts.append(
            f"Selected case study matched tokens: {', '.join(case_overlap) if case_overlap else 'no overlap'}."
        )
    else:
        justification_parts.append("No case studies available to match.")

    if best_tier:
        justification_parts.append(
            f"Selected pricing tier matched tokens: {', '.join(tier_overlap) if tier_overlap else 'no overlap'}."
        )
    else:
        justification_parts.append("No pricing tiers available to match.")

    if not query_tokens:
        justification_parts.append("Input key_pain_points was empty after normalization.")

    return {
        "selected_case_study": selected_case,
        "pricing_tier": selected_tier,
        "justification": " ".join(justification_parts).strip(),
    }


def extract_openrouter_content(data: dict) -> str:
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

def coerce_json_object(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise ValueError("Empty model content.")

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        raise ValueError("Model did not return a JSON object.")
    parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("Model returned non-object JSON.")
    return parsed


def openrouter_judge(api_key: str, model: str, key_pain_points: str, selection: dict[str, Any]) -> dict[str, Any]:
    system_prompt = "You are a strict evaluator. Return valid JSON only."
    user_prompt = (
        "Given key_pain_points and the selected case study and pricing tier, validate the choice.\n"
        "Return JSON with keys: valid (boolean), improved_selected_case_study (string), improved_pricing_tier (string), notes (string).\n\n"
        f"key_pain_points: {key_pain_points}\n"
        f"selection: {json.dumps(selection)}\n"
        "If you keep the same selections, repeat them as improved_*."
    )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 500,
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
            "X-Title": "Agentic Demo - Researcher Judge",
        },
        method="POST",
    )

    with request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        content = extract_openrouter_content(data)
        try:
            return coerce_json_object(content)
        except Exception as exc:
            finish_reason = data.get("choices", [{}])[0].get("finish_reason")
            raise ValueError(
                "Judge model did not return valid JSON. "
                f"finish_reason={finish_reason!r} content_head={(content or '')[:200]!r}"
            ) from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="Agent 2 (Researcher): match pain points to case study + pricing.")
    parser.add_argument("--key-pain-points", required=True, help="String describing the main challenges (from Agent 1).")
    parser.add_argument("--case-studies", default="case_studies.txt", help="Path to case studies file.")
    parser.add_argument("--pricing", default="pricing.txt", help="Path to pricing file.")
    parser.add_argument("--judge", action="store_true", help="Call OpenRouter to validate/improve selection.")
    parser.add_argument("--judge-model", default=None, help="Model for judge (defaults to OPENROUTER_MODEL).")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent
    env_path = repo_root / ".env"
    load_env(env_path)

    case_path = (repo_root / args.case_studies).resolve()
    pricing_path = (repo_root / args.pricing).resolve()

    selection = pick_case_study_and_pricing(args.key_pain_points, case_path, pricing_path)

    if args.judge:
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            selection["judge"] = {"error": "Missing OPENROUTER_API_KEY in .env"}
        else:
            model = args.judge_model or os.environ.get("OPENROUTER_MODEL", "openrouter/auto")
            try:
                selection["judge"] = openrouter_judge(api_key, model, args.key_pain_points, selection)
            except error.HTTPError as exc:
                selection["judge"] = {"error": exc.read().decode("utf-8", errors="replace"), "status": exc.code}
            except Exception as exc:
                selection["judge"] = {
                    "error": str(exc),
                    "hint": "Try specifying a different judge model via --judge-model or set OPENROUTER_MODEL to a model that reliably returns JSON.",
                }

    print(json.dumps(selection, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
