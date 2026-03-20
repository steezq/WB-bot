"""
ai_agent.py — Claude-powered agent that calls WB API as tools.

Flow:
  user message
    → Claude decides which tools to call
    → we execute WB API calls
    → Claude formulates final answer
"""

import json
import logging
import aiohttp
from datetime import datetime
from wb_api import WBApiClient
from config import ANTHROPIC_API_KEY

logger = logging.getLogger(__name__)

wb = WBApiClient()

# ── Tool definitions for Claude ────────────────────────────────────────────────

TOOLS = [
    {
        "name": "get_sales",
        "description": (
            "Получить данные о продажах за указанный период. "
            "Возвращает выручку, количество продаж, средний чек, топ товары и склады."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Количество дней назад от сегодня. Например: 7, 14, 30."
                }
            },
            "required": ["days"]
        }
    },
    {
        "name": "get_orders",
        "description": (
            "Получить данные о заказах за указанный период. "
            "Возвращает общее количество, активные, отменённые заказы, топ регионы и артикулы."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Количество дней назад от сегодня."
                }
            },
            "required": ["days"]
        }
    },
    {
        "name": "get_stocks",
        "description": (
            "Получить остатки товаров на складах WB. "
            "Показывает количество по складам, заканчивающиеся и нулевые позиции."
        ),
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
                }
            },
            "required": ["days"]
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
                    "description": (
                        "Длина периода в днях. Текущий период — последние N дней, "
                        "предыдущий — N дней до этого."
                    )
                }
            },
            "required": ["days"]
        }
    }
]

# ── Tool executors ─────────────────────────────────────────────────────────────

async def _run_get_sales(days: int) -> str:
    date_from, _ = wb.date_range(days)
    sales = await wb.get_sales(date_from)
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
        "period_days": days,
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


async def _run_get_orders(days: int) -> str:
    date_from, _ = wb.date_range(days)
    orders = await wb.get_orders(date_from)
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
        "period_days": days,
        "total_orders": total,
        "active_orders": active,
        "cancelled_orders": cancelled,
        "cancel_rate_percent": cancel_rate,
        "total_sum_rub": round(total_sum, 2),
        "top_regions": [{"name": r, "count": c} for r, c in top_regions],
        "top_articles": [{"article": a, "count": c} for a, c in top_art]
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


async def _run_get_finance(days: int) -> str:
    date_from, date_to = wb.date_range(days)
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
        "period_days": days,
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
    from datetime import timedelta
    import asyncio as aio

    now = datetime.now()
    fmt = "%Y-%m-%dT%H:%M:%S"
    cur_start = (now - timedelta(days=days)).strftime(fmt)
    prev_start = (now - timedelta(days=days * 2)).strftime(fmt)

    cur_sales, prev_sales, cur_orders, prev_orders = await aio.gather(
        wb.get_sales(cur_start),
        wb.get_sales(prev_start),
        wb.get_orders(cur_start),
        wb.get_orders(prev_start),
    )

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
        "period_days": days,
        "current_period": {
            "sales": cs,
            "orders": co
        },
        "previous_period": {
            "sales": ps,
            "orders": po
        },
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
            return await _run_get_sales(tool_input["days"])
        elif name == "get_orders":
            return await _run_get_orders(tool_input["days"])
        elif name == "get_stocks":
            return await _run_get_stocks()
        elif name == "get_finance":
            return await _run_get_finance(tool_input["days"])
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
- Продажи: выручка, средний чек, топ товары
- Заказы: количество, отмены, регионы
- Склад: остатки, заканчивающиеся товары
- Финансы: чистая выручка, логистика, штрафы, маржа
- Сравнение периодов: рост/падение показателей

Правила ответов:
- Пиши по-русски, живым и понятным языком
- Используй цифры и делай выводы, не просто перечисляй данные
- Выдели самое важное в начале ответа
- Если видишь проблему (много отмен, товар заканчивается, штрафы растут) — скажи об этом явно
- Форматируй ответ с эмодзи для читаемости в Telegram
- Если данных нет или период не покрыт отчётом WB — объясни почему
- Если вопрос не про аналитику — вежливо объясни, что ты специализируешься только на WB-аналитике

Сегодняшняя дата: """ + datetime.now().strftime("%d.%m.%Y")


async def ask_agent(user_message: str, history: list[dict]) -> str:
    """
    Send user message to Claude with WB tools.
    history — list of {"role": "user"|"assistant", "content": "..."} dicts
    Returns Claude's final text response.
    """
    messages = history + [{"role": "user", "content": user_message}]

    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    async with aiohttp.ClientSession() as session:
        # Agentic loop — Claude may call multiple tools
        for _ in range(5):  # max 5 tool-call rounds
            payload = {
                "model": "claude-sonnet-4-5",
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

            # If Claude wants to use tools
            if stop_reason == "tool_use":
                # Add Claude's response to history
                messages.append({"role": "assistant", "content": content})

                # Execute all requested tools
                tool_results = []
                for block in content:
                    if block.get("type") == "tool_use":
                        tool_result = await execute_tool(block["name"], block.get("input", {}))
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block["id"],
                            "content": tool_result
                        })

                # Add tool results and continue loop
                messages.append({"role": "user", "content": tool_results})
                continue

            # Claude finished — extract text
            for block in content:
                if block.get("type") == "text":
                    return block["text"]

            return "⚠️ Не удалось получить ответ от AI."

    return "⚠️ Превышено количество шагов агента."
