import os
import tempfile
import time
import base64
import telebot
from config import (
    TELEGRAM_TOKEN, AUTOPOST_CHANNEL_ID, AUTOPOST_HOUR, AUTOPOST_MINUTE,
    CACHE_TTL, MAX_TEXT_LENGTH, RATE_LIMIT_SECONDS,
    MUSIC_KEYWORDS, MUSIC_MAX_DURATION_SEC, MUSIC_POST_DAYS, MUSIC_POST_HOUR, MUSIC_POST_MINUTE
)
import jinja2
from pparser import fetch_iccup_stats_async
import asyncio
from telebot import types
from playwright.sync_api import sync_playwright
from pparser import fetch_top_streak_player
from apscheduler.schedulers.background import BackgroundScheduler
import pytz
import html
import subprocess
from ytmusicapi import YTMusic
import random
from datetime import datetime
import re
import glob
import shutil
from playwright.async_api import async_playwright
import datetime
import config
from PIL import Image
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    stream=sys.stdout
)

logging.info(f"Текущее время МСК: {datetime.datetime.now(pytz.timezone('Europe/Moscow'))}")

jinja_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader('templates'),
    autoescape=jinja2.select_autoescape(['html', 'xml'])
)

stats_cache = {}
CACHE_TTL = 60
MAX_TEXT_LENGTH = 32  # лимит символов для никнейма и команд
RATE_LIMIT_SECONDS = 1  # лимит частоты запросов (секунд)
user_last_request_time = {}
waiting_for_nickname = {}

def get_cached_stats(nickname):
    now = time.time()
    if nickname in stats_cache:
        cached = stats_cache[nickname]
        if now - cached['ts'] < CACHE_TTL:
            return cached['data']
    stats = asyncio.run(fetch_iccup_stats_async(nickname))
    stats_cache[nickname] = {'data': stats, 'ts': now}
    return stats

def render_stats_html(stats_data):
    template = jinja_env.get_template('index.html')
    

    try:
        with open('static/style.css', 'r', encoding='utf-8') as css_file:
            css_content = css_file.read()
    except FileNotFoundError:
        css_content = "/* CSS file not found */"
    
    def get_image_as_base64(image_path):
        """Конвертирует изображение в base64 для встраивания в HTML"""
        try:
            with open(image_path, 'rb') as img_file:
                img_data = img_file.read()
                img_base64 = base64.b64encode(img_data).decode('utf-8')

                if image_path.lower().endswith('.png'):
                    mime_type = 'image/png'
                elif image_path.lower().endswith('.jpg') or image_path.lower().endswith('.jpeg'):
                    mime_type = 'image/jpeg'
                else:
                    mime_type = 'image/png'  
                result = f'data:{mime_type};base64,{img_base64}'
                return result
        except FileNotFoundError:
            return ''  
        except Exception as e:
            return ''
    
    def fake_url_for_static(filename):
        """Возвращает base64 данные изображения вместо пути"""
        image_path = f'static/{filename}'
        result = get_image_as_base64(image_path)
        return result
    
    jinja_env.globals['url_for'] = lambda endpoint, filename: fake_url_for_static(filename) if endpoint == 'static' else ''
    jinja_env.globals['inline_css'] = css_content
    
    return template.render(data=stats_data)

def take_screenshot(html_content):
    with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False, encoding='utf-8') as f:
        f.write(html_content)
        temp_file = f.name
    screenshot = None
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=[
            '--no-sandbox',
            '--disable-dev-shm-usage',
            '--window-size=800,1200',
            '--disable-gpu',
            '--disable-web-security',
            '--allow-running-insecure-content',
        ])
        page = browser.new_page(viewport={"width": 800, "height": 1200})
        page.goto(f"file://{temp_file}")
        page.wait_for_selector('.glass', timeout=5000)
        glass_element = page.query_selector('.glass')
        screenshot = glass_element.screenshot(type='png')
        browser.close()
    os.unlink(temp_file)
    return screenshot

bot = telebot.TeleBot(TELEGRAM_TOKEN)

def main_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row('📈 Статистика игроков', '❓ FAQ')
    keyboard.row('🛠 Техническая поддержка', '🎉 Конкурсы')
    keyboard.row('Вакансии', 'Beta Star Lauchner')
    return keyboard
def is_rate_limited(user_id):
    now = time.time()
    last_time = user_last_request_time.get(user_id, 0)
    if now - last_time < RATE_LIMIT_SECONDS:
        return True
    user_last_request_time[user_id] = now
    return False

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    if not message.text:
        bot.send_message(message.chat.id, "❌ Пожалуйста, используйте текстовые команды.", reply_markup=main_keyboard())
        return
    if len(message.text) > MAX_TEXT_LENGTH:
        bot.send_message(message.chat.id, f"❌ Сообщение слишком длинное (максимум {MAX_TEXT_LENGTH} символов).", reply_markup=main_keyboard())
        return
    if is_rate_limited(message.from_user.id):
        bot.send_message(message.chat.id, f"⏳ Пожалуйста, не отправляйте команды так часто. Подождите {RATE_LIMIT_SECONDS} секунд.", reply_markup=main_keyboard())
        return
    bot.send_message(
        message.chat.id,
        "Привет! Выберите действие:",
        reply_markup=main_keyboard()
    )

