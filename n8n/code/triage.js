// Категория и сводка: берём ответ нейросети, а если её нет или ответ битый — правила по ключевым словам.
// R подставляется из prompts/triage_rules.json скриптом scripts/build_workflow.py (тот же файл читает Python-версия).
const R = __SHARED_RULES__;
const lead = $('Проверка и очистка').item.json.lead;

function byRules() {
  const text = (lead.message || '').toLowerCase();
  let category = 'question';
  for (const [cat, pattern] of R.rules) {
    if (new RegExp(pattern, 'u').test(text)) { category = cat; break; }
  }
  if (category === 'question' && lead.product === 'wholesale') category = 'wholesale';
  else if (category === 'question' && R.order_products.includes(lead.product)) category = 'order';
  const label = R.labels_ru[category];
  const tpl = R.reply_templates[lead.lang] || R.reply_templates.ru;
  return {
    category, urgency: R.default_urgency[category],
    summary: lead.message ? `${label}: ${lead.message.slice(0, 120)}` : label,
    reply_draft: tpl.replace('{name}', lead.name), by: 'rules',
  };
}

function fromAI() {
  const content = $json.choices?.[0]?.message?.content;
  const match = content && content.match(/\{[\s\S]*\}/);   // модель иногда оборачивает JSON в ```
  if (!match) return null;
  let d;
  try { d = JSON.parse(match[0]); } catch (e) { return null; }
  const fb = byRules();
  const category = R.labels_ru[d.category] ? d.category : fb.category;
  const urgency = R.urgency_ru[d.urgency] ? d.urgency : R.default_urgency[category];
  return {
    category, urgency, by: 'ai',
    summary: String(d.summary || fb.summary).trim(),
    reply_draft: String(d.reply_draft || fb.reply_draft).trim(),
  };
}

const t = fromAI() || byRules();
const esc = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const lines = [
  `${R.icons[t.category]} <b>${R.labels_ru[t.category]}</b>` + (t.urgency === 'high' ? ' · <b>срочно</b>' : ''),
  `👤 ${esc(lead.name)} · <code>${esc(lead.phone)}</code>`,
  `📝 ${esc(t.summary)}`,
];
if (lead.message) lines.push(`💬 <i>${esc(lead.message.slice(0, 600))}</i>`);
lines.push(`✍️ Черновик ответа: ${esc(t.reply_draft)}`);
lines.push(`🌐 ${esc(lead.source)} · ${lead.lang} · ${lead.id}`);

return { json: {
  'Дата': lead.received_at, 'ID': lead.id, 'Имя': lead.name, 'Телефон': lead.phone,
  'Категория': R.labels_ru[t.category], 'Срочность': R.urgency_ru[t.urgency], 'Сводка': t.summary,
  'Сообщение': lead.message, 'Источник': lead.source, 'Язык': lead.lang,
  'Черновик ответа': t.reply_draft, 'Разбор': t.by === 'ai' ? 'AI' : 'правила',
  category: t.category, telegram_text: lines.join('\n'),
} };
