import aiohttp
import logging
from datetime import datetime, timedelta
from config import WB_API_KEY, WB_STATS_URL, WB_FINANCE_URL

logger = logging.getLogger(__name__)


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
            "rrdid":
