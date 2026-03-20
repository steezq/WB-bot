"""
ai_agent.py — Claude-powered agent that calls WB API as tools.
"""

import json
import logging
import aiohttp
from datetime import datetime, timedelta
from wb_api import WBApiClient
from config import ANTHROPIC_API_KEY

logger = logging.getLogger(__name__)

wb = WBApiClient()

# ── Tool definitions for Claude ────────────────────────────────────────────────

TOOLS = [
    {
        "name": "get_sales",
        "description": (
            "Получить данные о продажах за произвольный период. "
            "Можно передать конкретные даты (date_from, date_to) ИЛИ количество дней назад (days). "
            "Возвращает выручку, количество продаж, средний чек, топ товары и склады. "
            "Используй date_from/date_to для исторических запросов типа 'с января по март' или 'за сентябрь'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Количество дней назад от сегодня. Используй если нет конкретных дат."
                },
                "date_from": {
                    "type": "string",
                    "description": "Начало периода в формате YYYY-MM-DD, например 2024-09-01"
                },
                "date_to": {
                    "type": "string",
                    "description": "Конец периода в формате YYYY-MM-DD, например 2024-09-30"
                }
            },
            "required": []
        }
    },
    {
        "name": "get_orders",
        "description": (
            "Получить данные о заказах за произвольный период. "
            "Можно передать конкретные даты (date_from, date_to) ИЛИ количество дней назад (days). "
            "Возвращает общее количество, активные, отменённые заказы, топ регионы и артикулы. "
            "Используй date_from/date_to для исторических запросов."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Количество дней назад от сегодня."
                },
                "date_from": {
                    "type": "string",
                    "description": "Начало периода в формате YYYY-MM-DD, например 2024-09-01"
                },
                "date_to": {
                    "type": "string",
                    "description": "Конец периода в формате YYYY-MM-DD, например 2025-03-20"
                },
                "article": {
                    "type": "string",
                    "description": "Артикул товара для фильтрации. Если не указан — возвращает все товары."
                }
            },
            "required": []
        }
    },
    {
        "name": "get_sales_by_weeks",
        "description": (
            "Получить продажи разбитые по неделям за произвольный период. "
            "Используй для вопросов типа 'какая неделя была лучшей', 'динамика по неделям', "
            "'как менялись продажи с сентября'. Возвращает статистику по каждой неделе."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date_from": {
                    "type": "string",
                    "description": "Начало периода в формате YYYY-MM-DD"
                },
                "date_to": {
                    "type": "string",
                    "description": "Конец периода в формате YYYY-MM-DD"
                },
                "article": {
                    "type": "string",
                    "description": "Артикул товара для фильтрации. Если не указан — все товары."
                }
            },
            "required": ["date_from", "date_to"]
        }
    },
    {
        "name": "get_stocks",
        "description": "Получить остатки товаров на складах WB. Показывает количество по складам, заканчивающиеся и нулевые позиции.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "get_finance",
        "description": (
            "Получить финансовый отчёт: выплаты от WB, логистика, хранение, штрафы, "
            "удержания, чистая выручка и маржа."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Количество дней назад от сегодня."
                },
                "date_from": {
                    "type": "string",
                    "description": "Начало периода в формате YYYY-MM-DD"
                },
                "date_to": {
                    "type": "string",
                    "description": "Конец периода в формате YYYY-MM-DD"
                }
            },
            "required": []
        }
    },
    {
        "name": "compare_periods",
        "description": (
            "Сравнить два периода по продажам и заказам. "
            "Показывает рост/падение выручки, заказов, среднего чека."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Длина периода в днях. Текущий период — последние N дней, предыдущий — N дней до этого."
                }
            },
            "required": ["days"]
        }
    }
]

# ── Helpers ────────────────────────────────────────────────────────────────────

def resolve_dates(tool_input: dict) -> tuple[str, str]:
    """Return (date_from_iso, date_to_iso) from either days or explicit dates."""
    if tool_input.get("date_from") and tool_input.get("date_to"):
        df = tool_input["date_from"]
        dt = tool_input["date_to"]
        return f"{df}T00:00:00", f"{dt}T23:59:59"
    days = tool_input.get("days", 30)
    return wb.date_range(days)


