import os
import aiohttp

ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL = "claude-sonnet-4-20250514"

SYSTEM_TIPS = """Ты опытный аналитик Wildberries. Анализируй данные магазина и давай конкретные, 
actionable советы на русском языке. Формат: маркированный список, каждый совет с конкретным действием.
Учитывай: остатки, выручку, возвраты, рекламу. Будь краток — максимум 5 советов."""

SYSTEM_ASK = """Ты опытный аналитик Wildberries. Отвечай на вопросы продавца на основе его данных.
Отвечай кратко, по делу, на русском. Давай конкретные рекомендации с числами."""


async def _call(system: str, user: str) -> str:
    if not ANTHROPIC_KEY:
        return "⚠️ Anthropic API ключ не задан (ANTHROPIC_API_KEY)"
    headers = {
        "x-api-key": ANTHROPIC_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = {
        "model": MODEL,
        "max_tokens": 1024,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    async with aiohttp.ClientSession() as s:
        async with s.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data["content"][0]["text"]


async def get_tips(context: str) -> str:
    prompt = f"Вот данные магазина:\n\n{context}\n\nДай советы что улучшить прямо сейчас."
    return await _call(SYSTEM_TIPS, prompt)


async def ask(question: str, context: str) -> str:
    prompt = f"Данные магазина:\n\n{context}\n\nВопрос: {question}"
    return await _call(SYSTEM_ASK, prompt)
