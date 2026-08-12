#!/usr/bin/env python3
"""
Ultimate Uptime Bot - Railway Optimized
Maximum Threads & RPS - Zero Error
"""

import os
import sys
import asyncio
import threading
import time
import json
import logging
import random
from datetime import datetime, timedelta
from typing import Dict, Optional, List
from dataclasses import dataclass, asdict
from concurrent.futures import ThreadPoolExecutor
import traceback

# Disable all warnings
import warnings
warnings.filterwarnings('ignore')

# Disable SSL warnings
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configure logging for Railway
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Railway environment check
IS_RAILWAY = os.environ.get('RAILWAY_ENVIRONMENT', False) or os.environ.get('RAILWAY_SERVICE_ID', False)

# Bot token from environment
BOT_TOKEN = os.environ.get('BOT_TOKEN', 'YOUR_BOT_TOKEN_HERE')

# Admin IDs - Add your Telegram user IDs here
ADMIN_IDS = [int(x.strip()) for x in os.environ.get('ADMIN_IDS', '123456789').split(',')]

# Data file
DATA_FILE = 'bot_data.json'

# MAXIMUM PERFORMANCE SETTINGS
MAX_THREADS = int(os.environ.get('MAX_THREADS', '200'))  # Maximum threads
MAX_RPS = int(os.environ.get('MAX_RPS', '1000'))        # Maximum RPS
DEFAULT_RPS = int(os.environ.get('DEFAULT_RPS', '300')) # Default RPS

# Import aiohttp with error handling
try:
    import aiohttp
    from aiohttp import ClientTimeout, TCPConnector, ClientSession
except ImportError:
    os.system('pip install aiohttp -q')
    import aiohttp
    from aiohttp import ClientTimeout, TCPConnector, ClientSession

# Import telegram with error handling
try:
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
except ImportError:
    os.system('pip install python-telegram-bot==20.7 -q')
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters

@dataclass
class UserData:
    user_id: int
    username: str = ""
    first_name: str = ""
    total_visits: int = 0
    daily_visits: int = 0
    last_visit_date: str = ""
    credits: int = 3
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    total_time: float = 0
    sessions: int = 0
    last_url: str = ""
    last_rps: int = 0
    is_admin: bool = False
    created_at: str = ""
    premium_until: str = ""
    
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
    
    def load_data(self):
        try:
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for user_id, user_data in data.get('users', {}).items():
                        self.users[int(user_id)] = UserData.from_dict(user_data)
                logger.info(f"Loaded {len(self.users)} users from data file")
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
                    first_name=first_name,
                    credits=9999 if is_admin else 3,
                    is_admin=is_admin,
                    created_at=datetime.now().isoformat()
                )
                self.users[user_id] = user
                self.save_data()
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
                        user.credits = 3
                    self.save_data()
            
            return user
    
    def use_credit(self, user_id: int) -> bool:
        user = self.get_user(user_id)
        
        if user.is_admin:
            return True
        
        # Check premium
        if user.premium_until:
            premium_date = datetime.fromisoformat(user.premium_until)
            if premium_date > datetime.now():
                return True
        
        if user.credits > 0:
            user.credits -= 1
            user.daily_visits += 1
            self.save_data()
            return True
        
        return False
    
    def add_credits(self, user_id: int, amount: int) -> bool:
        if user_id not in self.users:
            return False
        
        user = self.users[user_id]
        user.credits += amount
        self.save_data()
        return True
    
    def set_premium(self, user_id: int, days: int) -> bool:
        if user_id not in self.users:
            return False
        
        user = self.users[user_id]
        premium_until = datetime.now() + timedelta(days=days)
        user.premium_until = premium_until.isoformat()
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
            self.running_tasks[user_id].cancel()
            try:
                del self.running_tasks[user_id]
            except:
                pass

