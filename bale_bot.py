import requests
import json
import time
import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone as tz
import pytz
from bs4 import BeautifulSoup
from urllib.parse import urlparse, unquote, quote
import os
import tempfile
import re
import asyncio
import zipfile
import random
import threading
import math
import pyzipper
import py7zr
import multivolumefile
import shutil
import glob

try:
    from readability import Document
except ImportError:
    Document = None

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

try:
    from mutagen.mp3 import MP3
    from mutagen.id3 import ID3, TIT2, TPE1, APIC, ID3NoHeaderError
    from mutagen.mp4 import MP4, MP4Cover
    MUTAGEN_AVAILABLE = True
except ImportError:
    MUTAGEN_AVAILABLE = False

try:
    import pinscrape
    PINSCRAPE_AVAILABLE = True
except ImportError:
    PINSCRAPE_AVAILABLE = False

BOT_VERSION = "6.1.0"
BALE_TOKEN = 'ENTER_BOT_TOKEN_HERE'
ADMIN_ID = 'ENTER_BOT_ADMIN_ID_HERE' 
DEFAULT_TARGET_CHANNEL_ID = 'ENTER_DEFAULT_CHANNEL_ID'
BASE_URL = f"https://tapi.bale.ai/bot{BALE_TOKEN}"
IRAN_TZ = pytz.timezone('Asia/Tehran')
MAX_BALE_CHARS = 4000

GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_KEYS = [
    "ENTER_YOUR_GEMINI_API_KEYS_HERE"
]
current_key_index = 0
DEFAULT_TELEGRAM_INTERVAL = 1800
DEFAULT_TWITTER_INTERVAL  = 10800

NITTER_INSTANCES = [
    "nuku.trabun.org","nitter.privacydev.net","nitter.pek.li",
    "nitter.aishiteiru.moe","nitter.aosus.link","xcancel.com",
    "twitt.re","lightbrd.com","nitter.net","nitter.tiekoetter.com",
    "nitter.privacyredirect.com","nitter.space","nitter.catsarch.com","nitter.poast.org",
]

# ==========================================
# --- مدیریت دیتابیس ---
# ==========================================
def init_db():
    conn = sqlite3.connect('bale_bot.db')
    c = conn.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS channels (username TEXT PRIMARY KEY)')
    c.execute('CREATE TABLE IF NOT EXISTS last_posts (channel_username TEXT PRIMARY KEY, last_id INTEGER)')
    c.execute('CREATE TABLE IF NOT EXISTS bot_settings (key TEXT PRIMARY KEY, value TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS sent_posts (channel_username TEXT, sent_at TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS twitter_accounts (username TEXT PRIMARY KEY)')
    c.execute('CREATE TABLE IF NOT EXISTS twitter_last_tweets (username TEXT PRIMARY KEY, last_id INTEGER)')
    c.execute('''CREATE TABLE IF NOT EXISTS queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id TEXT, type TEXT, payload TEXT,
        status TEXT DEFAULT 'pending', created_at TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS groups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE, active INTEGER DEFAULT 1, created_at TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS group_sources (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        group_id INTEGER, source_type TEXT, username TEXT, active INTEGER DEFAULT 1,
        FOREIGN KEY(group_id) REFERENCES groups(id)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS group_destinations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        group_id INTEGER, channel_id TEXT,
        FOREIGN KEY(group_id) REFERENCES groups(id)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS group_last_posts (
        group_id INTEGER, username TEXT, last_id INTEGER,
        PRIMARY KEY(group_id, username)
    )''')
    defaults = [
        ('telegram_active','1'),('twitter_active','1'),
        ('target_channel_id',DEFAULT_TARGET_CHANNEL_ID),
        ('telegram_interval',str(DEFAULT_TELEGRAM_INTERVAL)),
        ('twitter_interval',str(DEFAULT_TWITTER_INTERVAL)),
        ('groups_migrated','0'),
        ('feature_sc','1'),('feature_dl','1'),('feature_web','1'),
        ('feature_pin','1'),('feature_yt','1'),('feature_ym','1'),('feature_football','1'),
        ('feature_tennis','1'),('feature_weather','1'),('feature_ai','1'),
        ('limit_dl_max_mb','20'),('limit_multipart_max_mb','200'),
        ('limit_sc_max_mb','20'),('limit_yt_max_mb','200'),
    ]
    for key, val in defaults:
        c.execute('INSERT OR IGNORE INTO bot_settings VALUES (?, ?)', (key, val))
    conn.commit()
    conn.close()
    _migrate_to_groups()

def _migrate_to_groups():
    if get_setting('groups_migrated','0') == '1': return
    target = get_setting('target_channel_id', DEFAULT_TARGET_CHANNEL_ID)
    now    = get_iran_time_now()
    old_channels = db_query('SELECT username FROM channels', fetch=True) or []
    if old_channels:
        db_query('INSERT OR IGNORE INTO groups (name,active,created_at) VALUES (?,1,?)',('default_telegram',now),commit=True)
        row = db_query('SELECT id FROM groups WHERE name=?',('default_telegram',),fetch=True)
        if row:
            gid = row[0][0]
            db_query('INSERT OR IGNORE INTO group_destinations (group_id,channel_id) VALUES (?,?)',(gid,target),commit=True)
            for (ch,) in old_channels:
                db_query('INSERT OR IGNORE INTO group_sources (group_id,source_type,username,active) VALUES (?,?,?,1)',(gid,'telegram',ch),commit=True)
                lp = db_query('SELECT last_id FROM last_posts WHERE channel_username=?',(ch,),fetch=True)
                if lp: db_query('INSERT OR IGNORE INTO group_last_posts VALUES (?,?,?)',(gid,ch,lp[0][0]),commit=True)
    old_tw = db_query('SELECT username FROM twitter_accounts', fetch=True) or []
    if old_tw:
        db_query('INSERT OR IGNORE INTO groups (name,active,created_at) VALUES (?,1,?)',('default_twitter',now),commit=True)
        row = db_query('SELECT id FROM groups WHERE name=?',('default_twitter',),fetch=True)
        if row:
            gid = row[0][0]
            db_query('INSERT OR IGNORE INTO group_destinations (group_id,channel_id) VALUES (?,?)',(gid,target),commit=True)
            for (tw,) in old_tw:
                db_query('INSERT OR IGNORE INTO group_sources (group_id,source_type,username,active) VALUES (?,?,?,1)',(gid,'twitter',tw),commit=True)
                lp = db_query('SELECT last_id FROM twitter_last_tweets WHERE username=?',(tw,),fetch=True)
                if lp: db_query('INSERT OR IGNORE INTO group_last_posts VALUES (?,?,?)',(gid,tw,lp[0][0]),commit=True)
    set_setting('groups_migrated','1')

def db_query(query, params=(), fetch=False, commit=False):
    try:
        conn = sqlite3.connect('bale_bot.db')
        c = conn.cursor()
        c.execute(query, params)
        result = c.fetchall() if fetch else None
        if commit: conn.commit()
        conn.close()
        return result
    except: return None

def get_setting(key, default=''):
    r = db_query('SELECT value FROM bot_settings WHERE key=?',(key,),fetch=True)
    return r[0][0] if r else default

def set_setting(key, value):
    db_query('INSERT OR REPLACE INTO bot_settings VALUES (?,?)',(key,value),commit=True)

def get_target_channel(): return get_setting('target_channel_id', DEFAULT_TARGET_CHANNEL_ID)
def get_tg_interval():
    try: return int(get_setting('telegram_interval',str(DEFAULT_TELEGRAM_INTERVAL)))
    except: return DEFAULT_TELEGRAM_INTERVAL
def get_tw_interval():
    try: return int(get_setting('twitter_interval',str(DEFAULT_TWITTER_INTERVAL)))
    except: return DEFAULT_TWITTER_INTERVAL
def is_feature_on(name): return get_setting(f'feature_{name}','1') == '1'

# ==========================================
# --- مدیریت گروه‌ها ---
# ==========================================
def group_create(name):
    db_query('INSERT OR IGNORE INTO groups (name,active,created_at) VALUES (?,1,?)',(name,get_iran_time_now()),commit=True)
def group_get(name):
    r = db_query('SELECT id,name,active FROM groups WHERE name=?',(name,),fetch=True)
    return r[0] if r else None
def group_list(): return db_query('SELECT id,name,active FROM groups ORDER BY id',fetch=True) or []
def group_set_active(name, active): db_query('UPDATE groups SET active=? WHERE name=?',(active,name),commit=True)
def group_add_source(group_id, source_type, username):
    db_query('INSERT OR IGNORE INTO group_sources (group_id,source_type,username,active) VALUES (?,?,?,1)',(group_id,source_type,username),commit=True)
def group_remove_source(group_id, username):
    db_query('DELETE FROM group_sources WHERE group_id=? AND username=?',(group_id,username),commit=True)
def group_set_dest(group_id, channel_id):
    db_query('DELETE FROM group_destinations WHERE group_id=?',(group_id,),commit=True)
    db_query('INSERT INTO group_destinations (group_id,channel_id) VALUES (?,?)',(group_id,channel_id),commit=True)
def group_get_dest(group_id):
    r = db_query('SELECT channel_id FROM group_destinations WHERE group_id=?',(group_id,),fetch=True)
    return r[0][0] if r else get_target_channel()
def group_sources(group_id):
    return db_query('SELECT source_type,username,active FROM group_sources WHERE group_id=?',(group_id,),fetch=True) or []
def group_get_last(group_id, username):
    r = db_query('SELECT last_id FROM group_last_posts WHERE group_id=? AND username=?',(group_id,username),fetch=True)
    return r[0][0] if r else 0
def group_set_last(group_id, username, last_id):
    db_query('INSERT OR REPLACE INTO group_last_posts VALUES (?,?,?)',(group_id,username,last_id),commit=True)
def group_info_text(name):
    g = group_get(name)
    if not g: return f"❌ گروه '{name}' پیدا نشد."
    gid,gname,active = g
    dest    = group_get_dest(gid)
    sources = group_sources(gid)
    status  = "✅ فعال" if active else "⏸ متوقف"
    msg = f"📋 گروه: {gname}\nوضعیت: {status}\nمقصد: {dest}\n\nمنابع:\n"
    for (stype,uname,sactive) in sources:
        icon = "📡" if stype=='telegram' else "🐦"
        st   = "✅" if sactive else "⏸"
        msg += f"  {icon} @{uname} {st}\n"
    return msg

# ==========================================
# --- توابع کمکی Bale ---
# ==========================================
def send_bale_message(chat_id, text, reply_markup=None):
    try:
        payload = {'chat_id': chat_id, 'text': text}
        if reply_markup: payload['reply_markup'] = json.dumps(reply_markup)
        r    = requests.post(f"{BASE_URL}/sendMessage", json=payload, timeout=10)
        data = r.json()
        if data.get('ok'): return data['result']['message_id']
    except: pass
    return None

def edit_bale_message(chat_id, message_id, text, reply_markup=None):
    try:
        payload = {'chat_id': chat_id, 'message_id': message_id, 'text': text}
        if reply_markup: payload['reply_markup'] = json.dumps(reply_markup)
        requests.post(f"{BASE_URL}/editMessageText", json=payload, timeout=10)
    except: pass

def delete_bale_message(chat_id, message_id):
    try:
        requests.post(f"{BASE_URL}/deleteMessage",json={'chat_id':chat_id,'message_id':message_id},timeout=10)
    except: pass

def answer_callback_query(callback_query_id, text=''):
    try:
        requests.post(f"{BASE_URL}/answerCallbackQuery",json={'callback_query_id':callback_query_id,'text':text},timeout=10)
    except: pass

def send_long_message(chat_id, text):
    chunks = [text[i:i+MAX_BALE_CHARS] for i in range(0,len(text),MAX_BALE_CHARS)]
    for i, chunk in enumerate(chunks):
        send_bale_message(chat_id, chunk)
        if i < len(chunks)-1: time.sleep(1)

def send_bale_photo(chat_id, photo_url, caption=""):
    try:
        requests.post(f"{BASE_URL}/sendPhoto",json={'chat_id':chat_id,'photo':photo_url,'caption':caption[:1000]},timeout=15)
    except: pass

def send_bale_document(chat_id, file_path, caption=""):
    """ارسال فایل — True اگر موفق، False اگر ناموفق"""
    try:
        with open(file_path,'rb') as f:
            resp = requests.post(
                f"{BASE_URL}/sendDocument",
                data={'chat_id':chat_id,'caption':caption[:1000]},
                files={'document':(os.path.basename(file_path),f)},
                timeout=120)
        data = resp.json()
        return data.get('ok', False)
    except Exception as e:
        print(f"[send_bale_document] {e}")
        return False

def send_bale_audio(chat_id, file_path, caption="", title="", performer=""):
    """ارسال فایل صوتی — True اگر موفق، False اگر ناموفق"""
    try:
        ext      = os.path.splitext(file_path)[1].lower()
        mime     = 'audio/mp4' if ext == '.m4a' else 'audio/mpeg'
        basename = os.path.basename(file_path)
        with open(file_path,'rb') as f:
            resp = requests.post(
                f"{BASE_URL}/sendAudio",
                data={'chat_id':chat_id,'caption':caption[:1000],'title':title,'performer':performer},
                files={'audio':(basename,f,mime)},
                timeout=120)
        data = resp.json()
        return data.get('ok', False)
    except Exception as e:
        print(f"[send_bale_audio] {e}")
        return False

def get_iran_time_now(): return datetime.now(IRAN_TZ).strftime('%Y/%m/%d - %H:%M')
def get_iran_date_today(): return datetime.now(IRAN_TZ).strftime('%Y-%m-%d')

# ==========================================
# --- Anti-403 helpers ---
# ==========================================
_UA_LIST = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0',
]

def _make_headers(referer='', json_req=False):
    headers = {
        'User-Agent': random.choice(_UA_LIST),
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'DNT': '1', 'Connection': 'keep-alive', 'Cache-Control': 'max-age=0',
    }
    if json_req:
        headers['Accept'] = 'application/json, text/javascript, */*; q=0.01'
        headers['X-Requested-With'] = 'XMLHttpRequest'
    else:
        headers['Accept'] = 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8'
        headers['Upgrade-Insecure-Requests'] = '1'
    if referer: headers['Referer'] = referer
    return headers

def _safe_get(url, referer='', timeout=15, retries=2, json_req=False, stream=False):
    last_err = None
    for attempt in range(retries):
        try:
            if attempt > 0: time.sleep(1.5 * attempt)
            resp = requests.get(url, headers=_make_headers(referer, json_req), timeout=timeout, stream=stream)
            if resp.status_code in (403,429) and attempt < retries-1: continue
            return resp
        except Exception as e:
            last_err = e
    raise last_err or Exception("_safe_get failed")

# ==========================================
# --- تلگرام ---
# ==========================================
def unwrap_link(href):
    if 'url=' in href: return unquote(href.split('url=')[1].split('&')[0])
    return href

