import time
import requests
import threading
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from config import TELEGRAM_TOKEN, CLORE_API_URL, CHECK_INTERVAL, API_TOKEN, API_HEADERS
import logging
from telegram.ext import MessageHandler, filters

# --- Логирование
logging.basicConfig(level=logging.INFO)

# --- Состояния
active_users = {}
sent_servers = {}
user_filters = {}
user_editing = {}  # {user_id: 'min_gpu' | 'max_price'}
user_efficiency = {}

# --- Коэффициенты GPU без разгона
GPU_EFFICIENCY = {
    "3060": 0.85, "3060 ti": 0.83, "3070": 0.82, "3080": 0.80,
    "3090": 0.78, "4090": 0.82, "4070": 0.83, "4070 ti": 0.82,
    "4080": 0.81, "a5000": 0.81, "a4000": 0.84
}

# --- WhatToMine API


# --- Расчёт данных о сервере (без прибыльности)
def calculate_server_info(user_id, srv):
    # Извлекаем модель GPU из specs.gpu
    gpu_model = srv.get("specs", {}).get("gpu", "").replace("1x ", "")
    
    # Получаем количество GPU из массива
    gpu_count = len(srv.get("gpu_array", []))
    
    # Безопасный доступ к цене через вложенную структуру
    price_data = srv.get("price", {})
    original_price = price_data.get("original_usd", {})
    price = float(original_price.get("on_demand", 0))  # Берем on-demand цену
    
    return {
        "gpu_model": gpu_model,
        "gpu_count": gpu_count,
        "price": price
    }

# --- Проверка серверов по фильтру
def check_servers_for_user(user_id, app):
    servers = get_clore_servers()
    if not servers:
        return

    filters = user_filters.get(user_id, {"min_gpu": 1, "max_price": 9999})
    already_sent = sent_servers.get(user_id, set())

    for srv in servers:
         # Пропускаем арендованные серверы
        if srv.get("rented", False):
            continue        
        # Безопасный доступ к цене
        price_data = srv.get("price", {})
        original_price = price_data.get("original_usd", {})
        price = float(original_price.get("on_demand", 0))
        
        gpu_count = len(srv.get("gpu_array", []))

        if gpu_count < filters["min_gpu"] or price > filters["max_price"]:
            continue

        income, profit, coin = calculate_profit(user_id, srv)

        srv_id = srv.get("id")
        if srv_id in already_sent and profit <= 0:
            continue
        if srv_id in already_sent and profit > 0:
            already_sent.remove(srv_id)

        if profit > 0:
            msg = (
                f"💻 GPU: {srv.get('specs', {}).get('gpu')} x{gpu_count}\n"
                f"💰 Цена: ${price:.2f}\n"
                f"📈 Монета: {coin}\n"
                f"📊 Доход: ${income:.2f}\n"
                f"✅ Прибыль: ${profit:.2f}\n"
                f"🆔 ID: {srv_id}"
                f"👤 Владелец: {srv.get('owner')}\n"  # ← Новая строка
            )
            app.bot.send_message(chat_id=user_id, text=msg)
            already_sent.add(srv_id)

    sent_servers[user_id] = already_sent




# --- Clore API
def get_clore_servers():
    try:
        r = requests.get(CLORE_API_URL, headers=API_HEADERS, timeout=5)
        if r.status_code == 200:
            logging.info(f"Запрос на получения северов с Clore получил {r.status_code}")
            return r.json().get("servers", [])
    except Exception as e:
        logging.warning(f"Clore error: {e}")
    return []


# --- Проверка серверов
def check_servers_for_user(user_id, app):
    servers = get_clore_servers()
    if not servers:
        return

    filters = user_filters.get(user_id, {"min_gpu": 1, "max_price": 9999})
    already_sent = sent_servers.get(user_id, set())

    for srv in servers:
        # Безопасный доступ к цене
        price_data = srv.get("price", {})
        original_price = price_data.get("original_usd", {})
        price = float(original_price.get("on_demand", 0))
        
        gpu_count = len(srv.get("gpu_array", []))

        if gpu_count < filters["min_gpu"] or price > filters["max_price"]:
            continue

        income, profit, coin = calculate_profit(user_id, srv)

        srv_id = srv.get("id")
        if srv_id in already_sent and profit <= 0:
            continue
        if srv_id in already_sent and profit > 0:
            already_sent.remove(srv_id)

        if profit > 0:
            msg = (
                f"💻 GPU: {srv.get('specs', {}).get('gpu')} x{gpu_count}\n"
                f"💰 Цена: ${price:.2f}\n"
                f"📈 Монета: {coin}\n"
                f"📊 Доход: ${income:.2f}\n"
                f"✅ Прибыль: ${profit:.2f}\n"
                f"🆔 ID: {srv_id}"
            )
            app.bot.send_message(chat_id=user_id, text=msg)
            already_sent.add(srv_id)

    sent_servers[user_id] = already_sent

