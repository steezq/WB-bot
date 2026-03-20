import asyncio
import logging
import os
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import wb_api
import ai_advisor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
WB_API_KEY = os.getenv("WB_API_KEY", "")
CHAT_ID = os.getenv("CHAT_ID", "")
REPORT_HOUR = int(os.getenv("REPORT_HOUR", "9"))  # час отправки ежедневного отчёта

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()


# ─── Команды ────────────────────────────────────────────────────────────────

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Я WB Analytics Bot.\n\n"
        "Команды:\n"
        "/report — ежедневный отчёт прямо сейчас\n"
        "/stock — остатки по складам\n"
        "/ads — статистика рекламы\n"
        "/ask [вопрос] — задать вопрос AI-аналитику\n\n"
        f"Автоотчёт приходит каждый день в {REPORT_HOUR}:00 🕘"
    )


@dp.message(Command("report"))
async def cmd_report(message: Message):
    await message.answer("⏳ Собираю данные...")
    try:
        text = await build_daily_report()
        await message.answer(text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Report error: {e}")
        await message.answer(f"❌ Ошибка при получении данных: {e}")


@dp.message(Command("stock"))
async def cmd_stock(message: Message):
    await message.answer("⏳ Загружаю остатки...")
    try:
        stocks = await wb_api.get_stocks(WB_API_KEY)
        if not stocks:
            await message.answer("Нет данных по остаткам.")
            return
        lines = ["📦 *Остатки по товарам:*\n"]
        for item in stocks[:20]:
            qty = item.get("quantity", 0)
            name = item.get("subject", "Без названия")
            article = item.get("supplierArticle", "—")
            emoji = "🔴" if qty <= 5 else "🟡" if qty <= 20 else "🟢"
            lines.append(f"{emoji} {name} (арт. {article}): *{qty} шт.*")
        await message.answer("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


@dp.message(Command("ads"))
async def cmd_ads(message: Message):
    await message.answer("⏳ Загружаю данные по рекламе...")
    try:
        ads = await wb_api.get_ads_stats(WB_API_KEY)
        if not ads:
            await message.answer("Нет данных по рекламе (или нет активных кампаний).")
            return
        lines = ["📢 *Статистика рекламы:*\n"]
        total_spend = 0
        for camp in ads[:10]:
            spend = camp.get("sum", 0)
            total_spend += spend
            name = camp.get("advertName", "Кампания")
            ctr = camp.get("ctr", 0)
            cpo = camp.get("cpo", 0)
            views = camp.get("views", 0)
            clicks = camp.get("clicks", 0)
            lines.append(
                f"• *{name}*\n"
                f"  Расход: {spend:,.0f} ₽ | CTR: {ctr:.1f}% | CPO: {cpo:,.0f} ₽\n"
                f"  Показы: {views:,} | Клики: {clicks:,}"
            )
        lines.append(f"\n💸 Итого расходов: *{total_spend:,.0f} ₽*")
        await message.answer("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


@dp.message(Command("ask"))
async def cmd_ask(message: Message):
    question = message.text.replace("/ask", "").strip()
    if not question:
        await message.answer("Напиши вопрос после команды, например:\n/ask почему растут возвраты?")
        return
    await message.answer("🤔 Думаю...")
    try:
        data = await wb_api.get_all_data(WB_API_KEY)
        context = wb_api.build_context(data)
        answer = await ai_advisor.ask(question, context)
        await message.answer(f"🤖 {answer}", parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"❌ Ошибка AI: {e}")


# ─── Построение ежедневного отчёта ──────────────────────────────────────────

async def build_daily_report() -> str:
    data = await wb_api.get_all_data(WB_API_KEY)
    context = wb_api.build_context(data)
    tips = await ai_advisor.get_tips(context)

    sales = data.get("sales", [])
    today = datetime.now().strftime("%Y-%m-%d")

    today_sales = [s for s in sales if s.get("date", "").startswith(today) and s.get("saleID", "").startswith("S")]
    today_returns = [s for s in sales if s.get("date", "").startswith(today) and s.get("saleID", "").startswith("R")]

    revenue = sum(s.get("finishedPrice", 0) for s in today_sales)
    orders = len(today_sales)
    returns = len(today_returns)
    return_rate = round(returns / max(orders + returns, 1) * 100, 1)

    stocks = data.get("stocks", [])
    critical = [s for s in stocks if s.get("quantity", 0) <= 5]

    date_str = datetime.now().strftime("%d.%m.%Y")

    lines = [
        f"📊 *Ежедневный отчёт WB — {date_str}*",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "💰 *Продажи и выручка*",
        f"  Выручка: *{revenue:,.0f} ₽*",
        f"  Заказов: *{orders}*",
        f"  Возвратов: *{returns}* ({return_rate}%)",
        "",
        "📦 *Склад*",
    ]

    if critical:
        lines.append(f"  🔴 Критически мало у {len(critical)} товаров:")
        for s in critical[:5]:
            lines.append(f"     • {s.get('subject', '—')} — {s.get('quantity', 0)} шт.")
    else:
        lines.append("  🟢 Остатки в норме")

    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "🤖 *Советы AI-аналитика:*",
        "",
        tips
    ]

    return "\n".join(lines)


async def send_scheduled_report():
    if not CHAT_ID:
        logger.warning("CHAT_ID не задан, автоотчёт не отправлен")
        return
    try:
        logger.info("Отправка ежедневного отчёта...")
        text = await build_daily_report()
        await bot.send_message(chat_id=CHAT_ID, text=text, parse_mode="Markdown")
        logger.info("Отчёт отправлен успешно")
    except Exception as e:
        logger.error(f"Ошибка отправки отчёта: {e}")


# ─── Запуск ─────────────────────────────────────────────────────────────────

async def main():
    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
    scheduler.add_job(send_scheduled_report, "cron", hour=REPORT_HOUR, minute=0)
    scheduler.start()
    logger.info(f"Бот запущен. Автоотчёт в {REPORT_HOUR}:00 МСК")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