def get_telegram_posts(channel_username):
    try:
        time.sleep(random.uniform(1.5,4.0))
        resp = _safe_get(f"https://t.me/s/{channel_username}", referer="https://t.me/")
        soup = BeautifulSoup(resp.text,'html.parser')
        posts = []
        for msg in soup.find_all('div', class_='tgme_widget_message'):
            link_tag = msg.find('a', class_='tgme_widget_message_date')
            if not link_tag: continue
            post_id   = int(link_tag['href'].split('/')[-1])
            post_time = get_iran_time_now()
            time_tag  = link_tag.find('time')
            if time_tag and time_tag.get('datetime'):
                try:
                    dt = datetime.fromisoformat(time_tag['datetime'])
                    post_time = dt.astimezone(IRAN_TZ).strftime('%Y/%m/%d - %H:%M')
                except: pass
            reply_text = ""
            reply_elem = msg.find('a', class_='tgme_widget_message_reply')
            if reply_elem:
                reply_body = reply_elem.find(class_='js-message_reply_text')
                reply_text = reply_body.get_text(strip=True) if reply_body else reply_elem.get_text(strip=True)
            text_area  = msg.find('div', class_='tgme_widget_message_text js-message_text')
            final_text = ""
            if text_area:
                for qw in text_area.find_all(class_=lambda c: c and 'quote' in c): qw.decompose()
                for a in text_area.find_all('a'):
                    href = a.get('href','')
                    a.replace_with(f"[{a.get_text()}]({unwrap_link(href)})")
                for br in text_area.find_all('br'): br.replace_with('\n')
                final_text = text_area.get_text().strip()
            if final_text:
                if reply_text: final_text = f"پاسخ به: {reply_text}\n\n{final_text}"
                posts.append({'id':post_id,'text':final_text,'time':post_time})
        return posts
    except Exception as e:
        print(f"[telegram] {channel_username}: {e}")
        return []

# ==========================================
# --- توییتر Nitter RSS ---
# ==========================================
def _extract_twitter_images(desc, instance):
    images = []
    if not desc: return images
    try:
        soup = BeautifulSoup(desc,'html.parser')
        for img in soup.find_all('img'):
            src = img.get('src','')
            if not src: continue
            if instance in src: src = src.replace(f"https://{instance}","https://pbs.twimg.com")
            if 'pbs.twimg.com' in src: src = re.sub(r'\?.*$','',src)+'?format=jpg&name=large'
            if src.startswith('http'): images.append(src)
    except: pass
    return images

def get_twitter_posts(username):
    instances = NITTER_INSTANCES.copy()
    random.shuffle(instances)
    for instance in instances:
        try:
            time.sleep(random.uniform(2.0,6.0))
            url  = f"https://{instance}/{username}/rss"
            resp = _safe_get(url, referer=f"https://{instance}/", timeout=12)
            if resp.status_code != 200: continue
            root    = ET.fromstring(resp.text)
            channel = root.find('channel')
            if not channel: continue
            posts = []
            for item in channel.findall('item'):
                link  = item.findtext('link','').strip()
                title = item.findtext('title','').strip()
                desc  = item.findtext('description','')
                try: tweet_id = int(link.rstrip('/').split('/')[-1].split('#')[0])
                except: continue
                images = _extract_twitter_images(desc, instance)
                posts.append({'id':tweet_id,'text':title,'image':images[0] if images else None,'images':images})
            if posts: return posts
        except Exception as e:
            print(f"[nitter] {instance}: {e}")
            continue
    return []

# ==========================================
# --- هواشناسی ---
# ==========================================
def get_detailed_weather(city_name):
    try:
        resp = requests.get(f"https://wttr.in/{city_name}?format=j1", timeout=15)
        if resp.status_code != 200: return "❌ شهر پیدا نشد یا سرویس در دسترس نیست."
        data    = resp.json()
        current = data['current_condition'][0]
        today   = data['weather'][0]
        status  = current.get('lang_fa', current.get('weatherDesc',[{'value':'نامشخص'}]))[0]['value']
        output  = f"🌤 گزارش وضعیت هوای {city_name}\n\n"
        output += f"🔸 وضعیت: {status}\n"
        output += f"🌡 دما: {current['temp_C']}°C (احساس: {current['FeelsLikeC']}°C)\n"
        output += f"📈 حداکثر: {today['maxtempC']}°C | 📉 حداقل: {today['mintempC']}°C\n"
        output += f"🌬 باد: {current['windspeedKmph']} km/h | 💧 رطوبت: {current['humidity']}%\n\n"
        output += "📅 پیش‌بینی:\n"
        for i in range(1, min(4,len(data['weather']))):
            day = data['weather'][i]
            h   = day['hourly'][len(day['hourly'])//2]
            ds  = h.get('lang_fa',h.get('weatherDesc',[{'value':'صاف'}]))[0]['value']
            output += f"🔹 {day['date']}: {ds} ({day['mintempC']}~{day['maxtempC']}°C)\n"
        return output
    except: return "❌ خطا در پردازش داده‌های آب و هوا."

# ==========================================
# --- هوش مصنوعی ---
# ==========================================
def ask_gemini(prompt):
    global current_key_index
    for _ in range(len(GEMINI_KEYS)):
        api_key = GEMINI_KEYS[current_key_index]
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={api_key}"
        try:
            res = requests.post(url, json={"contents":[{"parts":[{"text":prompt}]}]}, timeout=30)
            if res.status_code == 200:
                return res.json()['candidates'][0]['content']['parts'][0]['text']
            elif res.status_code == 429:
                current_key_index = (current_key_index+1) % len(GEMINI_KEYS)
                continue
        except: pass
        current_key_index = (current_key_index+1) % len(GEMINI_KEYS)
    return "❌ مشکلی در برقراری ارتباط با هوش مصنوعی وجود دارد."

# ==========================================
# --- وب اسکرپر ---
# ==========================================
def extract_article(url):
    if Document is None: return None, "❌ کتابخانه readability-lxml نصب نیست."
    try:
        resp = _safe_get(url, timeout=15)
        if resp.status_code == 403: return None, "❌ دسترسی به سایت مسدود شد (403)."
        resp.raise_for_status()
        doc  = Document(resp.text)
        soup = BeautifulSoup(doc.summary(),'html.parser')
        for tag in soup(['script','style','nav','footer','aside','form']): tag.decompose()
        text = re.sub(r'\n\s*\n','\n\n',soup.get_text(separator='\n').strip())
        images = []
        for img in soup.find_all('img'):
            src = img.get('src') or img.get('data-src')
            if src:
                if src.startswith('//'): src = 'https:' + src
                elif src.startswith('/'):
                    from urllib.parse import urljoin
                    src = urljoin(url, src)
                if src.startswith('http'): images.append(src)
        return {'title':doc.title(),'text':text,'images':list(dict.fromkeys(images))[:5]}, None
    except Exception as e:
        return None, f"❌ خطا در استخراج: {str(e)[:120]}"

# ==========================================
# --- ارسال چند پارتی ---
# ==========================================
def _send_file_multipart(chat_id, file_path, filename, caption=""):
    """True=موفق False=ناموفق — اگه کوچک باشه مستقیم، وگرنه 7z multipart"""
    part_size = 19 * 1024 * 1024
    try:
        file_size = os.path.getsize(file_path)
        if file_size <= part_size:
            ok = send_bale_document(chat_id, file_path, caption=caption or f"📁 {filename}")
            if not ok:
                send_bale_message(chat_id, f"❌ ارسال فایل '{filename}' ناموفق بود. لطفاً دوباره تلاش کنید.")
            return ok

        total_parts = math.ceil(file_size / part_size)
        send_bale_message(chat_id,
            f"📦 فایل {file_size//(1024*1024)}MB در {total_parts} پارت ارسال می‌شود.\n\n"
            f"📱 اندروید: فقط فایل .001 را با ZArchiver باز کنید.\n"
            f"💻 ویندوز: فقط فایل .001 را با 7Zip یا WinRAR باز کنید.")

        temp_dir     = tempfile.mkdtemp(prefix="multipart_")
        archive_base = os.path.join(temp_dir, filename + ".7z")

        with multivolumefile.open(archive_base, mode='wb', volume=part_size) as target_archive:
            with py7zr.SevenZipFile(target_archive,'w') as archive:
                archive.write(file_path, arcname=filename)

        parts = sorted(glob.glob(archive_base + ".*"))
        if os.path.exists(archive_base): parts.insert(0, archive_base)
        parts = sorted(parts)

        failed_parts = []
        for idx, part_path in enumerate(parts, start=1):
            part_name    = os.path.basename(part_path)
            caption_text = f"📁 {part_name}\n📦 پارت {idx}/{len(parts)}"
            if idx == 1: caption_text += "\n\n✅ فقط همین فایل را باز کنید."
            ok = send_bale_document(chat_id, part_path, caption=caption_text)
            if not ok: failed_parts.append(idx)
            time.sleep(1)

        shutil.rmtree(temp_dir, ignore_errors=True)

        if failed_parts:
            send_bale_message(chat_id, f"⚠️ پارت‌های {failed_parts} ارسال نشدند. لطفاً دوباره درخواست دهید.")
            return False
        send_bale_message(chat_id, "✅ همه پارت‌ها ارسال شدند.")
        return True

    except Exception as e:
        send_bale_message(chat_id, f"❌ خطا در ارسال چند پارتی:\n{str(e)[:200]}")
        return False

# ==========================================
# --- دانلود فایل مستقیم ---
# ==========================================
def download_direct_file(url, chat_id):
    max_total = int(get_setting('limit_multipart_max_mb','200')) * 1024 * 1024
    temp_path = None
    try:
        try:
            head = requests.head(url, timeout=15, allow_redirects=True)
            cl   = head.headers.get('Content-Length')
            if cl and int(cl) > max_total:
                send_bale_message(chat_id, f"⚠️ حجم فایل ({int(cl)//(1024*1024)}MB) بیش از {max_total//(1024*1024)}MB است.")
                return
        except: pass

        msg_id = send_bale_message(chat_id, "⏳ در حال دانلود فایل...")
        resp   = requests.get(url, stream=True, timeout=120)
        resp.raise_for_status()
        fd, temp_path = tempfile.mkstemp()
        total_size = 0
        stopped    = False
        with os.fdopen(fd,'wb') as f:
            for chunk in resp.iter_content(chunk_size=65536):
                if not chunk: continue
                f.write(chunk)
                total_size += len(chunk)
                if total_size > max_total: stopped = True; break

        if stopped:
            os.remove(temp_path)
            if msg_id: delete_bale_message(chat_id, msg_id)
            send_bale_message(chat_id, f"⚠️ دانلود متوقف شد. حجم بیش از {max_total//(1024*1024)}MB است.")
            return

        filename = None
        cd = resp.headers.get('Content-Disposition')
        if cd and 'filename=' in cd:
            found = re.findall('filename="?([^";]+)', cd)
            filename = found[0].strip() if found else None
        if not filename:
            filename = os.path.basename(urlparse(url).path) or 'downloaded_file.dat'

        final_path = os.path.join(tempfile.gettempdir(), filename)
        shutil.move(temp_path, final_path)
        temp_path = None
        if msg_id: delete_bale_message(chat_id, msg_id)
        _send_file_multipart(chat_id, final_path, filename)
        if os.path.exists(final_path): os.remove(final_path)

    except Exception as e:
        send_bale_message(chat_id, f"❌ خطا در دانلود:\n{str(e)[:200]}")
        try:
            if temp_path and os.path.exists(temp_path): os.remove(temp_path)
        except: pass

# ==========================================
# --- ساندکلاد ---
# ==========================================
user_soundcloud_searches = {}

def _normalize_soundcloud_url(url):
    try:
        parsed = urlparse(url)
        if 'soundcloud.com' not in parsed.netloc: return url
        return f"https://soundcloud.com{parsed.path.rstrip('/')}"
    except: return url

def _fetch_soundcloud_client_id():
    try:
        resp    = _safe_get('https://soundcloud.com', referer='https://soundcloud.com/', timeout=15)
        js_urls = re.findall(r'https://a-v2\.sndcdn\.com/assets/[^"]+\.js', resp.text)
        for js_url in reversed(js_urls):
            try:
                js_resp = requests.get(js_url, headers={'User-Agent': random.choice(_UA_LIST)}, timeout=10)
                match   = re.search(r'client_id:"([a-zA-Z0-9]+)"', js_resp.text)
                if match: return match.group(1)
            except: continue
    except: pass
    return None

def _get_soundcloud_track_data(track_url, client_id):
    clean_url = _normalize_soundcloud_url(track_url)
    resp = requests.get(
        f"https://api-v2.soundcloud.com/resolve?url={clean_url}&client_id={client_id}",
        headers={'User-Agent': random.choice(_UA_LIST)}, timeout=15)
    if resp.status_code == 401: raise ValueError("client_id_expired")
    if resp.status_code == 404: raise ValueError("track_not_found")
    if resp.status_code != 200: raise ValueError(f"resolve_failed:{resp.status_code}")
    return resp.json()

def _extract_stream_url(track_data, client_id):
    """بهترین progressive stream — اگه 404 داد fallback، اگه HLS بود hls_only"""
    transcodings     = track_data.get('media',{}).get('transcodings',[])
    best_mp3         = None
    best_mp3_quality = -1
    m4a_url          = None
    hls_url          = None

    for t in transcodings:
        fmt      = t.get('format',{})
        protocol = fmt.get('protocol','')
        mime     = fmt.get('mime_type','')
        t_url    = t.get('url','')
        quality  = t.get('quality','')
        if protocol == 'progressive':
            if 'mp3' in mime:
                q_score = 1 if quality == 'hq' else 0
                if q_score > best_mp3_quality:
                    best_mp3_quality = q_score
                    best_mp3 = t_url
            elif not m4a_url: m4a_url = t_url
        elif protocol == 'hls' and not hls_url: hls_url = t_url

    candidates = []
    if best_mp3: candidates.append((best_mp3, '.mp3'))
    if m4a_url:  candidates.append((m4a_url,  '.m4a'))

    for chosen, ext in candidates:
        try:
            sr = requests.get(f"{chosen}?client_id={client_id}",
                              headers={'User-Agent': random.choice(_UA_LIST)}, timeout=15)
            if sr.status_code == 401: raise ValueError("client_id_expired")
            if sr.status_code == 404: continue
            if sr.status_code != 200: continue
            stream_url = sr.json().get('url')
            if stream_url: return stream_url, ext
        except ValueError: raise
        except Exception: continue

    if hls_url: raise ValueError("hls_only")
    raise ValueError("no_format")

def _get_track_meta(track_data):
    title     = track_data.get('title','بی‌نام')
    user      = track_data.get('user',{}).get('username','')
    thumbnail = track_data.get('artwork_url','') or track_data.get('user',{}).get('avatar_url','')
    if thumbnail: thumbnail = thumbnail.replace('-large','-t500x500')
    return title, user, thumbnail

def search_soundcloud(query, max_results=10):
    if yt_dlp is None: return None, "❌ کتابخانه yt-dlp نصب نیست."
    try:
        with yt_dlp.YoutubeDL({'quiet':True,'no_warnings':True,'extract_flat':True}) as ydl:
            info = ydl.extract_info(f"scsearch{max_results}:{query}", download=False)
            if not info or 'entries' not in info: return None, "❌ نتیجه‌ای یافت نشد."
            results = []
            for e in info['entries']:
                if e:
                    results.append({
                        'title':e.get('title','بی‌نام'),'uploader':e.get('uploader',''),
                        'duration':e.get('duration',0),'webpage_url':e.get('webpage_url',''),
                        'id':e.get('id',''),'thumbnail':e.get('thumbnail',''),
                    })
            return results, None
    except Exception as e:
        return None, f"❌ خطا: {str(e)[:100]}"

def embed_metadata(file_path, title, artist, cover_data=None):
    """embed متادیتا + album art در MP3 یا M4A/WebM"""
    if not MUTAGEN_AVAILABLE: return
    try:
        ext = os.path.splitext(file_path)[1].lower()
        if ext == '.mp3':
            try:    tags = ID3(file_path)
            except ID3NoHeaderError: tags = ID3()
            tags['TIT2'] = TIT2(encoding=3, text=title)
            tags['TPE1'] = TPE1(encoding=3, text=artist)
            if cover_data:
                tags['APIC'] = APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=cover_data)
            tags.save(file_path)
        elif ext in ('.m4a','.webm','.opus'):
            try:
                audio = MP4(file_path)
                audio['\xa9nam'] = [title]
                audio['\xa9ART'] = [artist]
                if cover_data:
                    audio['covr'] = [MP4Cover(cover_data, imageformat=MP4Cover.FORMAT_JPEG)]
                audio.save()
            except Exception: pass
    except Exception as e:
        print(f"[mutagen] {e}")