def filter_by_period(items: list, date_from: str, date_to: str) -> list:
    """Filter records to exact period window."""
    try:
        s = datetime.fromisoformat(date_from)
        e = datetime.fromisoformat(date_to)
        result = []
        for item in items:
            date_str = item.get("date") or item.get("lastChangeDate") or ""
            if not date_str:
                result.append(item)
                continue
            try:
                dt = datetime.fromisoformat(date_str.replace("Z", "").split("+")[0][:19])
                if s <= dt <= e:
                    result.append(item)
            except Exception:
                continue
        return result
    except Exception:
        return items


def filter_by_article(items: list, article: str) -> list:
    if not article:
        return items
    article_lower = article.lower()
    return [
        i for i in items
        if article_lower in str(i.get("supplierArticle", "")).lower()
        or article_lower in str(i.get("nmId", "")).lower()
    ]


# ── Tool executors ─────────────────────────────────────────────────────────────

async def _run_get_sales(tool_input: dict) -> str:
    date_from, date_to = resolve_dates(tool_input)
    sales_raw = await wb.get_sales(date_from)
    sales = filter_by_period(sales_raw, date_from, date_to)

    article = tool_input.get("article", "")
    if article:
        sales = filter_by_article(sales, article)

    if not sales:
        return "Нет данных по продажам за указанный период."

    revenue = sum(s.get("finishedPrice", 0) or 0 for s in sales)
    count = len(sales)
    avg = revenue / count if count else 0

    products: dict[str, dict] = {}
    for s in sales:
        name = str(s.get("supplierArticle") or s.get("nmId", "?"))
        if name not in products:
            products[name] = {"count": 0, "revenue": 0.0}
        products[name]["count"] += 1
        products[name]["revenue"] += s.get("finishedPrice", 0) or 0
    top = sorted(products.items(), key=lambda x: x[1]["revenue"], reverse=True)[:5]

    warehouses: dict[str, int] = {}
    for s in sales:
        wh = s.get("warehouseName") or "Неизвестно"
        warehouses[wh] = warehouses.get(wh, 0) + 1
    top_wh = sorted(warehouses.items(), key=lambda x: x[1], reverse=True)[:3]

    result = {
        "period": f"{date_from[:10]} — {date_to[:10]}",
        "total_revenue_rub": round(revenue, 2),
        "total_sales_count": count,
        "average_check_rub": round(avg, 2),
        "top_products": [
            {"article": art, "revenue_rub": round(d["revenue"], 2), "count": d["count"]}
            for art, d in top
        ],
        "top_warehouses": [{"name": wh, "count": cnt} for wh, cnt in top_wh]
    }
    return json.dumps(result, ensure_ascii=False)


async def _run_get_orders(tool_input: dict) -> str:
    date_from, date_to = resolve_dates(tool_input)
    orders_raw = await wb.get_orders(date_from)
    orders = filter_by_period(orders_raw, date_from, date_to)

    article = tool_input.get("article", "")
    if article:
        orders = filter_by_article(orders, article)

    if not orders:
        return "Нет данных по заказам за указанный период."

    total = len(orders)
    cancelled = sum(1 for o in orders if o.get("isCancel") is True)
    active = total - cancelled
    cancel_rate = round(cancelled / total * 100, 1) if total else 0
    total_sum = sum(o.get("finishedPrice", 0) or 0 for o in orders)

    regions: dict[str, int] = {}
    for o in orders:
        reg = o.get("regionName") or o.get("oblast") or "Не указан"
        regions[reg] = regions.get(reg, 0) + 1
    top_regions = sorted(regions.items(), key=lambda x: x[1], reverse=True)[:5]

    articles: dict[str, int] = {}
    for o in orders:
        art = str(o.get("supplierArticle") or o.get("nmId", "?"))
        articles[art] = articles.get(art, 0) + 1
    top_art = sorted(articles.items(), key=lambda x: x[1], reverse=True)[:5]

    result = {
        "period": f"{date_from[:10]} — {date_to[:10]}",
        "total_orders": total,
        "active_orders": active,
        "cancelled_orders": cancelled,
        "cancel_rate_percent": cancel_rate,
        "total_sum_rub": round(total_sum, 2),
        "top_regions": [{"name": r, "count": c} for r, c in top_regions],
        "top_articles": [{"article": a, "count": c} for a, c in top_art]
    }
    return json.dumps(result, ensure_ascii=False)


