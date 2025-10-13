import time
import requests
import threading
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from config import TELEGRAM_TOKEN, CLORE_API_URL, CHECK_INTERVAL, API_TOKEN, API_HEADERS
import logging

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

# --- WhatToMine API
def get_whattomine_data():
    try:
        r = requests.get("https://whattomine.com/coins.json", timeout=5)
        
        if r.status_code == 200:
            return r.json().get("coins", {})
    except Exception as e:
        logging.warning(f"WhatToMine error: {e}")
    return {}
def get_best_coin_for_gpu(gpu_model):
    coins = get_whattomine_data()
    if not coins:
        return {"coin": "Unknown", "profit": 0.0}

    best = max(coins.values(), key=lambda x: x.get("profit", 0))
    return {"coin": best["tag"], "profit": best["profit"]}

def get_gpu_efficiency(gpu_model, user_id):
    if user_id in user_efficiency:
        return user_efficiency[user_id]
    model = gpu_model.lower()
    for key in GPU_EFFICIENCY:
        if key in model:
            return GPU_EFFICIENCY[key]
    return 0.8

# --- Clore API
def get_clore_servers():
    try:
        r = requests.get(CLORE_API_URL, headers=API_HEADERS, timeout=5)
        if r.status_code == 200:
            logging.info(f"Запрос на получения северов с Clore получил {r.status_code}")
            return r.json().get("result", [])
    except Exception as e:
        logging.warning(f"Clore error: {e}")
    return []

# --- Расчет доходности
def calculate_profit(user_id, srv):
    gpu_model = srv.get("gpu", "")
    gpu_count = srv.get("gpu_count", 1)
    price = float(srv.get("price", 0))

    best_coin = get_best_coin_for_gpu(gpu_model)
    eff = get_gpu_efficiency(gpu_model, user_id)
    income = best_coin["profit"] * gpu_count * eff
    profit = income - price
    return income, profit, best_coin["coin"]

# --- Проверка серверов
def check_servers_for_user(user_id, app):
    servers = get_clore_servers()
    if not servers:
        return

    filters = user_filters.get(user_id, {"min_gpu": 1, "max_price": 9999})
    already_sent = sent_servers.get(user_id, set())

    for srv in servers:
        gpu_count = srv.get("gpu_count", 1)
        price = float(srv.get("price", 0))

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
                f"💻 GPU: {srv.get('gpu')} x{gpu_count}\n"
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
    await update.message.reply_text(text)

async def filters(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Мин. 10 GPU", callback_data="filter_min_gpu_10")],
        [InlineKeyboardButton("Цена < $5", callback_data="filter_max_price_5")]
    ]
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

async def start_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_chat.id
    if active_users.get(user_id):
        await update.message.reply_text("⏳ Уже проверяю...")
        return
    active_users[user_id] = True
    # Передаем основной объект `app`, а не `context.application`
    threading.Thread(target=auto_check, args=(user_id, context.application), daemon=True).start()
    await update.message.reply_text("✅ Автоматическая проверка запущена!")

async def stop_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_chat.id
    active_users[user_id] = False
    await update.message.reply_text("⏸ Проверка остановлена.")
    
async def check_servers_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_chat.id
    filters = user_filters.get(user_id, {"min_gpu": 1, "max_price": 9999})
    already_sent = set()  # Используем временное множество для одноразовой проверки

    servers = get_clore_servers()
    if not servers:
        await update.message.reply_text("❌ Нет доступных серверов.")
        return

    result = []
    for srv in servers:
        gpu_count = srv.get("gpu_count", 1)
        price = float(srv.get("price", 0))

        if gpu_count < filters["min_gpu"] or price > filters["max_price"]:
            continue

        income, profit, coin = calculate_profit(user_id, srv)
        if profit <= 0:
            continue

        result.append(
            f"💻 GPU: {srv.get('gpu')} x{gpu_count}\n"
            f"💰 Цена: ${price:.2f}\n"
            f"📈 Монета: {coin}\n"
            f"📊 Доход: ${income:.2f}\n"
            f"✅ Прибыль: ${profit:.2f}\n"
            f"🆔 ID: {srv.get('id')}\n"
        )

    if result:
        await update.message.reply_text("🔍 Результаты проверки:\n\n" + "\n".join(result))
    else:
        await update.message.reply_text("❌ Нет подходящих серверов по вашим фильтрам.")

def main():
    # Создаем приложение через ApplicationBuilder
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    # Регистрируем обработчики команд
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("filters", filters))
    app.add_handler(CommandHandler("start_check", start_check))
    app.add_handler(CommandHandler("stop_check", stop_check))
    app.add_handler(CommandHandler("check_servers", check_servers_now))  # ← Новая команда
    app.add_handler(CallbackQueryHandler(button))
    # Запускаем опрос Telegram API
    app.run_polling()

if __name__ == "__main__":
    main()