class UltraVisitor:
    """Ultra high-performance visitor with maximum threads"""
    
    def __init__(self, url, target_rps=500):
        self.url = url
        self.target_rps = min(target_rps, MAX_RPS)
        self.total_requests = 0
        self.successful = 0
        self.failed = 0
        self.running = True
        self.lock = threading.Lock()
        self.start_time = None
        self.last_stats = 0
        
        # Maximum threads for performance
        self.thread_count = min(MAX_THREADS, max(20, int(self.target_rps / 3) + 10))
        
        # Extensive user agents
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.6099.210 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.6099.210 Safari/537.36 Edg/120.0.0.0',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.6099.210 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.6099.210 Safari/537.36',
            'Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/121.0',
            'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/537.36',
            'Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/537.36',
            'Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 Chrome/120.0.6099.210',
            'Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 Chrome/120.0.6099.210',
        ]
        
        print(f"[+] Ultra Mode: {self.thread_count} threads, {self.target_rps} RPS")
    
    async def make_request(self, session, headers):
        """Super fast request with connection pooling"""
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
        """High-performance worker"""
        # Optimized connection pool
        connector = TCPConnector(
            limit=200,
            limit_per_host=200,
            ttl_dns_cache=300,
            ssl=False,
            force_close=False,
            enable_cleanup_closed=True
        )
        timeout = ClientTimeout(total=3, connect=1.5, sock_read=2)
        
        async with ClientSession(connector=connector, timeout=timeout) as session:
            while self.running:
                headers = {
                    'User-Agent': random.choice(self.user_agents),
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'Connection': 'keep-alive',
                    'Cache-Control': 'no-cache, no-store, must-revalidate',
                    'Pragma': 'no-cache',
                    'Sec-Ch-Ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
                    'Sec-Ch-Ua-Mobile': '?0',
                    'Sec-Ch-Ua-Platform': '"Windows"',
                    'Sec-Fetch-Dest': 'document',
                    'Sec-Fetch-Mode': 'navigate',
                    'Sec-Fetch-Site': 'none',
                    'Sec-Fetch-User': '?1',
                    'Upgrade-Insecure-Requests': '1',
                }
                
                await self.make_request(session, headers)
                
                # Minimal delay for max performance
                if self.target_rps > 0:
                    delay = 1.0 / (self.target_rps / self.thread_count)
                    if delay > 0.001:
                        await asyncio.sleep(delay)
    
    def start(self):
        """Start the visitor"""
        print(f"\n{'='*60}")
        print(f"🚀 ULTRA VISITOR STARTED")
        print(f"📍 Target: {self.url}")
        print(f"⚡ RPS: {self.target_rps}")
        print(f"🧵 Threads: {self.thread_count}")
        print(f"{'='*60}\n")
        
        self.start_time = time.time()
        self.running = True
        
        # Stats updater thread
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
                success_rate = (success / max(1, total)) * 100
                
                print(f"\r📊 Total: {total:>6} | ✅ {success:>6} | ❌ {failed:>5} | ⚡ {rps:>6.0f} RPS | 📈 {success_rate:>5.1f}%", end="")
        
        stats_thread = threading.Thread(target=update_stats, daemon=True)
        stats_thread.start()
        
        # Start async workers
        try:
            asyncio.run(self.run_workers())
        except KeyboardInterrupt:
            self.stop()
        except Exception as e:
            logger.error(f"Visitor error: {e}")
            self.stop()
    
    async def run_workers(self):
        """Run all workers"""
        tasks = []
        for i in range(self.thread_count):
            task = asyncio.create_task(self.worker(i))
            tasks.append(task)
        
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            pass
    
    def stop(self):
        """Stop gracefully"""
        self.running = False
        time.sleep(1)
        
        elapsed = time.time() - self.start_time
        success_rate = (self.successful / max(1, self.total_requests)) * 100
        avg_rps = self.total_requests / elapsed if elapsed > 0 else 0
        
        print(f"\n\n{'='*60}")
        print(f"📊 FINAL REPORT")
        print(f"{'='*60}")
        print(f"⏱️ Duration: {elapsed:.1f}s")
        print(f"🔄 Total: {self.total_requests:,}")
        print(f"✅ Success: {self.successful:,}")
        print(f"❌ Failed: {self.failed:,}")
        print(f"📈 Success Rate: {success_rate:.1f}%")
        print(f"⚡ Average RPS: {avg_rps:.1f}")
        print(f"{'='*60}\n")