def _progress_bar(percent):
    filled = int(percent / 10)
    return f"[{'█'*filled}{'░'*(10-filled)}] {percent}%"

def _parse_title_artist(raw_title, uploader):
    if ' - ' in raw_title:
        parts  = raw_title.split(' - ',1)
        artist = parts[0].strip()
        title  = parts[1].strip()
        if not uploader or uploader.lower() in ['unknown','نامشخص','']: uploader = artist
        return title, uploader
    else:
        if not uploader or uploader.lower() in ['unknown','نامشخص','']: uploader = 'Unknown Artist'
        return raw_title.strip(), uploader

def _animated_upload_progress(chat_id, msg_id, stop_event):
    frames = [
        "📤 در حال آپلود...\n[⬜⬜⬜⬜⬜⬜⬜⬜⬜⬜]",
        "📤 در حال آپلود...\n[🟦⬜⬜⬜⬜⬜⬜⬜⬜⬜]",
        "📤 در حال آپلود...\n[🟦🟦⬜⬜⬜⬜⬜⬜⬜⬜]",
        "📤 در حال آپلود...\n[🟦🟦🟦⬜⬜⬜⬜⬜⬜⬜]",
        "📤 در حال آپلود...\n[🟦🟦🟦🟦⬜⬜⬜⬜⬜⬜]",
        "📤 در حال آپلود...\n[🟦🟦🟦🟦🟦⬜⬜⬜⬜⬜]",
        "📤 در حال آپلود...\n[🟦🟦🟦🟦🟦🟦⬜⬜⬜⬜]",
        "📤 در حال آپلود...\n[🟦🟦🟦🟦🟦🟦🟦⬜⬜⬜]",
        "📤 در حال آپلود...\n[🟦🟦🟦🟦🟦🟦🟦🟦⬜⬜]",
        "📤 در حال آپلود...\n[🟦🟦🟦🟦🟦🟦🟦🟦🟦⬜]",
    ]
    i = 0
    while not stop_event.is_set():
        if msg_id: edit_bale_message(chat_id, msg_id, frames[i % len(frames)])
        i += 1
        time.sleep(2)

