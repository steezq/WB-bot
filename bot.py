import asyncio
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import CommandStart, Command
from aiogram.fsm.storage.memory import MemoryStorage
from config import BOT_TOKEN, ALLOWED_USERS
from ai_agent import ask_agent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

user_histories: dict[int, list[dict]] = {}
MAX_HISTORY = 20


def is_allowed(user_id: int) -> bool:
    if not ALLOWED_USERS:
        return True
    return user_id in ALLOWED_USERS


def get_history(user_id: int) -> list[dict]:
    return user_histories.get(user_id, [])


def add_to_history(user_id: int, role: str, content: str):
    if user_id not in user_histories:
        user_histories[user_id] = []
    user_histories[user_id].append({"role": role, "content": content})
    if len(user_histories[user_id]) > MAX_HISTORY:
        user_histories[user_id] = user_histories[user_id][-MAX_HISTORY:]


async def main():
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    @dp.message(CommandStart())
    async def cmd_start(message: Message):
        if not is_allowed(message.from_user.id):
            await message.answer("⛔ Доступ запрещён.")
            return
        user_histories.pop(message.from_user.id, None)
        await message.answer(
            "👋 Привет! Я твой аналитик на Wildberries.\n\n"
            "Просто напиши мне что хочешь узнать, например:\n\n"
            "• <i>Какие продажи за последние 7 дней?</i>\n"
            "• <i>Сравни этот месяц с прошлым</i>\n"
            "• <i>Какие товары заканчиваются на складе?</i>\n"
            "• <i>Сколько я заработал чистыми за 30 дней?</i>\n"
            "• <i>Есть ли штрафы от WB?</i>\n\n"
            "Команда /reset — начать разговор заново.",
            parse_mode="HTML"
        )

    @dp.message(Command("reset"))
    async def cmd_reset(message: Message):
        user_histories.pop(message.from_user.id, None)
        await message.answer("🔄 История очищена. Начинаем заново!")

    @dp.message(F.text)
    async def handle_message(message: Message):
        if not is_allowed(message.from_user.id):
            return

        user_id = message.from_user.id
        user_text = message.text.strip()

        await bot.send_chat_action(message.chat.id, "typing")
        history = get_history(user_id)

        try:
            response = await ask_agent(user_text, history)
        except Exception as e:
            logger.error(f"Agent error for user {user_id}: {e}")
            response = "⚠️ Что-то пошло не так. Попробуй ещё раз или напиши /reset."

        add_to_history(user_id, "user", user_text)
        add_to_history(user_id, "assistant", response)

        if len(response) <= 4096:
            await message.answer(response, parse_mode="HTML")
        else:
            for i in range(0, len(response), 4096):
                await message.answer(response[i:i+4096], parse_mode="HTML")

    logger.info("Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
