import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.leads import normalize_phone
from app.main import build_app
from app.sinks import COLUMNS, CsvSink, TelegramNotifier, telegram_text
from app.triage import SYSTEM_PROMPT, LLMTriage, Triage, by_rules, from_llm_output


class MemorySink:
    def __init__(self):
        self.rows = []

    async def append(self, row):
        self.rows.append(row)


def fake_llm(reply: str | None = None, status: int = 200, seen: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if status != 200:
            return httpx.Response(status, json={"error": "quota"})
        return httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})
    return LLMTriage("https://llm.test/v1/chat/completions", "Api-Key test", "gpt://folder/yandexgpt/latest",
                     client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def fake_telegram(sent: list):
    def handler(request: httpx.Request) -> httpx.Response:
        sent.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"ok": True})
    return TelegramNotifier("123:ABC", "-100500", "https://tg.test",
                            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


LEAD = {"name": "Анна", "phone": "8 (921) 123-45-67", "message": "Таёжное разнотравье × 2. Доставка на Васильевский",
        "source": "landing-ru", "lang": "ru", "product": "taiga", "qty": 2}


@pytest.mark.parametrize("raw,expected", [
    ("8 (921) 123-45-67", "+79211234567"),
    ("+7 921 123 45 67", "+79211234567"),
    ("9211234567", "+79211234567"),
    ("+971 50 123 4567", "+971501234567"),
])
def test_normalize_phone(raw, expected):
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize("message,product,expected", [
    ("Хочу заказать 3 банки", None, "order"),
    ("Мы сеть кофеен, интересует опт от 50 кг", None, "wholesale"),
    ("Банка пришла разбитая, верните деньги", "taiga", "complaint"),
    ("Раскрутка сайта, SEO-продвижение недорого https://spam.example", None, "spam"),
    ("Можно ли давать мёд детям?", None, "question"),
    ("", "gift", "order"),
    ("", "wholesale", "wholesale"),
    ("أريد صندوق هدايا لرمضان", None, "order"),
    ("نحن سلسلة فنادق في دبي ونبحث عن موزع", None, "wholesale"),
    ("وصل الطلب والعبوة مكسورة", None, "complaint"),
])
def test_rules(message, product, expected):
    assert by_rules({"name": "X", "message": message, "product": product, "lang": "ru"}).category == expected


def test_llm_output_with_fences_and_bad_values():
    t = from_llm_output('Вот ответ:\n```json\n{"category": "order", "urgency": "asap", "summary": "Анна хочет 2 банки", '
                        '"reply_draft": "Спасибо!"}\n```', LEAD)
    assert (t.category, t.urgency, t.by) == ("order", "high", "ai")  # unknown urgency → default for category
    t = from_llm_output('{"category": "лид", "summary": ""}', LEAD)
    assert t.category == "order" and t.summary.startswith("Заказ")  # falls back to rules field by field


def test_request_uses_shared_prompt_and_auth_header():
    seen = []
    llm = fake_llm('{"category":"order","urgency":"high","summary":"s","reply_draft":"r"}', seen=seen)
    import asyncio
    asyncio.run(llm(LEAD))
    body = json.loads(seen[0].content)
    assert seen[0].headers["Authorization"] == "Api-Key test"
    assert body["model"] == "gpt://folder/yandexgpt/latest"
    assert body["messages"][0]["content"] == SYSTEM_PROMPT
    assert "Таёжное разнотравье" in body["messages"][1]["content"]


def test_endpoint_full_flow_with_ai():
    sink, sent = MemorySink(), []
    ai = '{"category": "order", "urgency": "high", "summary": "Анна: 2 банки таёжного, доставка на В.О.", "reply_draft": "Анна, спасибо! Перезвоним через 15 минут."}'
    client = TestClient(build_app(llm=fake_llm(ai), sink=sink, notifier=fake_telegram(sent)))
    r = client.post("/lead", json=LEAD)
    assert r.status_code == 200
    data = r.json()
    assert data["category"] == "order" and data["triage_by"] == "ai"
    assert data["delivered"] == {"sheet": True, "telegram": True}
    row = dict(zip(COLUMNS, sink.rows[0]))
    assert row["Телефон"] == "+79211234567" and row["Категория"] == "Заказ" and row["Срочность"] == "Срочно"
    path, payload = sent[0]
    assert path == "/bot123:ABC/sendMessage" and payload["chat_id"] == "-100500"
    assert payload["parse_mode"] == "HTML" and "<b>Заказ</b>" in payload["text"] and "срочно" in payload["text"]


def test_ai_down_falls_back_to_rules():
    sink = MemorySink()
    client = TestClient(build_app(llm=fake_llm(status=429), sink=sink, notifier=None))
    data = client.post("/lead", json={**LEAD, "message": "Интересует оптовая поставка в ресторан", "product": "wholesale"}).json()
    assert data["category"] == "wholesale" and data["triage_by"] == "rules"
    assert dict(zip(COLUMNS, sink.rows[0]))["Разбор"] == "правила"


def test_spam_saved_but_manager_not_pinged():
    sink, sent = MemorySink(), []
    client = TestClient(build_app(llm=None, sink=sink, notifier=fake_telegram(sent)))
    data = client.post("/lead", json={**LEAD, "message": "Раскрутка сайта https://x.example", "product": None}).json()
    assert data["category"] == "spam" and sink.rows and sent == []


def test_honeypot_and_validation():
    sink = MemorySink()
    client = TestClient(build_app(llm=None, sink=sink, notifier=None))
    assert client.post("/lead", json={**LEAD, "website": "http://bot"}).json() == {"ok": True}
    assert sink.rows == []
    assert client.post("/lead", json={**LEAD, "phone": "12"}).status_code == 422
    assert client.post("/lead", json={**LEAD, "name": ""}).status_code == 422


def test_cors_preflight_from_landing():
    client = TestClient(build_app(llm=None, sink=MemorySink(), notifier=None))
    r = client.options("/lead", headers={"Origin": "https://abdallah950.github.io",
                                         "Access-Control-Request-Method": "POST",
                                         "Access-Control-Request-Headers": "content-type"})
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] in ("*", "https://abdallah950.github.io")


def test_csv_sink_and_telegram_escaping(tmp_path):
    import asyncio
    sink = CsvSink(tmp_path / "leads.csv")
    asyncio.run(sink.append(["a"] * len(COLUMNS)))
    asyncio.run(sink.append(["b"] * len(COLUMNS)))
    lines = (tmp_path / "leads.csv").read_text(encoding="utf-8-sig").splitlines()
    assert lines[0].startswith("Дата,ID") and len(lines) == 3
    lead = {"name": "<script>", "phone": "+7", "message": "a & b", "source": "s", "lang": "ru", "id": "L-1"}
    text = telegram_text(lead, Triage("question", "normal", "x<y", "ok", "rules"))
    assert "&lt;script&gt;" in text and "a &amp; b" in text and "x&lt;y" in text


def test_arabic_lead_end_to_end():
    sink = MemorySink()
    client = TestClient(build_app(llm=None, sink=sink, notifier=None))
    data = client.post("/lead", json={"name": "خالد", "phone": "+971 50 123 4567", "lang": "ar",
                                      "source": "landing-ar", "product": "gift",
                                      "message": "صندوق هدايا 3 × 250 غ × 3. التوصيل إلى دبي مارينا. WhatsApp"}).json()
    row = dict(zip(COLUMNS, sink.rows[0]))
    assert data["category"] == "order"
    assert row["Телефон"] == "+971501234567" and row["Язык"] == "ar"
    assert "شكرًا" in row["Черновик ответа"]
