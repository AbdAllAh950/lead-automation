"""Builds n8n/lead-triage.json from readable sources:
n8n/code/*.js (Code nodes), prompts/triage_system.txt (AI prompt) and prompts/triage_rules.json (fallback rules).
Run after editing any of them:  python scripts/build_workflow.py
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "n8n" / "lead-triage.json"
NS = uuid.UUID("6f1c0a4e-2b7d-4c55-9a3e-1d2f3a4b5c6d")

SETTINGS = "Настройки"
VALIDATE = "Проверка и очистка"
TRIAGE = "Категория и сводка"


def uid(name: str) -> str:
    return str(uuid.uuid5(NS, name))


def node(name, type_, version, position, parameters, **extra):
    return {"parameters": parameters, "id": uid(name), "name": name, "type": f"n8n-nodes-base.{type_}",
            "typeVersion": version, "position": position, **extra}


def sticky(name, position, width, height, content, color=None):
    params = {"content": content, "height": height, "width": width}
    if color:
        params["color"] = color
    return node(name, "stickyNote", 1, position, params)


def build() -> dict:
    prompt = (ROOT / "prompts" / "triage_system.txt").read_text(encoding="utf-8").strip()
    rules = json.loads((ROOT / "prompts" / "triage_rules.json").read_text(encoding="utf-8"))
    rules.pop("_comment", None)
    validate_js = (ROOT / "n8n" / "code" / "validate.js").read_text(encoding="utf-8")
    triage_js = (ROOT / "n8n" / "code" / "triage.js").read_text(encoding="utf-8").replace(
        "__SHARED_RULES__", json.dumps(rules, ensure_ascii=False))
    cfg = f"$('{SETTINGS}').first().json"
    tri = f"$('{TRIAGE}').item.json"

    def setting(name, value):
        return {"id": uid("set-" + name), "name": name, "value": value, "type": "string"}

    nodes = [
        sticky("Как настроить", [-40, -420], 660, 340,
               "## Заявки с сайта → AI → таблица + Telegram\n\n"
               "1. Откройте **Настройки** и впишите модель, ID таблицы и ID чата менеджера.\n"
               "2. Создайте три доступа (Credentials):\n"
               "   - **Header Auth** для нейросети: Name `Authorization`, Value `Api-Key …` (YandexGPT) "
               "или `Bearer …` (OpenAI-совместимые API);\n"
               "   - **Google Service Account** — и дайте его email доступ «Редактор» к таблице;\n"
               "   - **Telegram API** — токен бота от @BotFather.\n"
               "3. В таблице создайте лист **«Заявки»** с заголовками в первой строке:\n"
               "   `Дата, ID, Имя, Телефон, Категория, Срочность, Сводка, Сообщение, Источник, Язык, Черновик ответа, Разбор`\n"
               "4. Активируйте workflow. Адрес для формы сайта: `https://ВАШ-N8N/webhook/lead`\n\n"
               "Формат заявки (JSON): `name`, `phone`, `message`, `source`, `lang`, `product`, `qty`. "
               "Формы Tilda подключаются напрямую: Настройки сайта → Формы → Webhook.",
               color=5),
        sticky("Если AI недоступна", [1100, 170], 420, 170,
               "### Заявка не теряется\nЕсли нейросеть не ответила (лимит, сеть, неверный ключ), узел "
               "**«Категория и сводка»** разберёт заявку по ключевым словам из `prompts/triage_rules.json`. "
               "В таблице это видно в колонке «Разбор»."),
        node("Заявка с сайта", "webhook", 2.1, [0, 0], {
            "httpMethod": "POST", "path": "lead", "responseMode": "responseNode",
            "options": {"allowedOrigins": "*"}}, webhookId=uid("webhook-lead")),
        node(SETTINGS, "set", 3.4, [220, 0], {
            "mode": "manual",
            "assignments": {"assignments": [
                setting("llm_url", "https://ai.api.cloud.yandex.net/v1/chat/completions"),
                setting("llm_model", "gpt://ВАШ_FOLDER_ID/yandexgpt/latest"),
                setting("sheet_id", "ВАШ_ID_GOOGLE_ТАБЛИЦЫ"),
                setting("telegram_chat_id", "ВАШ_CHAT_ID"),
                setting("timezone", "Europe/Moscow"),
                setting("system_prompt", prompt),
            ]},
            "includeOtherFields": True, "options": {}}),
        node(VALIDATE, "code", 2, [440, 0], {"mode": "runOnceForEachItem", "jsCode": validate_js}),
        node("Заявка корректна?", "if", 2.2, [660, 0], {
            "conditions": {
                "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 2},
                "conditions": [{"id": uid("cond-valid"), "leftValue": "={{ $json.valid }}", "rightValue": "",
                                "operator": {"type": "boolean", "operation": "true", "singleValue": True}}],
                "combinator": "and"},
            "options": {}}),
        node("AI: разбор заявки", "httpRequest", 4.2, [880, 0], {
            "method": "POST", "url": f"={{{{ {cfg}.llm_url }}}}",
            "authentication": "genericCredentialType", "genericAuthType": "httpHeaderAuth",
            "sendBody": True, "specifyBody": "json",
            "jsonBody": (f"={{{{ JSON.stringify({{ model: {cfg}.llm_model, temperature: 0.1, max_tokens: 400, "
                         f"messages: [{{ role: 'system', content: {cfg}.system_prompt }}, "
                         f"{{ role: 'user', content: $json.llm_user }}] }}) }}}}"),
            "options": {"timeout": 20000}},
            credentials={"httpHeaderAuth": {"id": "llmHeaderAuth01", "name": "LLM API key"}},
            onError="continueRegularOutput", retryOnFail=True, maxTries=2, waitBetweenTries=1000),
        node(TRIAGE, "code", 2, [1100, 0], {"mode": "runOnceForEachItem", "jsCode": triage_js}),
        node("Записать в таблицу", "googleSheets", 4.5, [1320, 0], {
            "authentication": "serviceAccount", "operation": "append",
            "documentId": {"__rl": True, "value": f"={{{{ {cfg}.sheet_id }}}}", "mode": "id"},
            "sheetName": {"__rl": True, "value": "Заявки", "mode": "name"},
            "columns": {"mappingMode": "autoMapInputData", "value": {}, "matchingColumns": [], "schema": []},
            "options": {"handlingExtraData": "ignoreIt"}},
            credentials={"googleApi": {"id": "googleSvcAcc01", "name": "Google Service Account"}},
            onError="continueRegularOutput"),
        node("Не спам?", "if", 2.2, [1540, 0], {
            "conditions": {
                "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 2},
                "conditions": [{"id": uid("cond-spam"), "leftValue": f"={{{{ {tri}.category }}}}",
                                "rightValue": "spam", "operator": {"type": "string", "operation": "notEquals"}}],
                "combinator": "and"},
            "options": {}}),
        node("Сообщение менеджеру", "telegram", 1.2, [1760, -160], {
            "resource": "message", "operation": "sendMessage",
            "chatId": f"={{{{ {cfg}.telegram_chat_id }}}}",
            "text": f"={{{{ {tri}.telegram_text }}}}",
            "additionalFields": {"appendAttribution": False, "parse_mode": "HTML",
                                 "disable_web_page_preview": True}},
            credentials={"telegramApi": {"id": "telegramBot01", "name": "Telegram bot"}},
            webhookId=uid("telegram"), onError="continueRegularOutput"),
        node("Ответ сайту", "respondToWebhook", 1.1, [1980, 0], {
            "respondWith": "json",
            "responseBody": f"={{{{ JSON.stringify({{ ok: true, id: {tri}.ID, category: {tri}.category }}) }}}}",
            "options": {}}),
        node("Ответ: ошибка", "respondToWebhook", 1.1, [880, 220], {
            "respondWith": "json",
            "responseBody": "={{ JSON.stringify(['honeypot', 'ping'].includes($json.error) ? { ok: true } : { ok: false, error: $json.error }) }}",
            "options": {"responseCode": "={{ ['honeypot', 'ping'].includes($json.error) ? 200 : 400 }}"}}),
    ]

    def to(*names):
        return [{"node": n, "type": "main", "index": 0} for n in names]

    connections = {
        "Заявка с сайта": {"main": [to(SETTINGS)]},
        SETTINGS: {"main": [to(VALIDATE)]},
        VALIDATE: {"main": [to("Заявка корректна?")]},
        "Заявка корректна?": {"main": [to("AI: разбор заявки"), to("Ответ: ошибка")]},
        "AI: разбор заявки": {"main": [to(TRIAGE)]},
        TRIAGE: {"main": [to("Записать в таблицу")]},
        "Записать в таблицу": {"main": [to("Не спам?")]},
        "Не спам?": {"main": [to("Сообщение менеджеру"), to("Ответ сайту")]},
        "Сообщение менеджеру": {"main": [to("Ответ сайту")]},
    }
    return {
        "name": "Заявки с сайта → AI → Google Таблица + Telegram",
        "nodes": nodes,
        "connections": connections,
        "pinData": {},
        "settings": {"executionOrder": "v1"},
        "active": False,
        "meta": {"templateCredsSetupCompleted": False},
        "tags": [],
    }


if __name__ == "__main__":
    OUT.write_text(json.dumps(build(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"→ {OUT.relative_to(ROOT)}")
