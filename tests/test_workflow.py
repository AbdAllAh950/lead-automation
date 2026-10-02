"""Checks the n8n workflow file and that its JavaScript gives the same answers as the Python mirror.

Run from the repo root:  pytest -q   (the JS parity tests need `node` on PATH and are skipped otherwise)
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT / "scripts"))

from app.leads import normalize_phone  # noqa: E402
from app.triage import SYSTEM_PROMPT, by_rules  # noqa: E402
from build_workflow import build  # noqa: E402

WORKFLOW = json.loads((ROOT / "n8n" / "lead-triage.json").read_text(encoding="utf-8"))
NODES = {n["name"]: n for n in WORKFLOW["nodes"]}


def test_workflow_file_is_up_to_date():
    assert WORKFLOW == build(), "run: python scripts/build_workflow.py"


def test_connections_point_to_existing_nodes_and_everything_is_reachable():
    edges = {src: [t["node"] for branch in c["main"] for t in branch] for src, c in WORKFLOW["connections"].items()}
    for src, targets in edges.items():
        assert src in NODES and all(t in NODES for t in targets)
    seen, todo = set(), ["Заявка с сайта"]
    while todo:
        name = todo.pop()
        if name not in seen:
            seen.add(name)
            todo += edges.get(name, [])
    working = {n for n, node in NODES.items() if node["type"] != "n8n-nodes-base.stickyNote"}
    assert seen == working


def test_prompt_and_settings():
    settings = {a["name"]: a["value"] for a in NODES["Настройки"]["parameters"]["assignments"]["assignments"]}
    assert settings["system_prompt"] == SYSTEM_PROMPT
    assert settings["llm_url"].endswith("/chat/completions")
    webhook = NODES["Заявка с сайта"]["parameters"]
    assert webhook["path"] == "lead" and webhook["responseMode"] == "responseNode"


def test_no_secrets_in_export():
    for node in WORKFLOW["nodes"]:
        for cred in node.get("credentials", {}).values():
            assert set(cred) == {"id", "name"}
    text = json.dumps(WORKFLOW, ensure_ascii=False)
    for marker in ("BEGIN PRIVATE KEY", "Api-Key AQV", "Bearer sk-"):
        assert marker not in text


def test_failures_never_stop_the_flow():
    for name in ("AI: разбор заявки", "Записать в таблицу", "Сообщение менеджеру"):
        assert NODES[name]["onError"] == "continueRegularOutput"
    assert NODES["Сообщение менеджеру"]["parameters"]["additionalFields"]["appendAttribution"] is False


# ---------- JavaScript ↔ Python parity ----------
NODE_BIN = shutil.which("node")
needs_node = pytest.mark.skipif(NODE_BIN is None, reason="node is not installed")

HARNESS = r"""
const fs = require('fs');
const [code, cases] = [process.argv[1], JSON.parse(fs.readFileSync(0, 'utf8'))];
const fn = new Function('$json', '$', code);
const out = cases.map(c => {
  const $ = (name) => ({ item: { json: c.upstream || {} }, first: () => ({ json: c.settings || {} }) });
  return fn(c.json || {}, $).json;
});
process.stdout.write(JSON.stringify(out));
"""

CASES = [
    ("Хочу заказать 3 банки", None, "ru"),
    ("Мы сеть кофеен, интересует опт от 50 кг", None, "ru"),
    ("Купить оптом 20 вёдер", None, "ru"),
    ("Это оптимальный вариант?", None, "ru"),
    ("Банка пришла разбитая, верните деньги", "taiga", "ru"),
    ("Раскрутка сайта, SEO-продвижение недорого https://spam.example", None, "ru"),
    ("Можно ли давать мёд детям?", None, "ru"),
    ("", "gift", "ru"),
    ("", "wholesale", "ar"),
    ("أريد صندوق هدايا لرمضان", None, "ar"),
    ("نحن سلسلة فنادق في دبي ونبحث عن موزع", None, "ar"),
    ("وصل الطلب والعبوة مكسورة", None, "ar"),
    ("Do you ship to Dubai? I want to buy 2 jars", None, "en"),
]


def run_js(node_name: str, cases: list[dict]) -> list[dict]:
    code = NODES[node_name]["parameters"]["jsCode"]
    res = subprocess.run([NODE_BIN, "-e", HARNESS, code], input=json.dumps(cases), capture_output=True,
                         text=True, check=True)
    return json.loads(res.stdout)


@needs_node
def test_js_rules_match_python():
    leads = [{"id": "L-1", "received_at": "2026-10-01 10:00", "name": "Тест", "phone": "+79990000000",
              "message": m, "source": "s", "lang": lang, "product": p} for m, p, lang in CASES]
    js = run_js("Категория и сводка", [{"json": {}, "upstream": {"lead": lead}} for lead in leads])
    for lead, out in zip(leads, js):
        py = by_rules(lead)
        assert out["category"] == py.category, lead["message"]
        assert out["Сводка"] == py.summary and out["Черновик ответа"] == py.reply_draft
        assert out["Разбор"] == "правила"


@needs_node
def test_js_reads_ai_answer_in_code_fences():
    lead = {"id": "L-1", "received_at": "t", "name": "Анна", "phone": "+7", "message": "m", "source": "s",
            "lang": "ru", "product": None}
    ai = {"choices": [{"message": {"content": '```json\n{"category":"complaint","urgency":"high",'
                                              '"summary":"Жалоба на доставку","reply_draft":"Извините!"}\n```'}}]}
    out = run_js("Категория и сводка", [{"json": ai, "upstream": {"lead": lead}}])[0]
    assert (out["category"], out["Срочность"], out["Разбор"]) == ("complaint", "Срочно", "AI")
    assert "🔴 <b>Жалоба</b> · <b>срочно</b>" in out["telegram_text"]


@needs_node
@pytest.mark.parametrize("raw", ["8 (921) 123-45-67", "+7 921 123 45 67", "9211234567", "+971 50 123 4567"])
def test_js_phone_matches_python(raw):
    out = run_js("Проверка и очистка", [{"json": {"body": {"name": "Анна", "phone": raw}},
                                         "settings": {"timezone": "Europe/Moscow"}}])[0]
    assert out["valid"] is True and out["lead"]["phone"] == normalize_phone(raw)


@needs_node
@pytest.mark.parametrize("body,error", [
    ({"name": "А", "phone": "+79211234567"}, "name"),
    ({"name": "Анна", "phone": "12"}, "phone"),
    ({"name": "Bot", "phone": "+79211234567", "website": "x"}, "honeypot"),
    ({"test": "test"}, "ping"),
])
def test_js_validation(body, error):
    out = run_js("Проверка и очистка", [{"json": {"body": body}, "settings": {"timezone": "Europe/Moscow"}}])[0]
    assert out["valid"] is False and out["error"] == error


@needs_node
def test_js_accepts_tilda_form_fields():
    body = {"Name": "Анна", "Phone": "+7 (921) 123-45-67", "Comments": "Нужен опт для кофейни",
            "formname": "Заявка", "tranid": "123:456"}
    out = run_js("Проверка и очистка", [{"json": {"body": body}, "settings": {"timezone": "Europe/Moscow"}}])[0]
    assert out["valid"] is True
    lead = out["lead"]
    assert (lead["name"], lead["phone"], lead["source"]) == ("Анна", "+79211234567", "tilda: Заявка")
    assert lead["message"] == "Нужен опт для кофейни"
