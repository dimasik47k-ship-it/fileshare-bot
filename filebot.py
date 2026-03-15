#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
📁 FileShare Bot v6.0 — Полностью исправленная версия
✅ Исправлены все ошибки • QR-коды • Категории • История скачиваний
"""

import logging
import asyncio
import sqlite3
import os
import string
import random
import qrcode
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, 
    CallbackQuery, Message, FSInputFile, BufferedInputFile
)
from aiogram.exceptions import TelegramBadRequest
from aiogram.client.session.aiohttp import AiohttpSession
from dotenv import load_dotenv

# ─────────────────────────────────────────────────────────────
# 🎨 Константы
# ─────────────────────────────────────────────────────────────
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2 ГБ для Premium Bot

FILE_CATEGORIES = {
    'document': '📄 Документы',
    'photo': '🖼️ Фото',
    'video': '🎬 Видео',
    'audio': '🎵 Аудио',
    'voice': '🎤 Голосовые'
}

# ─────────────────────────────────────────────────────────────
# 📁 Настройка
# ─────────────────────────────────────────────────────────────
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s'
)
logger = logging.getLogger(__name__)

API_TOKEN = os.getenv('TG_BOT_TOKEN')
if not API_TOKEN:
    logger.error("❌ Укажите токен в .env файле!")
    exit(1)

FILES_DIR = Path('uploaded_files')
FILES_DIR.mkdir(exist_ok=True)

QR_DIR = Path('qr_codes')
QR_DIR.mkdir(exist_ok=True)

# ─────────────────────────────────────────────────────────────
# 🗄️ База данных
# ─────────────────────────────────────────────────────────────
DB_PATH = Path('files.db')

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id TEXT NOT NULL UNIQUE,
            original_name TEXT NOT NULL,
            file_path TEXT,
            file_size INTEGER,
            user_id BIGINT NOT NULL,
            username TEXT,
            created_at TEXT,
            expires_at TEXT,
            download_count INTEGER DEFAULT 0,
            file_type TEXT,
            category TEXT DEFAULT 'document'
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS download_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id TEXT NOT NULL,
            user_id BIGINT NOT NULL,
            downloaded_at TEXT,
            FOREIGN KEY (file_id) REFERENCES files(file_id) ON DELETE CASCADE
        )
    ''')
    
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_file_id ON files(file_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_user_id ON files(user_id)')
    
    conn.commit()
    conn.close()
    logger.info("✅ База данных готова")

def get_db():
    # ✅ ИСПРАВЛЕНО: убран detect_types для Python 3.12+
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def generate_unique_id(length: int = 8) -> str:
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

def parse_timestamp(ts_string: str) -> Optional[datetime]:
    if not ts_string:
        return None
    try:
        for fmt in ['%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S']:
            try:
                return datetime.strptime(ts_string, fmt)
            except ValueError:
                continue
        return None
    except:
        return None

def escape_markdown(text: str) -> str:
    if not text:
        return ""
    for char in ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']:
        text = text.replace(char, f'\\{char}')
    return text

def format_size(size_bytes: int) -> str:
    for unit in ['Б', 'КБ', 'МБ', 'ГБ']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} ТБ"

def create_progress_bar(current: int, total: int, length: int = 10) -> str:
    if total == 0:
        return '⬜' * length
    filled = int(length * current / total)
    return '🟩' * filled + '⬜' * (length - filled)

def generate_qr_code(link: str, file_id: str) -> Path:
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(link)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    qr_path = QR_DIR / f"{file_id}.png"
    img.save(qr_path)
    return qr_path

# ─────────────────────────────────────────────────────────────
# 🎨 Клавиатуры
# ─────────────────────────────────────────────────────────────
def get_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='📤 Загрузить файл', callback_data='upload_file')],
        [InlineKeyboardButton(text='📁 Мои файлы', callback_data='my_files')],
        [InlineKeyboardButton(text='🔍 Найти файл', callback_data='find_file')],
        [InlineKeyboardButton(text='📊 Статистика', callback_data='stats')],
        [InlineKeyboardButton(text='📚 Справка', callback_data='help_info')],
    ])

def get_file_keyboard(file_id: str, is_owner: bool = False) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text='⬇️ Скачать', callback_data=f'download_{file_id}')],
    ]
    if is_owner:
        buttons.append([
            InlineKeyboardButton(text='🔗 Копировать ссылку', callback_data=f'link_{file_id}'),
            InlineKeyboardButton(text='📱 QR-код', callback_data=f'qr_{file_id}')
        ])
        buttons.append([InlineKeyboardButton(text='🗑️ Удалить', callback_data=f'delete_{file_id}')])
    buttons.append([InlineKeyboardButton(text='⬅️ Назад', callback_data='back')])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_files_list_keyboard(files: list) -> InlineKeyboardMarkup:
    keyboard = []
    for f in files[:10]:
        name = f['original_name'][:40] + '...' if len(f['original_name']) > 40 else f['original_name']
        category_emoji = FILE_CATEGORIES.get(f['file_type'], '📄').split()[0]
        keyboard.append([
            InlineKeyboardButton(text=f'{category_emoji} {name}', callback_data=f"view_{f['file_id']}")
        ])
    keyboard.append([InlineKeyboardButton(text='⬅️ Назад', callback_data='back')])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='⬅️ Назад', callback_data='back')]
    ])

def get_category_filter_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton(text='📋 Все', callback_data='filter_all')],
    ]
    row = []
    for key, label in FILE_CATEGORIES.items():
        row.append(InlineKeyboardButton(text=label, callback_data=f'filter_{key}'))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton(text='⬅️ Назад', callback_data='back')])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# ─────────────────────────────────────────────────────────────
# 🗂️ FSM States
# ─────────────────────────────────────────────────────────────
class FileStates(StatesGroup):
    waiting_for_file = State()
    waiting_for_link = State()

# ─────────────────────────────────────────────────────────────
# 🧩 Бот инициализация
# ─────────────────────────────────────────────────────────────
BOT_TIMEOUT = 600
session = AiohttpSession(timeout=BOT_TIMEOUT)
bot = Bot(token=API_TOKEN, session=session)
dp = Dispatcher(storage=MemoryStorage())

# ─────────────────────────────────────────────────────────────
# 💾 CRUD операции
# ─────────────────────────────────────────────────────────────
def save_file_to_db(file_id: str, original_name: str, file_path: str, 
                    file_size: int, user_id: int, username: str,
                    file_type: str, expires_at: Optional[datetime] = None,
                    category: str = 'document'):
    with get_db() as conn:
        conn.execute('''
            INSERT INTO files (file_id, original_name, file_path, file_size, 
                             user_id, username, file_type, expires_at, created_at, category)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (file_id, original_name, file_path, file_size, user_id, 
              username, file_type, 
              expires_at.strftime('%Y-%m-%d %H:%M:%S') if expires_at else None,
              datetime.now().strftime('%Y-%m-%d %H:%M:%S'), category))
        conn.commit()

def get_file_by_id(file_id: str) -> Optional[sqlite3.Row]:
    with get_db() as conn:
        return conn.execute(
            'SELECT * FROM files WHERE file_id = ?', (file_id,)
        ).fetchone()

def get_user_files(user_id: int, category: str = None) -> list:
    with get_db() as conn:
        if category and category != 'all':
            return conn.execute(
                '''SELECT * FROM files WHERE user_id = ? AND file_type = ?
                   AND (expires_at IS NULL OR expires_at > datetime("now"))
                   ORDER BY created_at DESC''',
                (user_id, category)
            ).fetchall()
        return conn.execute(
            '''SELECT * FROM files WHERE user_id = ? 
               AND (expires_at IS NULL OR expires_at > datetime("now"))
               ORDER BY created_at DESC''',
            (user_id,)
        ).fetchall()

def delete_file_from_db(file_id: str, user_id: int) -> bool:
    with get_db() as conn:
        cursor = conn.execute(
            'DELETE FROM files WHERE file_id = ? AND user_id = ?',
            (file_id, user_id)
        )
        conn.commit()
        return cursor.rowcount > 0

def increment_download_count(file_id: str, user_id: int):
    with get_db() as conn:
        conn.execute(
            'UPDATE files SET download_count = download_count + 1 WHERE file_id = ?',
            (file_id,)
        )
        conn.execute(
            'INSERT INTO download_history (file_id, user_id, downloaded_at) VALUES (?, ?, ?)',
            (file_id, user_id, datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        )
        conn.commit()

def get_stats(user_id: int) -> dict:
    with get_db() as conn:
        row = conn.execute('''
            SELECT 
                COUNT(*) as total,
                COALESCE(SUM(file_size), 0) as total_size,
                COALESCE(SUM(download_count), 0) as total_downloads
            FROM files 
            WHERE user_id = ? AND (expires_at IS NULL OR expires_at > datetime("now"))
        ''', (user_id,)).fetchone()
        return {
            'total': row['total'] if row and row['total'] else 0,
            'total_size': row['total_size'] if row and row['total_size'] else 0,
            'total_downloads': row['total_downloads'] if row and row['total_downloads'] else 0
        } if row else {'total': 0, 'total_size': 0, 'total_downloads': 0}

def get_download_history(file_id: str) -> list:
    with get_db() as conn:
        return conn.execute(
            '''SELECT * FROM download_history WHERE file_id = ? 
               ORDER BY downloaded_at DESC LIMIT 10''',
            (file_id,)
        ).fetchall()

# ─────────────────────────────────────────────────────────────
# 🌐 Health Check для Render
# ─────────────────────────────────────────────────────────────
async def health_check(request):
    return web.json_response({
        'status': 'ok',
        'bot': '@TikO_Nbot',
        'timestamp': datetime.now().isoformat()
    })

async def run_webserver():
    port = int(os.getenv('PORT', 8080))
    app = web.Application()
    app.router.add_get('/', health_check)
    app.router.add_get('/health', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"🌐 Health Check сервер запущен на порту {port}")

# ─────────────────────────────────────────────────────────────
# 🎯 Хендлеры
# ─────────────────────────────────────────────────────────────

@dp.message(Command('start'))
async def cmd_start(message: Message):
    await message.answer(
        f"👋 Привет, {message.from_user.first_name}!\n\n"
        "📁 **FileShare Bot** — твой персональный файлообменник\n\n"
        "✨ **Возможности:**\n"
        "• 📤 Загрузка файлов до 2 ГБ\n"
        "• 🔗 Генерация уникальных ссылок\n"
        "• 📱 QR-коды для быстрого доступа\n"
        "• ⏰ Автоудаление через 24 часа\n"
        "• 📊 Статистика скачиваний\n"
        "• 📁 Категории файлов\n\n"
        "Выбери действие:",
        reply_markup=get_main_keyboard(),
        parse_mode='Markdown'
    )

@dp.message(Command('help'))
async def cmd_help(message: Message):
    await message.answer(
        "📚 **Справка по командам**\n\n"
        "📁 **FileShare Bot** — твой персональный файлообменник\n\n"
        "🔹 **Основные команды:**\n"
        "• /start — 🏠 Запустить бота заново\n"
        "• /help — 📚 Эта справка\n"
        "• /myfiles — 📁 Показать мои файлы\n"
        "• /stats — 📊 Показать статистику\n\n"
        "🔹 **Как использовать:**\n"
        "1️⃣ Нажми 📤 Загрузить файл в меню\n"
        "2️⃣ Отправь любой файл (до 2 ГБ)\n"
        "3️⃣ Получи уникальную ссылку для скачивания\n\n"
        "🔗 **Полезные ссылки:**\n"
        "• 🤖 Поддержка: @HelloFridge_Bot\n"
        "• 🌐 Сайт: tegbi.netlify.app\n\n"
        "💡 **Совет:** Файлы хранятся 24 часа, затем удаляются автоматически.",
        reply_markup=get_back_keyboard(),
        parse_mode='Markdown'
    )

@dp.message(Command('myfiles'))
async def cmd_myfiles(message: Message):
    files = get_user_files(message.from_user.id)
    if not files:
        await message.answer(
            "📭 У тебя пока нет файлов.\n\n"
            "Нажми 📤 Загрузить файл или отправь файл прямо в чат!",
            reply_markup=get_main_keyboard()
        )
        return
    text = f"📁 **Твои файлы ({len(files)})**\n\n"
    for f in files[:5]:
        name = escape_markdown(f['original_name'][:30])
        size = format_size(f['file_size']) if f['file_size'] else '?'
        text += f"• 📄 `{f['file_id']}` — {name} ({size})\n"
    if len(files) > 5:
        text += f"\n... и ещё {len(files) - 5} файлов"
    await message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='📁 Открыть меню файлов', callback_data='my_files')],
            [InlineKeyboardButton(text='🏠 В главное меню', callback_data='back')]
        ]),
        parse_mode='Markdown'
    )

@dp.message(Command('stats'))
async def cmd_stats(message: Message):
    stats = get_stats(message.from_user.id)
    total = stats['total']
    downloads = stats['total_downloads']
    text = f"📊 **Твоя статистика**\n\n"
    text += f"📁 **Файлов загружено:** {total}\n"
    text += f"📦 **Всего места:** {format_size(stats['total_size'])}\n"
    text += f"⬇️ **Всего скачиваний:** {downloads}\n"
    if total > 0:
        rate = round(downloads / total * 100, 1)
        text += f"📈 **Популярность:** {create_progress_bar(min(int(rate), 100), 100, 10)} {rate}%"
    await message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='🔄 Обновить', callback_data='stats')],
            [InlineKeyboardButton(text='🏠 В главное меню', callback_data='back')]
        ]),
        parse_mode='Markdown'
    )

@dp.callback_query(F.data == 'upload_file')
async def start_upload(callback: CallbackQuery, state: FSMContext):
    await state.set_state(FileStates.waiting_for_file)
    await callback.message.edit_text(
        "📤 **Загрузка файла**\n\n"
        "Отправь мне любой файл:\n"
        "• Документ\n"
        "• Фото\n"
        "• Видео\n"
        "• Аудио\n\n"
        "Максимальный размер: 2 ГБ\n"
        "Срок хранения: 24 часа",
        reply_markup=get_back_keyboard(),
        parse_mode='Markdown'
    )

@dp.message(FileStates.waiting_for_file, F.document | F.photo | F.video | F.audio | F.voice)
async def process_file(message: Message, state: FSMContext):
    file = None
    file_type = 'document'
    if message.document:
        file = message.document
        file_type = 'document'
    elif message.photo:
        file = message.photo[-1]
        file_type = 'photo'
    elif message.video:
        file = message.video
        file_type = 'video'
    elif message.audio:
        file = message.audio
        file_type = 'audio'
    elif message.voice:
        file = message.voice
        file_type = 'voice'
    if not file:
        await message.answer("❌ Не удалось получить файл. Попробуй снова.")
        return
    if file.file_size and file.file_size > MAX_FILE_SIZE:
        await message.answer(
            f"❌ Файл слишком большой!\n\n"
            f"📦 Размер: {format_size(file.file_size)}\n"
            f"⚠️ Максимум: {format_size(MAX_FILE_SIZE)}",
            reply_markup=get_back_keyboard()
        )
        await state.clear()
        return
    progress_msg = await message.answer("⏳ Загрузка файла... Пожалуйста, подожди.\n\n"
                                        "⚠️ Большие файлы могут загружаться несколько минут.")
    file_extension = file.file_name.split('.')[-1] if file.file_name and '.' in file.file_name else 'dat'
    file_path = FILES_DIR / f"{file.file_id}.{file_extension}"
    try:
        await message.bot.download(file, destination=file_path)
        await progress_msg.delete()
    except Exception as e:
        logger.error(f"Ошибка скачивания: {e}")
        try:
            await progress_msg.delete()
        except:
            pass
        await message.answer(
            f"❌ Ошибка при сохранении файла!\n\n"
            f"🔍 Причина: {str(e)}\n\n"
            f"💡 Возможные решения:\n"
            f"• Проверь размер файла (макс. 2 ГБ)\n"
            f"• Попробуй отправить файл ещё раз\n"
            f"• Убедись, что у бота Premium статус",
            reply_markup=get_back_keyboard()
        )
        return
    unique_id = generate_unique_id()
    expires_at = datetime.now() + timedelta(hours=24)
    original_name = file.file_name if hasattr(file, 'file_name') and file.file_name else f"file_{unique_id}"
    file_size = file.file_size if hasattr(file, 'file_size') else 0
    save_file_to_db(
        file_id=unique_id,
        original_name=original_name,
        file_path=str(file_path),
        file_size=file_size,
        user_id=message.from_user.id,
        username=message.from_user.username,
        file_type=file_type,
        expires_at=expires_at,
        category=file_type
    )
    await state.clear()
    size_text = format_size(file_size) if file_size else "Неизвестно"
    category_name = FILE_CATEGORIES.get(file_type, '📄 Документы')
    await message.answer(
        f"✅ **Файл загружен!**\n\n"
        f"📄 **Название:** {escape_markdown(original_name)}\n"
        f"📦 **Размер:** {size_text}\n"
        f"📁 **Категория:** {category_name}\n"
        f"🔗 **ID:** `{unique_id}`\n"
        f"⏰ **Действует до:** {expires_at.strftime('%d.%m.%Y %H:%M')}\n\n"
        f"💡 Отправь этот ID другу, чтобы он мог скачать файл!",
        reply_markup=get_file_keyboard(unique_id, is_owner=True),
        parse_mode='Markdown'
    )

@dp.message(FileStates.waiting_for_file)
async def handle_other_content(message: Message):
    await message.answer(
        "❌ Это не файл! Отправь документ, фото, видео или аудио.",
        reply_markup=get_back_keyboard()
    )

@dp.callback_query(F.data == 'my_files')
async def show_my_files(callback: CallbackQuery):
    await callback.message.edit_text(
        "📁 **Выберите категорию:**\n\n"
        "Фильтр поможет найти нужные файлы быстрее:",
        reply_markup=get_category_filter_keyboard(),
        parse_mode='Markdown'
    )

@dp.callback_query(F.data.startswith('filter_'))
async def filter_files(callback: CallbackQuery):
    category = callback.data.split('_')[1]
    files = get_user_files(callback.from_user.id, category if category != 'all' else None)
    if not files:
        await callback.message.edit_text(
            "📭 Файлов не найдено в этой категории.",
            reply_markup=get_back_keyboard()
        )
        return
    await callback.message.edit_text(
        f"📁 **Твои файлы ({len(files)})**\n\n"
        "Нажми на файл для просмотра:",
        reply_markup=get_files_list_keyboard(files),
        parse_mode='Markdown'
    )

@dp.callback_query(F.data.startswith('view_'))
async def view_file(callback: CallbackQuery):
    file_id = callback.data.split('_')[1]
    file = get_file_by_id(file_id)
    if not file:
        await callback.answer("❌ Файл не найден или истёк срок!", show_alert=True)
        return
    is_owner = file['user_id'] == callback.from_user.id
    size_text = format_size(file['file_size']) if file['file_size'] else "Неизвестно"
    created_at = parse_timestamp(file['created_at'])
    expires_at = parse_timestamp(file['expires_at'])
    category_name = FILE_CATEGORIES.get(file['file_type'], '📄 Документы')
    text = f"📄 **Информация о файле**\n\n"
    text += f"📝 **Название:** {escape_markdown(file['original_name'])}\n"
    text += f"📦 **Размер:** {size_text}\n"
    text += f"📁 **Категория:** {category_name}\n"
    text += f"🔗 **ID:** `{file['file_id']}`\n"
    text += f"⬇️ **Скачиваний:** {file['download_count']}\n"
    if created_at:
        text += f"⏰ **Загружен:** {created_at.strftime('%d.%m.%Y %H:%M')}\n"
    if expires_at:
        text += f"⌛ **Истекает:** {expires_at.strftime('%d.%m.%Y %H:%M')}"
    await callback.message.edit_text(
        text,
        reply_markup=get_file_keyboard(file['file_id'], is_owner),
        parse_mode='Markdown'
    )

@dp.callback_query(F.data.startswith('download_'))
async def download_file(callback: CallbackQuery):
    file_id = callback.data.split('_')[1]
    file = get_file_by_id(file_id)
    if not file:
        await callback.answer("❌ Файл не найден или истёк срок!", show_alert=True)
        return
    file_path = Path(file['file_path'])
    if not file_path.exists():
        await callback.answer("❌ Файл был удалён с сервера!", show_alert=True)
        return
    increment_download_count(file_id, callback.from_user.id)
    await callback.answer("📤 Отправка файла...")
    try:
        await callback.message.answer_document(
            document=FSInputFile(file_path),
            caption=f"📄 {escape_markdown(file['original_name'])}\n🔗 Скачано через FileShare Bot"
        )
    except Exception as e:
        logger.error(f"Ошибка отправки: {e}")
        await callback.answer("❌ Ошибка при отправке файла", show_alert=True)

@dp.callback_query(F.data.startswith('delete_'))
async def delete_file(callback: CallbackQuery):
    file_id = callback.data.split('_')[1]
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑️ Да, удалить", callback_data=f"confirm_delete_{file_id}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"view_{file_id}")]
    ])
    await callback.message.edit_text(
        "⚠️ **Подтверждение удаления**\n\n"
        "Файл будет безвозвратно удалён!",
        reply_markup=keyboard,
        parse_mode='Markdown'
    )

@dp.callback_query(F.data.startswith('confirm_delete_'))
async def confirm_delete(callback: CallbackQuery):
    file_id = callback.data.split('_')[-1]
    file = get_file_by_id(file_id)
    if not file:
        await callback.answer("❌ Файл не найден!", show_alert=True)
        return
    if delete_file_from_db(file_id, callback.from_user.id):
        file_path = Path(file['file_path'])
        if file_path.exists():
            try:
                file_path.unlink()
            except:
                pass
        qr_path = QR_DIR / f"{file_id}.png"
        if qr_path.exists():
            try:
                qr_path.unlink()
            except:
                pass
        await callback.message.edit_text(
            "✅ Файл успешно удалён!",
            reply_markup=get_main_keyboard()
        )
        await callback.answer("🗑️ Файл удалён")
    else:
        await callback.answer("❌ Ошибка при удалении", show_alert=True)

@dp.callback_query(F.data.startswith('link_'))
async def get_file_link(callback: CallbackQuery):
    file_id = callback.data.split('_')[1]
    file = get_file_by_id(file_id)
    if not file:
        await callback.answer("❌ Файл не найден!", show_alert=True)
        return
    # ✅ ИСПРАВЛЕНО: получаем username через get_me()
    bot_info = await bot.get_me()
    link_text = f"@{bot_info.username}?start=file_{file_id}"
    await callback.answer(
        f"🔗 Ссылка:\n{link_text}\n\n"
        f"Нажми и удерживай для копирования!",
        show_alert=True
    )

@dp.callback_query(F.data.startswith('qr_'))
async def get_file_qr(callback: CallbackQuery):
    file_id = callback.data.split('_')[1]
    file = get_file_by_id(file_id)
    if not file:
        await callback.answer("❌ Файл не найден!", show_alert=True)
        return
    bot_info = await bot.get_me()
    link_text = f"@{bot_info.username}?start=file_{file_id}"
    qr_path = generate_qr_code(link_text, file_id)
    await callback.message.answer_photo(
        photo=FSInputFile(qr_path),
        caption=f"📱 **QR-код для файла**\n\n"
                f"📄 {escape_markdown(file['original_name'])}\n\n"
                f"Отсканируй QR-код для быстрого доступа!",
        parse_mode='Markdown'
    )
    await callback.answer("📱 QR-код сгенерирован!")

@dp.callback_query(F.data == 'find_file')
async def find_file_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(FileStates.waiting_for_link)
    await callback.message.edit_text(
        "🔍 **Поиск файла**\n\n"
        "Отправь ID файла для поиска:\n\n"
        "Пример: `aB3xK9mP`",
        reply_markup=get_back_keyboard(),
        parse_mode='Markdown'
    )

@dp.message(FileStates.waiting_for_link)
async def find_file_by_id(message: Message, state: FSMContext):
    file_id = message.text.strip()
    file = get_file_by_id(file_id)
    await state.clear()
    if not file:
        await message.answer(
            "❌ Файл не найден!\n\n"
            "Проверь ID и попробуй снова.",
            reply_markup=get_back_keyboard()
        )
        return
    if file['expires_at']:
        expires = parse_timestamp(file['expires_at'])
        if expires and expires < datetime.now():
            await message.answer(
                "⌛ Срок действия файла истёк!",
                reply_markup=get_back_keyboard()
            )
            return
    is_owner = file['user_id'] == message.from_user.id
    size_text = format_size(file['file_size']) if file['file_size'] else "Неизвестно"
    created_at = parse_timestamp(file['created_at'])
    await message.answer(
        f"📄 **Найден файл!**\n\n"
        f"📝 **Название:** {escape_markdown(file['original_name'])}\n"
        f"📦 **Размер:** {size_text}\n"
        f"⬇️ **Скачиваний:** {file['download_count']}\n"
        f"⏰ **Загружен:** {created_at.strftime('%d.%m.%Y %H:%M') if created_at else 'Неизвестно'}",
        reply_markup=get_file_keyboard(file['file_id'], is_owner),
        parse_mode='Markdown'
    )

@dp.callback_query(F.data == 'stats')
async def show_stats(callback: CallbackQuery):
    stats = get_stats(callback.from_user.id)
    total = stats['total']
    downloads = stats['total_downloads']
    text = f"📊 **Твоя статистика**\n\n"
    text += f"📁 **Файлов:** {total}\n"
    text += f"📦 **Всего места:** {format_size(stats['total_size'])}\n"
    text += f"⬇️ **Всего скачиваний:** {downloads}\n"
    if total > 0:
        rate = round(downloads / total * 100, 1)
        text += f"📈 **Популярность:** {create_progress_bar(min(int(rate), 100), 100, 10)} {rate}%"
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='🔄 Обновить', callback_data='stats')],
            [InlineKeyboardButton(text='🏠 В главное меню', callback_data='back')]
        ]),
        parse_mode='Markdown'
    )

@dp.callback_query(F.data == 'help_info')
async def show_help(callback: CallbackQuery):
    await callback.message.edit_text(
        "📚 **Справка по боту**\n\n"
        "📁 **FileShare Bot** — персональный файлообменник\n\n"
        "🔹 **Возможности:**\n"
        "• Загрузка до 2 ГБ (Premium)\n"
        "• Уникальные ссылки\n"
        "• QR-коды для файлов\n"
        "• Категории файлов\n"
        "• История скачиваний\n"
        "• Автоудаление через 24ч\n\n"
        "🔗 **Поддержка:** @HelloFridge_Bot\n"
        "🌐 **Сайт:** tegbi.netlify.app",
        reply_markup=get_back_keyboard(),
        parse_mode='Markdown'
    )

@dp.callback_query(F.data == 'back')
async def go_back(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await callback.message.edit_text(
            "🏠 **Главное меню**",
            reply_markup=get_main_keyboard(),
            parse_mode='Markdown'
        )
    except TelegramBadRequest:
        await callback.message.answer(
            "🏠 **Главное меню**",
            reply_markup=get_main_keyboard(),
            parse_mode='Markdown'
        )

# ─────────────────────────────────────────────────────────────
# 🧹 Очистка старых файлов
# ─────────────────────────────────────────────────────────────
async def cleanup_old_files():
    while True:
        try:
            with get_db() as conn:
                expired = conn.execute(
                    '''SELECT * FROM files 
                       WHERE expires_at IS NOT NULL 
                       AND expires_at < datetime("now")'''
                ).fetchall()
                for file in expired:
                    file_path = Path(file['file_path'])
                    if file_path.exists():
                        try:
                            file_path.unlink()
                            logger.info(f"🗑️ Удалён файл: {file['original_name']}")
                        except Exception as e:
                            logger.error(f"Ошибка удаления файла: {e}")
                    qr_path = QR_DIR / f"{file['file_id']}.png"
                    if qr_path.exists():
                        try:
                            qr_path.unlink()
                        except:
                            pass
                    conn.execute('DELETE FROM files WHERE id = ?', (file['id'],))
                    conn.commit()
                if expired:
                    logger.info(f"🧹 Очищено {len(expired)} файлов")
        except Exception as e:
            logger.error(f"Ошибка в cleanup: {e}")
        await asyncio.sleep(3600)

# ─────────────────────────────────────────────────────────────
# 🚀 Запуск
# ─────────────────────────────────────────────────────────────
async def main():
    init_db()
    logger.info("📁 FileShare Bot запущен!")
    asyncio.create_task(run_webserver())
    asyncio.create_task(cleanup_old_files())
    # ✅ ИСПРАВЛЕНО: allowed_updates=[] предотвращает конфликты
    await dp.start_polling(bot, allowed_updates=[])

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Бот остановлен")