@bot.message_handler(func=lambda m: m.text == '📈 Статистика игроков')
def handle_stats_button(message):
    if not message.text:
        bot.send_message(message.chat.id, "❌ Пожалуйста, выберите действие с помощью текстовой кнопки.", reply_markup=main_keyboard())
        return
    if len(message.text) > MAX_TEXT_LENGTH:
        bot.send_message(message.chat.id, f"❌ Сообщение слишком длинное (максимум {MAX_TEXT_LENGTH} символов).", reply_markup=main_keyboard())
        return
    if is_rate_limited(message.from_user.id):
        bot.send_message(message.chat.id, f"⏳ Пожалуйста, не отправляйте запросы так часто. Подождите {RATE_LIMIT_SECONDS} секунд.", reply_markup=main_keyboard())
        return
    waiting_for_nickname[message.from_user.id] = True
    msg = bot.send_message(message.chat.id, "Введите никнейм для статистики:", reply_markup=main_keyboard())
    bot.register_next_step_handler(msg, process_stats_nickname)

def process_stats_nickname(message):
    if not waiting_for_nickname.get(message.from_user.id):
        bot.send_message(message.chat.id, "❌ Повторите запрос.", reply_markup=main_keyboard())
        return
    waiting_for_nickname[message.from_user.id] = False
    if not message.text:
        bot.send_message(message.chat.id, "❌ Пожалуйста, отправьте никнейм текстом, а не файлом или другим типом сообщения.", reply_markup=main_keyboard())
        return
    if len(message.text) > MAX_TEXT_LENGTH:
        bot.send_message(message.chat.id, f"❌ Никнейм слишком длинный (максимум {MAX_TEXT_LENGTH} символов).", reply_markup=main_keyboard())
        return
    if is_rate_limited(message.from_user.id):
        bot.send_message(message.chat.id, f"⏳ Пожалуйста, не отправляйте запросы так часто. Подождите {RATE_LIMIT_SECONDS} секунд.", reply_markup=main_keyboard())
        return
    nickname = message.text.strip()
    msg = bot.send_message(message.chat.id, f"⏳ Получаю статистику для {nickname}...", reply_markup=main_keyboard())
    try:
        stats_data = get_cached_stats(nickname)
        if 'Ошибка' in stats_data:
            bot.send_message(message.chat.id, f"❌ Ошибка: {stats_data['Ошибка']}", reply_markup=main_keyboard())
            return
        html_content = render_stats_html(stats_data)
        screenshot_bytes = take_screenshot(html_content)
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
            tmp.write(screenshot_bytes)
            tmp_path = tmp.name
        with open(tmp_path, 'rb') as img_file:
            bot.send_photo(message.chat.id, img_file, reply_markup=main_keyboard())
        os.remove(tmp_path)
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Произошла ошибка: {e}", reply_markup=main_keyboard())

@bot.message_handler(func=lambda m: m.text == '❓ FAQ')
def handle_faq(message):
    if not message.text:
        bot.send_message(message.chat.id, "❌ Пожалуйста, выберите действие с помощью текстовой кнопки.", reply_markup=main_keyboard())
        return
    if len(message.text) > MAX_TEXT_LENGTH:
        bot.send_message(message.chat.id, f"❌ Сообщение слишком длинное (максимум {MAX_TEXT_LENGTH} символов).", reply_markup=main_keyboard())
        return
    if is_rate_limited(message.from_user.id):
        bot.send_message(message.chat.id, f"⏳ Пожалуйста, не отправляйте запросы так часто. Подождите {RATE_LIMIT_SECONDS} секунд.", reply_markup=main_keyboard())
        return
    with open('static/experimental.jpg', 'rb') as photo:
        bot.send_photo(message.chat.id, photo, reply_markup=main_keyboard())
    bot.send_message(
        message.chat.id,
        "*ВАЖНО* Если у вас проблемы при подключении к серверам с территории РФ - то вам нужно\n"
        "1. Нажать на лупу рядом с кнопкой \"The Abyss\"\n"
        "2. Выбрать RU3 Experimental\n"
        "Если вам это не помогло попробуйте зайти с VPN\n\n"
        "*ВАЖНО* Если вы нашли какие-то баги, заметили то, что работает плохо или совсем сломалось, приглашаем вас поделиться мнениями и идеями в Багтрекере: https://iccup.com/bugtracker?\n\n"
        "Q: Как создать аккаунт на iCCup?\n"
        "Ответ: <a href='https://t.me/iCCupTech/5'>Читайте тут</a>\n\n"
        "Q: Как начать играть?\n"
        "Ответ: <a href='https://t.me/iCCupTech/6'>Читайте тут</a>\n\n"
        "Q: Команды юзеров на сервере DotA:\n"
        "Ответ: <a href='https://t.me/iCCupTech/15'>Читайте тут</a>\n\n"
        "Q: Как работает рейтинг?\n"
        "Ответ: <a href='https://t.me/iCCupTech/16'>Читайте тут</a>\n\n"
        "Q: Какие есть правила iCCup'a?\n"
        "Ответ: <a href='https://t.me/iCCupTech/17'>Читайте тут</a>\n\n"
        "Q: Какие есть полезные ссылки?\n"
        "Ответ: <a href='https://t.me/iCCupTech/18'>Читайте тут</a>",
        parse_mode='HTML',
        reply_markup=main_keyboard()
    )