def auto_check(user_id, app):
    while active_users.get(user_id, False):
        check_servers_for_user(user_id, app)
        time.sleep(CHECK_INTERVAL)
        
        


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

async def set_filters(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
    [InlineKeyboardButton("Установить мин. GPU", callback_data="filter_min_gpu")],
    [InlineKeyboardButton("Установить макс. цену", callback_data="filter_max_price")]
]
    if update.message:
        await update.message.reply_text("⚙️ Установите фильтры:", reply_markup=InlineKeyboardMarkup(keyboard))

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.message.chat_id
    
    
    
    if query.data == "filter_min_gpu":
        await query.message.reply_text("Введите минимальное количество GPU:")
        user_editing[user_id] = 'min_gpu'
    elif query.data == "filter_max_price":
        await query.message.reply_text("Введите максимальную цену:")
        user_editing[user_id] = 'max_price'
        
    await query.edit_message_text(f"✅ Фильтры обновлены: {f}")
    
    
async def handle_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_chat.id
    if user_id in user_editing:
        try:
            value = float(update.message.text)
            filter_type = user_editing[user_id]
            # Обновление фильтра только после ввода значения
            user_filters.setdefault(user_id, {"min_gpu": 1, "max_price": 9999})
            
            if filter_type == 'min_gpu':
                user_filters[user_id]['min_gpu'] = max(1, int(value))
            elif filter_type == 'max_price':
                user_filters[user_id]['max_price'] = max(0.01, value)

            await update.message.reply_text(f"✅ Фильтр '{filter_type}' установлен на {value}")
            del user_editing[user_id]
        except ValueError:
            await update.message.reply_text("❌ Введите корректное число.")




async def start_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat:
        user_id = update.effective_chat.id
        if active_users.get(user_id):
            if update.message:
                await update.message.reply_text("⏳ Уже проверяю...")
                return
    active_users[user_id] = True
    # Передаем основной объект `app`, а не `context.application`
    threading.Thread(target=auto_check, args=(user_id, context.application), daemon=True).start()
    if update.message:
        await update.message.reply_text("✅ Автоматическая проверка запущена!")




async def stop_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat:
        user_id = update.effective_chat.id
        active_users[user_id] = False
        if update.message:
            await update.message.reply_text("⏸ Проверка остановлена.")
    
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
        # Пропускаем арендованные серверы
        if srv.get("rented", False):
            continue

        price_data = srv.get("price", {})
        original_price = price_data.get("original_usd", {})
        price = float(original_price.get("on_demand", 0))
        gpu_count = len(srv.get("gpu_array", []))

        if gpu_count < filters["min_gpu"] or price > filters["max_price"]:
            continue

        server_info = calculate_server_info(user_id, srv)
        
        result.append(
            f"💻 GPU: {srv.get('specs', {}).get('gpu')} x{server_info['gpu_count']}\n"
            f"💰 Цена: ${server_info['price']:.2f}\n"
            f"👤 Владелец: {srv.get('owner')}\n"  # ← Новая строка
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
    # --- Сначала создаем приложение ---
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # --- Затем добавляем обработчики ---
    # Регистрация команд
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("filters", set_filters))
    app.add_handler(CommandHandler("start_check", start_check))
    app.add_handler(CommandHandler("stop_check", stop_check))
    app.add_handler(CommandHandler("check_servers", check_servers_now))
    app.add_handler(CallbackQueryHandler(button))
    # Регистрация текстовых сообщений
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_input))  # ← После инициализации app

    # --- Запуск бота ---
    app.run_polling()

if __name__ == "__main__":
    main()
