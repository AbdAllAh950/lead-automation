"""Lead triage: an LLM (YandexGPT or any OpenAI-compatible API) sorts the lead; keyword rules take over if it fails."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

PROMPTS = Path(__file__).resolve().parents[2] / "prompts"
SYSTEM_PROMPT = (PROMPTS / "triage_system.txt").read_text(encoding="utf-8").strip()
SHARED = json.loads((PROMPTS / "triage_rules.json").read_text(encoding="utf-8"))  # same file feeds the n8n node

RULES = [(cat, re.compile(pattern)) for cat, pattern in SHARED["rules"]]
ORDER_PRODUCTS = set(SHARED["order_products"])
LABELS_RU: dict[str, str] = SHARED["labels_ru"]
URGENCY_RU: dict[str, str] = SHARED["urgency_ru"]
DEFAULT_URGENCY: dict[str, str] = SHARED["default_urgency"]
ICONS: dict[str, str] = SHARED["icons"]
REPLY_TEMPLATES: dict[str, str] = SHARED["reply_templates"]
CATEGORIES = tuple(LABELS_RU)
URGENCIES = tuple(URGENCY_RU)


@dataclass
class Triage:
    category: str
    urgency: str
    summary: str
    reply_draft: str
    by: str  # "ai" or "rules"

    def as_dict(self) -> dict:
        return asdict(self)


def lead_text(lead: dict) -> str:
    return (f"Имя: {lead.get('name', '')}\nТелефон: {lead.get('phone', '')}\n"
            f"Источник: {lead.get('source', '')}\nЯзык страницы: {lead.get('lang', '')}\n"
            f"Товар: {lead.get('product') or '-'}\nСообщение: {lead.get('message', '')}")


def by_rules(lead: dict) -> Triage:
    text = f"{lead.get('message', '')}".lower()
    category = "question"
    for cat, pattern in RULES:
        if pattern.search(text):
            category = cat
            break
    if category == "question" and lead.get("product") == "wholesale":
        category = "wholesale"
    elif category == "question" and lead.get("product") in ORDER_PRODUCTS:
        category = "order"
    message = " ".join(str(lead.get("message", "")).split())
    summary = f"{LABELS_RU[category]}: {message[:120]}" if message else LABELS_RU[category]
    lang = lead.get("lang") if lead.get("lang") in REPLY_TEMPLATES else "ru"
    reply = REPLY_TEMPLATES[lang].replace("{name}", lead.get("name", "").strip())
    return Triage(category, DEFAULT_URGENCY[category], summary, reply, "rules")


def parse_llm_json(content: str) -> dict:
    """Models sometimes wrap JSON in ``` fences or add a sentence; take the first {...} block."""
    match = re.search(r"\{.*\}", content, re.S)
    if not match:
        raise ValueError(f"no JSON in model output: {content[:200]!r}")
    return json.loads(match.group(0))


def from_llm_output(content: str, lead: dict) -> Triage:
    data = parse_llm_json(content)
    fallback = by_rules(lead)
    category = data.get("category") if data.get("category") in CATEGORIES else fallback.category
    urgency = data.get("urgency") if data.get("urgency") in URGENCIES else DEFAULT_URGENCY[category]
    summary = str(data.get("summary") or fallback.summary).strip()
    reply = str(data.get("reply_draft") or fallback.reply_draft).strip()
    return Triage(category, urgency, summary, reply, "ai")


class LLMTriage:
    """OpenAI-compatible /chat/completions. YandexGPT: url https://ai.api.cloud.yandex.net/v1/chat/completions,
    auth "Api-Key <key>", model "gpt://<folder_id>/yandexgpt/latest"."""

    def __init__(self, url: str, auth: str, model: str, client: httpx.AsyncClient | None = None,
                 timeout: float = 20.0):
        self.url, self.auth, self.model = url, auth, model
        self.client = client or httpx.AsyncClient(timeout=timeout)

    def request_body(self, lead: dict) -> dict:
        return {
            "model": self.model,
            "temperature": 0.1,
            "max_tokens": 400,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": lead_text(lead)},
            ],
        }

    async def __call__(self, lead: dict) -> Triage:
        r = await self.client.post(self.url, json=self.request_body(lead),
                                   headers={"Authorization": self.auth})
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        return from_llm_output(content, lead)


async def triage(lead: dict, llm: LLMTriage | None) -> Triage:
    if llm is None:
        return by_rules(lead)
    try:
        return await llm(lead)
    except Exception:  # network, quota, bad JSON: never lose a lead because the AI is down
        return by_rules(lead)