@bot.message_handler(func=lambda m: m.text == '🛠 Техническая поддержка')
def handle_support(message):
    if not message.text:
        bot.send_message(message.chat.id, "❌ Пожалуйста, выберите действие с помощью текстовой кнопки.", reply_markup=main_keyboard())
        return
    if len(message.text) > MAX_TEXT_LENGTH:
        bot.send_message(message.chat.id, f"❌ Сообщение слишком длинное (максимум {MAX_TEXT_LENGTH} символов).", reply_markup=main_keyboard())
        return
    if is_rate_limited(message.from_user.id):
        bot.send_message(message.chat.id, f"⏳ Пожалуйста, не отправляйте запросы так часто. Подождите {RATE_LIMIT_SECONDS} секунд.", reply_markup=main_keyboard())
        return
    bot.send_message(
        message.chat.id,
        "Для получения более подробной и индивидуальной помощи обращайтесь в <a href='https://iccup.com/support_user/cat_ask/35.html'>раздел на сайте</a> .\n\n"
        "Q. <a href='https://t.me/iCCupTech/2'>Существуют ли версии лаунчера для Mac OS и unix?</a> .\n"
        "Q. <a href='https://t.me/iCCupTech/3'> Could not connect to Battle.Net/Не удалось установить соединение</a>.\n"
        "Q. <a href='https://t.me/iCCupTech/19'>Unable to Validate Game Version / Ошибка при проверке версии игры</a>.\n"
        "Q. <a href='https://t.me/iCCupTech/20'>Приложение не было запущено, поскольку оно некорректно настроено</a>.\n"
        "Q. <a href='https://t.me/iCCupTech/21'>Не найден файл iccwc3.icc</a>.\n"
        "Q. <a href='https://t.me/iCCupTech/22'>You Broke It / Что-то пошло не так</a>.\n"
        "Q. <a href='https://t.me/iCCupTech/23'>That account does not exist / Учётной записи с таким именем не существует</a>.\n"
        "Q. <a href='https://t.me/iCCupTech/24'>Нет меню в Варкрафте</a>.\n"
        "Q. <a href='https://t.me/iCCupTech/25'>Ошибка «Could not open game.dll»</a>.\n"
        "Q. <a href='https://t.me/iCCupTech/26'>Ошибка при попытке сохранения данных, загруженных с Battle.Net</a>.\n"
        "Q. <a href='https://t.me/iCCupTech/27'>Не удалось инициализировать DirectX</a>.\n"
        "Q. <a href='https://t.me/iCCupTech/28'>Неверный компакт диск</a>.\n"
        "Q. <a href='https://t.me/iCCupTech/29'>Розово-чёрные квадраты / нет анимации некоторых умений</a>.\n"
        "Q. <a href='https://t.me/iCCupTech/30'>Crash it. FATAL ERROR</a>.\n"
        "Q. <a href='https://t.me/iCCupTech/31'>Капюшоны в батлнете</a>.\n"
        "Q. <a href='https://t.me/iCCupTech/32'>Ошибка ввода пароля три раза подряд</a>.\n"
        "Q. <a href='https://t.me/iCCupTech/33'>Ошибки с ACCESS VIOLATION</a>.\n"
        "Q. <a href='https://t.me/iCCupTech/34'>Не работают хоткеи</a>.\n",
        parse_mode='HTML',
        reply_markup=main_keyboard()
    )

