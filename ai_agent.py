import json
import logging
import aiohttp
from datetime import datetime, timedelta
from wb_api import WBApiClient
from config import ANTHROPIC_API_KEY

logger = logging.getLogger(__name__)
wb = WBApiClient()

# ── Tools ──────────────────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "get_sales",
        "description": "Получить данные о продажах за произвольный период. Можно передать конкретные даты (date_from, date_to) ИЛИ количество дней назад (days). Возвращает выручку, количество продаж, средний чек, топ товары и склады.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Количество дней назад от сегодня."},
                "date_from": {"type": "string", "description": "Начало периода YYYY-MM-DD"},
                "date_to": {"type": "string", "description": "Конец периода YYYY-MM-DD"},
                "article": {"type": "string", "description": "Артикул товара для фильтрации."}
            },
            "required": []
        }
    },
    {
        "name": "get_orders",
        "description": "Получить данные о заказах за произвольный период. Можно передать конкретные даты или количество дней. Возвращает количество, отмены, регионы, артикулы.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Количество дней назад от сегодня."},
                "date_from": {"type": "string", "description": "Начало периода YYYY-MM-DD"},
                "date_to": {"type": "string", "description": "Конец периода YYYY-MM-DD"},
                "article": {"type": "string", "description": "Артикул товара для фильтрации."}
            },
            "required": []
        }
    },
    {
        "name": "get_sales_by_weeks",
        "description": "Получить продажи разбитые по неделям. Используй для вопросов типа 'какая неделя была лучшей', 'динамика по неделям'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "description": "Начало периода YYYY-MM-DD"},
                "date_to": {"type": "string", "description": "Конец периода YYYY-MM-DD"},
                "article": {"type": "string", "description": "Артикул товара для фильтрации."}
            },
            "required": ["date_from", "date_to"]
        }
    },
    {
        "name": "get_stocks",
        "description": "Получить остатки товаров на складах WB.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "get_finance",
        "description": "Получить финансовый отчёт: выплаты WB, логистика, хранение, штрафы, чистая выручка, маржа.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Количество дней назад от сегодня."},
                "date_from": {"type": "string", "description": "Начало периода YYYY-MM-DD"},
                "date_to": {"type": "string", "description": "Конец периода YYYY-MM-DD"}
            },
            "required": []
        }
    },
    {
        "name": "get_adv_summary",
        "description": "Получить сводную статистику по рекламным кампаниям: показы, клики, CTR, расходы, заказы с рекламы, ДРР. Используй для вопросов про рекламу, продвижение, рекламные расходы.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Количество дней назад от сегодня. По умолчанию 7."},
                "date_from": {"type": "string", "description": "Начало периода YYYY-MM-DD"},
                "date_to": {"type": "string", "description": "Конец периода YYYY-MM-DD"}
            },
            "required": []
        }
    },
    {
        "name": "get_adv_campaigns",
        "description": "Получить список всех рекламных кампаний с их статусами и бюджетами.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "get_adv_balance",
        "description": "Получить текущий баланс рекламного кабинета WB.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "compare_periods",
        "description": "Сравнить два периода по продажам и заказам.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Длина периода в днях."}
            },
            "required": ["days"]
        }
    }
]

# ── Helpers ────────────────────────────────────────────────────────────────────

def resolve_dates(tool_input: dict) -> tuple[str, str]:
    if tool_input.get("date_from") and tool_input.get("date_to"):
        return f"{tool_input['date_from']}T00:00:00", f"{tool_input['date_to']}T23:59:59"
    days = tool_input.get("days", 30)
    return wb.date_range(days)

def resolve_dates_simple(tool_input: dict, default_days: int = 7) -> tuple[str, str]:
    if tool_input.get("date_from") and tool_input.get("date_to"):
        return tool_input["date_from"], tool_input["date_to"]
    days = tool_input.get("days", default_days)
    return wb.date_range_simple(days)

