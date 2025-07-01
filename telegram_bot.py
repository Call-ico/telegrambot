import os
import tempfile
import time
import telebot
from config import TELEGRAM_TOKEN
import jinja2
from pparser import fetch_iccup_stats_async
import asyncio
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service
from telebot import types

jinja_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader('templates'),
    autoescape=jinja2.select_autoescape(['html', 'xml'])
)

stats_cache = {}
CACHE_TTL = 60
CHROME_DRIVER_PATH = None

def ensure_chrome_driver():
    global CHROME_DRIVER_PATH
    if CHROME_DRIVER_PATH is None:
        CHROME_DRIVER_PATH = ChromeDriverManager().install()
    return CHROME_DRIVER_PATH

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

def setup_webdriver():
    chrome_options = Options()
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--window-size=800,1200')
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument('--disable-web-security')
    chrome_options.add_argument('--allow-running-insecure-content')
    driver_path = ensure_chrome_driver()
    service = Service(driver_path)
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver

def take_screenshot(html_content):
    driver = setup_webdriver()
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False, encoding='utf-8') as f:
            f.write(html_content)
            temp_file = f.name
        driver.get(f"file://{temp_file}")
        WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.CLASS_NAME, "glass"))
        )
        glass_element = driver.find_element(By.CLASS_NAME, "glass")
        screenshot = glass_element.screenshot_as_png
        os.unlink(temp_file)
        return screenshot
    finally:
        driver.quit()

bot = telebot.TeleBot(TELEGRAM_TOKEN)

def main_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row('Статистика', 'F.A.Q.')
    keyboard.row('🛠 Техническая поддержка', 'Конкурсы')
    keyboard.row('Вакансии', 'Beta Star Lauchner')
    return keyboard

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    bot.send_message(
        message.chat.id,
        "Привет! Выберите действие:",
        reply_markup=main_keyboard()
    )

@bot.message_handler(func=lambda m: m.text == 'Статистика')
def handle_stats_button(message):
    msg = bot.send_message(message.chat.id, "Введите никнейм для статистики:")
    bot.register_next_step_handler(msg, process_stats_nickname)

def process_stats_nickname(message):
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

@bot.message_handler(func=lambda m: m.text == 'F.A.Q.')
def handle_faq(message):
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

@bot.message_handler(func=lambda m: m.text == 'Конкурсы')
def handle_contests(message):
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

if __name__ == "__main__":
    bot.polling(none_stop=True) 