@bot.message_handler(func=lambda m: m.text == '🎉 Конкурсы')
def handle_contests(message):
    if not message.text:
        bot.send_message(message.chat.id, "❌ Пожалуйста, выберите действие с помощью текстовой кнопки.", reply_markup=main_keyboard())
        return
    if len(message.text) > MAX_TEXT_LENGTH:
        bot.send_message(message.chat.id, f"❌ Сообщение слишком длинное (максимум {MAX_TEXT_LENGTH} символов).", reply_markup=main_keyboard())
        return
    if is_rate_limited(message.from_user.id):
        bot.send_message(message.chat.id, f"⏳ Пожалуйста, не отправляйте запросы так часто. Подождите {RATE_LIMIT_SECONDS} секунд.", reply_markup=main_keyboard())
        return
    bot.send_message(
        message.chat.id,
        "🎮 DISCORD:\n"
        "\n"
        "🏆 Closed Games Вторник; Четверг; Суббота в 19:00 по МСК ⏰\n"
        "🔥 Приз за каждую выигранную игру 10 капсов💰 \n"
        "✅Подробности читайте в <a href='https://discord.com/channels/614513381600264202/890255824646176788'>канале дискорд</a>\n"
        "\n"
        "✈Telegram:\n"
        "<a href='https://t.me/iCCup/6989'>Актуальные конкурсы</a>\n"
        "\n"
        "🎯 FORUM конкурсы:\n"
        "Все актуальные конкурсы можете найти по <a href='https://iccup.com/community/thread/1571455.html'>ссылке</a>\n"
        "\n"
        "CUSTOM конкурсы\n"
        "Понедельник , Вторник , Пятница Custom Closed Games\n"
        "Среда Custom Closed Wave!\n"
        "Суббота Custom Closed IMBA\n"
        "Воскресенье Custom Closed LOD\n"
        "Время проведения: 19:00 по МСК\n",
        parse_mode='HTML',
        reply_markup=main_keyboard()
    )

@bot.message_handler(func=lambda m: m.text == 'Вакансии')
def handle_jobs(message):
    if not message.text:
        bot.send_message(message.chat.id, "❌ Пожалуйста, выберите действие с помощью текстовой кнопки.", reply_markup=main_keyboard())
        return
    if len(message.text) > MAX_TEXT_LENGTH:
        bot.send_message(message.chat.id, f"❌ Сообщение слишком длинное (максимум {MAX_TEXT_LENGTH} символов).", reply_markup=main_keyboard())
        return
    if is_rate_limited(message.from_user.id):
        bot.send_message(message.chat.id, f"⏳ Пожалуйста, не отправляйте запросы так часто. Подождите {RATE_LIMIT_SECONDS} секунд.", reply_markup=main_keyboard())
        return
    bot.send_message(
        message.chat.id,
        "Social Media Marketing — разработка и развитие группы «Вконтакте» и на канале «Telegram», "
        "привлечение и удержание новых пользователей, общение с нашей аудиторией, создание уникального "
        "контента и проведение топовых эвентов с нашими юзерами.\n\n"
        "Зарплата 350 капсов в месяц\n\n"
        "Заинтересованы? <a href='https://t.me/Otsutstvie_kreativa'>Обращайтесь</a>\n"
        "\n"
        "Forum Team — Создание качественного, креативного контента, модерация форума, "
        "поддержание чистоты и порядка, постоянное взаимодействие с игровым сообществом. Работа "
        "с аудиторией, направленная на улучшение качества общения.\n"
        "Заинтересованы? <a href='https://t.me/korolevaname'>Обращайтесь</a>\n"
        "\n"
        "Design Team — создание баннеров для новостей, а также других элементов оформления сайта.\n"
        "— Работа с Photoshop и его аналогами на среднем уровне и выше.\n"
        "Заинтересованы? <a href='https://t.me/ula4svv'>Обращайтесь</a>\n"
        "\n"
        "News — создание новостного мира платформы: красивый слог; абсолютное знание русского языка. "
        "Идут поиски ярких и неординарных индивидов, которые будут способны неустанно работать и хорошо зарабатывать.\n"
        "Заинтересованы? <a href='https://t.me/ula4svv'>Обращайтесь</a>\n"
        "\n"
        "Custom Maps Vacancy\n"
        "iCCup Custom League Team — Организация, создание и проведение турниров\n"
        "Custom Tournaments Team — Проведение турниров Custom секции\n"
        "Custom Arena Team — Начисление очков pts участникам арены\n"
        "Closed Games Team — Знание карт из списка /chost. Вашей задачей будет проведение закрытых игр для пользователей\n"
        "Custom Forum Team — Порядок нужен везде, в особенности, на форуме\n"
        "Заинтересованы? <a href='https://iccup.com/job_custom_forum'>Мы ждем вас!</a>\n",
        parse_mode='HTML',
        reply_markup=main_keyboard()
    )

@bot.message_handler(func=lambda m: m.text == 'Beta Star Lauchner')
def handle_beta(message):
    with open('static/launcher.png', 'rb') as photo:
        bot.send_photo(message.chat.id, photo, reply_markup=main_keyboard())

    description = (
        "Это публичная БЕТА версия нового iCCup Star Launcher-a, с помощью которого вы можете войти в свой аккаунт, "
        "подключиться к серверу, общаться с друзьями в продвинутой версии чата, искать игры с множеством фильтров. "
        "И конечно же, вы можете создать или зайти в игровое лобби только с помощью нового лаунчера.\n\n"
        "Warcraft 3 запускается только в момент загрузки самой игры. Благодаря этому мы наконец-то можем обновить и "
        "улучшить интерфейс Warcraft-а, добавить новые элементы, исправить старые баги и обойти ограничения, с которыми "
        "приходилось бороться до сих пор.\n\n"
        "❗ <b>ВАЖНО:</b> Это публичная, но БЕТА версия лаунчера. В нём есть баги, каких-то элементов может не хватать, "
        "какие-то функции могут работать неправильно. Это нормальная часть процесса тестирования. "
        "Новая версия лаунчера постоянно обновляется, изменяется и дорабатывается.\n\n"
        "Если вы нашли баги или хотите поделиться идеями — пишите в Багтрекер (раздел Лаунчер):\n"
        "https://iccup.com/bugtracker?type=launcher\n\n"
        "                      -------------------------\n"
        "                      <b><a href='https://iccup.com/files/download/3600ecf6b55f9e10d5f707c1134f0f1a/iCCup_BETA_Star_Launcher.html'>-  Установить  -</a></b>\n"
        "                      -------------------------\n\n"
        "⚠️ <b>ВНИМАНИЕ: Не нажимайте с мобильных устройств!</b>"
    )

    bot.send_message(message.chat.id, description, parse_mode='HTML', reply_markup=main_keyboard())


