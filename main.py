#!/usr/bin/env python3
"""
ULTIMATE UPTIME BOT - Python 3.14 Compatible
Render & Railway Ready
"""

# ===================== CONFIGURATION =====================
BOT_TOKEN = "8307741402:AAEebnm5-vk9g2i9m9Vij0TxyMGXr_jKQpI"  # আপনার বট টোকেন
ADMIN_IDS = [6417430059]  # আপনার টেলিগ্রাম আইডি
MAX_THREADS = 250
MAX_RPS = 1500
DAILY_CREDITS = 5
# ========================================================

import os
import sys
import asyncio
import threading
import time
import json
import logging
import random
import traceback
from datetime import datetime, timedelta
from typing import Dict, Optional, List
from dataclasses import dataclass, asdict
from concurrent.futures import ThreadPoolExecutor

# Disable warnings
import warnings
warnings.filterwarnings('ignore')
os.environ['PYTHONWARNINGS'] = 'ignore'
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Install dependencies if missing
def install_deps():
    try:
        import aiohttp
        import telegram
    except ImportError:
        os.system('pip install aiohttp==3.9.0 python-telegram-bot==21.9 urllib3==2.1.0 -q --no-cache-dir')

install_deps()

# Import dependencies
import aiohttp
from aiohttp import ClientTimeout, TCPConnector
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters

# ===================== DATA MANAGER =====================
DATA_FILE = 'bot_data.json'

@dataclass
class UserData:
    user_id: int
    username: str = ""
    first_name: str = ""
    total_visits: int = 0
    daily_visits: int = 0
    last_visit_date: str = ""
    credits: int = DAILY_CREDITS
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    total_time: float = 0
    sessions: int = 0
    last_url: str = ""
    last_rps: int = 0
    is_admin: bool = False
    premium_until: str = ""
    created_at: str = ""
    
    def to_dict(self):
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data):
        return cls(**data)