# Bot handlers
data_manager = DataManager()

async def show_main_menu(bot, user_id, edit=False, message_id=None, chat_id=None):
    """Show main menu"""
    user = data_manager.get_user(user_id)
    is_running = data_manager.is_running(user_id)
    session = data_manager.running_sessions.get(user_id, {})
    
    status_icon = "🟢" if is_running else "🔴"
    status_text = "Running" if is_running else "Stopped"
    url_display = session.get('url', 'N/A')[:35] + '...' if is_running else "No active visit"
    
    menu_text = (
        f"🤖 **ULTIMATE UPTIME BOT**\n\n"
        f"👤 {user.first_name}\n"
        f"💰 Credits: `{user.credits}`\n"
        f"📊 Visits: `{user.sessions}`\n"
        f"🔄 Requests: `{user.total_requests:,}`\n"
        f"📈 Status: {status_icon} {status_text}\n"
        f"🌐 {url_display}\n\n"
        f"⚡ Max RPS: `{MAX_RPS}` | 🧵 Threads: `{MAX_THREADS}`"
    )
    
    keyboard = []
    
    if is_running:
        keyboard.append([InlineKeyboardButton("🛑 STOP VISIT", callback_data="stop_visit")])
    else:
        if user.credits > 0 or user.is_admin:
            keyboard.append([InlineKeyboardButton("🚀 START VISIT", callback_data="start_visit")])
        else:
            keyboard.append([InlineKeyboardButton("⛔ NO CREDITS", callback_data="no_credits")])
    
    keyboard.append([
        InlineKeyboardButton("📊 STATS", callback_data="my_stats"),
        InlineKeyboardButton("💰 CREDITS", callback_data="check_credits")
    ])
    
    if user.is_admin:
        keyboard.append([InlineKeyboardButton("👑 ADMIN PANEL", callback_data="admin_panel")])
    
    keyboard.append([InlineKeyboardButton("❓ HELP", callback_data="help_menu")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if edit and message_id and chat_id:
        try:
            await bot.edit_message_text(
                menu_text,
                chat_id=chat_id,
                message_id=message_id,
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
        except:
            await bot.send_message(user_id, menu_text, parse_mode='Markdown', reply_markup=reply_markup)
    else:
        await bot.send_message(user_id, menu_text, parse_mode='Markdown', reply_markup=reply_markup)

async def run_ultra_visitor(user_id, url, rps, bot):
    """Run ultra visitor"""
    try:
        visitor = UltraVisitor(url, rps)
        
        # Start in thread pool
        def run_visitor():
            visitor.start()
        
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(data_manager.executor, run_visitor)
        
        # Update stats
        data_manager.update_stats(
            user_id,
            visitor.total_requests,
            visitor.successful,
            visitor.failed,
            time.time() - visitor.start_time,
            url,
            rps
        )
        
        # Send final report
        success_rate = (visitor.successful / max(1, visitor.total_requests)) * 100
        avg_rps = visitor.total_requests / max(1, time.time() - visitor.start_time)
        
        await bot.send_message(
            user_id,
            f"✅ **VISIT COMPLETED**\n\n"
            f"🔄 Total: `{visitor.total_requests:,}`\n"
            f"✅ Success: `{visitor.successful:,}`\n"
            f"❌ Failed: `{visitor.failed:,}`\n"
            f"📈 Rate: `{success_rate:.1f}%`\n"
            f"⚡ RPS: `{avg_rps:.1f}`\n\n"
            f"💰 Credits Left: `{data_manager.users[user_id].credits}`",
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Visitor error: {e}")
        await bot.send_message(
            user_id,
            f"❌ Error: `{str(e)[:200]}`",
            parse_mode='Markdown'
        )
    
    finally:
        data_manager.stop_session(user_id)
        await show_main_menu(bot, user_id)

# Command Handlers
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    data_manager.get_user(user.id, user.username or "", user.first_name or "")
    
    await update.message.reply_text(
        f"👋 **WELCOME {user.first_name}!**\n\n"
        f"🚀 Ultimate Uptime Bot\n"
        f"⚡ Max Performance Mode\n"
        f"🧵 {MAX_THREADS} Threads\n"
        f"📈 {MAX_RPS} Max RPS\n\n"
        f"Click below to start!",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 OPEN MENU", callback_data="main_menu")]
        ])
    )

async def visit_config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    user = data_manager.get_user(user_id)
    
    if not user.is_admin and user.credits <= 0:
        await query.edit_message_text(
            "⛔ **NO CREDITS LEFT**\n\n"
            f"Daily limit: 3 visits\n"
            f"Used today: {user.daily_visits}\n\n"
            "Contact admin for more credits.",
            parse_mode='Markdown'
        )
        return
    
    keyboard = [[InlineKeyboardButton("🔙 CANCEL", callback_data="main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"🌐 **ENTER URL**\n\n"
        f"Send the URL to visit.\n"
        f"Example: `https://example.com`\n\n"
        f"💰 Credits: `{user.credits - (0 if user.is_admin else 1)}`\n"
        f"⚡ Max RPS: `{MAX_RPS}`\n\n"
        f"⏳ Send URL now:",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )
    
    context.user_data['awaiting_url'] = True

async def handle_url_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if not context.user_data.get('awaiting_url', False):
        return
    
    url = update.message.text.strip()
    
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    
    if len(url) < 5:
        await update.message.reply_text("❌ Invalid URL!")
        return
    
    context.user_data['awaiting_url'] = False
    context.user_data['visit_url'] = url
    
    # RPS selection with max values
    keyboard = [
        [
            InlineKeyboardButton("🐢 100 RPS", callback_data="rps_100"),
            InlineKeyboardButton("🐇 200 RPS", callback_data="rps_200")
        ],
        [
            InlineKeyboardButton("🚀 300 RPS", callback_data="rps_300"),
            InlineKeyboardButton("⚡ 500 RPS", callback_data="rps_500")
        ],
        [
            InlineKeyboardButton("🔥 700 RPS", callback_data="rps_700"),
            InlineKeyboardButton("💥 1000 RPS", callback_data="rps_1000")
        ],
        [InlineKeyboardButton("🔙 CANCEL", callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"⚡ **SELECT SPEED**\n\n"
        f"🌐 URL: `{url}`\n"
        f"💰 Cost: 1 credit\n\n"
        f"Choose RPS:",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )
    
    context.user_data['awaiting_rps'] = True

async def rps_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    
    if not user.is_admin:
        if not data_manager.use_credit(user_id):
            await query.edit_message_text("⛔ No credits!", parse_mode='Markdown')
            return
    
    data_manager.start_session(user_id, url, rps)
    
    await query.edit_message_text(
        f"🚀 **VISIT STARTED**\n\n"
        f"🌐 {url}\n"
        f"⚡ {rps} RPS\n"
        f"🧵 {min(MAX_THREADS, int(rps / 3) + 10)} Threads\n\n"
        f"📊 Running in background...",
        parse_mode='Markdown'
    )
    
    # Start visitor
    task = asyncio.create_task(run_ultra_visitor(user_id, url, rps, context.bot))
    data_manager.running_tasks[user_id] = task

async def stop_visit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    
    if not data_manager.is_running(user_id):
        await query.edit_message_text("ℹ️ No active visit.", parse_mode='Markdown')
        return
    
    data_manager.stop_session(user_id)
    
    await query.edit_message_text(
        "🛑 **STOPPING...**\n\n"
        "Please wait for final report.",
        parse_mode='Markdown'
    )

async def my_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user = data_manager.get_user(update.effective_user.id)
    
    success_rate = (user.successful_requests / max(1, user.total_requests)) * 100
    avg_rps = user.total_requests / user.total_time if user.total_time > 0 else 0
    
    stats_text = (
        f"📊 **YOUR STATISTICS**\n\n"
        f"👤 {user.first_name}\n"
        f"🆔 `{user.user_id}`\n\n"
        f"🔄 Total Requests: `{user.total_requests:,}`\n"
        f"✅ Success: `{user.successful_requests:,}`\n"
        f"❌ Failed: `{user.failed_requests:,}`\n"
        f"📈 Rate: `{success_rate:.1f}%`\n"
        f"⚡ Avg RPS: `{avg_rps:.1f}`\n\n"
        f"📅 Sessions: `{user.sessions}`\n"
        f"💰 Credits: `{user.credits}`\n"
        f"🌐 Last: `{user.last_url[:40]}...`"
    )
    
    keyboard = [[InlineKeyboardButton("🔙 BACK", callback_data="main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(stats_text, parse_mode='Markdown', reply_markup=reply_markup)

async def check_credits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user = data_manager.get_user(update.effective_user.id)
    
    today = datetime.now().strftime('%Y-%m-%d')
    reset_time = datetime.strptime(f"{today} 00:00:00", '%Y-%m-%d %H:%M:%S') + timedelta(days=1)
    time_left = reset_time - datetime.now()
    
    credit_text = (
        f"💰 **CREDIT INFO**\n\n"
        f"Available: `{user.credits}`\n"
        f"Used Today: `{user.daily_visits}`\n"
        f"Daily Limit: `3`\n"
        f"Admin: `{'✅' if user.is_admin else '❌'}`\n\n"
        f"⏳ Reset in: `{str(time_left).split('.')[0]}`\n"
        f"📅 Max RPS: `{MAX_RPS}`\n"
        f"🧵 Max Threads: `{MAX_THREADS}`"
    )
    
    keyboard = [[InlineKeyboardButton("🔙 BACK", callback_data="main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(credit_text, parse_mode='Markdown', reply_markup=reply_markup)

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user = data_manager.get_user(update.effective_user.id)
    
    if not user.is_admin:
        await query.edit_message_text("⛔ Admin only!", parse_mode='Markdown')
        return
    
    total_users = len(data_manager.users)
    total_requests = sum(u.total_requests for u in data_manager.users.values())
    active_sessions = len(data_manager.running_sessions)
    
    admin_text = (
        f"👑 **ADMIN PANEL**\n\n"
        f"👥 Users: `{total_users}`\n"
        f"🔄 Requests: `{total_requests:,}`\n"
        f"🟢 Active: `{active_sessions}`\n"
        f"🧵 Threads: `{MAX_THREADS}`\n"
        f"⚡ Max RPS: `{MAX_RPS}`\n\n"
        f"🔧 Actions:"
    )
    
    keyboard = [
        [InlineKeyboardButton("➕ ADD CREDITS", callback_data="admin_add_credits")],
        [InlineKeyboardButton("⭐ ADD PREMIUM", callback_data="admin_add_premium")],
        [InlineKeyboardButton("👥 LIST USERS", callback_data="admin_list_users")],
        [InlineKeyboardButton("📊 SYSTEM STATUS", callback_data="admin_system_status")],
        [InlineKeyboardButton("🔙 BACK", callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(admin_text, parse_mode='Markdown', reply_markup=reply_markup)

async def admin_add_credits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user = data_manager.get_user(update.effective_user.id)
    if not user.is_admin:
        await query.edit_message_text("⛔ Admin only!", parse_mode='Markdown')
        return
    
    await query.edit_message_text(
        "➕ **ADD CREDITS**\n\n"
        "Send: `user_id amount`\n"
        "Example: `123456789 10`\n\n"
        "Type /cancel to cancel",
        parse_mode='Markdown'
    )
    
    context.user_data['awaiting_add_credits'] = True

async def admin_add_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user = data_manager.get_user(update.effective_user.id)
    if not user.is_admin:
        await query.edit_message_text("⛔ Admin only!", parse_mode='Markdown')
        return
    
    await query.edit_message_text(
        "⭐ **ADD PREMIUM**\n\n"
        "Send: `user_id days`\n"
        "Example: `123456789 30`\n\n"
        "Type /cancel to cancel",
        parse_mode='Markdown'
    )
    
    context.user_data['awaiting_add_premium'] = True

async def admin_list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user = data_manager.get_user(update.effective_user.id)
    if not user.is_admin:
        await query.edit_message_text("⛔ Admin only!", parse_mode='Markdown')
        return
    
    users_list = []
    for uid, u in list(data_manager.users.items())[-20:]:
        users_list.append(f"🆔 `{uid}` | {u.first_name[:15]} | 💰{u.credits}")
    
    text = "👥 **RECENT USERS**\n\n" + "\n".join(users_list)
    
    if len(data_manager.users) > 20:
        text += f"\n\n... and {len(data_manager.users) - 20} more"
    
    keyboard = [[InlineKeyboardButton("🔙 BACK", callback_data="admin_panel")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, parse_mode='Markdown', reply_markup=reply_markup)

async def admin_system_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user = data_manager.get_user(update.effective_user.id)
    if not user.is_admin:
        await query.edit_message_text("⛔ Admin only!", parse_mode='Markdown')
        return
    
    total_users = len(data_manager.users)
    total_requests = sum(u.total_requests for u in data_manager.users.values())
    active_sessions = len(data_manager.running_sessions)
    
    status_text = (
        f"📊 **SYSTEM STATUS**\n\n"
        f"👥 Users: `{total_users}`\n"
        f"🔄 Requests: `{total_requests:,}`\n"
        f"🟢 Active: `{active_sessions}`\n"
        f"🧵 Threads: `{MAX_THREADS}`\n"
        f"⚡ Max RPS: `{MAX_RPS}`\n"
        f"📁 Data: `{DATA_FILE}`\n"
        f"🚀 Status: `✅ Running`"
    )
    
    keyboard = [[InlineKeyboardButton("🔙 BACK", callback_data="admin_panel")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(status_text, parse_mode='Markdown', reply_markup=reply_markup)

async def help_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    help_text = (
        f"❓ **HELP GUIDE**\n\n"
        f"**How to use:**\n"
        f"1️⃣ Click START VISIT\n"
        f"2️⃣ Enter URL\n"
        f"3️⃣ Select RPS speed\n"
        f"4️⃣ Wait for completion\n\n"
        f"**Credits:**\n"
        f"• Free: 3 credits daily\n"
        f"• Each visit = 1 credit\n"
        f"• Resets at midnight\n\n"
        f"**Performance:**\n"
        f"• Max RPS: `{MAX_RPS}`\n"
        f"• Threads: `{MAX_THREADS}`\n"
        f"• Optimized for uptime\n\n"
        f"⚠️ Only use on own websites!"
    )
    
    keyboard = [[InlineKeyboardButton("🔙 BACK", callback_data="main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(help_text, parse_mode='Markdown', reply_markup=reply_markup)

async def no_credits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user = data_manager.get_user(update.effective_user.id)
    
    await query.edit_message_text(
        f"⛔ **NO CREDITS**\n\n"
        f"Available: `{user.credits}`\n"
        f"Used Today: `{user.daily_visits}`\n"
        f"Daily Limit: `3`\n\n"
        f"⏳ Resets at midnight\n"
        f"Contact admin for more!",
        parse_mode='Markdown'
    )

async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    
    if text == '/cancel':
        context.user_data.clear()
        await update.message.reply_text("✅ Cancelled!", parse_mode='Markdown')
        await show_main_menu(context.bot, user_id)
        return
    
    # Handle add credits
    if context.user_data.get('awaiting_add_credits', False):
        try:
            parts = text.split()
            if len(parts) != 2:
                await update.message.reply_text("❌ Format: `user_id amount`", parse_mode='Markdown')
                return
            
            target_id = int(parts[0])
            amount = int(parts[1])
            
            if data_manager.add_credits(target_id, amount):
                await update.message.reply_text(f"✅ Added {amount} credits to `{target_id}`!", parse_mode='Markdown')
                context.user_data['awaiting_add_credits'] = False
                await show_main_menu(context.bot, user_id)
            else:
                await update.message.reply_text("❌ User not found!", parse_mode='Markdown')
        except ValueError:
            await update.message.reply_text("❌ Invalid input!", parse_mode='Markdown')
        return
    
    # Handle add premium
    if context.user_data.get('awaiting_add_premium', False):
        try:
            parts = text.split()
            if len(parts) != 2:
                await update.message.reply_text("❌ Format: `user_id days`", parse_mode='Markdown')
                return
            
            target_id = int(parts[0])
            days = int(parts[1])
            
            if data_manager.set_premium(target_id, days):
                await update.message.reply_text(f"⭐ Added {days} days premium to `{target_id}`!", parse_mode='Markdown')
                context.user_data['awaiting_add_premium'] = False
                await show_main_menu(context.bot, user_id)
            else:
                await update.message.reply_text("❌ User not found!", parse_mode='Markdown')
        except ValueError:
            await update.message.reply_text("❌ Invalid input!", parse_mode='Markdown')
        return

async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        "admin_add_credits": lambda: admin_add_credits(update, context),
        "admin_add_premium": lambda: admin_add_premium(update, context),
        "admin_list_users": lambda: admin_list_users(update, context),
        "admin_system_status": lambda: admin_system_status(update, context),
    }
    
    if data.startswith("rps_"):
        await rps_selection(update, context)
    elif data in handlers:
        await handlers[data]()

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error: {context.error}")
    logger.error(traceback.format_exc())
    
    if update and update.effective_user:
        try:
            await context.bot.send_message(
                update.effective_user.id,
                "⚠️ An error occurred. Please try again.",
                parse_mode='Markdown'
            )
        except:
            pass

def main():
    """Main function"""
    if BOT_TOKEN == 'YOUR_BOT_TOKEN_HERE':
        print("⚠️ Set BOT_TOKEN environment variable!")
        sys.exit(1)
    
    print("=" * 60)
    print("🚀 ULTIMATE UPTIME BOT")
    print("=" * 60)
    print(f"🤖 Bot Token: {BOT_TOKEN[:10]}...")
    print(f"👑 Admins: {ADMIN_IDS}")
    print(f"🧵 Max Threads: {MAX_THREADS}")
    print(f"⚡ Max RPS: {MAX_RPS}")
    print(f"📁 Data File: {DATA_FILE}")
    print(f"🏗️ Platform: {'Railway' if IS_RAILWAY else 'Local'}")
    print("=" * 60)
    print("✅ Bot is running...")
    
    # Create application with optimized settings
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("cancel", handle_text_input))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url_input))
    application.add_handler(CallbackQueryHandler(menu_callback))
    application.add_error_handler(error_handler)
    
    # Run
    try:
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    except Exception as e:
        logger.error(f"Bot crashed: {e}")
        time.sleep(5)
        main()

if __name__ == "__main__":
    main()