@bot.message_handler(commands=['stats'])
def stats_command(message):
    if is_rate_limited(message.from_user.id):
        bot.send_message(message.chat.id, f"⏳ Пожалуйста, не отправляйте запросы так часто. Подождите {RATE_LIMIT_SECONDS} секунд.", reply_markup=main_keyboard())
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip():
        bot.send_message(message.chat.id, "❌ Пожалуйста, укажите никнейм после команды, например: /stats nickname", reply_markup=main_keyboard())
        return
    nickname = args[1].strip()
    if len(nickname) > MAX_TEXT_LENGTH:
        bot.send_message(message.chat.id, f"❌ Никнейм слишком длинный (максимум {MAX_TEXT_LENGTH} символов).", reply_markup=main_keyboard())
        return
    msg = bot.send_message(message.chat.id, f"⏳ Получаю статистику для {nickname}...", reply_markup=main_keyboard())
    try:
        stats_data = get_cached_stats(nickname)
        if 'Ошибка' in stats_data:
            bot.send_message(message.chat.id, f"❌ Ошибка: {stats_data['Ошибка']}", reply_markup=main_keyboard())
            return
        html_content = render_stats_html(stats_data)
        screenshot_bytes = take_screenshot(html_content)
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
            tmp.write(screenshot_bytes)
            tmp_path = tmp.name
        with open(tmp_path, 'rb') as img_file:
            bot.send_photo(message.chat.id, img_file, reply_markup=main_keyboard())
        os.remove(tmp_path)
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Произошла ошибка: {e}", reply_markup=main_keyboard())

@bot.message_handler(func=lambda m: m.text in ['❓ FAQ', '🛠 Техническая поддержка', '🎉 Конкурсы', 'Вакансии', '🚀 BETA STAR LAUNCHER', 'Beta Star Lauchner'])
def reset_context_on_other_buttons(message):
    waiting_for_nickname[message.from_user.id] = False
    if message.text == '❓ FAQ':
        handle_faq(message)
    elif message.text == '🛠 Техническая поддержка':
        handle_support(message)
    elif message.text == '🎉 Конкурсы':
        handle_contests(message)
    elif message.text == 'Вакансии':
        handle_jobs(message)
    elif message.text == '🚀 BETA STAR LAUNCHER' or message.text == 'Beta Star Lauchner':
        handle_beta(message)

@bot.message_handler(content_types=['new_chat_members'])
def greet_new_members(message):
    for new_member in message.new_chat_members:
        bot.send_message(
            message.chat.id,
            f"Привет, {new_member.first_name}! Добро пожаловать! Выберите действие:",
            reply_markup=main_keyboard()
        )

def auto_post_top_streak():
    try:
        moscow_tz = pytz.timezone('Europe/Moscow')
        current_time = datetime.datetime.now(moscow_tz)
        
        # Получаем ник первого игрока
        nickname = fetch_top_streak_player()
        if not nickname:
            return
        # Получаем статистику игрока
        stats_data = get_cached_stats(nickname)
        if 'Ошибка' in stats_data:
            return
        html_content = render_stats_html(stats_data)
        screenshot_bytes = take_screenshot(html_content)
        # ID канала/чата для публикации из конфига
        nickname_safe = html.escape(nickname)
        caption = (
        f"🔥🔥ИГРОК ДНЯ🔥🔥\n\n"
        f"Каждый день кто-то поднимается выше остальных. Сегодня - это "
        f"<a href=\"https://iccup.com/dota/gamingprofile/{nickname_safe}\">{nickname_safe}</a>.\n"
        "Его путь был безошибочен: матч за матчем, победа за победой.\n"
        "Без лишних слов - сегодня именно он держит самую длинную серию побед на платформе.\n"
        "Это не случайность и не везение - это стабильность, опыт и холодный разум.\n"
        "Поздравляем и грацуем!\n"
        "Пост создано автоматический\n"
        "#Игрокдня  #iCCup"
        )
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
            tmp.write(screenshot_bytes)
            tmp_path = tmp.name
        with open(tmp_path, 'rb') as img_file:
            bot.send_photo(AUTOPOST_CHANNEL_ID, img_file, caption=caption, parse_mode="HTML")
        os.remove(tmp_path)
    except Exception as e:
        pass

