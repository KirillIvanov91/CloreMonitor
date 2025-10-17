import time
import requests
import threading
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from config import TELEGRAM_TOKEN, CLORE_API_URL, CHECK_INTERVAL, API_TOKEN, API_HEADERS
import logging
import asyncio
from tenacity import retry, stop_after_attempt, wait_exponential

# --- Логирование
logging.basicConfig(level=logging.INFO)

# --- Состояния
active_users = {}
sent_servers = {}
user_filters = {}
user_efficiency = {}

# --- Коэффициенты GPU без разгона
GPU_EFFICIENCY = {
    "3060": 0.85, "3060 ti": 0.83, "3070": 0.82, "3080": 0.80,
    "3090": 0.78, "4090": 0.82, "4070": 0.83, "4070 ti": 0.82,
    "4080": 0.81, "a5000": 0.81, "a4000": 0.84
}



# --- Clore API
def get_clore_servers():
    
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    def _request():
        try:
            r = requests.get(
                CLORE_API_URL,
                headers=API_HEADERS,
                timeout=10  # Увеличенный таймаут
            )
            r.raise_for_status()
            logging.info(f"Успешный запрос к Clore: {r.status_code}")
            return r.json().get("servers", [])
        except Exception as e:
            logging.warning(f"Clore error: {e}")
            return []

    return _request()






# --- Telegram handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_chat.id

    logging.info(f"Пользователь {user_id} отправил команду /start")  # ← Лог
    text = (
        "👋 Привет! Я бот для мониторинга серверов Clore.\n\n"
        "Команды:\n"
        "/filters — установить фильтры\n"
        "/check_servers — проверить серверы сейчас\n"  # ← Новая команда
        "/start_check — начать авто-проверку\n"
        "/stop_check — остановить\n"
    )
    if update.message:
        await update.message.reply_text(text)

async def filters(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Мин. 10 GPU", callback_data="filter_min_gpu_10")],
        [InlineKeyboardButton("Цена < $5", callback_data="filter_max_price_5")]
    ]
    if update.message:
        await update.message.reply_text("⚙️ Установите фильтры:", reply_markup=InlineKeyboardMarkup(keyboard))

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    await query.answer()
    user_id = query.message.chat_id
    f = user_filters.get(user_id, {"min_gpu": 1, "max_price": 9999})
    
    if query.data == "filter_min_gpu_10":
        f["min_gpu"] = 10
    if query.data == "filter_max_price_5":
        f["max_price"] = 5
    user_filters[user_id] = f
    await query.edit_message_text(f"✅ Фильтры обновлены: {f}")



    
# --- Команда /check_servers
async def check_servers_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat:
        user_id = update.effective_chat.id
        filters = user_filters.get(user_id, {"min_gpu": 1, "max_price": 9999})

    servers = get_clore_servers()
    if not servers:
        if update.message:
            await update.message.reply_text("❌ Нет доступных серверов.")
            return

    result = []
    for srv in servers:
        price_data = srv.get("price", {})
        original_price = price_data.get("original_usd", {})
        price = float(original_price.get("on_demand", 0))
        gpu_count = len(srv.get("gpu_array", []))

        if gpu_count < filters["min_gpu"] or price > filters["max_price"]:
            continue

       

        result.append(
            f"💻 GPU: {srv.get('specs', {}).get('gpu')} x{gpu_count}\n"
            f"💰 Цена: ${price:.2f}\n"
            f"🆔 ID: {srv.get('id')}\n"
        )

    if result:
        # Разбиваем результаты на части по 20 серверов
        for i in range(0, len(result), 20):
            chunk = result[i:i+20]
            message = "🔍 Результаты проверки:\n\n" + "\n".join(chunk)
            try:
                await update.message.reply_text(message)
            except Exception as e:
                logging.error(f"Ошибка при отправке сообщения: {e}")
                await update.message.reply_text("❌ Не удалось отправить результаты.")
    else:
        if update.message:
            await update.message.reply_text("❌ Нет подходящих серверов по вашим фильтрам.")





            
def main():
    # Создаем приложение через ApplicationBuilder
    app = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .connect_timeout(60)  # Увеличенный таймаут подключения
        .read_timeout(60)     # Увеличенный таймаут чтения
        .build()
    )





    # Регистрируем обработчики команд
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("filters", filters))
    app.add_handler(CommandHandler("check_servers", check_servers_now))
    app.add_handler(CallbackQueryHandler(button))
    # Запускаем опрос Telegram API




    
    app.run_polling()
    

if __name__ == "__main__":
    main()
