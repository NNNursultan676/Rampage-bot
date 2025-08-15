import logging
import os
import asyncio
import random
import aiohttp
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, executor, types
from aiogram.dispatcher.middlewares import BaseMiddleware
from aiogram.dispatcher.handler import CancelHandler
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv
from flask import Flask, render_template_string
from threading import Thread

# Загружаем переменные окружения
load_dotenv()

# Настройки бота
API_TOKEN = os.getenv("BOT_TOKEN")
GROUP_1_ID = int(os.getenv("GROUP_1_ID"))        # ID первой группы
GROUP_1_THREAD = int(os.getenv("GROUP_1_THREAD")) # ID ветки первой группы
GROUP_2_ID = int(os.getenv("GROUP_2_ID"))        # ID второй группы  
GROUP_2_THREAD = int(os.getenv("GROUP_2_THREAD")) # ID ветки второй группы
PING_URL = os.getenv("PING_URL", "https://example.com")  # URL другого бота для пинга

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Инициализация бота
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)

# Middleware: блокирует команды вне разрешенных групп
class GroupOnlyMiddleware(BaseMiddleware):
    async def on_pre_process_message(self, message: types.Message, data: dict):
        # Разрешаем работу только в двух группах
        if message.chat.id not in [GROUP_1_ID, GROUP_2_ID]:
            raise CancelHandler()

dp.middleware.setup(GroupOnlyMiddleware())

# Хранилище заявок для каждой группы отдельно
join_requests = {
    GROUP_1_ID: [],
    GROUP_2_ID: []
}

# Активные сообщения со списками заявок (для автоудаления)
active_joinlist_messages = {}

async def get_group_thread_id(group_id):
    """Получить ID ветки для определенной группы"""
    if group_id == GROUP_1_ID:
        return GROUP_1_THREAD
    elif group_id == GROUP_2_ID:
        return GROUP_2_THREAD
    return None

async def send_to_thread(group_id, text):
    """Отправить сообщение в закрытую ветку группы"""
    thread_id = await get_group_thread_id(group_id)
    if thread_id:
        try:
            # Сначала пробуем с HTML форматированием
            await bot.send_message(
                chat_id=group_id,
                text=text,
                message_thread_id=thread_id,
                parse_mode='HTML'
            )
        except Exception as e:
            try:
                # Если не получилось, отправляем без форматирования
                await bot.send_message(
                    chat_id=group_id,
                    text=text,
                    message_thread_id=thread_id,
                    parse_mode=None
                )
            except Exception as e2:
                logging.error(f"Ошибка отправки в ветку: {e2}")

@dp.message_handler(commands=['joinlist'])
async def join_list_handler(message: types.Message):
    """Показать список входящих заявок с кнопками принять/игнорировать"""
    group_id = message.chat.id
    
    # Проверяем есть ли заявки для этой группы
    if not join_requests.get(group_id, []):
        await message.reply("📭 Нет входящих заявок.")
        return
    
    # Удаляем предыдущие активные сообщения со списками
    if group_id in active_joinlist_messages:
        try:
            await bot.delete_message(group_id, active_joinlist_messages[group_id])
        except:
            pass
    
    # Формируем список заявок с кнопками
    text = "📋 <b>Список входящих заявок:</b>\n\n"
    markup = InlineKeyboardMarkup()
    
    for i, user in enumerate(join_requests[group_id], 1):
        text += f"{i}. @{user['username']} (ID: {user['id']})\n"
        
        # Добавляем кнопки для каждого пользователя
        markup.row(
            InlineKeyboardButton(f"✅ Принять #{i}", callback_data=f"accept_{user['id']}_{group_id}"),
            InlineKeyboardButton(f"❌ Игнорировать #{i}", callback_data=f"ignore_{user['id']}_{group_id}")
        )
    
    # Кнопка закрытия списка
    markup.add(InlineKeyboardButton("🚫 Закрыть список", callback_data=f"close_list_{group_id}"))
    
    # Отправляем сообщение со списком
    sent_message = await message.reply(text, reply_markup=markup, parse_mode='HTML')
    
    # Сохраняем ID сообщения для автоудаления
    active_joinlist_messages[group_id] = sent_message.message_id
    
    # Задача автоудаления через 2 минуты
    asyncio.create_task(auto_delete_joinlist(group_id, sent_message.message_id))