def make_safe_filename(name):
    # Оставляем только буквы, цифры, пробел, дефис, подчёркивание, точку, запятую, круглые и квадратные скобки
    import re
    return re.sub(r'[^a-zA-Zа-яА-ЯёЁ0-9 \-_.(),\[\]]+', '', name)

def auto_post_music():
    logging.info("auto_post_music: запуск задачи")
    try:
        ytmusic = YTMusic()
        logging.info("auto_post_music: YTMusic инициализирован")
        search_query = random.choice(config.MUSIC_KEYWORDS)
        logging.info(f"auto_post_music: поисковый запрос: {search_query}")
        results = ytmusic.search(search_query, filter='songs')
        logging.info(f"auto_post_music: найдено {len(results)} треков")
        if not results:
            logging.info("auto_post_music: нет результатов")
            return
        top_tracks = results[:10] if len(results) >= 10 else results
        max_duration_sec = 300 
        filtered_tracks = []
        for t in top_tracks:
            duration_str = t.get('duration')
            if duration_str:
                parts = duration_str.split(':')
                try:
                    if len(parts) == 3:
                        seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                    elif len(parts) == 2:
                        seconds = int(parts[0]) * 60 + int(parts[1])
                    else:
                        seconds = int(parts[0])
                except Exception:
                    continue
                if seconds <= max_duration_sec:
                    filtered_tracks.append(t)
        if not filtered_tracks:
            logging.info("auto_post_music: нет подходящих треков по длительности")
            return
        tracks_to_send = random.sample(filtered_tracks, min(3, len(filtered_tracks)))
        media = []
        captions = []
        audio_files = []
        USE_TEMP_DIR = True  # True — использовать временную папку для каждого трека, False — только задержка
        for idx, track in enumerate(tracks_to_send, 1):
            logging.info(f"auto_post_music: выбран трек {track.get('title')}")
            video_id = track.get('videoId')
            if not video_id:
                logging.info("auto_post_music: нет videoId")
                continue
            url = f"https://music.youtube.com/watch?v={video_id}"

            artist = track['artists'][0]['name'] if track.get('artists') and track['artists'] else ''
            pretty_title = make_safe_filename(f"{track['title']} — {artist}".strip())
            output_template = f"{pretty_title}.%(ext)s"

            if USE_TEMP_DIR:
                import tempfile
                with tempfile.TemporaryDirectory() as tempdir:
                    logging.info(f"[LOG] Ожидаемое имя: {pretty_title}.%(ext)s (tempdir: {tempdir})")
                    before_files = set(os.listdir(tempdir))
                    try:
                        subprocess.run([
                            'yt-dlp',
                            '--no-part',
                            '--no-overwrites',
                            '-f', 'bestaudio[ext=m4a][acodec!=none][vcodec=none][container!=dash]/bestaudio/best',
                            '-o', os.path.join(tempdir, output_template),
                            url
                        ], check=True)
                    except Exception as e:
                        logging.error(f"[LOG] yt-dlp не смог скачать обычный m4a: {e}")
                        continue
                    after_files = set(os.listdir(tempdir))
                    new_files = list(after_files - before_files)
                    logging.info(f"[LOG] Новые файлы после скачивания: {new_files}")
                    found_file = None
                    for ext in ('.m4a', '.mp3', '.webm', '.opus'):
                        candidate = os.path.join(tempdir, f"{pretty_title}{ext}")
                        if os.path.exists(candidate):
                            found_file = candidate
                            logging.info(f"[LOG] Найден файл по шаблону: {found_file}")
                            break
                    if not found_file:
                        for f in new_files:
                            if f.lower().endswith(('.m4a', '.mp3', '.webm', '.opus')):
                                found_file = os.path.join(tempdir, f)
                                logging.info(f"[LOG] yt-dlp сохранил файл с другим именем: {found_file}")
                                break
                    if not found_file:
                        logging.info(f"[LOG] аудиофайл не найден после скачивания")
                        continue
                    # Переименовываем файл, если имя не совпадает с желаемым
                    desired_file = f"{pretty_title}{os.path.splitext(found_file)[1]}"
                    final_path = os.path.abspath(desired_file)
                    if os.path.abspath(found_file) != final_path:
                        try:
                            shutil.move(found_file, final_path)
                            logging.info(f"[LOG] Файл перемещён: {found_file} -> {final_path}")
                            found_file = final_path
                        except Exception as e:
                            logging.error(f"[LOG] не удалось переместить файл: {e}")
                            continue
                    else:
                        logging.info(f"[LOG] Файл уже с нужным именем: {found_file}")
            else:
                logging.info(f"[LOG] Ожидаемое имя: {pretty_title}.%(ext)s (cwd)")
                before_files = set(os.listdir('.'))
                try:
                    subprocess.run([
                        'yt-dlp',
                        '--no-part',
                        '--no-overwrites',
                        '-f', 'bestaudio[ext=m4a][acodec!=none][vcodec=none][container!=dash]/bestaudio/best',
                        '-o', output_template,
                        url
                    ], check=True)
                except Exception as e:
                    logging.error(f"[LOG] yt-dlp не смог скачать обычный m4a: {e}")
                    continue
                after_files = set(os.listdir('.'))
                new_files = list(after_files - before_files)
                logging.info(f"[LOG] Новые файлы после скачивания: {new_files}")
                found_file = None
                for ext in ('.m4a', '.mp3', '.webm', '.opus'):
                    candidate = f"{pretty_title}{ext}"
                    if os.path.exists(candidate):
                        found_file = candidate
                        logging.info(f"[LOG] Найден файл по шаблону: {found_file}")
                        break
                if not found_file:
                    for f in new_files:
                        if f.lower().endswith(('.m4a', '.mp3', '.webm', '.opus')):
                            found_file = f
                            logging.info(f"[LOG] yt-dlp сохранил файл с другим именем: {found_file}")
                            break
                if not found_file:
                    logging.info(f"[LOG] аудиофайл не найден после скачивания")
                    continue
                # Переименовываем файл, если имя не совпадает с желаемым
                desired_file = f"{pretty_title}{os.path.splitext(found_file)[1]}"
                if os.path.abspath(found_file) != os.path.abspath(desired_file):
                    try:
                        os.rename(found_file, desired_file)
                        logging.info(f"[LOG] Файл переименован: {found_file} -> {desired_file}")
                        found_file = desired_file
                    except Exception as e:
                        logging.error(f"[LOG] не удалось переименовать файл: {e}")
                        continue
                else:
                    logging.info(f"[LOG] Файл уже с нужным именем: {found_file}")
                import time
                time.sleep(1)  # Задержка между скачиваниями
            logging.info(f"[LOG] Итоговый файл для отправки: {found_file}")
            audio_files.append(found_file)
            captions.append(f"{track['title']} — {artist}" if artist else track['title'])

        if not audio_files:
            logging.info("auto_post_music: не удалось подготовить ни одного аудиофайла")
            return

        # Формируем общий caption для первого трека
        full_caption = "Музыка дня!\nСлушаем и тащим катки!\n\nПост создан автоматически\n\n"
        for idx, cap in enumerate(captions, 1):
            full_caption += f"#{idx}: {cap}\n\n"
        full_caption += "#Music || #iccup"

        from telebot.types import InputMediaAudio
        media = []
        opened_files = []
        for i, audio_path in enumerate(audio_files):
            audio_file = open(audio_path, 'rb')
            opened_files.append(audio_file)
            if i == 0:
                media.append(InputMediaAudio(audio_file, caption=full_caption))
            else:
                media.append(InputMediaAudio(audio_file))

        try:
            bot.send_media_group(AUTOPOST_CHANNEL_ID, media)
            logging.info("auto_post_music: media_group отправлен успешно")
        except Exception as e:
            logging.error(f'Ошибка отправки media_group: {e}')
        finally:
            for fobj in opened_files:
                try:
                    fobj.close()
                except Exception:
                    pass
            for f in audio_files:
                if os.path.exists(f):
                    try:
                        os.remove(f)
                    except Exception as e:
                        logging.error(f"[LOG] Не удалось удалить файл {f}: {e}")
    except Exception as e:
        logging.error(f'Ошибка автопостинга музыки: {e}')


