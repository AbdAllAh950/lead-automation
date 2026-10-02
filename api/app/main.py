"""FastAPI mirror of the n8n workflow: POST /lead → check → AI triage → Google Sheets/CSV → Telegram."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .leads import LeadIn, normalize
from .sinks import CsvSink, SheetsSink, TelegramNotifier, sheet_row
from .triage import LABELS_RU, LLMTriage, triage

load_dotenv()
log = logging.getLogger("leads")


def build_app(llm=None, sink=None, notifier=None, tz: str | None = None) -> FastAPI:
    """Dependencies are passed in for tests; by default they come from environment variables."""
    env = os.getenv
    if llm is None and env("LLM_URL") and env("LLM_AUTH") and env("LLM_MODEL"):
        llm = LLMTriage(env("LLM_URL"), env("LLM_AUTH"), env("LLM_MODEL"))
    if sink is None:
        if env("GOOGLE_SHEET_ID"):
            sink = SheetsSink(env("GOOGLE_SHEET_ID"), env("GOOGLE_CREDENTIALS_FILE", "credentials.json"))
        else:
            sink = CsvSink(Path(env("LEADS_CSV", "data/leads.csv")))
    if notifier is None and env("TELEGRAM_BOT_TOKEN") and env("TELEGRAM_CHAT_ID"):
        notifier = TelegramNotifier(env("TELEGRAM_BOT_TOKEN"), env("TELEGRAM_CHAT_ID"),
                                    env("TELEGRAM_API_URL", "https://api.telegram.org"))
    tz = tz or env("BUSINESS_TZ", "Europe/Moscow")

    app = FastAPI(title="Lead triage", version="1.0")
    origins = [o.strip() for o in env("ALLOWED_ORIGINS", "*").split(",") if o.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["POST", "OPTIONS"],
                       allow_headers=["Content-Type"])

    @app.get("/health")
    async def health():
        return {"ok": True, "ai": llm is not None, "telegram": notifier is not None,
                "storage": type(sink).__name__}

    @app.post("/lead")
    async def receive(body: LeadIn):
        if body.website:  # honeypot filled → bot; answer "ok" so it doesn't retry
            return {"ok": True}
        lead = normalize(body, tz)
        t = await triage(lead, llm)
        delivered = {"sheet": False, "telegram": False}
        try:
            await sink.append(sheet_row(lead, t))
            delivered["sheet"] = True
        except Exception:
            log.exception("could not save lead %s", lead["id"])
        if notifier and t.category != "spam":
            try:
                await notifier.send(lead, t)
                delivered["telegram"] = True
            except Exception:
                log.exception("could not notify about lead %s", lead["id"])
        return {"ok": True, "id": lead["id"], "category": t.category,
                "category_ru": LABELS_RU[t.category], "triage_by": t.by, "delivered": delivered}

    return app


app = build_app()