def _download_via_ytdlp_sc(track_url, out_dir, max_bytes):
    """fallback دانلود ساندکلاد از طریق yt-dlp (HLS یا 404)"""
    if yt_dlp is None: raise ValueError("yt_dlp_unavailable")
    opts = {
        'quiet':True,'no_warnings':True,'format':'bestaudio/best',
        'outtmpl':os.path.join(out_dir,'%(uploader)s - %(title)s.%(ext)s'),
        'noplaylist':True,'postprocessors':[],
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(track_url, download=True)
    files = glob.glob(os.path.join(out_dir,'*'))
    if not files: raise ValueError("ytdlp_no_file")
    file_path = files[0]
    if os.path.getsize(file_path) > max_bytes:
        os.remove(file_path)
        raise ValueError("ytdlp_too_large")
    ext      = os.path.splitext(file_path)[1].lower() or '.mp3'
    title    = info.get('title','آهنگ')
    uploader = info.get('uploader','')
    thumbnail= info.get('thumbnail','')
    return file_path, ext, title, uploader, thumbnail

def download_soundcloud_track(info, chat_id, max_mb=None):
    if max_mb is None: max_mb = int(get_setting('limit_sc_max_mb','20'))
    max_bytes    = max_mb * 1024 * 1024
    track_url    = info['webpage_url']
    temp_path    = None
    temp_dir     = None
    temp_msg_ids = []

    mid = send_bale_message(chat_id, "🔗 در حال دریافت اطلاعات آهنگ...")
    if mid: temp_msg_ids.append(mid)

    stream_url = None
    ext        = '.mp3'
    title      = info.get('title','آهنگ')
    uploader   = info.get('uploader','')
    thumbnail  = info.get('thumbnail','')
    use_ytdlp  = False

    for attempt in range(2):
        try:
            client_id  = _fetch_soundcloud_client_id()
            if not client_id: raise ValueError("no_client_id")
            track_data = _get_soundcloud_track_data(track_url, client_id)
            raw_title, raw_user, thumb = _get_track_meta(track_data)
            title = raw_title; uploader = raw_user; thumbnail = thumb or thumbnail
            stream_url, ext = _extract_stream_url(track_data, client_id)
            break
        except ValueError as e:
            err = str(e)
            if err in ("hls_only","no_format"):
                use_ytdlp = True; break
            elif err == "track_not_found":
                for m in temp_msg_ids: delete_bale_message(chat_id, m)
                send_bale_message(chat_id, "❌ آهنگ پیدا نشد. ممکن است private یا حذف شده باشد.")
                return
            elif attempt == 1:
                use_ytdlp = True; break
            time.sleep(1)
        except Exception:
            if attempt == 1: use_ytdlp = True; break
            time.sleep(1)

    # ─── مسیر ۱: yt-dlp fallback ───
    if use_ytdlp:
        if yt_dlp is None:
            for m in temp_msg_ids: delete_bale_message(chat_id, m)
            send_bale_message(chat_id, "❌ این آهنگ قابل دانلود نیست (yt-dlp نصب نیست).")
            return
        try:
            dl_mid = send_bale_message(chat_id, "⏳ در حال دانلود (روش جایگزین)...")
            if dl_mid: temp_msg_ids.append(dl_mid)
            temp_dir = tempfile.mkdtemp()
            file_path, ext, yt_title, yt_uploader, yt_thumb = _download_via_ytdlp_sc(track_url, temp_dir, max_bytes)
            if yt_title:    title     = yt_title
            if yt_uploader: uploader  = yt_uploader
            if yt_thumb:    thumbnail = yt_thumb
            title, uploader = _parse_title_artist(title, uploader)
            if dl_mid: edit_bale_message(chat_id, dl_mid, "🎨 پردازش متادیتا...")
            cover_data = None
            if thumbnail:
                try:
                    cr = requests.get(thumbnail, timeout=10)
                    if cr.status_code == 200: cover_data = cr.content
                except: pass
            embed_metadata(file_path, title, uploader, cover_data)
            stop_event = threading.Event()
            up_mid = send_bale_message(chat_id, "📤 در حال آپلود...\n[⬜⬜⬜⬜⬜⬜⬜⬜⬜⬜]")
            if up_mid: temp_msg_ids.append(up_mid)
            anim = threading.Thread(target=_animated_upload_progress, args=(chat_id,up_mid,stop_event), daemon=True)
            anim.start()
            ok = send_bale_audio(chat_id, file_path, caption=f"🎵 {title}\n👤 {uploader}", title=title, performer=uploader)
            stop_event.set(); anim.join(timeout=1)
            if not ok:
                ok2 = send_bale_document(chat_id, file_path, caption=f"🎵 {title}\n👤 {uploader}")
                if not ok2: send_bale_message(chat_id, "❌ ارسال آهنگ ناموفق بود. لطفاً دوباره تلاش کنید.")
            for m in temp_msg_ids: delete_bale_message(chat_id, m)
        except ValueError as e:
            err = str(e)
            for m in temp_msg_ids: delete_bale_message(chat_id, m)
            if err == "ytdlp_too_large": send_bale_message(chat_id, f"⚠️ حجم آهنگ بیش از {max_mb}MB است.")
            elif err == "ytdlp_no_file": send_bale_message(chat_id, "❌ دانلود انجام نشد.")
            else: send_bale_message(chat_id, f"❌ خطا: {err[:100]}")
        except Exception as e:
            for m in temp_msg_ids: delete_bale_message(chat_id, m)
            send_bale_message(chat_id, f"❌ خطا در دانلود: {str(e)[:100]}")
        finally:
            try:
                if temp_dir and os.path.exists(temp_dir): shutil.rmtree(temp_dir, ignore_errors=True)
            except: pass
        return

    # ─── مسیر ۲: progressive stream ───
    if not stream_url:
        for m in temp_msg_ids: delete_bale_message(chat_id, m)
        send_bale_message(chat_id, "❌ لینک دانلود دریافت نشد.")
        return

    title, uploader = _parse_title_artist(title, uploader)
    try:
        dl_mid = send_bale_message(chat_id, f"⏳ در حال دانلود...\n{_progress_bar(0)}")
        if dl_mid: temp_msg_ids.append(dl_mid)
        resp        = requests.get(stream_url, headers={'User-Agent':random.choice(_UA_LIST)}, stream=True, timeout=60)
        resp.raise_for_status()
        total_bytes = int(resp.headers.get('Content-Length',0))
        if total_bytes and total_bytes > max_bytes:
            for m in temp_msg_ids: delete_bale_message(chat_id, m)
            send_bale_message(chat_id, f"⚠️ حجم آهنگ ({total_bytes//(1024*1024)}MB) بیش از {max_mb}MB است.")
            return
        safe_uploader = re.sub(r'[\\/*?:"<>|]','_', uploader)[:40]
        safe_title    = re.sub(r'[\\/*?:"<>|]','_', title)[:60]
        temp_path     = os.path.join(tempfile.gettempdir(), f"{safe_uploader} - {safe_title}{ext}")
        downloaded = last_pct = 0
        stopped    = False
        with open(temp_path,'wb') as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
                downloaded += len(chunk)
                if downloaded > max_bytes: stopped = True; break
                if total_bytes and dl_mid:
                    pct = min(int(downloaded/total_bytes*100),99)
                    if pct - last_pct >= 5:
                        last_pct = pct
                        edit_bale_message(chat_id, dl_mid, f"⏳ در حال دانلود...\n{_progress_bar(pct)}")
        if stopped:
            os.remove(temp_path)
            for m in temp_msg_ids: delete_bale_message(chat_id, m)
            send_bale_message(chat_id, f"⚠️ حجم آهنگ بیش از {max_mb}MB شد.")
            return
        if dl_mid: edit_bale_message(chat_id, dl_mid, f"🎨 پردازش متادیتا...\n{_progress_bar(100)}")
        cover_data = None
        if thumbnail:
            try:
                cr = requests.get(thumbnail, timeout=10)
                if cr.status_code == 200: cover_data = cr.content
            except: pass
        embed_metadata(temp_path, title, uploader, cover_data)
        stop_event = threading.Event()
        up_mid = send_bale_message(chat_id, "📤 در حال آپلود...\n[⬜⬜⬜⬜⬜⬜⬜⬜⬜⬜]")
        if up_mid: temp_msg_ids.append(up_mid)
        anim = threading.Thread(target=_animated_upload_progress, args=(chat_id,up_mid,stop_event), daemon=True)
        anim.start()
        ok = send_bale_audio(chat_id, temp_path, caption=f"🎵 {title}\n👤 {uploader}", title=title, performer=uploader)
        stop_event.set(); anim.join(timeout=1)
        if not ok:
            ok2 = send_bale_document(chat_id, temp_path, caption=f"🎵 {title}\n👤 {uploader}")
            if not ok2: send_bale_message(chat_id, "❌ ارسال آهنگ ناموفق بود. لطفاً دوباره تلاش کنید.")
        os.remove(temp_path)
        for m in temp_msg_ids: delete_bale_message(chat_id, m)
    except Exception as e:
        try: stop_event.set()
        except: pass
        sc_title = title or info.get('title','')
        for m in temp_msg_ids:
            try: delete_bale_message(chat_id, m)
            except: pass
        if sc_title and is_feature_on('ym'):
            mid_fb = send_bale_message(chat_id,
                f"⚠️ ساندکلاد ناموفق بود.\n🔎 جستجو در YT Music: '{sc_title}'...")
            results_ym, err_ym = _ym_extract(sc_title, search=True, max_results=10)
            if err_ym or not results_ym:
                if mid_fb: delete_bale_message(chat_id, mid_fb)
                send_bale_message(chat_id, "❌ خطا در دانلود و YT Music هم نتیجه‌ای نداشت.")
            else:
                if mid_fb: delete_bale_message(chat_id, mid_fb)
                msg_list = "🎶 نتایج YT Music — روی آهنگ کلیک کنید:\n\n"
                for i, t in enumerate(results_ym, 1):
                    msg_list += f"{i}. {t['title']}\n   👤 {t['uploader']} | ⏱ {t['duration']}\n\n"
                user_ym_searches[chat_id] = results_ym
                send_bale_message(chat_id, msg_list,
                                  reply_markup=_build_ym_keyboard(results_ym, 0))
        else:
            send_bale_message(chat_id, f"❌ خطا در دانلود: {str(e)[:100]}")
        try:
            if temp_path and os.path.exists(temp_path): os.remove(temp_path)
        except: pass

# ==========================================
# --- Pinterest ---
# ==========================================
def get_pinterest_images(query, max_images=10):
    errors = []
    if PINSCRAPE_AVAILABLE:
        try:
            result = pinscrape.scraper.scrape(query,[],proxies={},threads=2,max_images=max_images)
            if result and result.get('urls'):
                urls = [u for u in result['urls'] if u.startswith('http')]
                if urls: return urls[:max_images], None
            errors.append("pinscrape: نتیجه‌ای برنگشت")
        except Exception as e: errors.append(f"pinscrape: {str(e)[:60]}")
    try:
        url = (f"https://www.pinterest.com/resource/BaseSearchResource/get/"
               f"?source_url=/search/pins/?q={quote(query)}"
               f"&data=%7B%22options%22%3A%7B%22query%22%3A%22{quote(query)}%22"
               f"%2C%22scope%22%3A%22pins%22%2C%22page_size%22%3A{max_images}%7D%7D"
               f"&_={int(time.time()*1000)}")
        resp = _safe_get(url, referer='https://www.pinterest.com/', timeout=15, json_req=True)
        if resp.status_code == 200:
            data    = resp.json()
            results = data.get('resource_response',{}).get('data',{}).get('results',[])
            images  = []
            for pin in results:
                img_data = pin.get('images',{})
                for size in ['orig','736x','474x','236x']:
                    candidate = img_data.get(size,{}).get('url','')
                    if candidate: images.append(candidate); break
                if len(images) >= max_images: break
            if images: return images, None
            errors.append("Pinterest API: نتیجه خالی")
        else: errors.append(f"Pinterest API: status {resp.status_code}")
    except Exception as e: errors.append(f"Pinterest API: {str(e)[:60]}")
    try:
        url  = f"https://www.pinterest.com/search/pins/?q={quote(query)}&rs=typed"
        resp = _safe_get(url, referer='https://www.pinterest.com/', timeout=15)
        if resp.status_code == 200:
            matches = re.findall(r'"orig":\{"url":"([^"]+)"', resp.text)
            images  = []
            seen    = set()
            for img_url in matches:
                img_url = img_url.replace('\\u002F','/').replace('\\/','/') 
                if img_url not in seen and img_url.startswith('http'):
                    seen.add(img_url); images.append(img_url)
                if len(images) >= max_images: break
            if images: return images, None
            errors.append("HTML scrape: نتیجه‌ای یافت نشد")
        else: errors.append(f"HTML scrape: status {resp.status_code}")
    except Exception as e: errors.append(f"HTML scrape: {str(e)[:60]}")
    return [], "❌ خطاهای Pinterest:\n" + "\n".join(f"• {e}" for e in errors)

# ==========================================
# --- YouTube ---
# ==========================================
user_yt_searches   = {}
user_yt_format_sel = {}
user_yt_pages      = {}
PAGE_SIZE = 8

def _yt_extract_info(url_or_query, search=False, channel=None, playlist=False, max_results=30):
    if yt_dlp is None: return None, "❌ yt-dlp نصب نیست."
    try:
        if search:
            if channel:
                ch = channel.lstrip('@')
                query    = f"https://www.youtube.com/@{ch}/search?query={url_or_query}" if url_or_query else f"https://www.youtube.com/@{ch}/videos"
                ydl_opts = {'quiet':True,'no_warnings':True,'extract_flat':True,'playlistend':max_results}
            else:
                query    = f"ytsearch{max_results}:{url_or_query}"
                ydl_opts = {'quiet':True,'no_warnings':True,'extract_flat':True}
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info    = ydl.extract_info(query, download=False)
                if not info: return None, "❌ نتیجه‌ای یافت نشد."
                entries = info.get('entries',[info])
                results = []
                for e in (entries or []):
                    if not e: continue
                    dur     = e.get('duration',0)
                    dur_str = time.strftime('%M:%S',time.gmtime(dur)) if dur else '??:??'
                    vid_url = (e.get('url') or e.get('webpage_url') or
                               (f"https://www.youtube.com/watch?v={e['id']}" if e.get('id') else ''))
                    upload_date = e.get('upload_date','')
                    if upload_date and len(upload_date)==8:
                        upload_date = f"{upload_date[:4]}/{upload_date[4:6]}/{upload_date[6:]}"
                    results.append({
                        'id':e.get('id',''),'title':e.get('title','بی‌نام'),
                        'uploader':e.get('uploader',e.get('channel','')),
                        'duration':dur_str,'url':vid_url,
                        'thumbnail':e.get('thumbnail',''),'view_count':e.get('view_count',0),
                        'upload_date':upload_date,
                    })
                return (results if results else None, None if results else "❌ نتیجه‌ای یافت نشد.")

        elif playlist:
            ch     = url_or_query.lstrip('@')
            pl_url = f"https://www.youtube.com/@{ch}/playlists"
            with yt_dlp.YoutubeDL({'quiet':True,'no_warnings':True,'extract_flat':True}) as ydl:
                info    = ydl.extract_info(pl_url, download=False)
                if not info: return None, "❌ پلی‌لیستی یافت نشد."
                entries = info.get('entries',[])
                results = []
                for e in (entries or []):
                    if not e: continue
                    results.append({
                        'id':e.get('id',''),'title':e.get('title','بی‌نام'),
                        'uploader':e.get('uploader',''),'duration':'',
                        'url':e.get('url') or e.get('webpage_url',''),
                        'thumbnail':e.get('thumbnail',''),'view_count':0,
                        'upload_date':'','type':'playlist',
                    })
                return (results if results else None, None if results else "❌ پلی‌لیستی یافت نشد.")

        else:
            with yt_dlp.YoutubeDL({'quiet':True,'no_warnings':True,'extract_flat':True}) as ydl:
                info = ydl.extract_info(url_or_query, download=False)
                if not info: return None, "❌ ویدیو پیدا نشد."
                if 'entries' in info:
                    entries = list(info.get('entries',[]))[:max_results]
                    results = []
                    for e in entries:
                        if not e: continue
                        dur     = e.get('duration',0)
                        dur_str = time.strftime('%M:%S',time.gmtime(dur)) if dur else '??:??'
                        upload_date = e.get('upload_date','')
                        if upload_date and len(upload_date)==8:
                            upload_date = f"{upload_date[:4]}/{upload_date[4:6]}/{upload_date[6:]}"
                        results.append({
                            'id':e.get('id',''),'title':e.get('title','بی‌نام'),
                            'uploader':e.get('uploader',''),'duration':dur_str,
                            'url':(e.get('url') or e.get('webpage_url') or
                                   f"https://www.youtube.com/watch?v={e.get('id','')}"),
                            'thumbnail':e.get('thumbnail',''),'view_count':0,'upload_date':upload_date,
                        })
                    return results, None
                else:
                    dur     = info.get('duration',0)
                    dur_str = time.strftime('%M:%S',time.gmtime(dur)) if dur else '??:??'
                    upload_date = info.get('upload_date','')
                    if upload_date and len(upload_date)==8:
                        upload_date = f"{upload_date[:4]}/{upload_date[4:6]}/{upload_date[6:]}"
                    return [{
                        'id':info.get('id',''),'title':info.get('title','بی‌نام'),
                        'uploader':info.get('uploader',''),'duration':dur_str,
                        'url':url_or_query,'thumbnail':info.get('thumbnail',''),
                        'view_count':0,'upload_date':upload_date,
                    }], None
    except Exception as e:
        return None, f"❌ خطا: {str(e)[:120]}"

def _yt_get_formats(url):
    """فقط فرمت‌های بدون نیاز به ffmpeg (combined audio+video یا audio-only)"""
    if yt_dlp is None: return None, "❌ yt-dlp نصب نیست."
    try:
        with yt_dlp.YoutubeDL({'quiet':True,'no_warnings':True}) as ydl:
            info    = ydl.extract_info(url, download=False)
            formats = info.get('formats',[])
            result  = []

            # بهترین صدای تنها
            audio_only = [f for f in formats if f.get('acodec','none')!='none' and f.get('vcodec','none')=='none']
            if audio_only:
                best_a  = max(audio_only, key=lambda x: x.get('abr') or x.get('tbr') or 0)
                abr     = best_a.get('abr') or best_a.get('tbr') or 0
                size_mb = (best_a.get('filesize') or best_a.get('filesize_approx') or 0)//(1024*1024)
                ext_lbl = best_a.get('ext','').upper()
                result.append({
                    'label':     f"🎵 فقط صدا — {ext_lbl} {int(abr)}kbps{f' (~{size_mb}MB)' if size_mb else ''}",
                    'format_id': best_a['format_id'], 'type': 'audio',
                })

            # ویدیو combined (audio+video یکجا) — بدون ffmpeg
            for height in [360,480,720,1080]:
                combined = [f for f in formats
                            if f.get('height')==height
                            and f.get('vcodec','none')!='none'
                            and f.get('acodec','none')!='none']
                if not combined: continue
                best    = max(combined, key=lambda x: x.get('filesize') or x.get('filesize_approx') or 0)
                size_mb = (best.get('filesize') or best.get('filesize_approx') or 0)//(1024*1024)
                result.append({
                    'label':     f"📹 {height}p با صدا{f' (~{size_mb}MB)' if size_mb else ''}",
                    'format_id': best['format_id'], 'type': 'video',
                })

            if not result:
                result.append({'label':'📹 بهترین کیفیت موجود','format_id':'best','type':'video'})
            return result, info.get('title','ویدیو')
    except Exception as e:
        return None, f"❌ خطا در دریافت فرمت‌ها: {str(e)[:100]}"

def _yt_download(url, format_id, chat_id, video_info=None):
    """دانلود یوتیوب با بررسی واقعی موفقیت ارسال + نمایش اطلاعات ویدیو"""
    max_mb    = int(get_setting('limit_yt_max_mb','200'))
    max_bytes = max_mb * 1024 * 1024
    temp_dir  = tempfile.mkdtemp(prefix="ytdl_")
    ydl_opts  = {
        'quiet':True,'no_warnings':True,'format':format_id,
        'outtmpl':os.path.join(temp_dir,'%(title)s.%(ext)s'),
        'noplaylist':True,'postprocessors':[],
        'prefer_ffmpeg':False,'ffmpeg_location':'',
    }
    msg_id = send_bale_message(chat_id, "⏳ در حال دانلود از یوتوب...")
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info     = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)

        if not os.path.exists(filename):
            files    = glob.glob(os.path.join(temp_dir,'*'))
            filename = files[0] if files else None

        if not filename or not os.path.exists(filename):
            if msg_id: delete_bale_message(chat_id, msg_id)
            send_bale_message(chat_id, "❌ فایل دانلود‌شده پیدا نشد.")
            return

        file_size = os.path.getsize(filename)
        if file_size > max_bytes:
            if msg_id: delete_bale_message(chat_id, msg_id)
            send_bale_message(chat_id, f"⚠️ حجم ({file_size//(1024*1024)}MB) بیش از {max_mb}MB است.")
            return

        # متادیتا برای فایل‌های صوتی
        ext = os.path.splitext(filename)[1].lower()
        vi  = video_info or {}
        if vi.get('title') and ext in ('.mp3','.m4a','.webm','.opus'):
            cover_data = None
            if vi.get('thumbnail'):
                try:
                    cr = requests.get(vi['thumbnail'], timeout=10)
                    if cr.status_code == 200: cover_data = cr.content
                except: pass
            embed_metadata(filename, vi.get('title',''), vi.get('uploader',''), cover_data)

        if msg_id: edit_bale_message(chat_id, msg_id, "📤 در حال آپلود...")

        # caption با اطلاعات کامل ویدیو
        title    = vi.get('title', info.get('title',''))
        uploader = vi.get('uploader', info.get('uploader',''))
        dur      = vi.get('duration','')
        date     = vi.get('upload_date','')
        caption  = f"🎬 {title}"
        if uploader: caption += f"\n👤 {uploader}"
        if dur:      caption += f" | ⏱ {dur}"
        if date:     caption += f"\n📅 {date}"

        ok = _send_file_multipart(chat_id, filename, os.path.basename(filename), caption=caption)
        if not ok:
            send_bale_message(chat_id, "❌ ارسال فایل ناموفق بود. لطفاً دوباره تلاش کنید.")
        if msg_id: delete_bale_message(chat_id, msg_id)

    except Exception as e:
        if msg_id: delete_bale_message(chat_id, msg_id)
        send_bale_message(chat_id, f"❌ خطا در دانلود یوتوب: {str(e)[:120]}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

def _yt_results_text(results, page=0):
    start       = page * PAGE_SIZE
    end         = min(start+PAGE_SIZE, len(results))
    total_pages = math.ceil(len(results)/PAGE_SIZE)
    text = f"▶️ نتایج ({len(results)} مورد) — صفحه {page+1}/{total_pages}:\n\n"
    for i in range(start, end):
        r    = results[i]
        date = f" | 📅 {r['upload_date']}" if r.get('upload_date') else ''
        icon = "📋" if r.get('type')=='playlist' else "🎬"
        text += f"{i+1}. {icon} {r['title']}\n   👤 {r['uploader']} | ⏱ {r['duration']}{date}\n\n"
    return text

def _build_yt_keyboard(results, page=0):
    start       = page * PAGE_SIZE
    end         = min(start+PAGE_SIZE, len(results))
    rows        = []
    for i in range(start, end, 2):
        row = []
        for j in range(2):
            idx = i+j
            if idx < end:
                t   = results[idx]
                lbl = f"{idx+1}. {t['title'][:18]}"
                row.append({"text":lbl,"callback_data":f"yt_{idx}"})
        rows.append(row)
    nav = []
    if page > 0: nav.append({"text":"◀️ قبلی","callback_data":f"ytpage_{page-1}"})
    total_pages = math.ceil(len(results)/PAGE_SIZE)
    nav.append({"text":f"📄 {page+1}/{total_pages}","callback_data":"noop"})
    if end < len(results): nav.append({"text":"بعدی ▶️","callback_data":f"ytpage_{page+1}"})
    if nav: rows.append(nav)
    rows.append([{"text":"❌ انصراف","callback_data":"cancel_yt"}])
    return {"inline_keyboard":rows}

def _build_yt_format_keyboard(formats, video_idx):
    rows = []
    for i, fmt in enumerate(formats):
        rows.append([{"text":fmt['label'],"callback_data":f"ytf_{video_idx}_{i}"}])
    rows.append([{"text":"❌ انصراف","callback_data":"cancel_yt"}])
    return {"inline_keyboard":rows}

# ==========================================
# --- صف (Queue) ---
# ==========================================
def add_to_queue(chat_id, task_type, payload):
    db_query(
        'INSERT INTO queue (chat_id,type,payload,status,created_at) VALUES (?,?,?,"pending",?)',
        (chat_id, task_type, json.dumps(payload), get_iran_time_now()), commit=True)
    rows = db_query('SELECT COUNT(*) FROM queue WHERE status="pending"', fetch=True)
    return rows[0][0] if rows else 1

def process_queue():
    rows = db_query(
        'SELECT id,chat_id,type,payload FROM queue WHERE status="pending" ORDER BY id ASC LIMIT 1',
        fetch=True)
    if not rows: return
    task_id, chat_id, task_type, payload_str = rows[0]
    db_query('UPDATE queue SET status="processing" WHERE id=?',(task_id,),commit=True)
    try:
        payload = json.loads(payload_str)
        if task_type == 'music':
            download_soundcloud_track(payload, chat_id)
        elif task_type == 'ym_track':
            _ym_download_track(
                payload.get('url',''), chat_id,
                title=payload.get('title','آهنگ'),
                uploader=payload.get('uploader',''),
                thumbnail=payload.get('thumbnail',''),
            )
        elif task_type == 'ym_pl_one_by_one':
            tracks   = payload.get('tracks',[])
            pl_title = payload.get('pl_title','پلی‌لیست')
            send_bale_message(chat_id,
                f"🎵 شروع ارسال {len(tracks)} آهنگ از '{pl_title}' تکی تکی...")
            for i, track in enumerate(tracks, 1):
                send_bale_message(chat_id, f"📥 {i}/{len(tracks)}: {track['title'][:40]}")
                _ym_download_track(track['url'], chat_id,
                    title=track['title'], uploader=track.get('uploader',''),
                    thumbnail=track.get('thumbnail',''))
                time.sleep(1)
            send_bale_message(chat_id, f"✅ همه {len(tracks)} آهنگ ارسال شد.")
        elif task_type == 'ym_pl_zip':
            _ym_download_all_zip(
                payload.get('tracks',[]), chat_id,
                pl_title=payload.get('pl_title','پلی‌لیست'),
            )
        elif task_type == 'web':
            url = payload.get('url','')
            send_bale_message(chat_id, "🔍 در حال استخراج مقاله...")
            article, err = extract_article(url)
            if err: send_bale_message(chat_id, err)
            else:
                send_long_message(chat_id, f"📄 {article['title']}\n\n{article['text']}")
                for img_url in article['images']:
                    send_bale_photo(chat_id, img_url)
                    time.sleep(1)
        elif task_type == 'dl':
            download_direct_file(payload.get('url',''), chat_id)
        elif task_type == 'yt_dl':
            _yt_download(
                payload.get('url',''),
                payload.get('format_id','best'),
                chat_id,
                video_info=payload.get('video_info'),
            )
        elif task_type == 'pin':
            query    = payload.get('query','')
            max_imgs = payload.get('max_images',10)
            images, err = get_pinterest_images(query, max_images=max_imgs)
            if not images: send_bale_message(chat_id, err or "❌ تصویری یافت نشد.")
            else:
                send_bale_message(chat_id, f"🖼 {len(images)} تصویر از پینترست برای '{query}':")
                for img_url in images:
                    send_bale_photo(chat_id, img_url)
                    time.sleep(0.5)
    except Exception as e:
        send_bale_message(chat_id, f"❌ خطا در پردازش: {str(e)[:80]}")
    db_query('DELETE FROM queue WHERE id=?',(task_id,),commit=True)

# ==========================================
# --- فوتبال (ESPN API) ---
# ==========================================
FOOTBALL_LEAGUES_ESPN = [
    ('eng.1','Premier League 🏴󠁧󠁢󠁥󠁮󠁧󠁿'),('eng.2','Championship 🏴󠁧󠁢󠁥󠁮󠁧󠁿'),
    ('esp.1','La Liga 🇪🇸'),('ita.1','Serie A 🇮🇹'),('ger.1','Bundesliga 🇩🇪'),
    ('fra.1','Ligue 1 🇫🇷'),('uefa.champions','Champions League 🏆'),
    ('uefa.europa','Europa League 🥈'),('uefa.europa.conf','Conference League 🥉'),
    ('fifa.world','World Cup 🌍'),
]

def _iran_day_range():
    now   = datetime.now(IRAN_TZ)
    start = now.replace(hour=0,minute=0,second=0,microsecond=0)
    end   = start + timedelta(days=1)
    return start, end, now.strftime('%Y/%m/%d')

def _utc_date_strs(start, end):
    d1 = start.astimezone(tz.utc).strftime('%Y%m%d')
    d2 = end.astimezone(tz.utc).strftime('%Y%m%d')
    return sorted(set([d1,d2]))

def _get_goal_scorers(details, team_id):
    scorers = []
    for d in details:
        if not d.get('scoringPlay'): continue
        if str(d.get('team',{}).get('id','')) != str(team_id): continue
        clock    = d.get('clock',{}).get('displayValue','')
        athletes = d.get('athletesInvolved',[])
        if athletes:
            name = athletes[0].get('shortName',athletes[0].get('displayName','?'))
            own  = d.get('ownGoal',False)
            pen  = d.get('penaltyKick',False)
            tag  = ' (og)' if own else (' (p)' if pen else '')
            scorers.append(f"{name}{tag} {clock}")
    return scorers

def _get_odds_str(comp):
    try:
        odds_list = comp.get('odds',[])
        if not odds_list: return ''
        odds = odds_list[0]
        if not odds: return ''
        ml        = odds.get('moneyline',{})
        home_odds = ml.get('home',{}).get('current',{}).get('odds','')
        away_odds = ml.get('away',{}).get('current',{}).get('odds','')
        draw_odds = ml.get('draw',{}).get('current',{}).get('odds','')
        if home_odds and away_odds:
            return f"📊 ضریب: خانه {home_odds} | مساوی {draw_odds} | مهمان {away_odds}"
    except: pass
    return ''

def get_football_today(league_key=None):
    start_iran, end_iran, today_iran = _iran_day_range()
    date_strs     = _utc_date_strs(start_iran, end_iran)
    out           = f"⚽ بازی‌های امروز — {today_iran}\n\n"
    found_any     = False
    live_statuses = ('STATUS_IN_PROGRESS','STATUS_HALFTIME','STATUS_SECOND_HALF','STATUS_FIRST_HALF','STATUS_END_PERIOD')
    final_statuses= ('STATUS_FINAL','STATUS_FULL_TIME','STATUS_FT_PENALTY','STATUS_POSTPONED','STATUS_ABANDONED')
    for league_slug, league_name in FOOTBALL_LEAGUES_ESPN:
        league_events = []
        seen_ids      = set()
        for date_str in date_strs:
            try:
                url  = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league_slug}/scoreboard?dates={date_str}"
                resp = requests.get(url, timeout=10)
                if resp.status_code != 200: continue
                for ev in resp.json().get('events',[]):
                    eid = ev.get('id','')
                    if eid in seen_ids: continue
                    raw_date = ev.get('date','')
                    try: dt_iran = datetime.fromisoformat(raw_date.replace('Z','+00:00')).astimezone(IRAN_TZ)
                    except: continue
                    comp    = ev.get('competitions',[{}])[0]
                    st      = comp.get('status',{}).get('type',{}).get('name','')
                    is_live = st in live_statuses
                    if is_live or (start_iran <= dt_iran < end_iran):
                        ev['_dt_iran'] = dt_iran
                        seen_ids.add(eid)
                        league_events.append(ev)
                time.sleep(0.2)
            except: continue
        if not league_events: continue
        found_any = True
        out += f"🏆 {league_name}\n"
        for ev in league_events:
            comp        = ev.get('competitions',[{}])[0]
            competitors = comp.get('competitors',[])
            if len(competitors) < 2: continue
            home    = next((c for c in competitors if c.get('homeAway')=='home'), competitors[0])
            away    = next((c for c in competitors if c.get('homeAway')=='away'), competitors[1])
            hn      = home.get('team',{}).get('shortDisplayName', home.get('team',{}).get('displayName','?'))
            an      = away.get('team',{}).get('shortDisplayName', away.get('team',{}).get('displayName','?'))
            hs      = home.get('score','0')
            as_     = away.get('score','0')
            st      = comp.get('status',{}).get('type',{}).get('name','')
            clk     = comp.get('status',{}).get('displayClock','')
            t_s     = ev['_dt_iran'].strftime('%H:%M')
            details = comp.get('details',[])
            home_id = home.get('team',{}).get('id','')
            away_id = away.get('team',{}).get('id','')
            if st in live_statuses:
                if st == 'STATUS_HALFTIME': out += f"🟡 {hn} {hs}–{as_} {an} | نیمه\n"
                else:                       out += f"🟢 {hn} {hs}–{as_} {an} | {clk}\n"
            elif st in final_statuses:
                if st in ('STATUS_POSTPONED','STATUS_ABANDONED'): out += f"🚫 {hn} vs {an} | لغو/تعویق\n"
                else: out += f"✅ {hn} {hs}–{as_} {an} | پایان\n"
            else: out += f"🕒 {hn} vs {an} | {t_s}\n"
            if details and st not in ('STATUS_SCHEDULED',):
                hs_  = _get_goal_scorers(details, home_id)
                as__ = _get_goal_scorers(details, away_id)
                if hs_:  out += f"   ⚽ {hn}: {', '.join(hs_)}\n"
                if as__: out += f"   ⚽ {an}: {', '.join(as__)}\n"
            if st not in live_statuses and st not in final_statuses:
                odds_str = _get_odds_str(comp)
                if odds_str: out += f"   {odds_str}\n"
        out += "\n"
    return out.strip() if found_any else f"⚽ امروز ({today_iran}) بازی‌ای یافت نشد."

