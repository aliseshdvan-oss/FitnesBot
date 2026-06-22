import os
import json
import logging
import random
import re
import datetime
import threading
import time

import telebot
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR

import pandas as pd
import matplotlib.pyplot as plt

# =====================================================
# ENV & CONFIG
# =====================================================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

PROFILES_FILE = os.path.join(DATA_DIR, "profiles.json")
MEASUREMENTS_FILE = os.path.join(DATA_DIR, "measurements.json")
HABITS_FILE = os.path.join(DATA_DIR, "habits.json")

# =====================================================
# LOGGING
# =====================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# =====================================================
# BOT
# =====================================================

bot = telebot.TeleBot(BOT_TOKEN)

# =====================================================
# LOAD / SAVE JSON
# =====================================================

def load_json(path):
    if not os.path.exists(path):
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return {int(k) if k.lstrip('-').isdigit() else k: v for k, v in data.items()}
            return data
    except json.JSONDecodeError as e:
        logger.error(f"Ошибка парсинга JSON в {path}: {e}")
        return {}
    except Exception as e:
        logger.error(f"Неизвестная ошибка при загрузке {path}: {e}")
        return {}

def save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.error(f"Не удалось сохранить {path}: {e}")

# =====================================================
# DATABASE INITIALIZATION
# =====================================================

user_profiles = load_json(PROFILES_FILE)
measurements_db = load_json(MEASUREMENTS_FILE)
habits_db = load_json(HABITS_FILE)

# =====================================================
# PROFILE (расширенный: список привычек, время уведомления)
# =====================================================

def get_profile(user_id):
    if user_id not in user_profiles:
        user_profiles[user_id] = {
            "name": "",
            "height": None,
            "goal": None,
            "habits": [],           # список названий привычек (макс 5)
            "reminder_time": None,  # строка "HH:MM" или None
            "last_reminder_date": None  # дата последнего отправленного уведомления
        }
        save_json(PROFILES_FILE, user_profiles)

    profile = user_profiles[user_id]
    # Инициализация новых полей для старых профилей
    if "habits" not in profile:
        profile["habits"] = []
    if "reminder_time" not in profile:
        profile["reminder_time"] = None
    if "last_reminder_date" not in profile:
        profile["last_reminder_date"] = None

    save_json(PROFILES_FILE, user_profiles)
    return profile

def save_profile(user_id):
    save_json(PROFILES_FILE, user_profiles)

# =====================================================
# MEASUREMENTS PARSING & VALIDATION
# =====================================================

def parse_measurements(text):
    text = text.lower()

    patterns = {
        "weight": r"вес\s*(\d+[.,]?\d*)",
        "waist": r"талия\s*(\d+[.,]?\d*)",
        "hips": r"ягоди[цц]?\s*(\d+[.,]?\d*)",
    }

    result = {}
    for k, p in patterns.items():
        m = re.search(p, text)
        if m:
            try:
                value = float(m.group(1).replace(",", "."))
                if k == "weight" and 20 <= value <= 500:
                    result[k] = value
                elif k in ("waist", "hips") and 30 <= value <= 300:
                    result[k] = value
                else:
                    logger.warning(f"Некорректное значение для {k}: {value}")
            except ValueError:
                pass

    return result if result else None

def get_latest_weight(user_id):
    data = measurements_db.get(user_id, [])
    for item in reversed(data):
        if "weight" in item:
            return item["weight"]
    return None

def get_weight_history(user_id):
    data = measurements_db.get(user_id, [])
    out = []
    for item in data:
        if "weight" in item:
            out.append({
                "date": item.get("date", "неизвестно"),
                "weight": item["weight"]
            })
    return out

# =====================================================
# HABITS (расширенные)
# =====================================================

def mark_habit(user_id, habit):
    today = datetime.datetime.now().strftime("%Y-%m-%d")

    if user_id not in habits_db:
        habits_db[user_id] = {}

    if habit not in habits_db[user_id]:
        habits_db[user_id][habit] = {}

    habits_db[user_id][habit][today] = 1
    save_json(HABITS_FILE, habits_db)

def get_habit_streak(user_id, habit):
    if user_id not in habits_db:
        return 0
    data = habits_db[user_id].get(habit, {})
    dates = sorted(data.keys())
    if not dates:
        return 0

    streak = 1
    for i in range(len(dates) - 1, 0, -1):
        d1 = datetime.datetime.strptime(dates[i], "%Y-%m-%d")
        d2 = datetime.datetime.strptime(dates[i - 1], "%Y-%m-%d")
        if (d1 - d2).days == 1:
            streak += 1
        else:
            break
    return streak

