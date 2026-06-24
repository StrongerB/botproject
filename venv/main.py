import asyncio
import logging
import io
import os
from datetime import datetime
from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.filters import Command
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, FSInputFile
import aiosqlite
from config import BOT_TOKEN
from texts import main_text, error_text
from pypdf import PdfReader
from docx import Document
from openai import AsyncOpenAI
import tempfile

DB_PATH = 'users.db'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

session = AiohttpSession(proxy="http://201.51.20.178:3128")
bot = Bot(token=BOT_TOKEN, session=session)
dp = Dispatcher(storage=MemoryStorage())
router = Router()

deepseek_client = AsyncOpenAI(
    api_key="sk-aitunnel-SyRxNmO45HRboKeVqaxZC3qBp8hCUNiF",
    base_url="https://api.aitunnel.ru/v1/"
)

mainkeyboard_markup = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="😊 Профиль", callback_data="Профиль")],
        [InlineKeyboardButton(text="❓ Задать вопрос", callback_data="Задать вопрос")],
        [InlineKeyboardButton(text="📄 Отправить файл", callback_data="Отправить файл")],
        [InlineKeyboardButton(text="📞 Помощь", callback_data="Помощь")]
    ]
)

profilekeyboard_markup = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="↶ Вернуться в меню", callback_data="Вернуться в меню")]   
    ]
)

helpkeyboard_markup = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="↶ Вернуться в меню", callback_data="Вернуться в меню")]
    ]
)

admin_keyboard_markup = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="👑 Панель администратора", callback_data="admin_panel")],
        [InlineKeyboardButton(text="↶ Вернуться в меню", callback_data="Вернуться в меню")]
    ]
)

class TaskStates(StatesGroup):
    waiting_for_question = State()
    waiting_for_document = State()
    waiting_for_file = State()
    waiting_for_file_question = State()

def extract_text_from_bytes(file_bytes: bytes, file_name: str) -> str:
    ext = file_name.lower().split('.')[-1]
    text = ""
    try:
        if ext == 'txt':
            return file_bytes.decode('utf-8', errors='ignore')
        elif ext == 'pdf':
            pdf_file = io.BytesIO(file_bytes)
            reader = PdfReader(pdf_file)
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
            return text
        elif ext == 'docx':
            docx_file = io.BytesIO(file_bytes)
            doc = Document(docx_file)
            return "\n".join([p.text for p in doc.paragraphs])
        elif ext in ['jpg', 'jpeg', 'png', 'gif']:
            return "[Изображение]"
        else:
            return f"[Файл типа {ext}]"
    except Exception as e:
        logger.error(f"Ошибка извлечения текста из {file_name}: {e}")
        return ""

async def upload_file_to_deepseek(file_bytes: bytes, file_name: str) -> str:
    """Загружает файл в DeepSeek и возвращает ID файла"""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{file_name.split('.')[-1]}") as tmp_file:
            tmp_file.write(file_bytes)
            tmp_file_path = tmp_file.name
        
        with open(tmp_file_path, 'rb') as file:
            response = await deepseek_client.files.create(
                file=file,
                purpose="assistants"
            )
        
        os.unlink(tmp_file_path)
        return response.id
    except Exception as e:
        logger.error(f"Ошибка загрузки файла в DeepSeek: {e}")
        return None

async def ask_deepseek_with_file_upload(user_question: str, file_bytes: bytes, file_name: str) -> str:
    """Отправляет вопрос с файлом в DeepSeek"""
    try:
        file_id = await upload_file_to_deepseek(file_bytes, file_name)
        
        if not file_id:
            return "❌ Не удалось загрузить файл в нейросеть. Попробуйте другой файл или отправьте текст напрямую."
        
        system_prompt = """Ты корпоративный ИИ-ассистент. Отвечай на вопросы пользователей профессионально, 
        четко и по делу. Используй информацию из предоставленного файла для ответа на вопрос.
        Если информации в файле недостаточно - честно скажи об этом."""

        response = await deepseek_client.chat.completions.create(
            model="deepseek-v4-pro",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": [
                    {"type": "text", "text": f"Проанализируй файл и ответь на вопрос:\n{user_question}"},
                    {"type": "file", "file_id": file_id}
                ]}
            ],
            stream=False
        )
        
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"Ошибка DeepSeek API с файлом: {e}")
        return f"❌ Произошла ошибка при анализе файла: {str(e)}"