scheduler = BackgroundScheduler(timezone=pytz.timezone('Europe/Moscow'))
scheduler.add_job(auto_post_top_streak, 'cron', hour=AUTOPOST_HOUR, minute=AUTOPOST_MINUTE)
scheduler.add_job(auto_post_music, 'cron', day_of_week=MUSIC_POST_DAYS, hour=MUSIC_POST_HOUR, minute=MUSIC_POST_MINUTE)
scheduler.start()
logging.info(f"Планировщик запущен. Музыка будет публиковаться по {MUSIC_POST_DAYS} в {MUSIC_POST_HOUR:02d}:{MUSIC_POST_MINUTE:02d} МСК")
logging.info(f"Игрок дня будет публиковаться в {AUTOPOST_HOUR:02d}:{AUTOPOST_MINUTE:02d} МСК")

async def screenshot_iccup_elements(output_path="iccup_screenshot.png"):
    import asyncio
    from playwright.async_api import async_playwright
    selectors = [
        "#level0 > main > div:nth-child(7) > div.data-primary.data-sm.pr-10.col-6",
        "#level0 > main > div:nth-child(7) > div:nth-child(8)"
    ]
    player_info_selector = "#level0 > main > div:nth-child(7) > div.data-primary.data-sm.pr-10.col-6 > div:nth-child(2)"
    team_info_selector = "#level0 > main > div:nth-child(7) > div:nth-child(8) > div:nth-child(2)"
    temp_files = []
    player_info = None
    team_info = None
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto("https://iccup.com/")
        images = []
        for idx, selector in enumerate(selectors):
            try:
                await page.wait_for_selector(selector, timeout=10000)
                element = await page.query_selector(selector)
                temp_file = tempfile.NamedTemporaryFile(suffix=f"_{idx}.png", delete=False)
                await element.screenshot(path=temp_file.name)
                temp_files.append(temp_file.name)
                images.append(Image.open(temp_file.name))
            except Exception as e:
                logging.error(f"[AUTOPOST][ERROR] Не удалось сделать скриншот селектора {selector}: {e}")
        # Парсим текст лучшего игрока
        try:
            await page.wait_for_selector(player_info_selector, timeout=10000)
            player_info_elem = await page.query_selector(player_info_selector)
            player_info = await player_info_elem.inner_text()
        except Exception as e:
            logging.error(f"[AUTOPOST][ERROR] Не удалось получить инфо игрока: {e}")
        # Парсим текст лучшей команды
        try:
            await page.wait_for_selector(team_info_selector, timeout=10000)
            team_info_elem = await page.query_selector(team_info_selector)
            team_info = await team_info_elem.inner_text()
        except Exception as e:
            logging.error(f"[AUTOPOST][ERROR] Не удалось получить инфо команды: {e}")
        await browser.close()
    if not images:
        raise Exception("Не удалось получить ни одного скриншота с iccup.com")
    # Склеиваем изображения горизонтально
    total_width = sum(img.width for img in images)
    max_height = max(img.height for img in images)
    combined = Image.new('RGB', (total_width, max_height), (255, 255, 255))
    x_offset = 0
    for img in images:
        combined.paste(img, (x_offset, 0))
        x_offset += img.width
    combined.save(output_path)
    # Удаляем временные файлы
    for f in temp_files:
        try:
            os.remove(f)
        except Exception:
            pass
    return output_path, player_info, team_info