class DataManager:
    def __init__(self):
        self.users: Dict[int, UserData] = {}
        self.running_sessions: Dict[int, Dict] = {}
        self.running_tasks: Dict[int, asyncio.Task] = {}
        self.lock = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=MAX_THREADS)
        self.load_data()
        logger.info(f"DataManager initialized with {len(self.users)} users")
    
    def load_data(self):
        try:
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for user_id, user_data in data.get('users', {}).items():
                        self.users[int(user_id)] = UserData.from_dict(user_data)
                logger.info(f"Loaded {len(self.users)} users")
        except Exception as e:
            logger.error(f"Error loading data: {e}")
    
    def save_data(self):
        try:
            with self.lock:
                data = {
                    'users': {str(uid): user.to_dict() for uid, user in self.users.items()}
                }
                with open(DATA_FILE, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error saving data: {e}")
    
    def get_user(self, user_id: int, username: str = "", first_name: str = "") -> UserData:
        with self.lock:
            if user_id not in self.users:
                is_admin = user_id in ADMIN_IDS
                user = UserData(
                    user_id=user_id,
                    username=username,
                    first_name=first_name or str(user_id),
                    credits=99999 if is_admin else DAILY_CREDITS,
                    is_admin=is_admin,
                    created_at=datetime.now().isoformat()
                )
                self.users[user_id] = user
                self.save_data()
                logger.info(f"New user: {user_id}")
            else:
                user = self.users[user_id]
                if username:
                    user.username = username
                if first_name:
                    user.first_name = first_name
                
                today = datetime.now().strftime('%Y-%m-%d')
                if user.last_visit_date != today:
                    user.daily_visits = 0
                    user.last_visit_date = today
                    if not user.is_admin and not user.premium_until:
                        user.credits = DAILY_CREDITS
                    self.save_data()
            return user
    
    def use_credit(self, user_id: int) -> bool:
        user = self.get_user(user_id)
        if user.is_admin:
            return True
        if user.premium_until:
            try:
                if datetime.fromisoformat(user.premium_until) > datetime.now():
                    return True
            except:
                pass
        if user.credits > 0:
            user.credits -= 1
            user.daily_visits += 1
            self.save_data()
            return True
        return False
    
    def add_credits(self, user_id: int, amount: int) -> bool:
        if user_id not in self.users:
            return False
        self.users[user_id].credits += amount
        self.save_data()
        return True
    
    def set_premium(self, user_id: int, days: int) -> bool:
        if user_id not in self.users:
            return False
        self.users[user_id].premium_until = (datetime.now() + timedelta(days=days)).isoformat()
        self.save_data()
        return True
    
    def update_stats(self, user_id: int, total: int, success: int, failed: int, duration: float, url: str, rps: int):
        user = self.get_user(user_id)
        user.total_requests += total
        user.successful_requests += success
        user.failed_requests += failed
        user.total_time += duration
        user.sessions += 1
        user.last_url = url
        user.last_rps = rps
        self.save_data()
    
    def is_running(self, user_id: int) -> bool:
        return user_id in self.running_sessions and self.running_sessions[user_id].get('running', False)
    
    def start_session(self, user_id: int, url: str, rps: int):
        self.running_sessions[user_id] = {
            'url': url,
            'rps': rps,
            'running': True,
            'start_time': datetime.now()
        }
    
    def stop_session(self, user_id: int):
        if user_id in self.running_sessions:
            self.running_sessions[user_id]['running'] = False
        if user_id in self.running_tasks:
            try:
                self.running_tasks[user_id].cancel()
                del self.running_tasks[user_id]
            except:
                pass

data_manager = DataManager()

# ===================== ULTRA VISITOR =====================
class UltraVisitor:
    def __init__(self, url, target_rps=500):
        self.url = url
        self.target_rps = min(target_rps, MAX_RPS)
        self.total_requests = 0
        self.successful = 0
        self.failed = 0
        self.running = True
        self.lock = threading.Lock()
        self.start_time = None
        self.thread_count = min(MAX_THREADS, max(15, int(self.target_rps / 4) + 10))
        
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.6099.210',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Firefox/121.0',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.6099.210',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.6099.210',
            'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/537.36',
            'Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 Chrome/120.0.6099.210',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/119.0.6045.199',
        ]
        logger.info(f"Visitor: {self.thread_count} threads, {self.target_rps} RPS")
    
    async def make_request(self, session, headers):
        try:
            async with session.get(self.url, headers=headers, ssl=False) as response:
                with self.lock:
                    self.total_requests += 1
                    if response.status in [200, 201, 202, 203, 204, 205, 206, 301, 302, 303, 304, 307, 308]:
                        self.successful += 1
                    else:
                        self.failed += 1
                return True
        except Exception:
            with self.lock:
                self.total_requests += 1
                self.failed += 1
            return False
    
    async def worker(self, worker_id):
        connector = TCPConnector(limit=300, limit_per_host=300, ssl=False)
        timeout = ClientTimeout(total=3, connect=1.5)
        
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            while self.running:
                headers = {
                    'User-Agent': random.choice(self.user_agents),
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Accept-Encoding': 'gzip, deflate',
                    'Connection': 'keep-alive',
                }
                await self.make_request(session, headers)
                if self.target_rps > 0:
                    delay = 1.0 / (self.target_rps / self.thread_count)
                    if delay > 0.0005:
                        await asyncio.sleep(delay)
    
    def start(self):
        logger.info(f"Starting visitor for {self.url}")
        self.start_time = time.time()
        self.running = True
        
        def update_stats():
            while self.running:
                time.sleep(2)
                if not self.running:
                    break
                with self.lock:
                    total = self.total_requests
                    success = self.successful
                    failed = self.failed
                elapsed = time.time() - self.start_time
                rps = total / elapsed if elapsed > 0 else 0
                rate = (success / max(1, total)) * 100
                logger.info(f"📊 Total: {total} | ✅ {success} | ❌ {failed} | ⚡ {rps:.0f} RPS")
        
        threading.Thread(target=update_stats, daemon=True).start()
        
        try:
            asyncio.run(self.run_workers())
        except Exception as e:
            logger.error(f"Visitor error: {e}")
        finally:
            self.stop()
    
    async def run_workers(self):
        tasks = [asyncio.create_task(self.worker(i)) for i in range(self.thread_count)]
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            pass
    
    def stop(self):
        self.running = False
        logger.info("Visitor stopped")