# =====================================================
# GENERATE HEATMAP
# =====================================================

def generate_heatmap(user_id, habit):
    if user_id not in habits_db:
        return None

    data = habits_db[user_id].get(habit, {})
    if not data:
        return None

    try:
        rows = []
        for date_str in data:
            d = datetime.datetime.strptime(date_str, "%Y-%m-%d")
            rows.append({"date": d, "value": 1})

        df = pd.DataFrame(rows)
        df["week"] = df["date"].dt.isocalendar().week
        df["day"] = df["date"].dt.weekday

        pivot = df.pivot_table(index="day", columns="week", values="value", fill_value=0)
        if pivot.empty:
            return None

        plt.figure(figsize=(10, 4))
        plt.imshow(pivot, cmap="YlGn", aspect="auto")
        plt.title(f"Habit: {habit}")
        plt.yticks(range(7), ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])
        plt.colorbar(label="Completed")

        path = os.path.join(DATA_DIR, f"heatmap_{user_id}_{habit}.png")
        plt.savefig(path, bbox_inches="tight")
        plt.close()
        return path
    except Exception as e:
        logger.error(f"Ошибка генерации тепловой карты: {e}")
        return None

# =====================================================
# DASHBOARD HELPERS
# =====================================================

def get_last_date(user_id):
    data = measurements_db.get(user_id, [])
    if not data:
        return None
    return data[-1].get("date")

def get_streak_days(user_id):
    data = measurements_db.get(user_id, [])
    dates = sorted({x.get("date") for x in data if "date" in x})
    if not dates:
        return 0

    streak = 1
    for i in range(len(dates) - 1, 0, -1):
        d1 = datetime.datetime.strptime(dates[i], "%Y-%m-%d")
        d2 = datetime.datetime.strptime(dates[i - 1], "%Y-%m-%d")
        if (d1 - d2).days == 1:
            streak += 1
        else:
            break
    return streak

# =====================================================
# SCHEDULER FOR REMINDERS
# =====================================================

scheduler = BackgroundScheduler()
scheduler.start()

def schedule_reminder(user_id, time_str):
    """
    Создаёт или обновляет задачу на ежедневное уведомление для пользователя.
    time_str: строка "HH:MM" или None (отключить)
    """
    job_id = f"reminder_{user_id}"
    # Удаляем существующую задачу
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)

    if time_str is None:
        logger.info(f"Уведомления отключены для {user_id}")
        return

    try:
        hour, minute = map(int, time_str.split(':'))
        # Создаём задачу с триггером на каждый день в указанное время
        trigger = CronTrigger(hour=hour, minute=minute, timezone='Europe/Moscow')
        scheduler.add_job(
            func=send_reminder,
            trigger=trigger,
            args=[user_id],
            id=job_id,
            replace_existing=True
        )
        logger.info(f"Уведомление установлено для {user_id} на {time_str}")
    except Exception as e:
        logger.error(f"Ошибка установки уведомления для {user_id}: {e}")

def send_reminder(user_id):
    """Отправляет напоминание пользователю."""
    profile = get_profile(user_id)
    today = datetime.datetime.now().strftime("%Y-%m-%d")

    # Проверяем, отправляли ли сегодня
    if profile.get("last_reminder_date") == today:
        logger.info(f"Напоминание уже отправлено сегодня для {user_id}")
        return

    try:
        bot.send_message(
            user_id,
            "🔔 Доброе утро! Не забудьте сегодня:\n"
            "• Записать замеры (вес, талия, бёдра)\n"
            "• Отметить свои привычки\n"
            "Я здесь, чтобы поддержать вас! 🌷"
        )
        # Обновляем дату последнего отправления
        profile["last_reminder_date"] = today
        save_profile(user_id)
        logger.info(f"Напоминание отправлено пользователю {user_id}")
    except Exception as e:
        logger.error(f"Не удалось отправить напоминание {user_id}: {e}")

# Загружаем все профили и устанавливаем задачи при запуске
def init_reminders():
    for user_id, profile in user_profiles.items():
        reminder_time = profile.get("reminder_time")
        if reminder_time:
            schedule_reminder(user_id, reminder_time)

