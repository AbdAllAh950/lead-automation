"""Incoming lead: validation and normalization (same rules as the n8n "Проверка и очистка" node)."""
from __future__ import annotations

import re
import secrets
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator


class LeadIn(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    phone: str = Field(min_length=5, max_length=40)
    message: str = Field(default="", max_length=4000)
    source: str = Field(default="", max_length=100)
    lang: str = Field(default="ru", max_length=5)
    product: str | None = Field(default=None, max_length=50)
    qty: int | None = Field(default=None, ge=0, le=100000)
    page: str | None = Field(default=None, max_length=500)
    website: str = Field(default="", max_length=200)  # honeypot: humans never fill it

    @field_validator("name", "message", "source")
    @classmethod
    def squash_spaces(cls, v: str) -> str:
        return " ".join(v.split())

    @field_validator("phone")
    @classmethod
    def valid_phone(cls, v: str) -> str:
        digits = re.sub(r"\D", "", v)
        if not 9 <= len(digits) <= 15:
            raise ValueError("phone must have 9–15 digits")
        return v


def normalize_phone(raw: str) -> str:
    """+7 for Russian numbers written as 8…/7…/9…, otherwise + and digits as given."""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits[0] in "78":
        return "+7" + digits[1:]
    if len(digits) == 10 and digits[0] == "9":
        return "+7" + digits
    return "+" + digits


def new_lead_id(now: datetime) -> str:
    return f"L-{now:%Y%m%d-%H%M}-{secrets.token_hex(2)}"


def normalize(lead: LeadIn, tz: str = "Europe/Moscow") -> dict:
    now = datetime.now(ZoneInfo(tz))
    return {
        "id": new_lead_id(now),
        "received_at": now.strftime("%Y-%m-%d %H:%M"),
        "name": lead.name,
        "phone": normalize_phone(lead.phone),
        "message": lead.message,
        "source": lead.source or "сайт",
        "lang": lead.lang if lead.lang in ("ru", "ar", "en") else "ru",
        "product": lead.product,
        "qty": lead.qty,
        "page": lead.page,
    }