def filter_by_period(items: list, date_from: str, date_to: str) -> list:
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
    sales = filter_by_period(await wb.get_sales(date_from), date_from, date_to)
    if tool_input.get("article"):
        sales = filter_by_article(sales, tool_input["article"])
    if not sales:
        return "Нет данных по продажам за указанный период."

    revenue = sum(s.get("finishedPrice", 0) or 0 for s in sales)
    count = len(sales)
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
    return json.dumps({
        "period": f"{date_from[:10]} — {date_to[:10]}",
        "total_revenue_rub": round(revenue, 2),
        "total_sales_count": count,
        "average_check_rub": round(revenue / count if count else 0, 2),
        "top_products": [{"article": a, "revenue_rub": round(d["revenue"], 2), "count": d["count"]} for a, d in top],
        "top_warehouses": [{"name": w, "count": c} for w, c in sorted(warehouses.items(), key=lambda x: x[1], reverse=True)[:3]]
    }, ensure_ascii=False)


async def _run_get_orders(tool_input: dict) -> str:
    date_from, date_to = resolve_dates(tool_input)
    orders = filter_by_period(await wb.get_orders(date_from), date_from, date_to)
    if tool_input.get("article"):
        orders = filter_by_article(orders, tool_input["article"])
    if not orders:
        return "Нет данных по заказам за указанный период."

    total = len(orders)
    cancelled = sum(1 for o in orders if o.get("isCancel") is True)
    regions: dict[str, int] = {}
    articles: dict[str, int] = {}
    for o in orders:
        reg = o.get("regionName") or o.get("oblast") or "Не указан"
        regions[reg] = regions.get(reg, 0) + 1
        art = str(o.get("supplierArticle") or o.get("nmId", "?"))
        articles[art] = articles.get(art, 0) + 1
    return json.dumps({
        "period": f"{date_from[:10]} — {date_to[:10]}",
        "total_orders": total,
        "active_orders": total - cancelled,
        "cancelled_orders": cancelled,
        "cancel_rate_percent": round(cancelled / total * 100, 1) if total else 0,
        "total_sum_rub": round(sum(o.get("finishedPrice", 0) or 0 for o in orders), 2),
        "top_regions": [{"name": r, "count": c} for r, c in sorted(regions.items(), key=lambda x: x[1], reverse=True)[:5]],
        "top_articles": [{"article": a, "count": c} for a, c in sorted(articles.items(), key=lambda x: x[1], reverse=True)[:5]]
    }, ensure_ascii=False)


async def _run_get_sales_by_weeks(tool_input: dict) -> str:
    date_from = f"{tool_input['date_from']}T00:00:00"
    date_to = f"{tool_input['date_to']}T23:59:59"
    article = tool_input.get("article", "")
    sales = filter_by_period(await wb.get_sales(date_from), date_from, date_to)
    if article:
        sales = filter_by_article(sales, article)
    if not sales:
        return "Нет данных о продажах за указанный период."

    weeks: dict[str, dict] = {}
    for s in sales:
        date_str = s.get("date") or s.get("lastChangeDate") or ""
        if not date_str:
            continue
        try:
            dt = datetime.fromisoformat(date_str.replace("Z", "").split("+")[0][:19])
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
    best_rev = max(weeks.items(), key=lambda x: x[1]["revenue"])
    best_cnt = max(weeks.items(), key=lambda x: x[1]["count"])
    return json.dumps({
        "period": f"{tool_input['date_from']} — {tool_input['date_to']}",
        "article_filter": article or "все товары",
        "best_week_by_revenue": {"week": best_rev[1]["label"], "revenue_rub": round(best_rev[1]["revenue"], 2), "sales_count": best_rev[1]["count"]},
        "best_week_by_count": {"week": best_cnt[1]["label"], "sales_count": best_cnt[1]["count"], "revenue_rub": round(best_cnt[1]["revenue"], 2)},
        "weeks": [{"week": v["label"], "sales_count": v["count"], "revenue_rub": round(v["revenue"], 2)} for _, v in sorted_weeks]
    }, ensure_ascii=False)


