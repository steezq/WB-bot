# WB Analytics Telegram Bot

Бот присылает ежедневный отчёт по Wildberries и отвечает на вопросы об аналитике.

## Возможности
- `/report` — отчёт прямо сейчас (продажи, выручка, возвраты)
- `/stock` — остатки по товарам с цветовыми статусами
- `/ads` — статистика рекламных кампаний (CTR, CPO, расходы)
- `/ask [вопрос]` — AI-аналитик отвечает на твой вопрос
- Автоотчёт каждый день в заданное время

---

## Установка (шаг за шагом)

### 1. Получи токен Telegram-бота
1. Напиши @BotFather в Telegram
2. `/newbot` → придумай имя → получи токен вида `123456:AAxxxxxx`

### 2. Узнай свой Telegram ID
1. Напиши @userinfobot в Telegram
2. Скопируй число из поля `Id`

### 3. Получи API-ключ WB
1. Личный кабинет WB → Настройки → Доступ к API
2. Создай ключ с правами: **Статистика**, **Аналитика**, **Реклама**

### 4. Получи Anthropic API ключ (для AI-советов)
1. Зайди на console.anthropic.com
2. API Keys → Create Key
3. Пополни баланс (от $5, хватит на тысячи отчётов)

### 5. Установи Python и зависимости
```bash
# Нужен Python 3.10+
python3 --version

# Установи зависимости
pip install -r requirements.txt
```

### 6. Настрой переменные окружения
```bash
cp .env.example .env
nano .env   # заполни все значения
```

### 7. Запусти бота
```bash
# Загрузи .env и запусти
export $(cat .env | xargs) && python bot.py
```

---

## Запуск на VPS (автозапуск через systemd)

Создай файл `/etc/systemd/system/wbbot.service`:
```
[Unit]
Description=WB Analytics Bot
After=network.target

[Service]
WorkingDirectory=/home/ubuntu/wb_bot
EnvironmentFile=/home/ubuntu/wb_bot/.env
ExecStart=/usr/bin/python3 /home/ubuntu/wb_bot/bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Затем:
```bash
sudo systemctl daemon-reload
sudo systemctl enable wbbot
sudo systemctl start wbbot
sudo systemctl status wbbot
```

---

## Структура проекта
```
wb_bot/
├── bot.py          — главный файл, команды Telegram
├── wb_api.py       — работа с API Wildberries
├── ai_advisor.py   — AI-советы через Anthropic
├── requirements.txt
└── .env.example    — шаблон конфигурации
```

## Права API-ключа WB (минимум)
| Раздел       | Нужно |
|--------------|-------|
| Статистика   | ✅    |
| Аналитика    | ✅    |
| Реклама      | ✅ (для /ads) |
| Контент      | ❌ не обязательно |