# ==========================================
# --- تنیس ATP+WTA ---
# ==========================================
def _fetch_tennis_events(tour):
    start_iran, end_iran, today_iran = _iran_day_range()
    date_strs = _utc_date_strs(start_iran, end_iran)
    all_comps = []
    seen_ids  = set()
    for date_str in date_strs:
        try:
            url  = f"https://site.api.espn.com/apis/site/v2/sports/tennis/{tour}/scoreboard?dates={date_str}"
            resp = requests.get(url, timeout=15)
            if resp.status_code != 200: continue
            data = resp.json()
            for ev in data.get('events',[]):
                ev_name = ev.get('name',ev.get('shortName','نامشخص'))
                for grp_item in ev.get('groupings',[]):
                    for comp in grp_item.get('competitions',[]):
                        cid = comp.get('id','')
                        if not cid or cid in seen_ids: continue
                        raw_date = comp.get('date','') or comp.get('startDate','')
                        if not raw_date: continue
                        try: dt_iran = datetime.fromisoformat(raw_date.replace('Z','+00:00')).astimezone(IRAN_TZ)
                        except: continue
                        st      = comp.get('status',{}).get('type',{}).get('name','')
                        is_live = st == 'STATUS_IN_PROGRESS'
                        if not (is_live or (start_iran <= dt_iran < end_iran)): continue
                        comp['_ev_name'] = ev_name
                        comp['_dt_iran'] = dt_iran
                        comp['_round']   = (comp.get('round') or {}).get('displayName','')
                        notes = comp.get('notes') or []
                        comp['_note'] = notes[0].get('text','') if notes else ''
                        seen_ids.add(cid)
                        all_comps.append(comp)
        except Exception as e:
            print(f"[tennis:{tour}/{date_str}] {e}")
            continue
    return all_comps, today_iran

def _format_tennis(comps, tour_label, today_iran):
    if not comps: return [f"🎾 {tour_label} — امروز ({today_iran}) بازی یافت نشد."]
    by_ev = {}
    for comp in comps:
        ev_key  = comp['_ev_name']
        rnd_key = comp['_round']
        if ev_key not in by_ev: by_ev[ev_key] = {}
        if rnd_key not in by_ev[ev_key]: by_ev[ev_key][rnd_key] = []
        by_ev[ev_key][rnd_key].append(comp)
    messages = []
    current  = f"🎾 {tour_label} — {today_iran}\n\n"
    for ev_name, rounds in by_ev.items():
        block = f"🏟 {ev_name}\n"
        for rnd_name, matches in rounds.items():
            if rnd_name: block += f"  ┌ {rnd_name}\n"
            for comp in matches:
                try:
                    players = comp.get('competitors',[])
                    if len(players) < 2: continue
                    home   = next((p for p in players if p.get('homeAway')=='home'), players[0])
                    away   = next((p for p in players if p.get('homeAway')=='away'), players[1])
                    h_name = (home.get('athlete') or {}).get('displayName','?')
                    a_name = (away.get('athlete') or {}).get('displayName','?')
                    h_flag = (home.get('athlete') or {}).get('flag',{}).get('alt','')
                    a_flag = (away.get('athlete') or {}).get('flag',{}).get('alt','')
                    h_win  = home.get('winner',False)
                    a_win  = away.get('winner',False)
                    h_ls   = home.get('linescores') or []
                    a_ls   = away.get('linescores') or []
                    sets_str = ''
                    if h_ls and a_ls:
                        sets = []
                        for i in range(min(len(h_ls),len(a_ls))):
                            try:
                                hv = int(h_ls[i].get('value') or 0)
                                av = int(a_ls[i].get('value') or 0)
                                htb = h_ls[i].get('tiebreak','')
                                atb = a_ls[i].get('tiebreak','')
                                hs_ = f"{hv}({htb})" if htb!='' else str(hv)
                                as_ = f"{av}({atb})" if atb!='' else str(av)
                                sets.append(f"{hs_}-{as_}")
                            except: pass
                        if sets: sets_str = f" ({' | '.join(sets)})"
                    st    = comp.get('status',{}).get('type',{}).get('name','')
                    t_str = comp['_dt_iran'].strftime('%H:%M')
                    note  = comp.get('_note','')
                    h_disp = f"{h_name}({h_flag})" if h_flag else h_name
                    a_disp = f"{a_name}({a_flag})" if a_flag else a_name
                    if st == 'STATUS_IN_PROGRESS':   line = f"  🟢 {h_disp} vs {a_disp}{sets_str}\n"
                    elif st == 'STATUS_FINAL':
                        if note: line = f"  ✅ {note}\n"
                        else:
                            winner = f" → 🏆 {h_name}" if h_win else (f" → 🏆 {a_name}" if a_win else '')
                            line = f"  ✅ {h_disp} vs {a_disp}{sets_str}{winner}\n"
                    else: line = f"  🕒 {h_disp} vs {a_disp} | {t_str}\n"
                    block += line
                except Exception as e: block += f"  ⚠️ خطا: {str(e)[:40]}\n"
            block += "\n"
        if len(current)+len(block) > 3500:
            messages.append(current.strip())
            current = block
        else: current += block
    if current.strip(): messages.append(current.strip())
    return messages if messages else [f"🎾 {tour_label} — امروز ({today_iran}) بازی یافت نشد."]

def get_tennis_today():
    atp_comps, today_iran = _fetch_tennis_events('atp')
    wta_comps, _          = _fetch_tennis_events('wta')
    return _format_tennis(atp_comps,'ATP',today_iran) + _format_tennis(wta_comps,'WTA',today_iran)

# ==========================================
# --- پردازش و ارسال - سیستم گروهی ---
# ==========================================
def check_and_broadcast():
    if get_setting('telegram_active','1') != '1': return
    groups = group_list()
    today  = get_iran_date_today()
    for (gid,gname,gactive) in groups:
        if not gactive: continue
        dest    = group_get_dest(gid)
        sources = group_sources(gid)
        for (stype,username,sactive) in sources:
            if not sactive or stype != 'telegram': continue
            time.sleep(random.uniform(1.0,3.0))
            posts   = get_telegram_posts(username)
            last_id = group_get_last(gid, username)
            for p in [p for p in posts if p['id'] > last_id]:
                if len(p['text']) > 5:
                    send_long_message(dest, f"🔹 منبع: @{username}\n🕐 زمان: {p['time']}\n------------------------\n\n{p['text']}")
                    group_set_last(gid, username, p['id'])
                    db_query('INSERT INTO sent_posts VALUES (?,?)',(username,today),commit=True)
                    time.sleep(random.uniform(1.5,4.0))

def check_twitter_and_broadcast():
    if get_setting('twitter_active','1') != '1': return
    groups = group_list()
    today  = get_iran_date_today()
    for (gid,gname,gactive) in groups:
        if not gactive: continue
        dest    = group_get_dest(gid)
        sources = group_sources(gid)
        for (stype,username,sactive) in sources:
            if not sactive or stype != 'twitter': continue
            time.sleep(random.uniform(3.0,8.0))
            posts   = get_twitter_posts(username)
            last_id = group_get_last(gid, username)
            for p in sorted([p for p in posts if p['id'] > last_id], key=lambda x: x['id']):
                if len(p['text']) < 3: continue
                caption = f"🐦 توییتر: @{username}\n🕐 زمان: {get_iran_time_now()}\n------------------------\n\n{p['text']}"
                images  = p.get('images',[])
                if images:
                    send_bale_photo(dest, images[0], caption)
                    for img in images[1:3]:
                        send_bale_photo(dest, img)
                        time.sleep(0.5)
                else:
                    send_long_message(dest, caption)
                group_set_last(gid, username, p['id'])
                db_query('INSERT INTO sent_posts VALUES (?,?)',(f"tw_{username}",today),commit=True)
                time.sleep(random.uniform(2.0,5.0))

# ==========================================
# --- آمار ---
# ==========================================
def get_stats_message():
    today   = get_iran_date_today()
    total   = (db_query('SELECT COUNT(*) FROM sent_posts',fetch=True) or [[0]])[0][0]
    today_c = (db_query('SELECT COUNT(*) FROM sent_posts WHERE sent_at=?',(today,),fetch=True) or [[0]])[0][0]
    tg_st   = "✅ فعال" if get_setting('telegram_active','1')=='1' else "⏸ متوقف"
    tw_st   = "✅ فعال" if get_setting('twitter_active','1')=='1'  else "⏸ متوقف"
    tg_int  = get_tg_interval() // 60
    tw_int  = get_tw_interval() // 60
    groups  = group_list()
    features = ['sc','ym','dl','web','pin','yt','football','tennis','weather','ai']
    feat_str = " ".join(f"{'✅' if is_feature_on(f) else '❌'}{f}" for f in features)
    msg  = f"📊 آمار — نسخه {BOT_VERSION}\n"
    msg += f"• کل: {total} | امروز: {today_c}\n\n"
    msg += f"📡 تلگرام: {tg_st} (هر {tg_int} دقیقه)\n"
    msg += f"🐦 توییتر: {tw_st} (هر {tw_int} دقیقه)\n\n"
    msg += f"🔧 قابلیت‌ها: {feat_str}\n\n"
    msg += f"📋 گروه‌ها ({len(groups)}):\n"
    for (gid,gname,gactive) in groups:
        st   = "✅" if gactive else "⏸"
        dest = group_get_dest(gid)
        srcs = group_sources(gid)
        msg += f"  {st} {gname} → {dest} ({len(srcs)} منبع)\n"
    return msg

# ==========================================
# --- YouTube Music ---
# ==========================================
user_ym_searches = {}
user_ym_pl_data  = {}

def _ym_is_link(text):
    return ('music.youtube.com' in text or 'youtube.com/watch' in text or
            'youtu.be/' in text or 'youtube.com/playlist' in text)