# ===================== BOT HANDLERS =====================
async def show_main_menu(bot, user_id, edit=False, message_id=None, chat_id=None):
    user = data_manager.get_user(user_id)
    is_running = data_manager.is_running(user_id)
    session = data_manager.running_sessions.get(user_id, {})
    
    menu_text = (
        f"🤖 **ULTIMATE UPTIME BOT**\n{'='*30}\n\n"
        f"👤 {user.first_name}\n💰 Credits: `{user.credits}`\n"
        f"📊 Visits: `{user.sessions}`\n🔄 Requests: `{user.total_requests:,}`\n"
        f"📈 Status: {'🟢 Running' if is_running else '🔴 Stopped'}\n"
        f"⚡ Max RPS: `{MAX_RPS}` | 🧵 Threads: `{MAX_THREADS}`"
    )
    
    keyboard = []
    if is_running:
        keyboard.append([InlineKeyboardButton("🛑 STOP", callback_data="stop_visit")])
    else:
        if user.credits > 0 or user.is_admin:
            keyboard.append([InlineKeyboardButton("🚀 START", callback_data="start_visit")])
        else:
            keyboard.append([InlineKeyboardButton("⛔ NO CREDITS", callback_data="no_credits")])
    
    keyboard.append([
        InlineKeyboardButton("📊 STATS", callback_data="my_stats"),
        InlineKeyboardButton("💰 CREDITS", callback_data="check_credits")
    ])
    if user.is_admin:
        keyboard.append([InlineKeyboardButton("👑 ADMIN", callback_data="admin_panel")])
    keyboard.append([InlineKeyboardButton("❓ HELP", callback_data="help_menu")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if edit and message_id and chat_id:
        try:
            await bot.edit_message_text(menu_text, chat_id=chat_id, message_id=message_id,
                                       parse_mode='Markdown', reply_markup=reply_markup)
        except:
            await bot.send_message(user_id, menu_text, parse_mode='Markdown', reply_markup=reply_markup)
    else:
        await bot.send_message(user_id, menu_text, parse_mode='Markdown', reply_markup=reply_markup)

async def run_visitor(user_id, url, rps, bot):
    try:
        visitor = UltraVisitor(url, rps)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(data_manager.executor, visitor.start)
        
        duration = time.time() - visitor.start_time if visitor.start_time else 0
        data_manager.update_stats(user_id, visitor.total_requests, visitor.successful, 
                                 visitor.failed, duration, url, rps)
        
        success_rate = (visitor.successful / max(1, visitor.total_requests)) * 100
        avg_rps = visitor.total_requests / max(1, duration)
        user = data_manager.get_user(user_id)
        
        await bot.send_message(user_id,
            f"✅ **VISIT COMPLETED**\n{'='*30}\n\n"
            f"🔄 Total: `{visitor.total_requests:,}`\n✅ Success: `{visitor.successful:,}`\n"
            f"❌ Failed: `{visitor.failed:,}`\n📈 Rate: `{success_rate:.1f}%`\n"
            f"⚡ RPS: `{avg_rps:.1f}`\n💰 Credits: `{user.credits}`",
            parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Visitor error: {e}")
        await bot.send_message(user_id, f"❌ Error: `{str(e)[:200]}`", parse_mode='Markdown')
    finally:
        data_manager.stop_session(user_id)
        await show_main_menu(bot, user_id)

# Command handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    data_manager.get_user(user.id, user.username or "", user.first_name or "")
    await show_main_menu(context.bot, user.id)

async def visit_config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    user = data_manager.get_user(user_id)
    
    if not user.is_admin and user.credits <= 0:
        await query.edit_message_text(f"⛔ No credits! Daily: {DAILY_CREDITS}", parse_mode='Markdown')
        return
    
    await query.edit_message_text("🌐 Send URL (e.g., https://example.com):", parse_mode='Markdown')
    context.user_data['awaiting_url'] = True

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get('awaiting_url', False):
        return
    
    url = update.message.text.strip()
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    
    context.user_data['awaiting_url'] = False
    context.user_data['visit_url'] = url
    
    keyboard = [
        [InlineKeyboardButton("🐢 100", callback_data="rps_100"), InlineKeyboardButton("🐇 200", callback_data="rps_200")],
        [InlineKeyboardButton("🚀 300", callback_data="rps_300"), InlineKeyboardButton("⚡ 500", callback_data="rps_500")],
        [InlineKeyboardButton("🔥 750", callback_data="rps_750"), InlineKeyboardButton("💥 1000", callback_data="rps_1000")],
        [InlineKeyboardButton("🔙 CANCEL", callback_data="main_menu")]
    ]
    await update.message.reply_text(f"⚡ Select RPS for {url}", parse_mode='Markdown',
                                    reply_markup=InlineKeyboardMarkup(keyboard))

async def rps_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    user = data_manager.get_user(user_id)
    rps = int(query.data.split('_')[1])
    url = context.user_data.get('visit_url', '')
    
    if not url:
        await query.edit_message_text("❌ URL not found!", parse_mode='Markdown')
        return
    if data_manager.is_running(user_id):
        await query.edit_message_text("⚠️ Already running!", parse_mode='Markdown')
        return
    if not user.is_admin and not data_manager.use_credit(user_id):
        await query.edit_message_text("⛔ No credits!", parse_mode='Markdown')
        return
    
    data_manager.start_session(user_id, url, rps)
    await query.edit_message_text(f"🚀 Started! {rps} RPS", parse_mode='Markdown')
    task = asyncio.create_task(run_visitor(user_id, url, rps, context.bot))
    data_manager.running_tasks[user_id] = task

async def stop_visit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data_manager.stop_session(update.effective_user.id)
    await query.edit_message_text("🛑 Stopped!", parse_mode='Markdown')
    await show_main_menu(context.bot, update.effective_user.id)

async def my_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = data_manager.get_user(update.effective_user.id)
    rate = (user.successful_requests / max(1, user.total_requests)) * 100
    avg_rps = user.total_requests / user.total_time if user.total_time > 0 else 0
    
    await query.edit_message_text(
        f"📊 **STATS**\n{'='*30}\n\n"
        f"👤 {user.first_name}\n🔄 Total: `{user.total_requests:,}`\n"
        f"✅ Success: `{user.successful_requests:,}`\n❌ Failed: `{user.failed_requests:,}`\n"
        f"📈 Rate: `{rate:.1f}%`\n⚡ RPS: `{avg_rps:.1f}`\n"
        f"📅 Sessions: `{user.sessions}`\n💰 Credits: `{user.credits}`",
        parse_mode='Markdown')

async def check_credits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = data_manager.get_user(update.effective_user.id)
    await query.edit_message_text(
        f"💰 **CREDITS**\nAvailable: `{user.credits}`\n"
        f"Used Today: `{user.daily_visits}`\nDaily Limit: `{DAILY_CREDITS}`",
        parse_mode='Markdown')

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = data_manager.get_user(update.effective_user.id)
    if not user.is_admin:
        await query.edit_message_text("⛔ Admin only!", parse_mode='Markdown')
        return
    
    keyboard = [
        [InlineKeyboardButton("➕ Add Credits", callback_data="admin_add")],
        [InlineKeyboardButton("⭐ Add Premium", callback_data="admin_premium")],
        [InlineKeyboardButton("👥 List Users", callback_data="admin_list")],
        [InlineKeyboardButton("🔙 Back", callback_data="main_menu")]
    ]
    await query.edit_message_text("👑 **ADMIN PANEL**", parse_mode='Markdown',
                                  reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Send: `user_id amount`\nExample: `123456789 10`", parse_mode='Markdown')
    context.user_data['awaiting_add_credits'] = True

async def admin_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Send: `user_id days`\nExample: `123456789 30`", parse_mode='Markdown')
    context.user_data['awaiting_add_premium'] = True

async def admin_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    users = []
    for uid, u in list(data_manager.users.items())[-20:]:
        users.append(f"🆔 `{uid}` | {u.first_name[:15]} | 💰{u.credits}")
    await query.edit_message_text("👥 **Users**\n\n" + "\n".join(users), parse_mode='Markdown')

async def help_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        f"❓ **HELP**\n{'='*30}\n\n"
        f"1. Click START\n2. Enter URL\n3. Select RPS\n4. Wait\n\n"
        f"💰 {DAILY_CREDITS} free credits daily\n⚡ Max RPS: {MAX_RPS}\n🧵 Threads: {MAX_THREADS}\n\n"
        f"⚠️ Use only on own websites!",
        parse_mode='Markdown')

async def no_credits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = data_manager.get_user(update.effective_user.id)
    await query.edit_message_text(
        f"⛔ No credits!\nAvailable: `{user.credits}`\nUsed: `{user.daily_visits}/{DAILY_CREDITS}`",
        parse_mode='Markdown')

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    
    if text == '/cancel':
        context.user_data.clear()
        await update.message.reply_text("✅ Cancelled!", parse_mode='Markdown')
        await show_main_menu(context.bot, user_id)
        return
    
    if context.user_data.get('awaiting_add_credits', False):
        try:
            parts = text.split()
            target = int(parts[0]); amount = int(parts[1])
            if data_manager.add_credits(target, amount):
                await update.message.reply_text(f"✅ Added {amount} credits to `{target}`!", parse_mode='Markdown')
                context.user_data['awaiting_add_credits'] = False
                await show_main_menu(context.bot, user_id)
        except:
            await update.message.reply_text("❌ Invalid format!", parse_mode='Markdown')
        return
    
    if context.user_data.get('awaiting_add_premium', False):
        try:
            parts = text.split()
            target = int(parts[0]); days = int(parts[1])
            if data_manager.set_premium(target, days):
                await update.message.reply_text(f"⭐ Added {days} days premium to `{target}`!", parse_mode='Markdown')
                context.user_data['awaiting_add_premium'] = False
                await show_main_menu(context.bot, user_id)
        except:
            await update.message.reply_text("❌ Invalid format!", parse_mode='Markdown')
        return

async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    data = query.data
    
    handlers = {
        "main_menu": lambda: show_main_menu(context.bot, user_id, True, query.message.message_id, query.message.chat_id),
        "start_visit": lambda: visit_config(update, context),
        "stop_visit": lambda: stop_visit(update, context),
        "my_stats": lambda: my_stats(update, context),
        "check_credits": lambda: check_credits(update, context),
        "admin_panel": lambda: admin_panel(update, context),
        "help_menu": lambda: help_menu(update, context),
        "no_credits": lambda: no_credits(update, context),
        "admin_add": lambda: admin_add(update, context),
        "admin_premium": lambda: admin_premium(update, context),
        "admin_list": lambda: admin_list(update, context),
    }
    
    if data.startswith("rps_"):
        await rps_select(update, context)
    elif data in handlers:
        await handlers[data]()

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Error: {context.error}")
    logger.error(traceback.format_exc())

# ===================== MAIN =====================
def main():
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("\n❌ ERROR: Set BOT_TOKEN in main.py!\n")
        sys.exit(1)
    
    print("\n" + "="*50)
    print("🚀 ULTIMATE UPTIME BOT")
    print("="*50)
    print(f"🤖 Bot: {BOT_TOKEN[:10]}...")
    print(f"👑 Admins: {ADMIN_IDS}")
    print(f"⚡ Max RPS: {MAX_RPS}")
    print(f"🧵 Threads: {MAX_THREADS}")
    print("="*50 + "\n")
    
    try:
        # Create application - Simplified for compatibility
        application = Application.builder().token(BOT_TOKEN).build()
        
        # Add handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("cancel", handle_text))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
        application.add_handler(CallbackQueryHandler(callback))
        application.add_error_handler(error_handler)
        
        print("✅ Bot running!\n")
        application.run_polling(allowed_updates=Update.ALL_TYPES)
        
    except Exception as e:
        logger.error(f"Bot crashed: {e}")
        logger.error(traceback.format_exc())
        time.sleep(5)
        main()

if __name__ == "__main__":
    main()