# =====================================================
# COMMANDS
# =====================================================

@bot.message_handler(commands=["start"])
def start(message):
    bot.reply_to(
        message,
        "Привет! Я бот для отслеживания здоровья и привычек. 📊\n\n"
        "Доступные команды:\n"
        "/help — показать справку\n"
        "/dashboard — показать сводку\n"
        "/habitmap [привычка] — тепловая карта\n"
        "/addhabit [название] — добавить привычку (макс 5)\n"
        "/removehabit [название] — удалить привычку\n"
        "/habits — список привычек\n"
        "/setreminder HH:MM — установить время уведомления (например, /setreminder 09:00)\n"
        "/reminder off — отключить уведомления\n"
        "/editlast [параметр] [значение] — изменить последний замер (например, /editlast weight 72)\n"
        "/del_last — удалить последний замер\n"
        "/setname [имя]\n"
        "/setheight [см]\n"
        "/setgoal [кг]"
    )

@bot.message_handler(commands=["help"])
def help_command(message):
    bot.reply_to(
        message,
        "📋 Полная справка:\n\n"
        "🔹 Управление профилем:\n"
        "/setname Имя\n"
        "/setheight Рост (см)\n"
        "/setgoal Целевой вес (кг)\n\n"
        "🔹 Замеры:\n"
        "Отправьте сообщение вида: «Вес 70.5, талия 80»\n"
        "/editlast weight 72 — изменить вес в последнем замере\n"
        "/editlast waist 82 — изменить талию\n"
        "/editlast hips 100 — изменить бёдра\n"
        "/del_last — удалить последний замер\n\n"
        "🔹 Привычки (максимум 5):\n"
        "/addhabit спорт — добавить привычку\n"
        "/removehabit спорт — удалить\n"
        "/habits — показать список\n"
        "Чтобы отметить привычку, просто напишите её название в чате.\n\n"
        "🔹 Уведомления:\n"
        "/setreminder 09:00 — ежедневное напоминание\n"
        "/reminder off — отключить\n\n"
        "🔹 Статистика:\n"
        "/dashboard — сводка\n"
        "/habitmap water — тепловая карта для привычки"
    )

@bot.message_handler(commands=["setname"])
def set_name(message):
    user_id = message.from_user.id
    name = message.text.replace("/setname", "").strip()
    if not name:
        bot.reply_to(message, "Напишите имя после команды, например: /setname Анна")
        return
    profile = get_profile(user_id)
    profile["name"] = name
    save_profile(user_id)
    bot.reply_to(message, f"✅ Имя установлено: {name}")

@bot.message_handler(commands=["setheight"])
def set_height(message):
    user_id = message.from_user.id
    try:
        height = float(message.text.replace("/setheight", "").strip().replace(",", "."))
        if height <= 0 or height > 300:
            raise ValueError
    except:
        bot.reply_to(message, "Укажите рост в сантиметрах числом, например: /setheight 175")
        return
    profile = get_profile(user_id)
    profile["height"] = height
    save_profile(user_id)
    bot.reply_to(message, f"✅ Рост установлен: {height} см")

@bot.message_handler(commands=["setgoal"])
def set_goal(message):
    user_id = message.from_user.id
    try:
        goal = float(message.text.replace("/setgoal", "").strip().replace(",", "."))
        if goal <= 0 or goal > 500:
            raise ValueError
    except:
        bot.reply_to(message, "Укажите целевой вес в кг числом, например: /setgoal 65")
        return
    profile = get_profile(user_id)
    profile["goal"] = goal
    save_profile(user_id)
    bot.reply_to(message, f"✅ Целевой вес установлен: {goal} кг")

@bot.message_handler(commands=["addhabit"])
def add_habit(message):
    user_id = message.from_user.id
    habit_name = message.text.replace("/addhabit", "").strip().lower()
    if not habit_name:
        bot.reply_to(message, "Укажите название привычки, например: /addhabit спорт")
        return

    profile = get_profile(user_id)
    habits = profile.get("habits", [])

    if habit_name in habits:
        bot.reply_to(message, f"Привычка «{habit_name}» уже есть.")
        return

    if len(habits) >= 5:
        bot.reply_to(message, "❌ Нельзя добавить больше 5 привычек. Сначала удалите одну.")
        return

    habits.append(habit_name)
    profile["habits"] = habits
    save_profile(user_id)
    bot.reply_to(message, f"✅ Привычка «{habit_name}» добавлена. Теперь вы можете отмечать её, просто написав это слово в чате.")

@bot.message_handler(commands=["removehabit"])
def remove_habit(message):
    user_id = message.from_user.id
    habit_name = message.text.replace("/removehabit", "").strip().lower()
    if not habit_name:
        bot.reply_to(message, "Укажите название привычки, например: /removehabit спорт")
        return

    profile = get_profile(user_id)
    habits = profile.get("habits", [])

    if habit_name not in habits:
        bot.reply_to(message, f"Привычка «{habit_name}» не найдена.")
        return

    habits.remove(habit_name)
    profile["habits"] = habits
    save_profile(user_id)

    # Также удаляем историю этой привычки (очищаем данные)
    if user_id in habits_db and habit_name in habits_db[user_id]:
        del habits_db[user_id][habit_name]
        save_json(HABITS_FILE, habits_db)

    bot.reply_to(message, f"✅ Привычка «{habit_name}» удалена.")

@bot.message_handler(commands=["habits"])
def list_habits(message):
    user_id = message.from_user.id
    profile = get_profile(user_id)
    habits = profile.get("habits", [])

    if not habits:
        bot.reply_to(message, "У вас пока нет привычек. Добавьте через /addhabit")
        return

    text = "📋 Ваши привычки:\n\n"
    for h in habits:
        streak = get_habit_streak(user_id, h)
        text += f"• {h} — серия: {streak} дней\n"
    bot.reply_to(message, text)

@bot.message_handler(commands=["setreminder"])
def set_reminder(message):
    user_id = message.from_user.id
    time_str = message.text.replace("/setreminder", "").strip()
    if not time_str:
        bot.reply_to(message, "Укажите время в формате HH:MM, например: /setreminder 09:00")
        return

    # Проверка формата
    if not re.match(r"^([01]\d|2[0-3]):([0-5]\d)$", time_str):
        bot.reply_to(message, "Неверный формат. Используйте HH:MM (например, 09:00)")
        return

    profile = get_profile(user_id)
    profile["reminder_time"] = time_str
    save_profile(user_id)

    # Обновляем задачу в планировщике
    schedule_reminder(user_id, time_str)

    bot.reply_to(message, f"✅ Ежедневное напоминание установлено на {time_str}.")

@bot.message_handler(commands=["reminder"])
def reminder_off(message):
    user_id = message.from_user.id
    command_parts = message.text.split()
    if len(command_parts) > 1 and command_parts[1].lower() == "off":
        profile = get_profile(user_id)
        profile["reminder_time"] = None
        save_profile(user_id)
        # Удаляем задачу
        job_id = f"reminder_{user_id}"
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)
        bot.reply_to(message, "✅ Уведомления отключены.")
    else:
        bot.reply_to(message, "Чтобы отключить уведомления, напишите: /reminder off")

@bot.message_handler(commands=["editlast"])
def edit_last_measurement(message):
    user_id = message.from_user.id
    parts = message.text.replace("/editlast", "").strip().split()
    if len(parts) != 2:
        bot.reply_to(message, "Использование: /editlast weight 72  или /editlast waist 80")
        return

    param, value_str = parts[0].lower(), parts[1]
    if param not in ("weight", "waist", "hips"):
        bot.reply_to(message, "Параметр должен быть: weight, waist или hips")
        return

    try:
        new_value = float(value_str.replace(",", "."))
        if param == "weight" and not (20 <= new_value <= 500):
            raise ValueError
        if param in ("waist", "hips") and not (30 <= new_value <= 300):
            raise ValueError
    except:
        bot.reply_to(message, "Укажите корректное числовое значение.")
        return

    data = measurements_db.get(user_id, [])
    if not data:
        bot.reply_to(message, "Нет замеров для редактирования.")
        return

    # Редактируем последний замер
    last = data[-1]
    last[param] = new_value
    measurements_db[user_id] = data
    save_json(MEASUREMENTS_FILE, measurements_db)

    bot.reply_to(message, f"✅ Последний замер обновлён: {param} = {new_value}")