async def _run_get_sales_by_weeks(tool_input: dict) -> str:
    date_from = f"{tool_input['date_from']}T00:00:00"
    date_to = f"{tool_input['date_to']}T23:59:59"
    article = tool_input.get("article", "")

    sales_raw = await wb.get_sales(date_from)
    sales = filter_by_period(sales_raw, date_from, date_to)

    if article:
        sales = filter_by_article(sales, article)

    if not sales:
        return "Нет данных о продажах за указанный период."

    # Group by week
    weeks: dict[str, dict] = {}
    for s in sales:
        date_str = s.get("date") or s.get("lastChangeDate") or ""
        if not date_str:
            continue
        try:
            dt = datetime.fromisoformat(date_str.replace("Z", "").split("+")[0][:19])
            # Week start = Monday
            week_start = dt - timedelta(days=dt.weekday())
            week_key = week_start.strftime("%Y-%m-%d")
            week_label = f"{week_start.strftime('%d.%m')}–{(week_start + timedelta(days=6)).strftime('%d.%m.%Y')}"
            if week_key not in weeks:
                weeks[week_key] = {"label": week_label, "count": 0, "revenue": 0.0}
            weeks[week_key]["count"] += 1
            weeks[week_key]["revenue"] += s.get("finishedPrice", 0) or 0
        except Exception:
            continue

    if not weeks:
        return "Не удалось разбить данные по неделям."

    sorted_weeks = sorted(weeks.items(), key=lambda x: x[0])
    best_week = max(weeks.items(), key=lambda x: x[1]["revenue"])
    best_by_count = max(weeks.items(), key=lambda x: x[1]["count"])

    weeks_list = [
        {
            "week": v["label"],
            "sales_count": v["count"],
            "revenue_rub": round(v["revenue"], 2)
        }
        for _, v in sorted_weeks
    ]

    result = {
        "period": f"{tool_input['date_from']} — {tool_input['date_to']}",
        "article_filter": article or "все товары",
        "total_weeks": len(weeks),
        "best_week_by_revenue": {
            "week": best_week[1]["label"],
            "revenue_rub": round(best_week[1]["revenue"], 2),
            "sales_count": best_week[1]["count"]
        },
        "best_week_by_count": {
            "week": best_by_count[1]["label"],
            "sales_count": best_by_count[1]["count"],
            "revenue_rub": round(best_by_count[1]["revenue"], 2)
        },
        "weeks": weeks_list
    }
    return json.dumps(result, ensure_ascii=False)


async def _run_get_stocks() -> str:
    date_from, _ = wb.date_range(30)
    stocks = await wb.get_stocks(date_from)
    if not stocks:
        return "Нет данных по складским остаткам."

    total_qty = sum(s.get("quantity", 0) or 0 for s in stocks)
    total_sku = len(set(str(s.get("nmId", "")) for s in stocks))
    out_of_stock = sum(1 for s in stocks if (s.get("quantity") or 0) == 0)

    warehouses: dict[str, dict] = {}
    for s in stocks:
        wh = s.get("warehouseName") or "Неизвестно"
        if wh not in warehouses:
            warehouses[wh] = {"qty": 0, "skus": set()}
        warehouses[wh]["qty"] += s.get("quantity", 0) or 0
        warehouses[wh]["skus"].add(str(s.get("nmId", "")))
    top_wh = sorted(warehouses.items(), key=lambda x: x[1]["qty"], reverse=True)[:5]

    low_stock = [
        str(s.get("supplierArticle") or s.get("nmId", "?"))
        for s in stocks
        if 0 < (s.get("quantity") or 0) < 5
    ]

    result = {
        "total_units": total_qty,
        "unique_skus": total_sku,
        "out_of_stock_positions": out_of_stock,
        "top_warehouses": [
            {"name": wh, "units": d["qty"], "skus": len(d["skus"])}
            for wh, d in top_wh
        ],
        "low_stock_articles": list(set(low_stock))[:15]
    }
    return json.dumps(result, ensure_ascii=False)


