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
# --- Кэширование данных WhatToMine
WHAT_TO_MINE_CACHE = {}
WHAT_TO_MINE_CACHE_TTL = 300  # 5 минут в секундах

def get_whattomine_data():
    # Проверяем, не устарел ли кэш
    current_time = time.time()
    if WHAT_TO_MINE_CACHE and current_time - WHAT_TO_MINE_CACHE.get("timestamp", 0) < WHAT_TO_MINE_CACHE_TTL:
        return WHAT_TO_MINE_CACHE["data"]

    try:
        r = requests.get("https://whattomine.com/coins.json", timeout=5)
        if "error" in r.json():
            logging.error(f"Ошибка WhatToMine API: {r.json()['error']}")
            return {}

        if r.status_code == 200:
            coins_data = r.json().get("coins", {})
            # Обновляем кэш
            WHAT_TO_MINE_CACHE.update({
                "data": coins_data,
                "timestamp": current_time
            })
            logging.info(f"Получены данные от WhatToMine: {list(coins_data.values())[:3]}...")
            return coins_data
    except Exception as e:
        logging.warning(f"WhatToMine error: {e}")
    return {}



def get_best_coin_for_gpu(gpu_model):
    coins = get_whattomine_data()  # Использует кэшированные данные

    if not coins:
        return {"coin": "Unknown", "profit": 0.0}

    valid_coins = []
    for coin in coins.values():
        if isinstance(coin, dict) and "profitability" in coin and "tag" in coin:
            valid_coins.append({
                "tag": coin["tag"],
                "profit": float(coin["profitability"])
            })

    if not valid_coins:
        logging.warning("Нет валидных монет в ответе WhatToMine")
        return {"coin": "Unknown", "profit": 0.0}

    try:
        best = max(valid_coins, key=lambda x: x.get("profit", 0))
        return {
            "coin": best.get("tag", "Unknown"),
            "profit": float(best.get("profit", 0.0))
        }
    except Exception as e:
        logging.error(f"Ошибка при поиске лучшей монеты: {e}")
        return {"coin": "Unknown", "profit": 0.0}









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
            return r.json().get("servers", [])
    except Exception as e:
        logging.warning(f"Clore error: {e}")
    return []

# --- Расчет доходности
def calculate_profit(user_id, srv):
    # Извлекаем модель GPU из specs.gpu
    gpu_model = srv.get("specs", {}).get("gpu", "").replace("1x ", "")
    
    # Получаем количество GPU из массива
    gpu_count = len(srv.get("gpu_array", []))
    
    # Безопасный доступ к цене через вложенную структуру
    price_data = srv.get("price", {})
    original_price = price_data.get("original_usd", {})
    price = float(original_price.get("on_demand", 0))  # Берем on-demand цену
    
    ########best_coin = get_best_coin_for_gpu(gpu_model)

    try:
            best_coin = get_best_coin_for_gpu(gpu_model)
    except Exception as e:
            logging.error(f"Ошибка расчета прибыли: {e}")
            return 0.0, 0.0, "Error"

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
        price_data = srv.get("price", {})
        original_price = price_data.get("original_usd", {})
        price = float(original_price.get("on_demand", 0))
        gpu_count = len(srv.get("gpu_array", []))

        if gpu_count < filters["min_gpu"] or price > filters["max_price"]:
            continue

        income, profit, coin = calculate_profit(user_id, srv)
        if profit <= 0:
            continue

        result.append(
            f"💻 GPU: {srv.get('specs', {}).get('gpu')} x{gpu_count}\n"
            f"💰 Цена: ${price:.2f}\n"
            f"📈 Монета: {coin}\n"
            f"📊 Доход: ${income:.2f}\n"
            f"✅ Прибыль: ${profit:.2f}\n"
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