def _ym_extract(url_or_query, search=True, max_results=15):
    """جستجو یا استخراج لینک از YT Music"""
    if yt_dlp is None: return None, "❌ yt-dlp نصب نیست."
    try:
        if search:
            # جستجو با ytsearchmusic برای نتایج موزیکی خالص
            ydl_opts = {'quiet':True,'no_warnings':True,'extract_flat':True}
            query    = f"ytsearchmusic{max_results}:{url_or_query}"
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info    = ydl.extract_info(query, download=False)
                entries = info.get('entries',[]) if info else []
                results = []
                for e in entries:
                    if not e: continue
                    # ساخت URL قابل دانلود
                    vid_id  = e.get('id','')
                    vid_url = (e.get('webpage_url') or
                               (f"https://www.youtube.com/watch?v={vid_id}" if vid_id else ''))
                    if not vid_url: continue
                    # duration
                    dur     = e.get('duration',0) or 0
                    dur_str = time.strftime('%M:%S',time.gmtime(dur)) if dur else '??:??'
                    # title و uploader
                    title    = e.get('title','') or e.get('fulltitle','')
                    uploader = e.get('uploader','') or e.get('channel','') or e.get('uploader_id','')
                    if not title: continue  # نتایج بدون عنوان رو skip کن
                    results.append({
                        'id':vid_id,'title':title,'uploader':uploader,
                        'duration':dur_str,'url':vid_url,
                        'thumbnail':e.get('thumbnail',''),'type':'track',
                    })
                    if len(results) >= max_results: break
                return (results or None, None if results else "❌ نتیجه‌ای یافت نشد.")
        else:
            ydl_opts = {'quiet':True,'no_warnings':True,'extract_flat':True}
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url_or_query, download=False)
            if not info: return None, "❌ اطلاعاتی یافت نشد."
            if 'entries' in info:
                # پلی‌لیست
                results = []
                for e in (info.get('entries',[]) or []):
                    if not e: continue
                    dur     = e.get('duration',0)
                    dur_str = time.strftime('%M:%S',time.gmtime(dur)) if dur else '??:??'
                    vid_url = (e.get('url') or e.get('webpage_url') or
                               (f"https://music.youtube.com/watch?v={e['id']}" if e.get('id') else ''))
                    results.append({
                        'id':e.get('id',''),'title':e.get('title','بی‌نام'),
                        'uploader':e.get('uploader',info.get('uploader','')),
                        'duration':dur_str,'url':vid_url,
                        'thumbnail':e.get('thumbnail',''),'type':'track',
                    })
                pl_info = {
                    'title':info.get('title','پلی‌لیست'),
                    'uploader':info.get('uploader',''),
                    'tracks':results,'url':url_or_query,
                }
                return pl_info, 'playlist'
            else:
                dur     = info.get('duration',0)
                dur_str = time.strftime('%M:%S',time.gmtime(dur)) if dur else '??:??'
                return [{
                    'id':info.get('id',''),'title':info.get('title','بی‌نام'),
                    'uploader':info.get('uploader',''),'duration':dur_str,
                    'url':url_or_query,'thumbnail':info.get('thumbnail',''),'type':'track',
                }], None
    except Exception as e:
        return None, f"❌ خطا: {str(e)[:120]}"