async def _run_get_finance(tool_input: dict) -> str:
    date_from, date_to = resolve_dates(tool_input)
    rows = await wb.get_report_detail(date_from[:10], date_to[:10])
    if not rows:
        return (
            "Нет финансовых данных за указанный период. "
            "Отчёт формируется еженедельно по пятницам."
        )

    retail = sum(r.get("retail_amount", 0) or 0 for r in rows)
    for_pay = sum(r.get("ppvz_for_pay", 0) or 0 for r in rows)
    delivery = sum(r.get("delivery_rub", 0) or 0 for r in rows)
    storage = sum(r.get("storage_fee", 0) or 0 for r in rows)
    penalty = sum(r.get("penalty", 0) or 0 for r in rows)
    deduction = sum(r.get("deduction", 0) or 0 for r in rows)
    acceptance = sum(r.get("acceptance", 0) or 0 for r in rows)
    net = for_pay - delivery - storage - penalty - deduction - acceptance
    margin = round(net / retail * 100, 1) if retail else 0

    subjects: dict[str, float] = {}
    for r in rows:
        s = str(r.get("subject_name") or r.get("nm_id") or "Прочее")
        subjects[s] = subjects.get(s, 0.0) + (r.get("ppvz_for_pay", 0) or 0)
    top_subj = sorted(subjects.items(), key=lambda x: x[1], reverse=True)[:5]

    result = {
        "period": f"{date_from[:10]} — {date_to[:10]}",
        "retail_amount_rub": round(retail, 2),
        "wb_payout_rub": round(for_pay, 2),
        "logistics_rub": round(delivery, 2),
        "storage_rub": round(storage, 2),
        "penalties_rub": round(penalty, 2),
        "deductions_rub": round(deduction, 2),
        "acceptance_rub": round(acceptance, 2),
        "net_revenue_rub": round(net, 2),
        "margin_percent": margin,
        "top_categories": [
            {"name": s, "revenue_rub": round(v, 2)} for s, v in top_subj
        ]
    }
    return json.dumps(result, ensure_ascii=False)


async def _run_compare_periods(days: int) -> str:
    import asyncio as aio

    now = datetime.now()
    fmt = "%Y-%m-%dT%H:%M:%S"
    cur_start = (now - timedelta(days=days)).strftime(fmt)
    prev_start = (now - timedelta(days=days * 2)).strftime(fmt)
    cur_end = now.strftime(fmt)
    prev_end = (now - timedelta(days=days)).strftime(fmt)

    cur_sales_raw, prev_sales_raw, cur_orders_raw, prev_orders_raw = await aio.gather(
        wb.get_sales(cur_start),
        wb.get_sales(prev_start),
        wb.get_orders(cur_start),
        wb.get_orders(prev_start),
    )

    cur_sales = filter_by_period(cur_sales_raw, cur_start, cur_end)
    prev_sales = filter_by_period(prev_sales_raw, prev_start, prev_end)
    cur_orders = filter_by_period(cur_orders_raw, cur_start, cur_end)
    prev_orders = filter_by_period(prev_orders_raw, prev_start, prev_end)

    def _sales_stats(items):
        rev = sum(s.get("finishedPrice", 0) or 0 for s in items)
        cnt = len(items)
        return {"revenue": round(rev, 2), "count": cnt, "avg": round(rev / cnt if cnt else 0, 2)}

    def _orders_stats(items):
        total = len(items)
        cancelled = sum(1 for o in items if o.get("isCancel") is True)
        return {
            "total": total,
            "cancelled": cancelled,
            "cancel_rate": round(cancelled / total * 100, 1) if total else 0
        }

    def _delta(cur, prev):
        if prev == 0:
            return None
        return round((cur - prev) / prev * 100, 1)

    cs, ps = _sales_stats(cur_sales), _sales_stats(prev_sales)
    co, po = _orders_stats(cur_orders), _orders_stats(prev_orders)

    result = {
        "current_period": f"{cur_start[:10]} — {cur_end[:10]}",
        "previous_period": f"{prev_start[:10]} — {prev_end[:10]}",
        "current": {"sales": cs, "orders": co},
        "previous": {"sales": ps, "orders": po},
        "growth": {
            "revenue_percent": _delta(cs["revenue"], ps["revenue"]),
            "sales_count_percent": _delta(cs["count"], ps["count"]),
            "avg_check_percent": _delta(cs["avg"], ps["avg"]),
            "orders_percent": _delta(co["total"], po["total"]),
        }
    }
    return json.dumps(result, ensure_ascii=False)