async def _run_get_stocks() -> str:
    date_from, _ = wb.date_range(30)
    stocks = await wb.get_stocks(date_from)
    if not stocks:
        return "Нет данных по складским остаткам."
    total_qty = sum(s.get("quantity", 0) or 0 for s in stocks)
    warehouses: dict[str, dict] = {}
    for s in stocks:
        wh = s.get("warehouseName") or "Неизвестно"
        if wh not in warehouses:
            warehouses[wh] = {"qty": 0, "skus": set()}
        warehouses[wh]["qty"] += s.get("quantity", 0) or 0
        warehouses[wh]["skus"].add(str(s.get("nmId", "")))
    low_stock = list(set(str(s.get("supplierArticle") or s.get("nmId", "?")) for s in stocks if 0 < (s.get("quantity") or 0) < 5))
    return json.dumps({
        "total_units": total_qty,
        "unique_skus": len(set(str(s.get("nmId", "")) for s in stocks)),
        "out_of_stock_positions": sum(1 for s in stocks if (s.get("quantity") or 0) == 0),
        "top_warehouses": [{"name": wh, "units": d["qty"], "skus": len(d["skus"])} for wh, d in sorted(warehouses.items(), key=lambda x: x[1]["qty"], reverse=True)[:5]],
        "low_stock_articles": low_stock[:15]
    }, ensure_ascii=False)


async def _run_get_finance(tool_input: dict) -> str:
    date_from, date_to = resolve_dates(tool_input)
    rows = await wb.get_report_detail(date_from[:10], date_to[:10])
    if not rows:
        return "Нет финансовых данных за указанный период. Отчёт формируется еженедельно по пятницам."
    retail = sum(r.get("retail_amount", 0) or 0 for r in rows)
    for_pay = sum(r.get("ppvz_for_pay", 0) or 0 for r in rows)
    delivery = sum(r.get("delivery_rub", 0) or 0 for r in rows)
    storage = sum(r.get("storage_fee", 0) or 0 for r in rows)
    penalty = sum(r.get("penalty", 0) or 0 for r in rows)
    deduction = sum(r.get("deduction", 0) or 0 for r in rows)
    acceptance = sum(r.get("acceptance", 0) or 0 for r in rows)
    net = for_pay - delivery - storage - penalty - deduction - acceptance
    subjects: dict[str, float] = {}
    for r in rows:
        s = str(r.get("subject_name") or r.get("nm_id") or "Прочее")
        subjects[s] = subjects.get(s, 0.0) + (r.get("ppvz_for_pay", 0) or 0)
    return json.dumps({
        "period": f"{date_from[:10]} — {date_to[:10]}",
        "retail_amount_rub": round(retail, 2),
        "wb_payout_rub": round(for_pay, 2),
        "logistics_rub": round(delivery, 2),
        "storage_rub": round(storage, 2),
        "penalties_rub": round(penalty, 2),
        "deductions_rub": round(deduction, 2),
        "acceptance_rub": round(acceptance, 2),
        "net_revenue_rub": round(net, 2),
        "margin_percent": round(net / retail * 100, 1) if retail else 0,
        "top_categories": [{"name": s, "revenue_rub": round(v, 2)} for s, v in sorted(subjects.items(), key=lambda x: x[1], reverse=True)[:5]]
    }, ensure_ascii=False)


async def _run_get_adv_summary(tool_input: dict) -> str:
    date_from, date_to = resolve_dates_simple(tool_input, default_days=7)
    stats = await wb.get_adv_stats(date_from, date_to)
    if not stats:
        return "Нет данных по рекламным кампаниям за указанный период. Убедись что в WB токене включено разрешение 'Продвижение'."

    total_views = 0
    total_clicks = 0
    total_spend = 0.0
    total_orders = 0
    total_revenue = 0.0
    campaigns_summary = []

    for camp in stats:
        camp_views = 0
        camp_clicks = 0
        camp_spend = 0.0
        camp_orders = 0
        camp_revenue = 0.0
        camp_name = camp.get("advertName") or camp.get("advertId") or "Неизвестно"

        for day in camp.get("days", []):
            for app in day.get("apps", []):
                for nm in app.get("nm", []):
                    camp_views += nm.get("views", 0) or 0
                    camp_clicks += nm.get("clicks", 0) or 0
                    camp_spend += nm.get("sum", 0) or 0
                    camp_orders += nm.get("orders", 0) or 0
                    camp_revenue += nm.get("sum_price", 0) or 0

        total_views += camp_views
        total_clicks += camp_clicks
        total_spend += camp_spend
        total_orders += camp_orders
        total_revenue += camp_revenue

        if camp_views > 0 or camp_spend > 0:
            campaigns_summary.append({
                "name": str(camp_name),
                "views": camp_views,
                "clicks": camp_clicks,
                "ctr_percent": round(camp_clicks / camp_views * 100, 2) if camp_views else 0,
                "spend_rub": round(camp_spend, 2),
                "orders": camp_orders,
                "revenue_rub": round(camp_revenue, 2),
                "drr_percent": round(camp_spend / camp_revenue * 100, 1) if camp_revenue else None,
                "cpc_rub": round(camp_spend / camp_clicks, 2) if camp_clicks else None
            })

    campaigns_summary.sort(key=lambda x: x["spend_rub"], reverse=True)

    return json.dumps({
        "period": f"{date_from} — {date_to}",
        "total": {
            "views": total_views,
            "clicks": total_clicks,
            "ctr_percent": round(total_clicks / total_views * 100, 2) if total_views else 0,
            "spend_rub": round(total_spend, 2),
            "orders": total_orders,
            "revenue_rub": round(total_revenue, 2),
            "drr_percent": round(total_spend / total_revenue * 100, 1) if total_revenue else None,
            "cpc_rub": round(total_spend / total_clicks, 2) if total_clicks else None
        },
        "campaigns": campaigns_summary[:10]
    }, ensure_ascii=False)