async def auto_delete_joinlist(group_id, message_id):
    """Автоудаление списка заявок через 2 минуты"""
    await asyncio.sleep(120)  # 2 минуты
    
    if active_joinlist_messages.get(group_id) == message_id:
        try:
            await bot.delete_message(group_id, message_id)
            del active_joinlist_messages[group_id]
        except:
            pass

@dp.callback_query_handler(lambda c: c.data.startswith(('accept_', 'ignore_', 'close_list_')))
async def handle_callback(callback_query: types.CallbackQuery):
    """Обработка нажатий кнопок принять/игнорировать/закрыть"""
    data = callback_query.data
    
    if data.startswith('accept_'):
        # Принять пользователя
        parts = data.split('_')
        user_id = int(parts[1])
        group_id = int(parts[2])
        
        user = next((u for u in join_requests.get(group_id, []) if u['id'] == user_id), None)
        
        if not user:
            await callback_query.answer("❗ Заявка не найдена.")
            return
        
        try:
            # Принимаем пользователя в группу
            await bot.approve_chat_join_request(chat_id=group_id, user_id=user_id)
            
            # Удаляем из списка заявок
            join_requests[group_id].remove(user)
            
            # Формируем сообщение о принятии
            admin_username = callback_query.from_user.username or "админ"
            admin_id = callback_query.from_user.id
            user_username = user['username']
            now = (datetime.utcnow() + timedelta(hours=3)).strftime("%d.%m.%Y %H:%M")
            
            log_message = (
                f"✅ <b>Пользователь принят в группу</b>\n"
                f"👤 Принят: @{user_username} (ID: {user_id})\n"
                f"👨‍💼 Принял: @{admin_username} (ID: {admin_id})\n"
                f"⏰ Время: {now} МСК"
            )
            
            # Отправляем в закрытую ветку
            await send_to_thread(group_id, log_message)
            
            await callback_query.answer("✅ Пользователь принят!")
            
        except Exception as e:
            await callback_query.answer("❗ Ошибка при принятии пользователя.")
            logging.error(f"Ошибка принятия пользователя: {e}")
            return
    
    elif data.startswith('ignore_'):
        # Игнорировать пользователя
        parts = data.split('_')
        user_id = int(parts[1])
        group_id = int(parts[2])
        
        user = next((u for u in join_requests.get(group_id, []) if u['id'] == user_id), None)
        
        if user:
            join_requests[group_id].remove(user)
            await callback_query.answer("❌ Заявка проигнорирована.")
        else:
            await callback_query.answer("❗ Заявка не найдена.")
    
    elif data.startswith('close_list_'):
        # Закрыть список
        await callback_query.answer("🚫 Список закрыт.")
    
    # Удаляем сообщение со списком
    group_id = int(data.split('_')[-1])
    try:
        await bot.delete_message(callback_query.message.chat.id, callback_query.message.message_id)
        if group_id in active_joinlist_messages:
            del active_joinlist_messages[group_id]
    except:
        pass

@dp.chat_join_request_handler()
async def handle_join_request(request: types.ChatJoinRequest):
    """Обработка новых заявок на вступление"""
    group_id = request.chat.id
    user_id = request.from_user.id
    username = request.from_user.username or "без_ника"
    
    # Проверяем, что группа поддерживается
    if group_id not in [GROUP_1_ID, GROUP_2_ID]:
        return
    
    # Инициализируем список заявок для группы если его нет
    if group_id not in join_requests:
        join_requests[group_id] = []
    
    # Исключаем дублирование заявок
    if any(u['id'] == user_id for u in join_requests[group_id]):
        return
    
    # Добавляем заявку в список
    join_requests[group_id].append({
        "id": user_id,
        "username": username
    })
    
    # Отправляем уведомление о новой заявке
    notification_text = (
        f"🆕 <b>Новая заявка на вступление!</b>\n"
        f"👤 От: @{username} (ID: {user_id})\n"
        f"📝 Для просмотра списка используйте /joinlist"
    )
    
    await bot.send_message(group_id, notification_text, parse_mode='HTML')

@dp.message_handler(commands=['start'], chat_type=types.ChatType.PRIVATE)
async def private_start(message: types.Message):
    """Обработка команды /start в личных сообщениях"""
    await message.reply("❗ Этот бот работает только внутри групп.")

