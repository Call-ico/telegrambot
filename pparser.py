
from bs4 import BeautifulSoup
import aiohttp

BASE_URL = "https://iccup.com/ru/dota/gamingprofile/"
PROFILE_BASE_URL = "https://iccup.com/profile/view/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

def get_rank_letter(rating):
    if rating >= 30000:
        return "Ultimate"
    elif rating >= 25000:
        return "S+"
    elif rating >= 20000:
        return "S"
    elif rating >= 15000:
        return "S-"
    elif rating >= 12000:
        return "A+"
    elif rating >= 10500:
        return "A"
    elif rating >= 9000:
        return "A-"
    elif rating >= 8000:
        return "B+"
    elif rating >= 7000:
        return "B"
    elif rating >= 6000:
        return "B-"
    elif rating >= 5000:
        return "C+"
    elif rating >= 4000:
        return "C"
    elif rating >= 3000:
        return "C-"
    elif rating >= 2000:
        return "D+"
    elif rating >= 900:
        return "D"
    elif rating >= 400:
        return "D-"
    else:
        return "Default"

async def fetch_iccup_stats_async(nickname):
    try:
        dota_url = f"{BASE_URL}{nickname}.html"
        profile_url = f"{PROFILE_BASE_URL}{nickname}.html"

        async def get_html(url):
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=HEADERS) as resp:
                    resp.raise_for_status()
                    html = await resp.text()
                    return html

        dota_html = await get_html(dota_url)
        from bs4 import BeautifulSoup
        soup_dota = BeautifulSoup(dota_html, "html.parser")
        stats = {"Имя": nickname}
        stats["Рейтинг"] = soup_dota.select_one("span.i-pts").text.strip() if soup_dota.select_one(
            "span.i-pts") else "—"
        rating_text = stats["Рейтинг"]
        try:
            rating_value = int(rating_text.replace(',', '').replace(' ', ''))
        except ValueError:
            rating_value = 0

        stats["РангБуква"] = get_rank_letter(rating_value)
        main_stata_tds = soup_dota.select("div.main-stata-5x5 td")
        stats["Положение в рейтинге"] = main_stata_tds[1].text.strip() if len(main_stata_tds) > 1 else "—"
        k_value = "0"
        k_num_tag = soup_dota.select_one("span#k-num")
        if k_num_tag:
            k_raw = k_num_tag.text.strip()
            k_raw = k_raw.replace("K", "").replace("k", "").strip()
            k_value = ''.join(c for c in k_raw if c.isdigit() or c == '.')
            if not k_value:
                k_value = "0"
        stats["Крутость"] = k_value
        stats["K/D/A"] = parse_kda(soup_dota)
        def get_stat_value(soup, label):
            td_label = soup.find("td", string=label)
            return td_label.find_next_sibling("td").text.strip() if td_label and td_label.find_next_sibling(
                "td") else "—"
        stats["Победы / Поражения / Ливы"] = get_stat_value(soup_dota, "Win/Lose/Leave")
        stats["Курьеров убито"] = get_stat_value(soup_dota, "Курьеров убито")
        stats["Нейтралов убито"] = get_stat_value(soup_dota, "Нейтралов убито")
        stats["Налетанные часы"] = get_stat_value(soup_dota, "Налетанные часы")
        raw = get_stat_value(soup_dota, "Победы")
        stats["Победы %"] = float(raw.strip('%').replace(',', '.'))
        stats["Кол-во ливов"] = get_stat_value(soup_dota, "Кол-во ливов")
        лучший_счет_td = soup_dota.find("td", string="Лучший счет")
        stats["Лучший счет"] = лучший_счет_td.find_next_sibling("td").get_text(
            separator=" ").strip() if лучший_счет_td and лучший_счет_td.find_next_sibling("td") else "—"
        stats["Макс. стрик побед"] = get_stat_value(soup_dota, "Макс. стрик побед")
        stats["Текущий стрик"] = get_stat_value(soup_dota, "Текущий стрик")
        status_html = str(soup_dota)
        stats["Онлайн"] = parse_online_status(status_html)
        kda_table_container = soup_dota.find("div", class_="kda-table")
        if kda_table_container:
            kda_spans = kda_table_container.find_all("span", class_="bidlokod1")
            stats["KDA из таблицы"] = [span.get_text(strip=True) for span in kda_spans[:3]] if kda_spans else ["—", "—",
                                                                                                               "—"]
        else:
            stats["KDA из таблицы"] = ["—", "—", "—"]
        best_hero_section = soup_dota.select("div.top-hero")
        if best_hero_section:
            for section in best_hero_section:
                title_tag = section.find("h4")
                if title_tag and "Лучший герой" in title_tag.text.strip():
                    hero_portrait = section.find("div", class_="hero-portrait")
                    if hero_portrait:
                        hero_img = hero_portrait.find("img")
                        hero_name_span = hero_portrait.find("span")
                        k_coefficient_p = section.find("p")
                        hero_data = {}
                        if hero_img:
                            img_src = hero_img.get("src", "")
                            if img_src.startswith("//"):
                                img_src = "https:" + img_src
                            elif img_src.startswith("/"):
                                img_src = "https://iccup.com" + img_src
                            elif not img_src.startswith("http"):
                                img_src = "https://iccup.com/" + img_src
                            hero_data["image"] = img_src
                            hero_data["name"] = hero_img.get("alt", "")
                        if hero_name_span:
                            hero_data["name"] = hero_name_span.text.strip()
                        if k_coefficient_p:
                            k_text = k_coefficient_p.text.strip()
                            if "коэффициент крутости:" in k_text:
                                k_value = k_text.split("коэффициент крутости:")[1].strip()
                                k_value = k_value.replace("<b>", "").replace("</b>", "").strip()
                                hero_data["k_coefficient"] = k_value
                        stats["Лучший герой"] = hero_data
                        break
        best_hero_section = soup_dota.select("div.top-hero")
        for section in best_hero_section:
            title_tag = section.find("h4")
            hero_tag = section.find("span")
            value_tag = section.find("p")
            if title_tag and hero_tag and value_tag:
                title = title_tag.text.strip()
                hero = hero_tag.text.strip()
                value = value_tag.text.strip()
                stats[f"Лучший {title}"] = f"{hero} ({value})"
        soup_profile = BeautifulSoup(await get_html(profile_url), "html.parser")
        ls_inside = soup_profile.find('div', class_='ls-inside')
        if ls_inside:
            img_tag = ls_inside.find('img')
            if img_tag:
                stats['Аватар'] = img_tag.get('src', '—')
                stats['Звание'] = img_tag.get('alt', '—')
        else:
            stats['Аватар'] = '—'
            stats['Звание'] = '—'
        last_seen = soup_profile.select_one("div.last-seen")
        stats['Последний вход'] = last_seen.text.strip() if last_seen else "—"
        uname_tag = soup_profile.find("h2", class_="profile-uname")
        stats["Элитный"] = "p-elite" in uname_tag.get("class", []) if uname_tag else False
        flag_img = soup_profile.find("img", class_="user--flag")
        if flag_img:
            flag_src = flag_img.get("src", "")
            flag_url_full = "https:" + flag_src if flag_src.startswith("//") else flag_src
            stats['Флаг'] = flag_url_full
        else:
            stats['Флаг'] = None
        games = []
        table = soup_dota.find("tbody", id="result-table")
        if table:
            rows = table.find_all("tr")
            for i, row in enumerate(rows):
                if i >= 5:
                    break
                cols = row.find_all("td")
                if len(cols) >= 5:
                    hero_img_tag = cols[0].find("img")
                    hero_img = None
                    if hero_img_tag:
                        img_src = hero_img_tag.get("src", "")
                        if img_src.startswith("//"):
                            hero_img = "https:" + img_src
                        elif img_src.startswith("/"):
                            hero_img = "https://iccup.com" + img_src
                        elif not img_src.startswith("http"):
                            hero_img = "https://iccup.com/" + img_src
                        else:
                            hero_img = img_src
                        hero_name = hero_img_tag.get("alt", "—")
                    else:
                        hero_name = cols[0].get_text(strip=True)
                    mode = cols[1].text.strip()
                    time_ = cols[2].text.strip()
                    kda = cols[3].get_text(" ", strip=True)
                    score = "—"
                    for span in cols[4].find_all("span"):
                        txt = span.get_text(strip=True)
                        if txt.startswith("+") or txt.startswith("-"):
                            score = txt
                            break
                    if score.startswith('+'):
                        result = 'win'
                    elif score.startswith('-'):
                        result = 'lose'
                    else:
                        result = 'unknown'
                    games.append({
                        'hero_img': hero_img,
                        'hero_name': hero_name,
                        'mode': mode,
                        'time': time_,
                        'kda': kda,
                        'score': score,
                        'result': result,
                    })
        stats["Последние игры"] = games
        return stats
    except Exception as e:
        return {'Ошибка': f"Произошла непредвиденная ошибка: {e}"}

def parse_kda(soup):
    kda_block = soup.find("div", class_="i-kda left")
    if not kda_block:
        return "0/0/0"
    numbers = kda_block.find_all("span", class_=["c-green", "c-red", "c-blue"])
    if not numbers or len(numbers) < 3:
        return "0/0/0"
    kda = "/".join(num.text.strip() for num in numbers)
    return kda

def parse_online_status(html):
    if 'online' in html.lower():
        return "online"
    elif 'offline' in html.lower():
        return "offline"
    else:
        return "unknown"