def _ym_download_track(track_url, chat_id, title='آهنگ', uploader='', thumbnail=''):
    """دانلود یک آهنگ از YT Music و ارسال — با پشتیبانی multipart"""
    if yt_dlp is None:
        send_bale_message(chat_id, "❌ yt-dlp نصب نیست.")
        return False
    max_mb    = int(get_setting('limit_yt_max_mb','200'))
    max_bytes = max_mb * 1024 * 1024
    temp_dir  = tempfile.mkdtemp()
    temp_msg_ids = []
    try:
        dl_mid = send_bale_message(chat_id, f"⏳ دانلود از YT Music: {title[:40]}...")
        if dl_mid: temp_msg_ids.append(dl_mid)
        ydl_opts = {
            'quiet':True,'no_warnings':True,'format':'bestaudio/best',
            'outtmpl':os.path.join(temp_dir,'%(uploader)s - %(title)s.%(ext)s'),
            'noplaylist':True,'postprocessors':[],
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(track_url, download=True)
            if not thumbnail: thumbnail = info.get('thumbnail','')
            if not uploader:  uploader  = info.get('uploader','') or info.get('channel','')
            if not title or title == 'آهنگ': title = info.get('title','آهنگ')

        files = glob.glob(os.path.join(temp_dir,'*'))
        if not files:
            for m in temp_msg_ids: delete_bale_message(chat_id, m)
            send_bale_message(chat_id, "❌ فایل دانلود‌شده پیدا نشد.")
            return False

        file_path = files[0]
        file_size = os.path.getsize(file_path)

        if file_size > max_bytes:
            for m in temp_msg_ids: delete_bale_message(chat_id, m)
            send_bale_message(chat_id, f"⚠️ حجم '{title}' ({file_size//(1024*1024)}MB) بیش از {max_mb}MB است.")
            return False

        title, uploader = _parse_title_artist(title, uploader)
        ext = os.path.splitext(file_path)[1].lower()

        if dl_mid: edit_bale_message(chat_id, dl_mid, "🎨 پردازش متادیتا...")
        cover_data = None
        if thumbnail:
            try:
                cr = requests.get(thumbnail, timeout=10)
                if cr.status_code == 200: cover_data = cr.content
            except: pass
        embed_metadata(file_path, title, uploader, cover_data)

        for m in temp_msg_ids: delete_bale_message(chat_id, m)
        temp_msg_ids = []

        # اگه فایل صوتی کوچیکه → sendAudio، وگرنه multipart
        part_size = 19 * 1024 * 1024
        if file_size <= part_size:
            stop_event = threading.Event()
            up_mid = send_bale_message(chat_id, "📤 در حال آپلود...\n[⬜⬜⬜⬜⬜⬜⬜⬜⬜⬜]")
            anim = threading.Thread(target=_animated_upload_progress,
                                    args=(chat_id,up_mid,stop_event), daemon=True)
            anim.start()
            ok = send_bale_audio(chat_id, file_path,
                                 caption=f"🎵 {title}\n👤 {uploader}\n🎶 YT Music",
                                 title=title, performer=uploader)
            stop_event.set(); anim.join(timeout=1)
            if up_mid: delete_bale_message(chat_id, up_mid)
            if not ok:
                ok2 = send_bale_document(chat_id, file_path,
                                         caption=f"🎵 {title}\n👤 {uploader}\n🎶 YT Music")
                if not ok2:
                    send_bale_message(chat_id, "❌ ارسال آهنگ ناموفق بود. لطفاً دوباره تلاش کنید.")
                    return False
        else:
            # فایل بزرگ → multipart
            _send_file_multipart(chat_id, file_path, os.path.basename(file_path),
                                 caption=f"🎵 {title}\n👤 {uploader}\n🎶 YT Music")
        return True

    except Exception as e:
        for m in temp_msg_ids: delete_bale_message(chat_id, m)
        send_bale_message(chat_id, f"❌ خطا دانلود از YT Music: {str(e)[:100]}")
        return False
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

def _build_ym_keyboard(results, page=0):
    page_size   = 10
    start       = page * page_size
    end         = min(start+page_size, len(results))
    total_pages = math.ceil(len(results)/page_size)
    rows = []
    for i in range(start, end, 2):
        row = []
        for j in range(2):
            idx = i+j
            if idx < end:
                t   = results[idx]
                lbl = f"{idx+1}. {t['title'][:18]}"
                row.append({"text":lbl,"callback_data":f"ym_{idx}"})
        rows.append(row)
    nav = []
    if page > 0: nav.append({"text":"◀️ قبلی","callback_data":f"ympage_{page-1}"})
    nav.append({"text":f"📄 {page+1}/{total_pages}","callback_data":"noop"})
    if end < len(results): nav.append({"text":"بعدی ▶️","callback_data":f"ympage_{page+1}"})
    if nav: rows.append(nav)
    rows.append([{"text":"❌ انصراف","callback_data":"cancel_ym"}])
    return {"inline_keyboard":rows}

def _build_ym_playlist_keyboard(pl_info):
    tracks = pl_info['tracks']
    total  = len(tracks)
    rows   = []
    rows.append([{"text":f"🎵 دانلود تکی تکی ({total} آهنگ)","callback_data":"ym_pl_one_by_one"}])
    rows.append([{"text":f"📦 دانلود همه به صورت zip ({total} آهنگ)","callback_data":"ym_pl_zip"}])
    if total > 30:
        chunks = math.ceil(total/30)
        for c in range(chunks):
            s = c*30; e = min(s+30, total)
            rows.append([{"text":f"📥 دانلود {s+1}–{e}","callback_data":f"ym_pl_chunk_{s}_{e}"}])
    rows.append([{"text":"❌ انصراف","callback_data":"cancel_ym"}])
    return {"inline_keyboard":rows}

def _ym_download_all_zip(tracks, chat_id, pl_title='پلی‌لیست'):
    """دانلود همه آهنگ‌ها و ارسال zip — اگه بزرگ بود تکی تکی"""
    if yt_dlp is None:
        send_bale_message(chat_id, "❌ yt-dlp نصب نیست.")
        return
    max_mb    = int(get_setting('limit_yt_max_mb','200'))
    max_bytes = max_mb * 1024 * 1024
    temp_dir  = tempfile.mkdtemp()
    dl_mid    = send_bale_message(chat_id,
                    f"⏳ دانلود {len(tracks)} آهنگ از '{pl_title}'...\nممکن است چند دقیقه طول بکشد.")
    downloaded_files = []
    try:
        for i, track in enumerate(tracks, 1):
            try:
                track_dir = os.path.join(temp_dir,'tracks')
                os.makedirs(track_dir, exist_ok=True)
                ydl_opts = {
                    'quiet':True,'no_warnings':True,'format':'bestaudio/best',
                    'outtmpl':os.path.join(track_dir,f"{i:02d} - %(title)s.%(ext)s"),
                    'noplaylist':True,'postprocessors':[],
                }
                if dl_mid:
                    edit_bale_message(chat_id, dl_mid,
                        f"⏳ دانلود {i}/{len(tracks)}: {track['title'][:35]}...")
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(track['url'], download=True)
                    thumb_url = info.get('thumbnail', track.get('thumbnail',''))
                    upl       = info.get('uploader', track.get('uploader',''))
                    ttl       = info.get('title', track.get('title','آهنگ'))
                files = sorted(glob.glob(os.path.join(track_dir,f"{i:02d}*")))
                if not files: continue
                fp  = files[-1]
                ext = os.path.splitext(fp)[1].lower()
                cover_data = None
                if thumb_url:
                    try:
                        cr = requests.get(thumb_url, timeout=8)
                        if cr.status_code == 200: cover_data = cr.content
                    except: pass
                embed_metadata(fp, ttl, upl, cover_data)
                downloaded_files.append(fp)
            except Exception as e:
                send_bale_message(chat_id, f"⚠️ خطا دانلود '{track['title'][:30]}': {str(e)[:60]}")
                continue

        if not downloaded_files:
            if dl_mid: delete_bale_message(chat_id, dl_mid)
            send_bale_message(chat_id, "❌ هیچ آهنگی دانلود نشد.")
            return

        if dl_mid: edit_bale_message(chat_id, dl_mid, "📦 در حال فشرده‌سازی...")
        safe_pl  = re.sub(r'[\\/*?:"<>|]','_',pl_title)[:40]
        zip_path = os.path.join(temp_dir, f"{safe_pl}.zip")
        with zipfile.ZipFile(zip_path,'w',zipfile.ZIP_DEFLATED) as zf:
            for fp in downloaded_files:
                zf.write(fp, os.path.basename(fp))

        if dl_mid: delete_bale_message(chat_id, dl_mid)

        up_mid = send_bale_message(chat_id, "📤 در حال آپلود zip...\n[⬜⬜⬜⬜⬜⬜⬜⬜⬜⬜]")
        stop_event = threading.Event()
        anim = threading.Thread(target=_animated_upload_progress,
                                args=(chat_id,up_mid,stop_event), daemon=True)
        anim.start()
        ok = _send_file_multipart(chat_id, zip_path, f"{safe_pl}.zip",
                                  caption=f"🎵 {pl_title}\n📦 {len(downloaded_files)} آهنگ")
        stop_event.set(); anim.join(timeout=1)
        if up_mid: delete_bale_message(chat_id, up_mid)
        if ok:
            send_bale_message(chat_id, f"✅ {len(downloaded_files)} آهنگ از '{pl_title}' ارسال شد.")
    except Exception as e:
        if dl_mid: delete_bale_message(chat_id, dl_mid)
        send_bale_message(chat_id, f"❌ خطا: {str(e)[:100]}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

# ==========================================
# --- keyboard موزیک ---
# ==========================================
def _build_music_keyboard(results):
    rows = []
    for i in range(0, len(results), 2):
        row = []
        for j in range(2):
            idx = i+j
            if idx < len(results):
                t   = results[idx]
                lbl = f"{idx+1}. {t['title'][:18]}"
                row.append({"text":lbl,"callback_data":f"sc_{idx}"})
        rows.append(row)
    rows.append([{"text":"❌ انصراف","callback_data":"cancel_search"}])
    return {"inline_keyboard":rows}

# ==========================================
# --- پردازش آپدیت‌ها ---
# ==========================================
def process_updates():
    global user_soundcloud_searches, user_yt_searches, user_yt_format_sel, user_ym_searches, user_ym_pl_data
    res    = db_query('SELECT value FROM bot_settings WHERE key="offset"', fetch=True)
    offset = int(res[0][0]) if res else 0
    try:
        updates = requests.get(
            f"{BASE_URL}/getUpdates?offset={offset+1}&timeout=20", timeout=25).json()
        if not updates.get("ok"): return

        for up in updates["result"]:
            uid = up["update_id"]
            db_query('INSERT OR REPLACE INTO bot_settings VALUES ("offset", ?)', (str(uid),), commit=True)

            if up.get("callback_query"):
                cq      = up["callback_query"]
                cq_id   = cq["id"]
                cq_data = cq.get("data", "")
                cq_chat = str(cq["message"]["chat"]["id"])
                cq_mid  = cq["message"]["message_id"]

                if cq_data == "cancel_search":
                    user_soundcloud_searches.pop(cq_chat, None)
                    answer_callback_query(cq_id, "جستجو لغو شد ✅")
                    delete_bale_message(cq_chat, cq_mid)
                elif cq_data == "cancel_ym":
                    user_ym_searches.pop(cq_chat, None)
                    user_ym_pl_data.pop(cq_chat, None)
                    answer_callback_query(cq_id, "لغو شد ✅")
                    delete_bale_message(cq_chat, cq_mid)
                elif cq_data.startswith("ympage_"):
                    page    = int(cq_data.split('_')[1])
                    results = user_ym_searches.get(cq_chat)
                    answer_callback_query(cq_id)
                    if results:
                        total_pages = math.ceil(len(results)/10)
                        start = page*10; end = min(start+10, len(results))
                        text  = f"🎶 YT Music ({len(results)} نتیجه) — صفحه {page+1}/{total_pages}:\n\n"
                        for i in range(start, end):
                            r = results[i]
                            text += f"{i+1}. {r['title']}\n   👤 {r['uploader']} | ⏱ {r['duration']}\n\n"
                        edit_bale_message(cq_chat, cq_mid, text,
                                          reply_markup=_build_ym_keyboard(results, page))
                    else:
                        send_bale_message(cq_chat, "❗ جستجو منقضی شده. دوباره امتحان کنید.")
                elif cq_data.startswith("ym_pl_"):
                    pl_data = user_ym_pl_data.get(cq_chat)
                    answer_callback_query(cq_id)
                    delete_bale_message(cq_chat, cq_mid)
                    if not pl_data:
                        send_bale_message(cq_chat, "❗ اطلاعات پلی‌لیست منقضی شده.")
                    elif cq_data == "ym_pl_one_by_one":
                        tracks = pl_data['tracks'][:30]
                        pos = add_to_queue(cq_chat, 'ym_pl_one_by_one', {
                            'tracks':tracks,'pl_title':pl_data['title']})
                        send_bale_message(cq_chat,
                            f"✅ {len(tracks)} آهنگ از '{pl_data['title']}'\n📋 در صف {pos} — تکی تکی.")
                    elif cq_data == "ym_pl_zip":
                        tracks = pl_data['tracks'][:30]
                        pos = add_to_queue(cq_chat, 'ym_pl_zip', {
                            'tracks':tracks,'pl_title':pl_data['title']})
                        send_bale_message(cq_chat,
                            f"✅ {len(tracks)} آهنگ از '{pl_data['title']}'\n📋 در صف {pos} — zip.")
                    elif cq_data.startswith("ym_pl_chunk_"):
                        parts  = cq_data.split('_')
                        s, e   = int(parts[3]), int(parts[4])
                        tracks = pl_data['tracks'][s:e]
                        pos = add_to_queue(cq_chat, 'ym_pl_zip', {
                            'tracks':tracks,'pl_title':f"{pl_data['title']} ({s+1}–{e})"})
                        send_bale_message(cq_chat,
                            f"✅ آهنگ {s+1}–{e} از '{pl_data['title']}'\n📋 در صف {pos}.")
                elif cq_data.startswith("ym_"):
                    try:
                        idx     = int(cq_data[3:])
                        results = user_ym_searches.get(cq_chat)
                        answer_callback_query(cq_id)
                        delete_bale_message(cq_chat, cq_mid)
                        if results and 0 <= idx < len(results):
                            track = results[idx]
                            pos   = add_to_queue(cq_chat, 'ym_track', {
                                'url':track['url'],'title':track['title'],
                                'uploader':track.get('uploader',''),
                                'thumbnail':track.get('thumbnail',''),
                            })
                            send_bale_message(cq_chat,
                                f"✅ {track['title']}\n👤 {track.get('uploader','')}\n📋 در صف {pos}.")
                        else:
                            send_bale_message(cq_chat, "❗ جستجو منقضی شده. دوباره امتحان کنید.")
                    except Exception as e:
                        print(f"Error ym_ callback: {e}")
                        answer_callback_query(cq_id, "خطا در پردازش")
                elif cq_data == "cancel_yt":
                    user_yt_searches.pop(cq_chat, None)
                    user_yt_format_sel.pop(cq_chat, None)
                    user_yt_pages.pop(cq_chat, None)
                    answer_callback_query(cq_id, "لغو شد ✅")
                    delete_bale_message(cq_chat, cq_mid)
                elif cq_data == "noop":
                    answer_callback_query(cq_id)
                elif cq_data.startswith("ytpage_"):
                    page    = int(cq_data.split('_')[1])
                    pg_data = user_yt_pages.get(cq_chat)
                    answer_callback_query(cq_id)
                    if pg_data:
                        results = pg_data['results']
                        user_yt_pages[cq_chat]['page'] = page
                        user_yt_searches[cq_chat]      = results
                        edit_bale_message(cq_chat, cq_mid,
                            _yt_results_text(results, page),
                            reply_markup=_build_yt_keyboard(results, page))
                    else:
                        send_bale_message(cq_chat, "❗ جستجو منقضی شده. دوباره امتحان کنید.")
                elif cq_data.startswith("yt_"):
                    idx     = int(cq_data[3:])
                    results = user_yt_searches.get(cq_chat)
                    answer_callback_query(cq_id)
                    delete_bale_message(cq_chat, cq_mid)
                    if results and 0 <= idx < len(results):
                        video = results[idx]
                        if video.get('type') == 'playlist':
                            send_bale_message(cq_chat, f"⏳ دریافت ویدیوهای پلی‌لیست '{video['title']}'...")
                            pl_results, pl_err = _yt_extract_info(video['url'], search=False, max_results=30)
                            if pl_err or not pl_results:
                                send_bale_message(cq_chat, pl_err or "❌ ویدیویی یافت نشد.")
                            else:
                                user_yt_searches[cq_chat] = pl_results
                                user_yt_pages[cq_chat]    = {'results': pl_results, 'page': 0}
                                send_bale_message(cq_chat,
                                    _yt_results_text(pl_results, 0),
                                    reply_markup=_build_yt_keyboard(pl_results, 0))
                        else:
                            send_bale_message(cq_chat, f"⏳ دریافت فرمت‌های '{video['title']}'...")
                            formats, title_or_err = _yt_get_formats(video['url'])
                            if formats is None:
                                send_bale_message(cq_chat, f"❌ {title_or_err}")
                            else:
                                user_yt_format_sel[cq_chat] = {'video': video, 'formats': formats}
                                date_str = f"\n📅 {video['upload_date']}" if video.get('upload_date') else ''
                                send_bale_message(cq_chat,
                                    f"🎬 {video['title']}\n"
                                    f"👤 {video['uploader']} | ⏱ {video['duration']}{date_str}\n"
                                    f"فرمت دانلود را انتخاب کنید:",
                                    reply_markup=_build_yt_format_keyboard(formats, idx))
                    else:
                        send_bale_message(cq_chat, "❗ جستجو منقضی شده. دوباره امتحان کنید.")
                elif cq_data.startswith("ytf_"):
                    parts   = cq_data.split('_')
                    fmt_idx = int(parts[2]) if len(parts) > 2 else 0
                    sel     = user_yt_format_sel.pop(cq_chat, None)
                    user_yt_searches.pop(cq_chat, None)
                    user_yt_pages.pop(cq_chat, None)
                    answer_callback_query(cq_id)
                    delete_bale_message(cq_chat, cq_mid)
                    if sel and 0 <= fmt_idx < len(sel['formats']):
                        video = sel['video']
                        fmt   = sel['formats'][fmt_idx]
                        pos   = add_to_queue(cq_chat, 'yt_dl', {
                            'url':       video['url'],
                            'format_id': fmt['format_id'],
                            'video_info': {
                                'title':       video.get('title', ''),
                                'uploader':    video.get('uploader', ''),
                                'duration':    video.get('duration', ''),
                                'upload_date': video.get('upload_date', ''),
                                'thumbnail':   video.get('thumbnail', ''),
                            },
                        })
                        send_bale_message(cq_chat,
                            f"✅ {video['title']}\n"
                            f"📋 فرمت: {fmt['label']}\n"
                            f"📋 در صف شماره {pos}.")
                    else:
                        send_bale_message(cq_chat, "❗ انتخاب نامعتبر.")
                elif cq_data.startswith("sc_"):
                    try:
                        idx     = int(cq_data[3:])
                        results = user_soundcloud_searches.get(cq_chat)
                        answer_callback_query(cq_id)
                        delete_bale_message(cq_chat, cq_mid)
                        if results and 0 <= idx < len(results):
                            track = results[idx]
                            pos   = add_to_queue(cq_chat, 'music', {
                                'webpage_url': track['webpage_url'],
                                'title':       track['title'],
                                'uploader':    track.get('uploader', ''),
                                'thumbnail':   track.get('thumbnail', ''),
                            })
                            send_bale_message(cq_chat,
                                f"✅ آهنگ انتخاب شد:\n"
                                f"🎵 {track['title']}\n"
                                f"👤 {track.get('uploader', '')}\n"
                                f"📋 در صف شماره {pos}.")
                        else:
                            send_bale_message(cq_chat, "❗ جستجو منقضی شده. دوباره جستجو کنید.")
                    except Exception as e:
                        print(f"Error handling sc_ callback: {e}")
                        answer_callback_query(cq_id, "خطا در پردازش")
                else:
                    answer_callback_query(cq_id)
                continue

            msg     = up.get("message") or up.get("channel_post") or {}
            chat_id = str(msg.get("chat", {}).get("id", ""))
            text    = msg.get("text", "").strip()
            if not chat_id or not text: continue

            try:
                if text == "/help" or text.startswith("/help "):
                    lines = [f"🤖 ربات بله — نسخه {BOT_VERSION}\n"]
                    if is_feature_on('web'):      lines.append("🌐 /web لینک — استخراج مقاله")
                    if is_feature_on('dl'):       lines.append("📥 /dl لینک — دانلود فایل (چند پارتی تا 200MB)")
                    if is_feature_on('sc'):       lines.append("🎵 /sc نام آهنگ — جستجو در ساندکلاد")
                    if is_feature_on('ym'):       lines.append("🎶 /ym نام آهنگ — جستجو در YT Music\n   /ym لینک — دانلود آهنگ یا پلی‌لیست")
                    if is_feature_on('yt'):       lines.append("▶️ /yt query — جستجو و دانلود یوتوب\n   /ytc @channel query — جستجو در کانال")
                    if is_feature_on('football'): lines.append("⚽ /football — بازی‌های فوتبال امروز")
                    if is_feature_on('tennis'):   lines.append("🎾 /tennis — بازی‌های ATP/WTA امروز")
                    if is_feature_on('pin'):      lines.append("🖼 /pin موضوع — جستجوی تصویر در پینترست")
                    if is_feature_on('weather'):  lines.append("🌤 /city شهر — وضعیت آب و هوا")
                    if is_feature_on('ai'):       lines.append("🤖 ربات سوال — پرسش از هوش مصنوعی")
                    send_bale_message(chat_id, "\n".join(lines))

                elif text.startswith("/web ") and is_feature_on('web'):
                    url_to = text[5:].strip()
                    if not url_to.startswith(('http://','https://')): url_to = 'https://' + url_to
                    pos = add_to_queue(chat_id, 'web', {'url': url_to})
                    send_bale_message(chat_id, f"✅ درخواست وب در صف شماره {pos}.")

                elif text.startswith("/dl ") and is_feature_on('dl'):
                    pos = add_to_queue(chat_id, 'dl', {'url': text[4:].strip()})
                    send_bale_message(chat_id, f"✅ دانلود در صف شماره {pos}.")

                elif text.startswith("/sc ") and is_feature_on('sc'):
                    query = text[4:].strip()
                    if not query:
                        send_bale_message(chat_id, "❗ نام آهنگ را وارد کنید.")
                    else:
                        send_bale_message(chat_id, f"🔎 در حال جستجوی '{query}'...")
                        results, err = search_soundcloud(query, max_results=10)
                        if err:
                            send_bale_message(chat_id, err)
                        else:
                            msg_list = "🎧 نتایج جستجو — روی آهنگ کلیک کنید:\n\n"
                            for i, t in enumerate(results, 1):
                                dur = time.strftime('%M:%S', time.gmtime(t['duration'])) if t['duration'] else '??:??'
                                msg_list += f"{i}. {t['title']}\n   👤 {t['uploader']} | ⏱ {dur}\n\n"
                            user_soundcloud_searches[chat_id] = results
                            send_bale_message(chat_id, msg_list, reply_markup=_build_music_keyboard(results))

                elif text.startswith("/ym ") and is_feature_on('ym'):
                    if yt_dlp is None:
                        send_bale_message(chat_id, "❌ yt-dlp نصب نیست.")
                    else:
                        query   = text[4:].strip()
                        is_link = _ym_is_link(query)
                        if is_link:
                            send_bale_message(chat_id, "⏳ در حال دریافت اطلاعات...")
                            result, flag = _ym_extract(query, search=False)
                            if flag == 'playlist':
                                pl_info = result
                                total   = len(pl_info['tracks'])
                                user_ym_pl_data[chat_id] = pl_info
                                preview = "\n".join(
                                    f"{i+1}. {t['title']} ({t['duration']})"
                                    for i,t in enumerate(pl_info['tracks'][:15]))
                                if total > 15: preview += f"\n... و {total-15} آهنگ دیگر"
                                send_bale_message(chat_id,
                                    f"📋 پلی‌لیست: {pl_info['title']}\n"
                                    f"👤 {pl_info['uploader']}\n"
                                    f"🎵 {total} آهنگ\n\n{preview}",
                                    reply_markup=_build_ym_playlist_keyboard(pl_info))
                            elif result is None:
                                send_bale_message(chat_id, flag or "❌ نتیجه‌ای یافت نشد.")
                            else:
                                track = result[0]
                                pos   = add_to_queue(chat_id, 'ym_track', {
                                    'url':track['url'],'title':track['title'],
                                    'uploader':track.get('uploader',''),
                                    'thumbnail':track.get('thumbnail',''),
                                })
                                send_bale_message(chat_id,
                                    f"✅ {track['title']}\n👤 {track.get('uploader','')}\n📋 در صف {pos}.")
                        else:
                            send_bale_message(chat_id, f"🔎 جستجو در YT Music: '{query}'...")
                            results, err = _ym_extract(query, search=True, max_results=15)
                            if err or not results:
                                send_bale_message(chat_id, err or "❌ نتیجه‌ای یافت نشد.")
                            else:
                                user_ym_searches[chat_id] = results
                                total_pages = math.ceil(len(results)/10)
                                text_out = f"🎶 YT Music ({len(results)} نتیجه) — صفحه 1/{total_pages}:\n\n"
                                for i, t in enumerate(results[:10], 1):
                                    text_out += f"{i}. {t['title']}\n   👤 {t['uploader']} | ⏱ {t['duration']}\n\n"
                                send_bale_message(chat_id, text_out,
                                                  reply_markup=_build_ym_keyboard(results, 0))

                elif (text.startswith("/yt ") or text.startswith("/ytc ") or text.startswith("/ytp ")) and is_feature_on('yt'):
                    if yt_dlp is None:
                        send_bale_message(chat_id, "❌ yt-dlp نصب نیست.")

                    elif text.startswith("/ytc "):
                        parts = text[5:].strip().split(maxsplit=1)
                        ch    = parts[0]
                        q     = parts[1] if len(parts) > 1 else ''
                        label = f"جستجوی '{q}' در {ch}" if q else f"آخرین ویدیوهای {ch}"
                        send_bale_message(chat_id, f"🔎 {label}...")
                        results, err = _yt_extract_info(q, search=True, channel=ch, max_results=30)
                        if err or not results:
                            send_bale_message(chat_id, err or "❌ نتیجه‌ای یافت نشد.")
                        else:
                            user_yt_searches[chat_id] = results
                            user_yt_pages[chat_id]    = {'results': results, 'page': 0}
                            send_bale_message(chat_id,
                                _yt_results_text(results, 0),
                                reply_markup=_build_yt_keyboard(results, 0))

                    elif text.startswith("/ytp "):
                        ch = text[5:].strip()
                        send_bale_message(chat_id, f"🔎 دریافت پلی‌لیست‌های {ch}...")
                        results, err = _yt_extract_info(ch, playlist=True)
                        if err or not results:
                            send_bale_message(chat_id, err or "❌ پلی‌لیستی یافت نشد.")
                        else:
                            user_yt_searches[chat_id] = results
                            user_yt_pages[chat_id]    = {'results': results, 'page': 0, 'type': 'playlist'}
                            send_bale_message(chat_id,
                                _yt_results_text(results, 0),
                                reply_markup=_build_yt_keyboard(results, 0))

                    elif text.startswith("/yt "):
                        query   = text[4:].strip()
                        is_link = query.startswith('http') or 'youtube.com' in query or 'youtu.be' in query
                        if is_link:
                            send_bale_message(chat_id, "⏳ در حال دریافت اطلاعات ویدیو...")
                            results, err = _yt_extract_info(query, search=False)
                        else:
                            send_bale_message(chat_id, f"🔎 جستجوی '{query}' در یوتوب...")
                            results, err = _yt_extract_info(query, search=True, max_results=30)

                        if err or not results:
                            send_bale_message(chat_id, err or "❌ نتیجه‌ای یافت نشد.")
                        elif len(results) == 1:
                            video = results[0]
                            fmts, title_or_err = _yt_get_formats(video['url'])
                            if fmts is None:
                                send_bale_message(chat_id, f"❌ {title_or_err}")
                            else:
                                user_yt_format_sel[chat_id] = {'video': video, 'formats': fmts}
                                date_str = f"\n📅 {video['upload_date']}" if video.get('upload_date') else ''
                                send_bale_message(chat_id,
                                    f"🎬 {video['title']}\n"
                                    f"👤 {video['uploader']} | ⏱ {video['duration']}{date_str}\n"
                                    f"فرمت دانلود را انتخاب کنید:",
                                    reply_markup=_build_yt_format_keyboard(fmts, 0))
                        else:
                            user_yt_searches[chat_id] = results
                            user_yt_pages[chat_id]    = {'results': results, 'page': 0}
                            send_bale_message(chat_id,
                                _yt_results_text(results, 0),
                                reply_markup=_build_yt_keyboard(results, 0))

                elif text.startswith("/football") and is_feature_on('football'):
                    send_bale_message(chat_id, get_football_today())

                elif text == "/tennis" and is_feature_on('tennis'):
                    msgs = get_tennis_today()
                    for m in msgs:
                        send_bale_message(chat_id, m)
                        time.sleep(0.5)

                elif text.startswith("/pin ") and is_feature_on('pin'):
                    query = text[5:].strip()
                    if not query:
                        send_bale_message(chat_id, "❗ موضوع جستجو را وارد کنید.\nمثال: /pin messi")
                    else:
                        pos = add_to_queue(chat_id, 'pin', {'query': query, 'max_images': 10})
                        send_bale_message(chat_id, f"✅ جستجوی پینترست در صف شماره {pos}.")

                elif text.startswith("/city") and is_feature_on('weather'):
                    city = text.replace("/city","").strip() or "Tehran"
                    send_bale_message(chat_id, get_detailed_weather(city))

                elif text.startswith("ربات") and is_feature_on('ai'):
                    prompt = text.replace("ربات","",1).strip()
                    if prompt: send_long_message(chat_id, ask_gemini(prompt))

                elif text == "/start":
                    send_bale_message(chat_id, f"ربات فعال است. نسخه {BOT_VERSION}\n/help برای راهنما.")

                elif chat_id == ADMIN_ID:
                    if text == "/ad":
                        tg_int = get_tg_interval() // 60
                        tw_int = get_tw_interval() // 60
                        features = ['sc','ym','dl','web','pin','yt','football','tennis','weather','ai']
                        feat_str = " ".join(f"{'✅' if is_feature_on(f) else '❌'}{f}" for f in features)
                        msg_text  = f"🛠 راهنمای ادمین — نسخه {BOT_VERSION}\n\n"
                        msg_text += "── مدیریت گروه‌ها ──\n"
                        msg_text += "/newgroup name — ساخت گروه جدید\n"
                        msg_text += "/addtogroup name tg|tw username — افزودن منبع\n"
                        msg_text += "/removefrom name username — حذف منبع\n"
                        msg_text += "/setdest name channel_id — تنظیم مقصد\n"
                        msg_text += "/stopgroup name — خاموش کردن گروه\n"
                        msg_text += "/startgroup name — روشن کردن گروه\n"
                        msg_text += "/groups — لیست گروه‌ها\n"
                        msg_text += "/groupinfo name — جزئیات گروه\n\n"
                        msg_text += "── کنترل کلی ──\n"
                        msg_text += "/check /checktg /checktw\n"
                        msg_text += "/stoptg | /starttg | /stoptw | /starttw\n\n"
                        msg_text += "── قابلیت‌ها ──\n"
                        msg_text += f"وضعیت: {feat_str}\n"
                        msg_text += "/feature on|off name\n"
                        msg_text += "  مقادیر: sc dl web pin yt football tennis weather ai\n\n"
                        msg_text += "── محدودیت‌ها ──\n"
                        msg_text += f"/setlimit dl_max_mb {get_setting('limit_dl_max_mb','20')}\n"
                        msg_text += f"/setlimit multipart_max_mb {get_setting('limit_multipart_max_mb','200')}\n"
                        msg_text += f"/setlimit sc_max_mb {get_setting('limit_sc_max_mb','20')}\n"
                        msg_text += f"/setlimit yt_max_mb {get_setting('limit_yt_max_mb','200')}\n\n"
                        msg_text += "── تنظیمات ──\n"
                        msg_text += "/setchannel ID\n"
                        msg_text += f"/settimertg دقیقه (الان: {tg_int})\n"
                        msg_text += f"/settimertw دقیقه (الان: {tw_int})\n"
                        msg_text += "/stats\n\n"
                        msg_text += "── دستورات قدیمی ──\n"
                        msg_text += "/add /del /list /addtw /deltw /listtw"
                        send_bale_message(chat_id, msg_text)

                    elif text.startswith("/feature "):
                        parts = text.split()
                        if len(parts) >= 3:
                            action = parts[1].lower()
                            fname  = parts[2].lower()
                            valid  = ['sc','ym','dl','web','pin','yt','football','tennis','weather','ai']
                            if fname not in valid:
                                send_bale_message(chat_id, f"❗ قابلیت نامعتبر. مقادیر مجاز:\n{', '.join(valid)}")
                            elif action not in ('on','off'):
                                send_bale_message(chat_id, "❗ فرمت: /feature on|off name")
                            else:
                                set_setting(f'feature_{fname}', '1' if action=='on' else '0')
                                st = "✅ فعال" if action=='on' else "⏸ غیرفعال"
                                send_bale_message(chat_id, f"{st}: {fname}")
                        else:
                            send_bale_message(chat_id, "❗ فرمت: /feature on|off name")

                    elif text.startswith("/setlimit "):
                        parts = text.split()
                        if len(parts) >= 3:
                            limit_name = parts[1].lower()
                            try:
                                value = int(parts[2])
                                if value < 1: raise ValueError
                                set_setting(f'limit_{limit_name}', str(value))
                                send_bale_message(chat_id, f"✅ محدودیت {limit_name} به {value}MB تغییر کرد.")
                            except:
                                send_bale_message(chat_id, "❗ مقدار باید عدد صحیح مثبت باشد.")
                        else:
                            send_bale_message(chat_id, "❗ فرمت: /setlimit name value")

                    elif text.startswith("/newgroup "):
                        name = text.split(maxsplit=1)[1].strip()
                        if group_get(name):
                            send_bale_message(chat_id, f"⚠️ گروه '{name}' از قبل وجود دارد.")
                        else:
                            group_create(name)
                            g = group_get(name)
                            if g: group_set_dest(g[0], get_target_channel())
                            send_bale_message(chat_id, f"✅ گروه '{name}' ساخته شد.\nمقصد: {get_target_channel()}")

                    elif text.startswith("/addtogroup "):
                        parts = text.split()
                        if len(parts) >= 4:
                            gname = parts[1]; stype = parts[2].lower(); uname = parts[3].replace("@","")
                            if stype not in ('tg','tw','telegram','twitter'):
                                send_bale_message(chat_id, "❗ نوع باید tg یا tw باشد.")
                            else:
                                stype = 'telegram' if stype in ('tg','telegram') else 'twitter'
                                g = group_get(gname)
                                if not g: send_bale_message(chat_id, f"❌ گروه '{gname}' پیدا نشد.")
                                else:
                                    group_add_source(g[0], stype, uname)
                                    send_bale_message(chat_id, f"✅ @{uname} ({stype}) به '{gname}' اضافه شد.")
                        else:
                            send_bale_message(chat_id, "❗ فرمت: /addtogroup name tg|tw username")

                    elif text.startswith("/removefrom "):
                        parts = text.split()
                        if len(parts) >= 3:
                            g = group_get(parts[1])
                            if not g: send_bale_message(chat_id, f"❌ گروه '{parts[1]}' پیدا نشد.")
                            else:
                                group_remove_source(g[0], parts[2].replace("@",""))
                                send_bale_message(chat_id, "✅ حذف شد.")
                        else:
                            send_bale_message(chat_id, "❗ فرمت: /removefrom name username")

                    elif text.startswith("/setdest "):
                        parts = text.split()
                        if len(parts) >= 3:
                            g = group_get(parts[1])
                            if not g: send_bale_message(chat_id, f"❌ گروه '{parts[1]}' پیدا نشد.")
                            else:
                                group_set_dest(g[0], parts[2])
                                send_bale_message(chat_id, f"✅ مقصد گروه '{parts[1]}' به {parts[2]} تغییر کرد.")
                        else:
                            send_bale_message(chat_id, "❗ فرمت: /setdest name channel_id")

                    elif text.startswith("/stopgroup "):
                        gname = text.split(maxsplit=1)[1].strip()
                        g     = group_get(gname)
                        if not g: send_bale_message(chat_id, f"❌ گروه '{gname}' پیدا نشد.")
                        else:
                            group_set_active(gname, 0)
                            send_bale_message(chat_id, f"⏸ گروه '{gname}' خاموش شد.")

                    elif text.startswith("/startgroup "):
                        gname = text.split(maxsplit=1)[1].strip()
                        g     = group_get(gname)
                        if not g: send_bale_message(chat_id, f"❌ گروه '{gname}' پیدا نشد.")
                        else:
                            group_set_active(gname, 1)
                            send_bale_message(chat_id, f"▶️ گروه '{gname}' فعال شد.")

                    elif text == "/groups":
                        groups = group_list()
                        if not groups:
                            send_bale_message(chat_id, "📭 هیچ گروهی وجود ندارد.")
                        else:
                            msg_text = f"📋 گروه‌ها ({len(groups)}):\n\n"
                            for (gid, gname, gactive) in groups:
                                st   = "✅" if gactive else "⏸"
                                dest = group_get_dest(gid)
                                srcs = group_sources(gid)
                                msg_text += f"{st} {gname} → {dest} ({len(srcs)} منبع)\n"
                            send_bale_message(chat_id, msg_text)

                    elif text.startswith("/groupinfo "):
                        send_bale_message(chat_id, group_info_text(text.split(maxsplit=1)[1].strip()))

                    elif text == "/check":
                        send_bale_message(chat_id, "⏳ بررسی همه منابع...")
                        check_and_broadcast()
                        check_twitter_and_broadcast()
                        send_bale_message(chat_id, "✅ تمام شد.")

                    elif text == "/checktg":
                        send_bale_message(chat_id, "⏳ بررسی تلگرام...")
                        check_and_broadcast()
                        send_bale_message(chat_id, "✅ تمام شد.")

                    elif text == "/checktw":
                        send_bale_message(chat_id, "⏳ بررسی توییتر...")
                        check_twitter_and_broadcast()
                        send_bale_message(chat_id, "✅ تمام شد.")

                    elif text == "/stoptg":
                        set_setting('telegram_active','0')
                        send_bale_message(chat_id, "⏸ اسکن تلگرام متوقف شد.")

                    elif text == "/starttg":
                        set_setting('telegram_active','1')
                        send_bale_message(chat_id, "▶️ اسکن تلگرام فعال شد.")

                    elif text == "/stoptw":
                        set_setting('twitter_active','0')
                        send_bale_message(chat_id, "⏸ اسکن توییتر متوقف شد.")

                    elif text == "/starttw":
                        set_setting('twitter_active','1')
                        send_bale_message(chat_id, "▶️ اسکن توییتر فعال شد.")

                    elif text.startswith("/settimertg "):
                        try:
                            mins = int(text.split()[1])
                            if mins < 5: send_bale_message(chat_id, "❗ حداقل ۵ دقیقه.")
                            else:
                                set_setting('telegram_interval', str(mins*60))
                                send_bale_message(chat_id, f"✅ تایمر تلگرام به {mins} دقیقه تغییر کرد.")
                        except: send_bale_message(chat_id, "❗ فرمت: /settimertg 30")

                    elif text.startswith("/settimertw "):
                        try:
                            mins = int(text.split()[1])
                            if mins < 5: send_bale_message(chat_id, "❗ حداقل ۵ دقیقه.")
                            else:
                                set_setting('twitter_interval', str(mins*60))
                                send_bale_message(chat_id, f"✅ تایمر توییتر به {mins} دقیقه تغییر کرد.")
                        except: send_bale_message(chat_id, "❗ فرمت: /settimertw 180")

                    elif text.startswith("/setchannel "):
                        new_ch = text.split(maxsplit=1)[1].strip()
                        set_setting('target_channel_id', new_ch)
                        send_bale_message(chat_id, f"✅ کانال پیش‌فرض به {new_ch} تغییر کرد.")

                    elif text == "/stats":
                        send_bale_message(chat_id, get_stats_message())

                    elif text.startswith("/add "):
                        ch = text.split()[1].replace("@","")
                        g  = group_get('default_telegram')
                        if not g:
                            group_create('default_telegram')
                            g = group_get('default_telegram')
                            if g: group_set_dest(g[0], get_target_channel())
                        if g: group_add_source(g[0], 'telegram', ch)
                        send_bale_message(chat_id, f"✅ کانال @{ch} به default_telegram اضافه شد.")

                    elif text.startswith("/del ") or text.startswith("/remove "):
                        ch = text.split()[1].replace("@","")
                        g  = group_get('default_telegram')
                        if g: group_remove_source(g[0], ch)
                        send_bale_message(chat_id, f"🗑 کانال @{ch} حذف شد.")

                    elif text == "/list":
                        g = group_get('default_telegram')
                        if g:
                            srcs = [(u,a) for (t,u,a) in group_sources(g[0]) if t=='telegram']
                            if srcs:
                                send_bale_message(chat_id, "📋 کانال‌های تلگرام:\n\n" +
                                    "\n".join(f"{i}. @{u} {'✅' if a else '⏸'}" for i,(u,a) in enumerate(srcs,1)))
                            else: send_bale_message(chat_id, "📭 لیست خالی است.")
                        else: send_bale_message(chat_id, "📭 گروه default_telegram وجود ندارد.")

                    elif text.startswith("/addtw "):
                        tw = text.split()[1].replace("@","")
                        g  = group_get('default_twitter')
                        if not g:
                            group_create('default_twitter')
                            g = group_get('default_twitter')
                            if g: group_set_dest(g[0], get_target_channel())
                        if g: group_add_source(g[0], 'twitter', tw)
                        send_bale_message(chat_id, f"✅ اکانت توییتر @{tw} به default_twitter اضافه شد.")

                    elif text.startswith("/deltw "):
                        tw = text.split()[1].replace("@","")
                        g  = group_get('default_twitter')
                        if g: group_remove_source(g[0], tw)
                        send_bale_message(chat_id, f"🗑 اکانت توییتر @{tw} حذف شد.")

                    elif text == "/listtw":
                        g = group_get('default_twitter')
                        if g:
                            srcs = [(u,a) for (t,u,a) in group_sources(g[0]) if t=='twitter']
                            if srcs:
                                send_bale_message(chat_id, "🐦 اکانت‌های توییتر:\n\n" +
                                    "\n".join(f"{i}. @{u} {'✅' if a else '⏸'}" for i,(u,a) in enumerate(srcs,1)))
                            else: send_bale_message(chat_id, "📭 لیست خالی است.")
                        else: send_bale_message(chat_id, "📭 گروه default_twitter وجود ندارد.")

            except Exception as e:
                print(f"[WARNING] Error processing update {uid}: {e}")

    except Exception as e:
        print(f"[ERROR] getUpdates failed: {e}")

# ==========================================
# --- حلقه اصلی ---
# ==========================================
if __name__ == "__main__":
    init_db()
    print(f"🚀 ربات نسخه {BOT_VERSION} راه‌اندازی شد...")
    last_tg = 0
    last_tw = 0
    while True:
        try:
            process_updates()
            process_queue()
            now = time.time()
            if get_setting('telegram_active','1') == '1' and now - last_tg >= get_tg_interval():
                check_and_broadcast()
                last_tg = now + random.uniform(0, 120)
            if get_setting('twitter_active','1') == '1' and now - last_tw >= get_tw_interval():
                check_twitter_and_broadcast()
                last_tw = now + random.uniform(0, 300)
            time.sleep(random.uniform(4, 7))
        except Exception as e:
            print(f"[FATAL] {e}")
            time.sleep(random.uniform(8, 15))
 