# Система пинга для поддержания активности бота
async def ping_other_bot():
    """Пинг другого бота для поддержания активности"""
    while True:
        try:
            # Случайная задержка от 1 до 17 минут
            delay = random.randint(60, 1020)  # 60-1020 секунд
            await asyncio.sleep(delay)
            
            async with aiohttp.ClientSession() as session:
                # Случайные запросы для имитации активности
                endpoints = ["/", "/status", "/health", "/ping"]
                endpoint = random.choice(endpoints)
                
                try:
                    async with session.get(f"{PING_URL}{endpoint}", timeout=10) as response:
                        logging.info(f"Пинг {PING_URL}{endpoint}: {response.status}")
                except:
                    logging.warning(f"Не удалось пинговать {PING_URL}{endpoint}")
                    
        except Exception as e:
            logging.error(f"Ошибка в системе пинга: {e}")

async def self_ping():
    """Самопинг бота"""
    while True:
        try:
            # Случайная задержка от 1 до 14 минут
            delay = random.randint(60, 840)  # 60-840 секунд
            await asyncio.sleep(delay)
            
            # Простой HTTP запрос к собственному серверу
            async with aiohttp.ClientSession() as session:
                try:
                    async with session.get("http://localhost:10000/", timeout=5) as response:
                        logging.info(f"Самопинг: {response.status}")
                except:
                    logging.warning("Самопинг неудачен")
                    
        except Exception as e:
            logging.error(f"Ошибка самопинга: {e}")

# Flask приложение с анимацией Рик и Морти
app = Flask(__name__)

@app.route('/')
def home():
    """Главная страница с анимацией Рик и Морти"""
    html_template = """
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Rick & Morty Portal Bot</title>
        <style>
            * {
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }
            
            body {
                font-family: 'Arial', sans-serif;
                background: linear-gradient(45deg, #1a1a2e, #16213e, #0f3460);
                background-size: 600% 600%;
                animation: gradientShift 8s ease infinite;
                height: 100vh;
                display: flex;
                justify-content: center;
                align-items: center;
                overflow: hidden;
            }
            
            @keyframes gradientShift {
                0% { background-position: 0% 50%; }
                50% { background-position: 100% 50%; }
                100% { background-position: 0% 50%; }
            }
            
            .container {
                text-align: center;
                z-index: 10;
            }
            
            .portal {
                width: 200px;
                height: 200px;
                border-radius: 50%;
                background: radial-gradient(circle, #00ff41, #00b8ff, #9400ff);
                animation: portalSpin 3s linear infinite, portalPulse 2s ease-in-out infinite alternate;
                margin: 0 auto 30px;
                position: relative;
                box-shadow: 0 0 50px #00ff41, inset 0 0 50px #00b8ff;
            }
            
            @keyframes portalSpin {
                from { transform: rotate(0deg); }
                to { transform: rotate(360deg); }
            }
            
            @keyframes portalPulse {
                from { transform: scale(1); box-shadow: 0 0 50px #00ff41, inset 0 0 50px #00b8ff; }
                to { transform: scale(1.1); box-shadow: 0 0 80px #00ff41, inset 0 0 80px #00b8ff; }
            }
            
            .characters {
                position: absolute;
                width: 100%;
                height: 100%;
                top: 0;
                left: 0;
            }
            
            .rick, .morty {
                position: absolute;
                width: 60px;
                height: 80px;
                background-size: contain;
                background-repeat: no-repeat;
                animation: jump 4s ease-in-out infinite;
            }
            
            .rick {
                background: linear-gradient(to bottom, #87ceeb 30%, #ffffff 30% 60%, #1e90ff 60%);
                left: -80px;
                top: 50%;
                transform: translateY(-50%);
                animation-delay: 0s;
            }
            
            .morty {
                background: linear-gradient(to bottom, #ffd700 30%, #ffffff 30% 60%, #1e90ff 60%);
                right: -80px;
                top: 50%;
                transform: translateY(-50%);
                animation-delay: 2s;
            }
            
            @keyframes jump {
                0%, 100% { transform: translateX(0) translateY(-50%) scale(1); }
                25% { transform: translateX(150px) translateY(-80%) scale(1.2); }
                50% { transform: translateX(300px) translateY(-50%) scale(1); }
                75% { transform: translateX(150px) translateY(-80%) scale(1.2); }
            }
            
            .title {
                color: #00ff41;
                font-size: 2.5em;
                margin-bottom: 20px;
                text-shadow: 0 0 20px #00ff41;
                animation: textGlow 3s ease-in-out infinite alternate;
            }
            
            @keyframes textGlow {
                from { text-shadow: 0 0 20px #00ff41; }
                to { text-shadow: 0 0 40px #00ff41, 0 0 60px #00ff41; }
            }
            
            .status {
                color: #ffffff;
                font-size: 1.2em;
                margin-top: 20px;
                animation: fadeInOut 2s ease-in-out infinite;
            }
            
            @keyframes fadeInOut {
                0%, 100% { opacity: 0.7; }
                50% { opacity: 1; }
            }
            
            .particles {
                position: absolute;
                width: 100%;
                height: 100%;
                top: 0;
                left: 0;
                pointer-events: none;
            }
            
            .particle {
                position: absolute;
                width: 4px;
                height: 4px;
                background: #00ff41;
                border-radius: 50%;
                animation: float 6s linear infinite;
            }
            
            @keyframes float {
                0% { 
                    transform: translateY(100vh) scale(0); 
                    opacity: 0;
                }
                10% {
                    opacity: 1;
                }
                90% {
                    opacity: 1;
                }
                100% { 
                    transform: translateY(-100vh) scale(1); 
                    opacity: 0;
                }
            }
        </style>
    </head>
    <body>
        <div class="particles" id="particles"></div>
        
        <div class="container">
            <div class="portal">
                <div class="characters">
                    <div class="rick"></div>
                    <div class="morty"></div>
                </div>
            </div>
            <h1 class="title">RICK & MORTY BOT</h1>
            <div class="status">✅ Портал активен! Бот работает...</div>
        </div>
        
        <script>
            // Создание частиц
            function createParticle() {
                const particle = document.createElement('div');
                particle.className = 'particle';
                particle.style.left = Math.random() * 100 + '%';
                particle.style.animationDelay = Math.random() * 6 + 's';
                particle.style.animationDuration = (Math.random() * 4 + 4) + 's';
                
                // Случайные цвета для частиц
                const colors = ['#00ff41', '#00b8ff', '#9400ff', '#ffd700'];
                particle.style.background = colors[Math.floor(Math.random() * colors.length)];
                
                document.getElementById('particles').appendChild(particle);
                
                // Удаляем частицу после анимации
                setTimeout(() => {
                    particle.remove();
                }, 8000);
            }
            
            // Создаем частицы каждые 200мс
            setInterval(createParticle, 200);
            
            // Эффект при клике
            document.addEventListener('click', (e) => {
                for (let i = 0; i < 10; i++) {
                    setTimeout(() => {
                        const spark = document.createElement('div');
                        spark.style.position = 'absolute';
                        spark.style.left = e.clientX + 'px';
                        spark.style.top = e.clientY + 'px';
                        spark.style.width = '6px';
                        spark.style.height = '6px';
                        spark.style.background = '#00ff41';
                        spark.style.borderRadius = '50%';
                        spark.style.pointerEvents = 'none';
                        spark.style.zIndex = '1000';
                        
                        const angle = (Math.PI * 2 * i) / 10;
                        const velocity = 100;
                        const vx = Math.cos(angle) * velocity;
                        const vy = Math.sin(angle) * velocity;
                        
                        let opacity = 1;
                        let x = 0;
                        let y = 0;
                        
                        document.body.appendChild(spark);
                        
                        const animate = () => {
                            x += vx * 0.02;
                            y += vy * 0.02;
                            opacity -= 0.02;
                            
                            spark.style.transform = `translate(${x}px, ${y}px)`;
                            spark.style.opacity = opacity;
                            
                            if (opacity > 0) {
                                requestAnimationFrame(animate);
                            } else {
                                spark.remove();
                            }
                        };
                        
                        animate();
                    }, i * 20);
                }
            });
        </script>
    </body>
    </html>
    """
    return render_template_string(html_template)

@app.route('/status')
def status():
    """Статус бота"""
    return {"status": "active", "timestamp": datetime.now().isoformat()}

@app.route('/health')  
def health():
    """Проверка здоровья"""
    return {"health": "ok"}

@app.route('/ping')
def ping():
    """Простой пинг"""
    return "pong"

def run_flask():
    """Запуск Flask сервера"""
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)

# Запуск всех компонентов
if __name__ == '__main__':
    # Запуск Flask в отдельном потоке
    Thread(target=run_flask, daemon=True).start()
    
    # Запуск фоновых задач пинга
    loop = asyncio.get_event_loop()
    loop.create_task(ping_other_bot())
    loop.create_task(self_ping())
    
    # Запуск бота
    logging.info("🚀 Бот запущен!")
    executor.start_polling(dp, skip_updates=True)
