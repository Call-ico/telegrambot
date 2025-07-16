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

print("Текущее время МСК:", datetime.now(pytz.timezone('Europe/Moscow')))

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
    keyboard.row('Музыкальные подборки')
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
        bot.send_message(message.chat.id, "❌ Пожалуйста, используйте текстовые команды.")
        return
    if len(message.text) > MAX_TEXT_LENGTH:
        bot.send_message(message.chat.id, f"❌ Сообщение слишком длинное (максимум {MAX_TEXT_LENGTH} символов).")
        return
    if is_rate_limited(message.from_user.id):
        bot.send_message(message.chat.id, f"⏳ Пожалуйста, не отправляйте команды так часто. Подождите {RATE_LIMIT_SECONDS} секунд.")
        return
    bot.send_message(
        message.chat.id,
        "Привет! Выберите действие:",
        reply_markup=main_keyboard()
    )

@bot.message_handler(func=lambda m: m.text == '📈 Статистика игроков')
def handle_stats_button(message):
    if not message.text:
        bot.send_message(message.chat.id, "❌ Пожалуйста, выберите действие с помощью текстовой кнопки.")
        return
    if len(message.text) > MAX_TEXT_LENGTH:
        bot.send_message(message.chat.id, f"❌ Сообщение слишком длинное (максимум {MAX_TEXT_LENGTH} символов).")
        return
    if is_rate_limited(message.from_user.id):
        bot.send_message(message.chat.id, f"⏳ Пожалуйста, не отправляйте запросы так часто. Подождите {RATE_LIMIT_SECONDS} секунд.")
        return
    waiting_for_nickname[message.from_user.id] = True
    msg = bot.send_message(message.chat.id, "Введите никнейм для статистики:")
    bot.register_next_step_handler(msg, process_stats_nickname)

def process_stats_nickname(message):
    if not waiting_for_nickname.get(message.from_user.id):
        bot.send_message(message.chat.id, "❌ Повторите запрос.")
        return
    waiting_for_nickname[message.from_user.id] = False
    if not message.text:
        bot.send_message(message.chat.id, "❌ Пожалуйста, отправьте никнейм текстом, а не файлом или другим типом сообщения.")
        return
    if len(message.text) > MAX_TEXT_LENGTH:
        bot.send_message(message.chat.id, f"❌ Никнейм слишком длинный (максимум {MAX_TEXT_LENGTH} символов).")
        return
    if is_rate_limited(message.from_user.id):
        bot.send_message(message.chat.id, f"⏳ Пожалуйста, не отправляйте запросы так часто. Подождите {RATE_LIMIT_SECONDS} секунд.")
        return
    nickname = message.text.strip()
    msg = bot.send_message(message.chat.id, f"⏳ Получаю статистику для {nickname}...")
    try:
        stats_data = get_cached_stats(nickname)
        if 'Ошибка' in stats_data:
            bot.send_message(message.chat.id, f"❌ Ошибка: {stats_data['Ошибка']}")
            return
        html_content = render_stats_html(stats_data)
        screenshot_bytes = take_screenshot(html_content)
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
            tmp.write(screenshot_bytes)
            tmp_path = tmp.name
        with open(tmp_path, 'rb') as img_file:
            bot.send_photo(message.chat.id, img_file)
        os.remove(tmp_path)
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Произошла ошибка: {e}")

@bot.message_handler(func=lambda m: m.text == '❓ FAQ')
def handle_faq(message):
    if not message.text:
        bot.send_message(message.chat.id, "❌ Пожалуйста, выберите действие с помощью текстовой кнопки.")
        return
    if len(message.text) > MAX_TEXT_LENGTH:
        bot.send_message(message.chat.id, f"❌ Сообщение слишком длинное (максимум {MAX_TEXT_LENGTH} символов).")
        return
    if is_rate_limited(message.from_user.id):
        bot.send_message(message.chat.id, f"⏳ Пожалуйста, не отправляйте запросы так часто. Подождите {RATE_LIMIT_SECONDS} секунд.")
        return
    with open('static/experimental.jpg', 'rb') as photo:
        bot.send_photo(message.chat.id, photo)
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
        parse_mode='HTML'
    )

@bot.message_handler(func=lambda m: m.text == '🛠 Техническая поддержка')
def handle_support(message):
    if not message.text:
        bot.send_message(message.chat.id, "❌ Пожалуйста, выберите действие с помощью текстовой кнопки.")
        return
    if len(message.text) > MAX_TEXT_LENGTH:
        bot.send_message(message.chat.id, f"❌ Сообщение слишком длинное (максимум {MAX_TEXT_LENGTH} символов).")
        return
    if is_rate_limited(message.from_user.id):
        bot.send_message(message.chat.id, f"⏳ Пожалуйста, не отправляйте запросы так часто. Подождите {RATE_LIMIT_SECONDS} секунд.")
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
        parse_mode='HTML'
    )

@bot.message_handler(func=lambda m: m.text == '🎉 Конкурсы')
def handle_contests(message):
    if not message.text:
        bot.send_message(message.chat.id, "❌ Пожалуйста, выберите действие с помощью текстовой кнопки.")
        return
    if len(message.text) > MAX_TEXT_LENGTH:
        bot.send_message(message.chat.id, f"❌ Сообщение слишком длинное (максимум {MAX_TEXT_LENGTH} символов).")
        return
    if is_rate_limited(message.from_user.id):
        bot.send_message(message.chat.id, f"⏳ Пожалуйста, не отправляйте запросы так часто. Подождите {RATE_LIMIT_SECONDS} секунд.")
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
        parse_mode='HTML'
    )

@bot.message_handler(func=lambda m: m.text == 'Вакансии')
def handle_jobs(message):
    if not message.text:
        bot.send_message(message.chat.id, "❌ Пожалуйста, выберите действие с помощью текстовой кнопки.")
        return
    if len(message.text) > MAX_TEXT_LENGTH:
        bot.send_message(message.chat.id, f"❌ Сообщение слишком длинное (максимум {MAX_TEXT_LENGTH} символов).")
        return
    if is_rate_limited(message.from_user.id):
        bot.send_message(message.chat.id, f"⏳ Пожалуйста, не отправляйте запросы так часто. Подождите {RATE_LIMIT_SECONDS} секунд.")
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
        parse_mode='HTML'
    )

@bot.message_handler(func=lambda m: m.text == 'Beta Star Lauchner')
def handle_beta(message):
    with open('static/launcher.png', 'rb') as photo:
        bot.send_photo(message.chat.id, photo)

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

    bot.send_message(message.chat.id, description, parse_mode='HTML')


@bot.message_handler(commands=['stats'])
def stats_command(message):
    if is_rate_limited(message.from_user.id):
        bot.send_message(message.chat.id, f"⏳ Пожалуйста, не отправляйте запросы так часто. Подождите {RATE_LIMIT_SECONDS} секунд.")
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip():
        bot.send_message(message.chat.id, "❌ Пожалуйста, укажите никнейм после команды, например: /stats nickname")
        return
    nickname = args[1].strip()
    if len(nickname) > MAX_TEXT_LENGTH:
        bot.send_message(message.chat.id, f"❌ Никнейм слишком длинный (максимум {MAX_TEXT_LENGTH} символов).")
        return
    msg = bot.send_message(message.chat.id, f"⏳ Получаю статистику для {nickname}...")
    try:
        stats_data = get_cached_stats(nickname)
        if 'Ошибка' in stats_data:
            bot.send_message(message.chat.id, f"❌ Ошибка: {stats_data['Ошибка']}")
            return
        html_content = render_stats_html(stats_data)
        screenshot_bytes = take_screenshot(html_content)
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
            tmp.write(screenshot_bytes)
            tmp_path = tmp.name
        with open(tmp_path, 'rb') as img_file:
            bot.send_photo(message.chat.id, img_file)
        os.remove(tmp_path)
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Произошла ошибка: {e}")

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
        current_time = datetime.now(moscow_tz)
        
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

def auto_post_music():
    print("auto_post_music: запуск задачи")
    try:
        ytmusic = YTMusic()
        print("auto_post_music: YTMusic инициализирован")
        keywords = [
            "Dota music", "Epic gaming music", "Dark fantasy soundtrack",
            "Anime battle music", "Underground rap gaming",
            "Dota 2 playlist", "Instrumental action music",
            "Slavic gaming music", "Warcraft music",
            "Tryhard playlist",
            "phonk gaming",
            "trap instrumental",
            "drill type beat",
            "chill rap",
            "dubstep gaming",
            "synthwave gamer",
            "nu metal",
            "lofi gaming",
            "hardstyle gaming",
            "dota soundtrack"
        ]
        search_query = random.choice(keywords)
        print(f"auto_post_music: поисковый запрос: {search_query}")
        results = ytmusic.search(search_query, filter='songs')
        print(f"auto_post_music: найдено {len(results)} треков")
        if not results:
            print("auto_post_music: нет результатов")
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
            print("auto_post_music: нет подходящих треков по длительности")
            return
        track = random.choice(filtered_tracks)
        print(f"auto_post_music: выбран трек {track.get('title')}")
        video_id = track.get('videoId')
        if not video_id:
            print("auto_post_music: нет videoId")
            return
        url = f"https://music.youtube.com/watch?v={video_id}"

        safe_title = re.sub(r'[\\/*?:"<>|#]', "", track['title'])
        output_template = f"{safe_title}.%(ext)s"
        print(f"auto_post_music: скачивание {url} в шаблон {output_template}")
        subprocess.run([
            'yt-dlp',
            '-f', 'bestaudio[ext=m4a]/bestaudio',
            '-o', output_template,
            url
        ], check=True)

        files = glob.glob(f"{safe_title}*.m4a")
        if not files:
            print("auto_post_music: аудиофайл не найден после скачивания")
            return
        output_file = files[0]
        print(f"auto_post_music: отправка аудио {output_file} в канал {AUTOPOST_CHANNEL_ID}")
        try:
            with open(output_file, 'rb') as audio:
                caption = (
                    f"{track['title']} — {track['artists'][0]['name']}" if track.get('artists') else track['title']
                ) + "\n\nМузыка дня.\nСлушаем и тащим катки \n Пост создано автоматический\n\n#Music || #iccup"
                bot.send_audio(AUTOPOST_CHANNEL_ID, audio, caption=caption)
            print("auto_post_music: аудио отправлено успешно")
        except Exception as e:
            print(f'Ошибка отправки аудио: {e}')
        for f in files:
            if os.path.exists(f):
                os.remove(f)
    except Exception as e:
        print(f'Ошибка автопостинга музыки: {e}')


scheduler = BackgroundScheduler(timezone=pytz.timezone('Europe/Moscow'))
scheduler.add_job(auto_post_top_streak, 'cron', hour=AUTOPOST_HOUR, minute=AUTOPOST_MINUTE)
scheduler.add_job(auto_post_music, 'cron', day_of_week=MUSIC_POST_DAYS, hour=MUSIC_POST_HOUR, minute=MUSIC_POST_MINUTE)
scheduler.start()
print(f"Планировщик запущен. Музыка будет публиковаться по {MUSIC_POST_DAYS} в {MUSIC_POST_HOUR:02d}:{MUSIC_POST_MINUTE:02d} МСК")

if __name__ == "__main__":
    bot.polling(none_stop=True) 
