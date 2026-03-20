import aiohttp
import logging
from datetime import datetime, timedelta
from config import WB_API_KEY, WB_STATS_URL, WB_FINANCE_URL

logger = logging.getLogger(__name__)

WB_ADV_URL = "https://advert-api.wildberries.ru"


class WBApiClient:
    def __init__(self, api_key: str = WB_API_KEY):
        self.api_key = api_key
        self.headers = {
            "Authorization": api_key,
            "Content-Type": "application/json"
        }

    async def _get(self, url: str, params: dict = None) -> dict | list | None:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self.headers, params=params) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    else:
                        text = await resp.text()
                        logger.error(f"WB API error {resp.status}: {text}")
                        return None
        except Exception as e:
            logger.error(f"Request error: {e}")
            return None

    async def _post(self, url: str, body: dict = None) -> dict | list | None:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=self.headers, json=body) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    else:
                        text = await resp.text()
                        logger.error(f"WB API POST error {resp.status}: {text}")
                        return None
        except Exception as e:
            logger.error(f"POST request error: {e}")
            return None

    async def get_sales(self, date_from: str, flag: int = 0) -> list:
        url = f"{WB_STATS_URL}/supplier/sales"
        data = await self._get(url, {"dateFrom": date_from, "flag": flag})
        return data or []

    async def get_orders(self, date_from: str, flag: int = 0) -> list:
        url = f"{WB_STATS_URL}/supplier/orders"
        data = await self._get(url, {"dateFrom": date_from, "flag": flag})
        return data or []

    async def get_stocks(self, date_from: str) -> list:
        url = f"{WB_STATS_URL}/supplier/stocks"
        data = await self._get(url, {"dateFrom": date_from})
        return data or []

    async def get_report_detail(self, date_from: str, date_to: str, rrd_id: int = 0) -> list:
        url = f"{WB_FINANCE_URL}/supplier/reportDetailByPeriod"
        params = {
            "dateFrom": date_from,
            "dateTo": date_to,
            "rrdid": rrd_id,
            "limit": 100000
        }
        data = await self._get(url, params)
        return data or []

    async def get_incomes(self, date_from: str) -> list:
        url = f"{WB_STATS_URL}/supplier/incomes"
        data = await self._get(url, {"dateFrom": date_from})
        return data or []

    async def get_adv_campaigns(self) -> list:
        url = f"{WB_ADV_URL}/adv/v1/promotion/count"
        data = await self._get(url)
        if not data:
            return []
        campaigns = []
        for adv_type in data.get("adverts", []):
            for camp in adv_type.get("advert_list", []):
                camp["type"] = adv_type.get("type")
                campaigns.append(camp)
        return campaigns

    async def get_adv_stats(self, date_from: str, date_to: str, campaign_ids: list = None) -> list:
        """Get advertising stats using new v3 API endpoint."""
        if not campaign_ids:
            campaigns = await self.get_adv_campaigns()
            campaign_ids = [c.get("advertId") for c in campaigns if c.get("advertId")]
        if not campaign_ids:
            return []
        import asyncio
        await asyncio.sleep(1)
        campaign_ids = campaign_ids[:100]
        ids_str = ",".join(str(cid) for cid in campaign_ids)
        url = f"{WB_ADV_URL}/adv/v3/fullstats"
        params = {"ids": ids_str, "beginDate": date_from, "endDate": date_to}
        data = await self._get(url, params)
        if isinstance(data, list):
            return data
        return []

    async def get_adv_balance(self) -> dict:
        url = f"{WB_ADV_URL}/adv/v1/balance"
        data = await self._get(url)
        return data or {}

    @staticmethod
    def date_range(days_ago: int) -> tuple[str, str]:
        today = datetime.now()
        date_from = (today - timedelta(days=days_ago)).strftime("%Y-%m-%dT00:00:00")
        date_to = today.strftime("%Y-%m-%dT23:59:59")
        return date_from, date_to

    @staticmethod
    def date_range_simple(days_ago: int) -> tuple[str, str]:
        today = datetime.now()
        date_from = (today - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        date_to = today.strftime("%Y-%m-%d")
        return date_from, date_to

    @staticmethod
    def format_date(dt_str: str) -> str:
        try:
            dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            return dt.strftime("%d.%m.%Y")
        except Exception:
            return dt_str[:10] if dt_str else "—"