async def ask_deepseek_with_file_text(user_question: str, file_text: str, file_name: str) -> str:
    """Отправляет вопрос с извлеченным текстом файла в DeepSeek"""
    try:
        if not file_text or file_text.strip() == "":
            return "⚠️ Не удалось извлечь текст из файла. Попробуйте другой файл."
        
        system_prompt = f"""Ты корпоративный ИИ-ассистент. Отвечай на вопросы пользователей профессионально, 
        четко и по делу. Используй информацию из предоставленного файла для ответа на вопрос.
        Если информации в файле недостаточно - честно скажи об этом.

        Имя файла: {file_name}
        
        Содержимое файла:
        {file_text[:8000]}
        """
        
        response = await deepseek_client.chat.completions.create(
            model="deepseek-v4-flash",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_question}
            ],
            stream=False
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"Ошибка DeepSeek API: {e}")
        return f"❌ Произошла ошибка при анализе файла: {str(e)}"

async def ask_deepseek(user_question: str) -> str:
    try:
        system_prompt = """Ты корпоративный ИИ-ассистент. Отвечай на вопросы пользователей профессионально, 
        четко и по делу. Если информации недостаточно - честно скажи об этом."""
        
        response = await deepseek_client.chat.completions.create(
            model="deepseek-v4-flash",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_question}
            ],
            stream=False
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"Ошибка DeepSeek API: {e}")
        return "Извините, произошла ошибка при обработке вашего вопроса. Попробуйте позже."

async def init_db(user_id, username, first_name, is_bot):
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    join_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_bot BOOL,
                    status TEXT DEFAULT 'user',
                    question_amount INTEGER DEFAULT 0
                )
            ''')
            await db.commit()
        except aiosqlite.Error as e:
            logger.error(f'Ошибка создания таблицы: {e}')
            return False
        
        cursor = await db.execute('SELECT 1 FROM users WHERE id = ?', (user_id,))
        exists = await cursor.fetchone()
        if exists:
            return True
        
        try:
            await db.execute(
                'INSERT INTO users (id, username, first_name, is_bot) VALUES (?, ?, ?, ?)',
                (user_id, username or "Не указан", first_name or "Не указан", is_bot)
            )
            await db.commit()
            logger.info(f"Добавлен пользователь: {user_id}")
            return True
        except aiosqlite.Error as e:
            logger.error(f'Ошибка добавления пользователя: {e}')
            return False

async def select_value_db(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            cursor = await db.execute(
                'SELECT status, question_amount FROM users WHERE id=?', 
                (user_id,)
            )
            result = await cursor.fetchone()
            if result is None:
                logger.info(f'Пользователь {user_id} не найден')
                return None
            return result
        except aiosqlite.Error as e:
            logger.error(f"Ошибка загрузки данных: {e}")
            return None

async def check_user_status(user_id: int) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            'SELECT status FROM users WHERE id = ?', 
            (user_id,)
        )
        result = await cursor.fetchone()
        if result:
            return result[0]
        return "user"

async def update_user_status(user_id: int, new_status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            'UPDATE users SET status = ? WHERE id = ?',
            (new_status, user_id)
        )
        await db.commit()

@dp.message(Command('start'))
async def start_command(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name
    is_bot = message.from_user.is_bot
    
    await init_db(user_id, username, first_name, is_bot)
    user_status = await check_user_status(user_id)
    
    if user_status == "admin":
        keyboard = admin_keyboard_markup
    else:
        keyboard = mainkeyboard_markup
    
    await message.answer(
        text=main_text,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard
    )

@dp.callback_query(F.data == 'Меню')
async def show_menu(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    username = callback_query.from_user.username
    first_name = callback_query.from_user.first_name
    is_bot = callback_query.from_user.is_bot
    
    await init_db(user_id, username, first_name, is_bot)
    user_status = await check_user_status(user_id)
    
    if user_status == "admin":
        keyboard = admin_keyboard_markup
    else:
        keyboard = mainkeyboard_markup
    
    await callback_query.message.edit_text(
        text=main_text,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard
    )
    await callback_query.answer()

@dp.callback_query(F.data == 'Профиль')
async def show_profile(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    username = callback_query.from_user.username
    first_name = callback_query.from_user.first_name
    is_bot = callback_query.from_user.is_bot
    
    await init_db(user_id, username, first_name, is_bot)
    result = await select_value_db(user_id)
    
    if result is None:
        await callback_query.message.answer(
            text=error_text,
            parse_mode=ParseMode.HTML
        )
        await callback_query.answer("Ошибка загрузки профиля")
        return
    
    profile_text = f'''