@bot.message_handler(commands=["del_last"])
def delete_last_measurement(message):
    user_id = message.from_user.id
    data = measurements_db.get(user_id, [])
    if not data:
        bot.reply_to(message, "Нет замеров для удаления.")
        return

    removed = data.pop()
    measurements_db[user_id] = data
    save_json(MEASUREMENTS_FILE, measurements_db)

    bot.reply_to(
        message,
        f"🗑️ Удалён последний замер от {removed.get('date', 'неизвестно')}:\n"
        f"{', '.join(f'{k}: {v}' for k, v in removed.items() if k != 'date')}"
    )

@bot.message_handler(commands=["dashboard"])
def dashboard(message):
    user_id = message.from_user.id
    profile = get_profile(user_id)

    name = profile.get("name") or "Пользователь"
    height = profile.get("height") or "—"
    goal = profile.get("goal")

    weight = get_latest_weight(user_id)
    remaining = "—"
    if goal and weight:
        remaining = round(weight - goal, 1)

    last_date = get_last_date(user_id) or "—"
    streak = get_streak_days(user_id)

    # Список привычек с сериями
    habits = profile.get("habits", [])
    habits_text = ""
    if habits:
        habits_text = "\n📌 Привычки:\n"
        for h in habits:
            s = get_habit_streak(user_id, h)
            habits_text += f"• {h} — серия: {s} дней\n"
    else:
        habits_text = "\n📌 Привычек пока нет. Добавьте через /addhabit"

    history = get_weight_history(user_id)
    chart = ""
    if history:
        chart = "\n📊 Последние веса:\n"
        for h in history[-5:]:
            chart += f"{h['date']}: {h['weight']} кг\n"

    text = (
        f"🌷 Профиль: {name}\n\n"
        f"📏 Рост: {height} см\n"
        f"⚖️ Вес: {weight or '—'} кг\n"
        f"🎯 Цель: {goal or '—'} кг\n"
        f"📉 До цели: {remaining}\n\n"
        f"📅 Последний замер: {last_date}\n"
        f"🔥 Серия замеров: {streak} дней\n"
        f"{habits_text}\n"
        f"{chart}"
    )

    bot.reply_to(message, text)

@bot.message_handler(commands=["habitmap"])
def habitmap(message):
    user_id = message.from_user.id
    habit = message.text.replace("/habitmap", "").strip().lower()

    if not habit:
        bot.reply_to(message, "Укажите привычку, например: /habitmap спорт")
        return

    path = generate_heatmap(user_id, habit)

    if not path or not os.path.exists(path):
        bot.reply_to(message, f"Нет данных для привычки «{habit}» или ошибка генерации.")
        return

    try:
        with open(path, "rb") as f:
            bot.send_photo(message.chat.id, f)
    except Exception as e:
        logger.error(f"Ошибка отправки фотографии: {e}")
        bot.reply_to(message, "Не удалось отправить график.")
    finally:
        try:
            os.remove(path)
        except:
            pass

# =====================================================
# TEXT HANDLER (отметка привычек + распознавание замеров)
# =====================================================

@bot.message_handler(content_types=["text"])
def handle_text(message):
    user_id = message.from_user.id
    text = message.text

    # 1. Проверяем, не является ли сообщение замером
    parsed = parse_measurements(text)
    if parsed:
        parsed["date"] = datetime.datetime.now().strftime("%Y-%m-%d")
        if user_id not in measurements_db:
            measurements_db[user_id] = []
        measurements_db[user_id].append(parsed)
        save_json(MEASUREMENTS_FILE, measurements_db)
        bot.reply_to(message, "📊 Замер сохранён!")
        return

    # 2. Проверяем, не является ли сообщение названием привычки
    profile = get_profile(user_id)
    habits = profile.get("habits", [])
    for habit in habits:
        # Если текст содержит название привычки (или совпадает)
        if habit in text.lower():
            mark_habit(user_id, habit)
            bot.reply_to(message, f"✅ Привычка «{habit}» засчитана!")
            return

    # 3. Если ничего не подошло — случайный ответ (позже заменим на ИИ)
    bot.reply_to(message, random.choice([
        "Поняла 🌷",
        "Расскажи подробнее",
        "Я рядом",
        "Интересно",
        "Хорошо, учту"
    ]))

# =====================================================
# RUN
# =====================================================

if __name__ == "__main__":
    logger.info("Запуск бота...")
    # Инициализируем напоминания для всех пользователей
    init_reminders()
    logger.info("Бот запущен и ожидает сообщения...")
    bot.infinity_polling(skip_pending=True)
