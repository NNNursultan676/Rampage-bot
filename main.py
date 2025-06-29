import logging
import os
import asyncio
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, executor, types
from aiogram.dispatcher.middlewares import BaseMiddleware
from aiogram.dispatcher.handler import CancelHandler
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv

# 🔹 Для Flask
from flask import Flask
from threading import Thread

load_dotenv()

API_TOKEN = os.getenv("BOT_TOKEN")
GROUP_ID = -1002175210800         # новый ID группы
LEADER_ID = 6352363504            # новый ID лидера

logging.basicConfig(level=logging.INFO)

bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)

# Middleware: только для группы
class GroupOnlyMiddleware(BaseMiddleware):
    async def on_pre_process_message(self, message: types.Message, data: dict):
        if message.chat.id != GROUP_ID:
            raise CancelHandler()
dp.middleware.setup(GroupOnlyMiddleware())

# Хранилища
join_requests = []
accepted_users = []
log = []

# Команды
@dp.message_handler(commands=['joinlist'])
async def join_list_handler(message: types.Message):
    if not join_requests:
        await message.reply("Нет входящих заявок.")
        return

    for user in join_requests:
        markup = InlineKeyboardMarkup().add(
            InlineKeyboardButton("✅ Принять", callback_data=f"accept_{user['id']}")
        )
        await message.reply(f"Заявка от @{user['username']} (ID: {user['id']})", reply_markup=markup)

@dp.callback_query_handler(lambda c: c.data.startswith('accept_'))
async def accept_callback(callback_query: types.CallbackQuery):
    user_id = int(callback_query.data.split('_')[1])
    user = next((u for u in join_requests if u['id'] == user_id), None)

    if not user:
        await callback_query.answer("Заявка не найдена.")
        return

    try:
        await bot.approve_chat_join_request(chat_id=GROUP_ID, user_id=user_id)
        join_requests.remove(user)
        accepted_users.append(user)
        now = (datetime.utcnow() + timedelta(hours=3)).strftime("%d.%m.%Y %H:%M")  # МСК (UTC+3)
        log.append(
            f"✅ @{callback_query.from_user.username or 'admin'} принял @{user['username']} "
            f"(ID: {user['id']}) в {now} по МСК"
        )
        await callback_query.answer("Пользователь принят.")
    except Exception as e:
        await callback_query.answer("Ошибка при принятии.")
        logging.error(e)

@dp.message_handler(commands=['info4leader'])
async def info_for_leader(message: types.Message):
    if message.from_user.id != LEADER_ID:
        return
    if not log:
        await message.reply("Журнал пуст.")
    else:
        await message.reply("\n".join(log))

@dp.message_handler(commands=['clearlog'])
async def clear_log(message: types.Message):
    if message.from_user.id == LEADER_ID:
        log.clear()
        await message.reply("Журнал очищен.")

@dp.chat_join_request_handler()
async def handle_join_request(request: types.ChatJoinRequest):
    join_requests.append({
        "id": request.from_user.id,
        "username": request.from_user.username or "без ника"
    })
    await bot.send_message(GROUP_ID, f"🆕 Новая заявка от @{request.from_user.username or 'без ника'}")

@dp.message_handler(commands=['start'], chat_type=types.ChatType.PRIVATE)
async def private_start(message: types.Message):
    await message.reply("❗ Этот бот работает только внутри группы.")

# 🔹 Flask сервер для пинга
app = Flask(__name__)

@app.route('/')
def home():
    return "✅ Bot is alive!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# ⏯ Запуск бота и Flask
if __name__ == '__main__':
    Thread(target=run_flask).start()
    executor.start_polling(dp, skip_updates=True)