<b><i>Ваш профиль:</i></b>

<blockquote>ID: {user_id}</blockquote>
<blockquote>Статус: {result[0]}</blockquote>
<blockquote>Кол-во отправленных вопросов: {result[1]}</blockquote>
'''
    await callback_query.message.answer(
        text=profile_text,
        parse_mode=ParseMode.HTML,
        reply_markup=profilekeyboard_markup
    )
    await callback_query.answer("Профиль загружен")

@dp.callback_query(F.data == 'Задать вопрос')
async def ask_question(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = callback_query.from_user.id
    username = callback_query.from_user.username
    first_name = callback_query.from_user.first_name
    is_bot = callback_query.from_user.is_bot
    
    await init_db(user_id, username, first_name, is_bot)
    await state.set_state(TaskStates.waiting_for_question)
    
    await callback_query.message.edit_text(
        text="❓ Напишите свой вопрос:",
        reply_markup=mainkeyboard_markup
    )
    await callback_query.answer()

@dp.message(TaskStates.waiting_for_question)
async def process_question(message: types.Message, state: FSMContext):
    question = message.text
    user_id = message.from_user.id
    
    logger.info(f"Получен вопрос от пользователя {user_id}: {question[:50]}...")
    
    await message.answer(
        text="⏳ Обрабатываю ваш вопрос... Пожалуйста, подождите.",
        parse_mode=ParseMode.HTML
    )
    
    try:
        ai_response = await ask_deepseek(question)
        
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                'UPDATE users SET question_amount = question_amount + 1 WHERE id = ?',
                (user_id,)
            )
            await db.commit()
        
        await state.clear()
        
        response_text = f"""
<b>🤖 Ответ:</b>

{ai_response}

<blockquote>📝 Ваш вопрос: {question}</blockquote>
"""
        await message.answer(
            text=response_text,
            parse_mode=ParseMode.HTML,
            reply_markup=mainkeyboard_markup
        )
    except Exception as e:
        logger.error(f"Ошибка обработки вопроса: {e}")
        await message.answer(
            text="❌ Произошла ошибка при обработке вашего вопроса. Попробуйте позже.",
            parse_mode=ParseMode.HTML,
            reply_markup=mainkeyboard_markup
        )
        await state.clear()

@dp.callback_query(F.data == 'Отправить файл')
async def ask_for_file(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = callback_query.from_user.id
    username = callback_query.from_user.username
    first_name = callback_query.from_user.first_name
    is_bot = callback_query.from_user.is_bot
    
    await init_db(user_id, username, first_name, is_bot)
    await state.set_state(TaskStates.waiting_for_file)
    
    await callback_query.message.edit_text(
        text="📄 Отправьте файл (PDF, DOCX, TXT, JPG, PNG).\n\nПосле отправки файла вы сможете задать вопрос по его содержанию.",
        reply_markup=mainkeyboard_markup
    )
    await callback_query.answer()

@dp.message(TaskStates.waiting_for_file, F.document)
async def process_file(message: types.Message, state: FSMContext):
    document = message.document
    user_id = message.from_user.id
    file_name = document.file_name
    file_id = document.file_id
    
    logger.info(f"Получен файл от пользователя {user_id}: {file_name}")
    
    try:
        await message.answer(
            text="⏳ Загружаю и анализирую файл... Пожалуйста, подождите.",
            parse_mode=ParseMode.HTML
        )
        
        file_info = await bot.get_file(file_id)
        file_buffer = io.BytesIO()
        await bot.download_file(file_info.file_path, destination=file_buffer)
        file_bytes = file_buffer.getvalue()
        
        file_text = extract_text_from_bytes(file_bytes, file_name)
        
        logger.info(f"Извлечен текст из файла {file_name}: {len(file_text)} символов")
        
        if not file_text or file_text.strip() == "":
            await message.answer(
                text="⚠️ Не удалось извлечь текст из файла. Попробуйте другой файл.",
                parse_mode=ParseMode.HTML,
                reply_markup=mainkeyboard_markup
            )
            await state.clear()
            return
        
        await state.update_data(
            file_text=file_text, 
            file_name=file_name,
            file_bytes=file_bytes
        )
        await state.set_state(TaskStates.waiting_for_file_question)
        
        await message.answer(
            text=f"✅ Файл '{file_name}' успешно загружен! Извлечено {len(file_text)} символов.\n\nТеперь напишите ваш вопрос по этому файлу:",
            parse_mode=ParseMode.HTML,
            reply_markup=mainkeyboard_markup
        )
    except Exception as e:
        logger.error(f"Ошибка обработки файла: {e}")
        await message.answer(
            text="❌ Произошла ошибка при обработке файла. Попробуйте еще раз.",
            parse_mode=ParseMode.HTML,
            reply_markup=mainkeyboard_markup
        )
        await state.clear()

@dp.message(TaskStates.waiting_for_file_question)
async def process_file_question(message: types.Message, state: FSMContext):
    question = message.text
    user_id = message.from_user.id
    
    data = await state.get_data()
    file_text = data.get('file_text', '')
    file_name = data.get('file_name', 'Неизвестный файл')
    file_bytes = data.get('file_bytes', None)
    
    logger.info(f"Получен вопрос по файлу от пользователя {user_id}: {question[:50]}...")
    
    await message.answer(
        text="⏳ Анализирую файл и обрабатываю ваш вопрос... Пожалуйста, подождите.",
        parse_mode=ParseMode.HTML
    )
    
    try:
        # Пытаемся загрузить файл напрямую в DeepSeek
        if file_bytes:
            logger.info("Пытаюсь загрузить файл напрямую в DeepSeek...")
            ai_response = await ask_deepseek_with_file_upload(question, file_bytes, file_name)
        else:
            # Если не удалось загрузить, используем извлеченный текст
            logger.info("Использую извлеченный текст файла...")
            ai_response = await ask_deepseek_with_file_text(question, file_text, file_name)
        
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                'UPDATE users SET question_amount = question_amount + 1 WHERE id = ?',
                (user_id,)
            )
            await db.commit()
        
        await state.clear()
        
        response_text = f"""
<b>📄 Анализ файла '{file_name}':</b>

<b>🤖 Ответ:</b>

{ai_response}

<blockquote>📝 Ваш вопрос: {question}</blockquote>
"""
        await message.answer(
            text=response_text,
            parse_mode=ParseMode.HTML,
            reply_markup=mainkeyboard_markup
        )
    except Exception as e:
        logger.error(f"Ошибка обработки вопроса по файлу: {e}")
        await message.answer(
            text=f"❌ Произошла ошибка при обработке вашего вопроса: {str(e)}",
            parse_mode=ParseMode.HTML,
            reply_markup=mainkeyboard_markup
        )
        await state.clear()

@dp.callback_query(F.data == 'Помощь')
async def show_help(callback_query: types.CallbackQuery):
    help_text = """
📞 <b>Помощь</b>

Доступные команды:
• /start - Главное меню
• Профиль - Ваш профиль
• Задать вопрос - Задать вопрос
• Отправить файл - Отправить файл для анализа
• Помощь - Эта справка

<i>Для получения ответа на ваш вопрос используйте соответствующие кнопки.</i>
"""
    await callback_query.message.edit_text(
        text=help_text,
        parse_mode=ParseMode.HTML,
        reply_markup=helpkeyboard_markup
    )
    await callback_query.answer()

@dp.callback_query(F.data == 'Вернуться в меню')
async def back_to_menu(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    user_status = await check_user_status(user_id)
    
    if user_status == "admin":
        keyboard = admin_keyboard_markup
    else:
        keyboard = mainkeyboard_markup
    
    await callback_query.message.edit_text(
        text=main_text,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard
    )
    await callback_query.answer()

@dp.callback_query(F.data == 'admin_panel')
async def admin_panel(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    user_status = await check_user_status(user_id)
    
    if user_status != "admin":
        await callback_query.message.answer(
            text="❌ У вас нет прав администратора!",
            parse_mode=ParseMode.HTML
        )
        await callback_query.answer()
        return
    
    admin_text = """
👑 <b>Панель администратора</b>

<b>Доступные действия:</b>
• Выдать права администратора пользователю
• Посмотреть статистику
• Управление пользователями

<i>Используйте команды для управления:</i>
/admin_add <user_id> - Выдать права админа
/admin_remove <user_id> - Забрать права админа
/stats - Показать статистику
"""
    await callback_query.message.edit_text(
        text=admin_text,
        parse_mode=ParseMode.HTML,
        reply_markup=admin_keyboard_markup
    )
    await callback_query.answer()

@dp.message(Command('admin_add'))
async def add_admin(message: types.Message):
    user_id = message.from_user.id
    sender_status = await check_user_status(user_id)
    
    if sender_status != "admin":
        await message.answer("❌ У вас нет прав администратора!")
        return
    
    try:
        target_user_id = int(message.text.split()[1])
        await update_user_status(target_user_id, "admin")
        await message.answer(f"✅ Пользователю {target_user_id} выданы права администратора!")
    except (IndexError, ValueError):
        await message.answer("❌ Использование: /admin_add <user_id>")
    except Exception as e:
        logger.error(f"Ошибка добавления админа: {e}")
        await message.answer("❌ Произошла ошибка при добавлении администратора.")

@dp.message(Command('admin_remove'))
async def remove_admin(message: types.Message):
    user_id = message.from_user.id
    sender_status = await check_user_status(user_id)
    
    if sender_status != "admin":
        await message.answer("❌ У вас нет прав администратора!")
        return
    
    try:
        target_user_id = int(message.text.split()[1])
        await update_user_status(target_user_id, "user")
        await message.answer(f"✅ У пользователя {target_user_id} забраны права администратора!")
    except (IndexError, ValueError):
        await message.answer("❌ Использование: /admin_remove <user_id>")
    except Exception as e:
        logger.error(f"Ошибка удаления админа: {e}")
        await message.answer("❌ Произошла ошибка при удалении администратора.")

@dp.message(Command('stats'))
async def show_stats(message: types.Message):
    user_id = message.from_user.id
    user_status = await check_user_status(user_id)
    
    if user_status != "admin":
        await message.answer("❌ У вас нет прав администратора!")
        return
    
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute('SELECT COUNT(*) FROM users')
        total_users = (await cursor.fetchone())[0]
        
        cursor = await db.execute('SELECT COUNT(*) FROM users WHERE status = "admin"')
        total_admins = (await cursor.fetchone())[0]
        
        cursor = await db.execute('SELECT SUM(question_amount) FROM users')
        total_questions = (await cursor.fetchone())[0] or 0
    
    stats_text = f"""
📊 <b>Статистика бота</b>

👥 Всего пользователей: {total_users}
👑 Администраторов: {total_admins}
❓ Всего вопросов: {total_questions}
"""
    await message.answer(
        text=stats_text,
        parse_mode=ParseMode.HTML
    )

async def main():
    if not os.path.isfile(DB_PATH):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    join_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_bot BOOL,
                    status TEXT DEFAULT 'user',
                    question_amount INTEGER DEFAULT 0
                )
            ''')
            await db.commit()
        logger.info("База данных создана")
    
    logger.info("Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен")