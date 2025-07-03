import os
import tempfile
import time
import telebot
from config import TELEGRAM_TOKEN
import jinja2
from pparser import fetch_iccup_stats_async
import asyncio
from telebot import types
from playwright.sync_api import sync_playwright

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
    def fake_url_for_static(filename):
        abs_path = os.path.abspath(os.path.join('static', filename))
        return 'file:///' + abs_path.replace('\\', '/')
    jinja_env.globals['url_for'] = lambda endpoint, filename: fake_url_for_static(filename) if endpoint == 'static' else ''
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
        bot.send_message(message.chat.id, "❌ Контекст запроса статистики утерян. Пожалуйста, нажмите '📈 Статистика игроков' ещё раз.")
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
        "Заинтересованы? <a href='https://t.me/Otsustvie_kreativa'>Обращайтесь</a>\n"
        "\n"
        "Forum Team — Создание качественного, креативного контента, модерация форума, "
        "поддержание чистоты и порядка, постоянное взаимодействие с игровым сообществом. Работа "
        "с аудиторией, направленная на улучшение качества общения.\n"
        "Заинтересованы? <a href='https://t.me/Absolutecinemas'>Обращайтесь</a>\n"
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
        "Это публичная БЕТА версия нового iCCup Star Launcher-a, с помощью которого вы можете войти в свой аккаунт, подключится к серверу, общаться с друзьями в продвинутой версии чата, искать игры, с множеством фильтров. И конечно же, вы можете создать или зайти в игровое лобби только с помощью нового лаунчера. Варкрафт 3 запускается только в момент загрузки самой игры. Благодаря этому мы наконец-то можем обновить и улучшить интерфейс Варкрафта, добавить новых элементов, исправить старинные баги и просто обойти ограничения с которыми преходилось бороться до сих пор. !ВАЖНО! Это публичная, но БЕТА версия лаунчера. В нем есть баги, каких-то элементов может пока не хватать, какие-то функции могут работать неправильно. Это нормальная часть процесса тестирования. Новая версия лаунчера постоянно обновляется, изменяется и дорабатывается. Если вы нашли какие-то баги, заметили то, что работает плохо или совсем сломалось, приглашаем вас поделиться мнениями и идеями в Багтрекере (раздел Лаунчер). https://iccup.com/bugtracker?type=launcher\n\n"
        "<b><a href='https://iccup.com/files/download/3600ecf6b55f9e10d5f707c1134f0f1a/iCCup_BETA_Star_Launcher.html'>Установить</a></b>"
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

@bot.message_handler(func=lambda m: m.text in ['❓ FAQ', '🛠 Техническая поддержка', '🎉 Конкурсы', 'Вакансии', '�� BETA STAR LAUNCHER', 'Beta Star Lauchner'])
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

if __name__ == "__main__":
    bot.polling(none_stop=True) 