# ── Tool dispatcher ────────────────────────────────────────────────────────────

async def execute_tool(name: str, tool_input: dict) -> str:
    try:
        if name == "get_sales":
            return await _run_get_sales(tool_input)
        elif name == "get_orders":
            return await _run_get_orders(tool_input)
        elif name == "get_sales_by_weeks":
            return await _run_get_sales_by_weeks(tool_input)
        elif name == "get_stocks":
            return await _run_get_stocks()
        elif name == "get_finance":
            return await _run_get_finance(tool_input)
        elif name == "compare_periods":
            return await _run_compare_periods(tool_input["days"])
        else:
            return f"Неизвестный инструмент: {name}"
    except Exception as e:
        logger.error(f"Tool {name} error: {e}")
        return f"Ошибка при выполнении {name}: {str(e)}"


# ── Main agent function ────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Ты — персональный аналитик магазина на Wildberries.
Ты помогаешь продавцу анализировать его бизнес через удобный чат.

Твои возможности (через инструменты):
- Продажи: выручка, средний чек, топ товары — за любой период (конкретные даты или последние N дней)
- Заказы: количество, отмены, регионы, фильтр по артикулу — за любой период
- Склад: остатки, заканчивающиеся товары
- Финансы: чистая выручка, логистика, штрафы, маржа — за любой период
- Сравнение периодов: рост/падение показателей
- Анализ по неделям: какая неделя была лучшей за любой период

Правила работы с датами:
- Если пользователь называет конкретные даты — используй date_from и date_to
- Если называет месяц — бери с 1-го по последнее число месяца
- Если говорит "с сентября" — бери с 2024-09-01 до сегодня
- Текущий год 2026, прошлый год 2025
- Для анализа по неделям используй инструмент get_sales_by_weeks

Правила ответов:
- Пиши по-русски, живым и понятным языком
- Используй цифры и делай выводы, не просто перечисляй данные
- Выдели самое важное в начале ответа
- Если видишь проблему (много отмен, товар заканчивается, штрафы растут) — скажи об этом явно
- Форматируй ответ с эмодзи для читаемости в Telegram
- НИКОГДА не используй символы ** для выделения текста — Telegram бот не поддерживает markdown
- Используй только эмодзи и обычный текст для акцентов
- Если данных нет или период не покрыт отчётом WB — объясни почему
- Если вопрос не про аналитику — вежливо объясни специализацию
- ВАЖНО: если в разговоре уже упоминался конкретный артикул и пользователь продолжает про него говорить (например "а сколько заказов?", "покажи склад", "а за прошлый месяц?") — ВСЕГДА передавай этот артикул в параметр article инструмента. Не теряй контекст артикула между сообщениями.
- Если пользователь говорит "этот товар", "он", "по нему", "за тот же период" — используй артикул и даты из предыдущих сообщений разговора.

Сегодняшняя дата: """ + datetime.now().strftime("%d.%m.%Y")


async def ask_agent(user_message: str, history: list[dict]) -> str:
    messages = history + [{"role": "user", "content": user_message}]

    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    async with aiohttp.ClientSession() as session:
        for _ in range(8):
            payload = {
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 2048,
                "system": SYSTEM_PROMPT,
                "tools": TOOLS,
                "messages": messages,
            }

            async with session.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=payload
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error(f"Anthropic API error {resp.status}: {text}")
                    return "⚠️ Ошибка при обращении к AI. Попробуй ещё раз."

                data = await resp.json()

            stop_reason = data.get("stop_reason")
            content = data.get("content", [])

            if stop_reason == "tool_use":
                messages.append({"role": "assistant", "content": content})
                tool_results = []
                for block in content:
                    if block.get("type") == "tool_use":
                        tool_result = await execute_tool(block["name"], block.get("input", {}))
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block["id"],
                            "content": tool_result
                        })
                messages.append({"role": "user", "content": tool_results})
                continue

            for block in content:
                if block.get("type") == "text":
                    return block["text"]

            return "⚠️ Не удалось получить ответ от AI."

    return "⚠️ Превышено количество шагов агента."
