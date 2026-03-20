import asyncio
from datetime import datetime, timedelta
from typing import Any

import aiohttp

BASE_STAT = "https://statistics-api.wildberries.ru/api/v1/supplier"
BASE_ADS  = "https://advert-api.wildberries.ru/adv/v2"


async def _get(session: aiohttp.ClientSession, url: str, key: str, params: dict = None) -> Any:
    headers = {"Authorization": key}
    async with session.get(url, headers=headers, params=params or {}) as resp:
        if resp.status == 401:
            raise ValueError("Неверный API-ключ или недостаточно прав")
        if resp.status == 429:
            await asyncio.sleep(60)
            return await _get(session, url, key, params)
        resp.raise_for_status()
        return await resp.json()


async def get_sales(key: str, days: int = 7) -> list:
    date_from = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00")
    async with aiohttp.ClientSession() as s:
        data = await _get(s, f"{BASE_STAT}/sales", key, {"dateFrom": date_from, "flag": 0})
    return data if isinstance(data, list) else []


async def get_stocks(key: str) -> list:
    date_from = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%dT00:00:00")
    async with aiohttp.ClientSession() as s:
        data = await _get(s, f"{BASE_STAT}/stocks", key, {"dateFrom": date_from})
    # Схлопываем по артикулу
    grouped: dict[str, dict] = {}
    for item in (data if isinstance(data, list) else []):
        art = item.get("supplierArticle") or str(item.get("nmId", ""))
        if art not in grouped:
            grouped[art] = {**item, "quantity": 0}
        grouped[art]["quantity"] += item.get("quantity", 0)
    return list(grouped.values())


async def get_orders(key: str, days: int = 7) -> list:
    date_from = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00")
    async with aiohttp.ClientSession() as s:
        data = await _get(s, f"{BASE_STAT}/orders", key, {"dateFrom": date_from, "flag": 0})
    return data if isinstance(data, list) else []


async def get_ads_stats(key: str) -> list:
    """Получаем список кампаний и их статистику за вчера."""
    try:
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        async with aiohttp.ClientSession() as s:
            # Получаем список активных кампаний
            camps = await _get(s, f"{BASE_ADS}/advert/list", key, {"status": 9, "type": 8})
            if not camps or not isinstance(camps, list):
                return []

            results = []
            for camp in camps[:10]:  # не более 10 кампаний
                camp_id = camp.get("advertId")
                if not camp_id:
                    continue
                try:
                    stat = await _get(
                        s,
                        f"{BASE_ADS}/fullstats",
                        key,
                        {"id": camp_id, "dates[]": yesterday}
                    )
                    days_data = stat[0].get("days", []) if stat else []
                    if days_data:
                        d = days_data[0]
                        views = d.get("views", 0)
                        clicks = d.get("clicks", 0)
                        orders_cnt = d.get("orders", 0)
                        spend = d.get("sum", 0)
                        ctr = round(clicks / max(views, 1) * 100, 2)
                        cpo = round(spend / max(orders_cnt, 1), 0)
                        results.append({
                            "advertName": camp.get("name", f"Кампания {camp_id}"),
                            "sum": spend,
                            "views": views,
                            "clicks": clicks,
                            "orders": orders_cnt,
                            "ctr": ctr,
                            "cpo": cpo,
                        })
                except Exception:
                    continue
            return results
    except Exception:
        return []


async def get_all_data(key: str) -> dict:
    """Загружаем все нужные данные параллельно."""
    sales_task = asyncio.create_task(get_sales(key, days=7))
    stocks_task = asyncio.create_task(get_stocks(key))
    ads_task = asyncio.create_task(get_ads_stats(key))

    sales, stocks, ads = await asyncio.gather(sales_task, stocks_task, ads_task)
    return {"sales": sales, "stocks": stocks, "ads": ads}


def build_context(data: dict) -> str:
    """Строим текстовый контекст для AI."""
    sales = data.get("sales", [])
    stocks = data.get("stocks", [])
    ads = data.get("ads", [])

    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    today_sales = [s for s in sales if s.get("date", "").startswith(today) and s.get("saleID", "").startswith("S")]
    today_returns = [s for s in sales if s.get("date", "").startswith(today) and s.get("saleID", "").startswith("R")]
    yest_sales = [s for s in sales if s.get("date", "").startswith(yesterday) and s.get("saleID", "").startswith("S")]

    revenue_today = sum(s.get("finishedPrice", 0) for s in today_sales)
    revenue_yest = sum(s.get("finishedPrice", 0) for s in yest_sales)
    rev_delta = revenue_today - revenue_yest

    critical_stock = [s for s in stocks if s.get("quantity", 0) <= 5]
    low_stock = [s for s in stocks if 5 < s.get("quantity", 0) <= 15]

    total_ad_spend = sum(a.get("sum", 0) for a in ads)
    avg_ctr = sum(a.get("ctr", 0) for a in ads) / max(len(ads), 1)

    lines = [
        f"Данные магазина WB за {today}:",
        f"- Выручка сегодня: {revenue_today:,.0f} ₽ ({len(today_sales)} заказов)",
        f"- Вчера: {revenue_yest:,.0f} ₽, разница: {rev_delta:+,.0f} ₽",
        f"- Возвраты сегодня: {len(today_returns)} шт.",
        f"- Товаров с критическим остатком (≤5 шт): {len(critical_stock)}",
    ]
    if critical_stock:
        for s in critical_stock[:5]:
            lines.append(f"  * {s.get('subject','?')} арт.{s.get('supplierArticle','?')}: {s.get('quantity',0)} шт.")
    if low_stock:
        lines.append(f"- Товаров с низким остатком (6–15 шт): {len(low_stock)}")

    if ads:
        lines.append(f"- Реклама: потрачено {total_ad_spend:,.0f} ₽, средний CTR {avg_ctr:.1f}%")
        for a in ads[:3]:
            lines.append(f"  * {a['advertName']}: CTR {a['ctr']}%, CPO {a['cpo']:,.0f} ₽")

    return "\n".join(lines)
