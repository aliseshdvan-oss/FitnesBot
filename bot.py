import os
import logging
import telebot
from openai import OpenAI
from dotenv import load_dotenv

# 1. Загружаем ключи из .env
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

# 2. Настраиваем логирование
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# 3. Создаём клиента DeepSeek (синхронный)
deepseek_client = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com"
)

# 4. Создаём бота
bot = telebot.TeleBot(BOT_TOKEN)

# 5. Обработчик команды /start
@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(
        message,
        f"Привет, {message.from_user.first_name}! 👋\n"
        "Я твой личный ИИ-наставник на базе DeepSeek. Я здесь, чтобы поддержать тебя в сложные времена.\n"
        "Напиши мне, что у тебя на душе, и я постараюсь помочь."
    )

# 6. Обработчик всех текстовых сообщений
@bot.message_handler(func=lambda message: True)
def handle_message(message):
    user_text = message.text
    logging.info(f"Получено сообщение от {message.from_user.id}: {user_text}")

    try:
        # Отправляем запрос к DeepSeek
        response = deepseek_client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "Ты — мудрый и добрый наставник. Твоя задача — поддерживать, давать мудрые советы и помогать человеку справляться с трудными эмоциями и ситуациями. Отвечай кратко, по делу и с теплотой."},
                {"role": "user", "content": user_text}
            ],
            max_tokens=300
        )
        ai_answer = response.choices[0].message.content
        bot.reply_to(message, ai_answer)

    except Exception as e:
        logging.error(f"Ошибка: {e}")
        bot.reply_to(message, "Извини, у меня сейчас что-то с головой. Попробуй ещё раз через минуту.")

# 7. Запускаем бота (бесконечный опрос)
if __name__ == "__main__":
    logging.info("Бот запущен и ожидает сообщения...")
    bot.infinity_polling()