"""Where a sorted lead goes: a row in Google Sheets (or CSV) and a message to the manager in Telegram."""
from __future__ import annotations

import asyncio
import csv
import html
from pathlib import Path

import httpx

from .triage import ICONS, LABELS_RU, URGENCY_RU, Triage

COLUMNS = ["Дата", "ID", "Имя", "Телефон", "Категория", "Срочность", "Сводка", "Сообщение",
           "Источник", "Язык", "Черновик ответа", "Разбор"]


def sheet_row(lead: dict, t: Triage) -> list[str]:
    return [lead["received_at"], lead["id"], lead["name"], lead["phone"], LABELS_RU[t.category],
            URGENCY_RU[t.urgency], t.summary, lead["message"], lead["source"], lead["lang"],
            t.reply_draft, "AI" if t.by == "ai" else "правила"]


class CsvSink:
    def __init__(self, path: Path):
        self.path = path
        self._lock = asyncio.Lock()

    async def append(self, row: list[str]) -> None:
        async with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            new = not self.path.exists()
            with self.path.open("a", newline="", encoding="utf-8-sig" if new else "utf-8") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(COLUMNS)
                w.writerow(row)


class SheetsSink:
    def __init__(self, sheet_id: str, credentials_file: str, worksheet: str = "Заявки"):
        import gspread

        book = gspread.service_account(filename=credentials_file).open_by_key(sheet_id)
        try:
            self.ws = book.worksheet(worksheet)
        except gspread.WorksheetNotFound:
            self.ws = book.add_worksheet(title=worksheet, rows=1000, cols=len(COLUMNS))
            self.ws.append_row(COLUMNS)

    async def append(self, row: list[str]) -> None:
        await asyncio.to_thread(self.ws.append_row, row, value_input_option="USER_ENTERED")


def telegram_text(lead: dict, t: Triage) -> str:
    e = html.escape
    urgent = " · <b>срочно</b>" if t.urgency == "high" else ""
    lines = [
        f"{ICONS[t.category]} <b>{LABELS_RU[t.category]}</b>{urgent}",
        f"👤 {e(lead['name'])} · <code>{e(lead['phone'])}</code>",
        f"📝 {e(t.summary)}",
    ]
    if lead["message"]:
        lines.append(f"💬 <i>{e(lead['message'][:600])}</i>")
    lines.append(f"✍️ Черновик ответа: {e(t.reply_draft)}")
    lines.append(f"🌐 {e(lead['source'])} · {lead['lang']} · {lead['id']}")
    return "\n".join(lines)


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, base_url: str = "https://api.telegram.org",
                 client: httpx.AsyncClient | None = None):
        self.url = f"{base_url.rstrip('/')}/bot{token}/sendMessage"
        self.chat_id = chat_id
        self.client = client or httpx.AsyncClient(timeout=15)

    async def send(self, lead: dict, t: Triage) -> None:
        r = await self.client.post(self.url, json={
            "chat_id": self.chat_id, "text": telegram_text(lead, t),
            "parse_mode": "HTML", "disable_web_page_preview": True,
        })
        r.raise_for_status()
