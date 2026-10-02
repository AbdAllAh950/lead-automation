// Проверка и очистка заявки. Те же правила, что в api/app/leads.py.
const cfg = $('Настройки').first().json;
const raw = $json.body || {};
const clean = (s) => String(s ?? '').replace(/\s+/g, ' ').trim();
// Свои формы шлют JSON (name, phone…). Tilda шлёт поля формы как Name / Phone / Comments,
// а при подключении вебхука — проверочный запрос test=test, на него нужно ответить 200.
const pick = (...keys) => keys.map((k) => raw[k]).find((v) => v !== undefined && v !== '');
const b = {
  name: pick('name', 'Name'), phone: pick('phone', 'Phone'),
  message: pick('message', 'Comments', 'comments', 'comment', 'Textarea'),
  source: pick('source') || (raw.formname ? 'tilda: ' + raw.formname : raw.tranid ? 'tilda' : ''),
  lang: raw.lang, product: raw.product, qty: raw.qty, website: raw.website,
};

const name = clean(b.name).slice(0, 100);
const digits = clean(b.phone).replace(/\D/g, '');
let error = '';
if (raw.test === 'test') error = 'ping';
else if (clean(b.website)) error = 'honeypot';      // скрытое поле заполняют только боты
else if (name.length < 2) error = 'name';
else if (digits.length < 9 || digits.length > 15) error = 'phone';

let phone = '+' + digits;                             // 8 921… / 921… → +7 921…
if (digits.length === 11 && (digits[0] === '7' || digits[0] === '8')) phone = '+7' + digits.slice(1);
else if (digits.length === 10 && digits[0] === '9') phone = '+7' + digits;

const local = new Date().toLocaleString('sv-SE', { timeZone: cfg.timezone }).slice(0, 16);
const id = 'L-' + local.slice(0, 10).replace(/-/g, '') + '-' + local.slice(11, 16).replace(':', '') +
  '-' + Math.random().toString(16).slice(2, 6);
const lang = ['ru', 'ar', 'en'].includes(b.lang) ? b.lang : 'ru';
const message = clean(b.message).slice(0, 4000);
const source = clean(b.source) || 'сайт';
const product = b.product ? String(b.product) : null;

const lead = { id, received_at: local, name, phone, message, source, lang, product, qty: b.qty ?? null };
const llm_user = `Имя: ${name}\nТелефон: ${phone}\nИсточник: ${source}\nЯзык страницы: ${lang}\n` +
  `Товар: ${product || '-'}\nСообщение: ${message}`;

return { json: { valid: !error, error, lead, llm_user } };