async def _run_get_adv_campaigns() -> str:
    campaigns = await wb.get_adv_campaigns()
    if not campaigns:
        return "Нет активных рекламных кампаний или нет доступа к разделу Продвижение."

    STATUS_MAP = {
        -1: "удалена", 4: "готова к запуску", 7: "завершена",
        8: "отказ", 9: "идут показы", 11: "на паузе"
    }
    TYPE_MAP = {
        4: "каталог", 5: "карточка товара", 6: "поиск", 7: "рекомендации", 8: "автореклама"
    }
    result = []
    for c in campaigns[:30]:
        # WB API v1/promotion/adverts returns different field names
        advert_id = c.get("advertId") or c.get("id")
        name = c.get("name") or c.get("campaignName") or "Без названия"
        status_code = c.get("status")
        adv_type = c.get("type") or c.get("advertType")
        budget = c.get("budget") or c.get("dailyBudget")
        result.append({
            "id": advert_id,
            "name": name,
            "status_code": status_code,
            "status": STATUS_MAP.get(status_code, f"код {status_code}"),
            "type": TYPE_MAP.get(adv_type, f"тип {adv_type}"),
            "budget_rub": budget
        })

    active = [r for r in result if r.get("status_code") == 9]
    paused = [r for r in result if r.get("status_code") == 11]
    return json.dumps({
        "total_campaigns": len(campaigns),
        "active_count": len(active),
        "paused_count": len(paused),
        "campaigns": result
    }, ensure_ascii=False)


async def _run_get_adv_balance() -> str:
    balance = await wb.get_adv_balance()
    if not balance:
        return "Не удалось получить баланс рекламного кабинета."
    return json.dumps({
        "balance_rub": balance.get("balance", 0),
        "bonus_rub": balance.get("bonus", 0),
        "net_rub": balance.get("net", 0)
    }, ensure_ascii=False)


async def _run_compare_periods(days: int) -> str:
    import asyncio as aio
    now = datetime.now()
    fmt = "%Y-%m-%dT%H:%M:%S"
    cur_start = (now - timedelta(days=days)).strftime(fmt)
    prev_start = (now - timedelta(days=days * 2)).strftime(fmt)
    cur_end = now.strftime(fmt)
    prev_end = (now - timedelta(days=days)).strftime(fmt)

    cur_s_raw, prev_s_raw, cur_o_raw, prev_o_raw = await aio.gather(
        wb.get_sales(cur_start), wb.get_sales(prev_start),
        wb.get_orders(cur_start), wb.get_orders(prev_start),
    )
    cur_s = filter_by_period(cur_s_raw, cur_start, cur_end)
    prev_s = filter_by_period(prev_s_raw, prev_start, prev_end)
    cur_o = filter_by_period(cur_o_raw, cur_start, cur_end)
    prev_o = filter_by_period(prev_o_raw, prev_start, prev_end)

    def ss(items):
        rev = sum(s.get("finishedPrice", 0) or 0 for s in items)
        cnt = len(items)
        return {"revenue": round(rev, 2), "count": cnt, "avg": round(rev / cnt if cnt else 0, 2)}

    def os(items):
        total = len(items)
        cancelled = sum(1 for o in items if o.get("isCancel") is True)
        return {"total": total, "cancelled": cancelled, "cancel_rate": round(cancelled / total * 100, 1) if total else 0}

    def delta(cur, prev):
        return round((cur - prev) / prev * 100, 1) if prev else None

    cs, ps, co, po = ss(cur_s), ss(prev_s), os(cur_o), os(prev_o)
    return json.dumps({
        "current_period": f"{cur_start[:10]} — {cur_end[:10]}",
        "previous_period": f"{prev_start[:10]} — {prev_end[:10]}",
        "current": {"sales": cs, "orders": co},
        "previous": {"sales": ps, "orders": po},
        "growth": {
            "revenue_percent": delta(cs["revenue"], ps["revenue"]),
            "sales_count_percent": delta(cs["count"], ps["count"]),
            "avg_check_percent": delta(cs["avg"], ps["avg"]),
            "orders_percent": delta(co["total"], po["total"])
        }
    }, ensure_ascii=False)


