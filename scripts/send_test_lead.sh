#!/usr/bin/env bash
# Sends three test leads. Usage: scripts/send_test_lead.sh [URL]
#   n8n:     http://localhost:5678/webhook/lead   (default; use /webhook-test/lead while the editor is listening)
#   FastAPI: http://localhost:8000/lead
URL="${1:-http://localhost:5678/webhook/lead}"
post() { curl -s -w "  [%{http_code}]\n" -H 'Content-Type: application/json' -X POST "$URL" -d "$1"; }

echo "Заказ (RU):";   post '{"name":"Анна","phone":"8 (921) 123-45-67","message":"Таёжное разнотравье × 2. Доставка на Васильевский","source":"landing-ru","lang":"ru","product":"taiga","qty":2}'
echo "Опт (RU):";     post '{"name":"Олег","phone":"+7 911 000-11-22","message":"Мы сеть кофеен, интересует опт от 50 кг в месяц","source":"landing-ru","lang":"ru","product":"wholesale"}'
echo "Заказ (AR):";   post '{"name":"خالد","phone":"+971 50 123 4567","message":"صندوق هدايا × 3. التوصيل إلى دبي مارينا","source":"landing-ar","lang":"ar","product":"gift","qty":3}'