import re

def parse_player_team_info(text):
    # Пример строки: "NickName Победы: 10 | Поражения: 2"
    if not text:
        return None, None, None
    # Ищем никнейм/название (до Победы:)
    m = re.match(r"(.+?) Победы: (\d+) \| Поражения: (\d+)", text)
    if m:
        name = m.group(1).strip()
        wins = m.group(2)
        losses = m.group(3)
        return name, wins, losses
    return text.strip(), None, None

async def autopost_iccup_screenshot(bot):
    import pytz
    posted_times = set()
    while True:
        now = datetime.datetime.now(pytz.timezone('Europe/Moscow'))
        now_day = now.strftime("%a").lower()  # 'mon', 'tue', ...
        now_hour = now.hour
        now_minute = now.minute
        for sched in config.AUTOPOST_SCHEDULE:
            sched_day = sched["day"].lower()
            sched_hour = sched["hour"]
            sched_minute = sched["minute"]
            key = f"{sched_day}_{sched_hour:02d}_{sched_minute:02d}_{now.date()}"
            if now_day == sched_day and now_hour == sched_hour and now_minute == sched_minute and key not in posted_times:
                try:
                    screenshot_path, player_info, team_info = await screenshot_iccup_elements()
                    player_name, player_wins, player_losses = parse_player_team_info(player_info)
                    team_name, team_wins, team_losses = parse_player_team_info(team_info)
                    # Формируем caption по шаблону пользователя с учётом условий (без ссылки)
                    caption = (
                        "🔥 Итоги недели на ICCup! 🔥\n"
                        "Каждую неделю мы подводим итоги и чествуем лучших - тех, кто показал максимум скилла и не побоялся бросить вызов топам!\n\n"
                        "🎯 Лучший игрок недели:\n"
                        f"🏆 {player_name}"
                    )
                    if player_wins and player_losses:
                        caption += f" - {player_wins} : {player_losses}"
                    caption += "\nУверенная игра, стабильные победы и заслуженное первое место! Красавчик!\n\n"
                    caption += "🛡 Лучшая команда недели:\n"
                    if team_name:
                        caption += f"🥇 {team_name}"
                        if team_wins and team_losses:
                            caption += f" - {team_wins} : {team_losses}"
                    caption += "\nКомандная мощь в действии! Эти ребята сыграны, как единый организм. Респект!\n\n"
                    caption += "(Пост создано автоматичесикй)\n\n#итогинедели #iCCup"
                    with open(screenshot_path, "rb") as photo:
                        bot.send_photo(config.AUTOPOST_CHANNEL_ID, photo, caption=caption, parse_mode="HTML")
                    try:
                        os.remove(screenshot_path)
                    except Exception:
                        pass
                except Exception:
                    pass
                posted_times.add(key)
        await asyncio.sleep(20)

if __name__ == "__main__":
    import asyncio
    import threading
    
    def start_polling():
        bot.polling(none_stop=True)
    threading.Thread(target=start_polling, daemon=True).start()
    asyncio.run(autopost_iccup_screenshot(bot)) 