# ── Dispatcher ─────────────────────────────────────────────────────────────────

async def execute_tool(name: str, tool_input: dict) -> str:
    try:
        if name == "get_sales": return await _run_get_sales(tool_input)
        elif name == "get_orders": return await _run_get_orders(tool_input)
        elif name == "get_sales_by_weeks": return await _run_get_sales_by_weeks(tool_input)
        elif name == "get_stocks": return await _run_get_stocks()
        elif name == "get_finance": return await _run_get_finance(tool_input)
        elif name == "get_adv_summary": return await _run_get_adv_summary(tool_input)
        elif name == "get_adv_campaigns": return await _run_get_adv_campaigns()
        elif name == "get_adv_balance": return await _run_get_adv_balance()
        elif name == "compare_periods": return await _run_compare_periods(tool_input["days"])
        else: return f"Неизвестный инструмент: {name}"
    except Exception as e:
        logger.error(f"Tool {name} error: {e}")
        return f"Ошибка при выполнении {name}: {str(e)}"


# ── Agent ──────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Ты — персональный аналитик магазина на Wildberries.
Ты помогаешь продавцу анализировать его бизнес через удобный чат.

Твои возможности:
- Продажи, заказы — за любой период (конкретные даты или последние N дней)
- Склад — остатки, заканчивающиеся товары
- Финансы — чистая выручка, логистика, штрафы, маржа
- Сравнение периодов
- Анализ по неделям
- Реклама — показы, клики, CTR, расходы, ДРР, ROI по кампаниям

Правила работы с датами:
- Если пользователь называет конкретные даты — используй date_from и date_to
- Если называет месяц — бери с 1-го по последнее число месяца
- Если говорит "с сентября" — бери с 2024-09-01 до сегодня
- Текущий год 2026, прошлый год 2025

Правила контекста:
- ВАЖНО: если в разговоре уже упоминался конкретный артикул и пользователь продолжает про него говорить — ВСЕГДА передавай этот артикул в параметр article
- Если пользователь говорит "этот товар", "он", "по нему" — используй артикул из предыдущих сообщений
- Не теряй контекст артикула и дат между сообщениями

Правила ответов:
- Пиши по-русски, живым и понятным языком
- Используй цифры и делай выводы, не просто перечисляй данные
- Выдели самое важное в начале ответа
- Если видишь проблему (много отмен, товар заканчивается, высокий ДРР) — скажи об этом явно
- Используй эмодзи для читаемости в Telegram
- НИКОГДА не используй символы ** для выделения текста
- Если данных нет — объясни почему

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
            async with session.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error(f"Anthropic API error {resp.status}: {text}")
                    return "Ошибка при обращении к AI. Попробуй ещё раз."
                data = await resp.json()

            stop_reason = data.get("stop_reason")
            content = data.get("content", [])

            if stop_reason == "tool_use":
                messages.append({"role": "assistant", "content": content})
                tool_results = []
                for block in content:
                    if block.get("type") == "tool_use":
                        result = await execute_tool(block["name"], block.get("input", {}))
                        tool_results.append({"type": "tool_result", "tool_use_id": block["id"], "content": result})
                messages.append({"role": "user", "content": tool_results})
                continue

            for block in content:
                if block.get("type") == "text":
                    return block["text"]

            return "Не удалось получить ответ от AI."

    return "Превышено количество шагов агента."
