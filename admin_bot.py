import json
import asyncio
import os
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import StatesGroup, State
from dotenv import load_dotenv
import datetime
from contextlib import suppress
from aiogram.types import FSInputFile

load_dotenv()
TOKEN = os.getenv('BOT_TOKEN')
CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.json')
# --- Ограничение доступа по user_id ---
OWNER_ID = int(os.getenv('OWNER_ID'))

from functools import wraps

def owner_only(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        message = None
        for arg in args:
            if isinstance(arg, Message):
                message = arg
                break
        if not message:
            message = kwargs.get('message')
        if not message:
            return await func(*args, **kwargs)
        if message.from_user.id != OWNER_ID:
            print(f"[SECURITY] Попытка доступа не-OWNER: {message.from_user.id}, text: {getattr(message, 'text', None)}")
            await message.answer("<b>Доступ запрещён.</b>", parse_mode="HTML")
            return
        print(f"[OWNER] Доступ разрешён: {message.from_user.id}, text: {getattr(message, 'text', None)}")
        return await func(*args, **kwargs)
    return wrapper
bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# -----------------------------------
# FSM состояния
# -----------------------------------

class BotStates(StatesGroup):
    waiting_for_msg = State()
    waiting_for_delay_hours = State()
    waiting_for_delay_minutes = State()
    waiting_for_delay_seconds = State()
    waiting_for_delay_unit = State()
    waiting_for_delay = State()
    selected_group = State()

class ScheduleStates(StatesGroup):
    waiting_for_time = State()
    waiting_for_scheduled_message = State()

class EditScheduleStates(StatesGroup):
    waiting_for_new_time = State()
    waiting_for_new_message = State()

class DeleteScheduleStates(StatesGroup):
    waiting_for_group = State()
    waiting_for_entry = State()

# -----------------------------------
# Конфиг
# -----------------------------------

def load_config():
    try:
        with open(CONFIG_PATH, 'r') as f:
            data = json.load(f)
            print(f"[LOG] Загружен config: {data}")
            return data
    except Exception as e:
        print(f"[ERROR] Не удалось загрузить config: {e}")
        return {"chats": {}, "active": False, "scheduled": {}, "schedule_active": False}

def save_config(data):
    try:
        with open(CONFIG_PATH, 'w') as f:
            json.dump(data, f, indent=2)
            print(f"[LOG] Сохранён config: {data}")
    except Exception as e:
        print(f"[ERROR] Не удалось сохранить config: {e}")

# -----------------------------------
# Кнопки для групп
# -----------------------------------
def get_group_keyboard(action_prefix):
    config = load_config()
    buttons = []
    for chat in config["chats"].keys():
        buttons.append([InlineKeyboardButton(text=chat, callback_data=f"{action_prefix}:{chat}")])

    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_groups_reply_keyboard():
    config = load_config()
    buttons = [[KeyboardButton(text=chat)] for chat in config["chats"].keys()]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_back_inline_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu")]
    ])

def get_groups_with_back_keyboard():
    config = load_config()
    buttons = [[KeyboardButton(text=chat)] for chat in config["chats"].keys()]
    buttons.append([KeyboardButton(text="🔙 Назад")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_edit_group_inline_keyboard(groups):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=group, callback_data=f"edit_schedule_group:{group}")]
            for group in groups
        ]
    )

# -----------------------------------
# Добавление группы
# -----------------------------------

@dp.message(F.text == "➕ Добавить группу")
@owner_only
async def btn_add(message: Message):
    # Создаем клавиатуру с только кнопкой назад
    back_button = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔙 Назад")]  # Кнопка "Назад"
        ],
        resize_keyboard=True
    )
    await message.answer("<i> Введите ссылку на группу, которую хотите добавить: </i> ", parse_mode="HTML", reply_markup=back_button)

@dp.message(lambda m: m.text and (m.text.startswith("https://t.me/") or m.text.startswith("@")))
@owner_only
async def handle_group_add(message: Message):
    config = load_config()
    link = message.text.strip()
    # Приводим к формату @groupname
    if link.startswith("https://t.me/"):
        link = "@" + link.split("https://t.me/")[-1]
    if link.startswith("@"):  # убираем лишние символы после username
        link = link.split()[0].split("/")[0]
    # Проверяем наличие группы только по @groupname
    groupnames = [k if k.startswith("@") else "@" + k.split("https://t.me/")[-1].split()[0].split("/")[0] for k in config["chats"].keys()]
    if link in groupnames:
        await message.answer("<i> 🔺 Данная группа уже добавлена </i>" , parse_mode="HTML",)
        return
    config["chats"][link] = {"message": None, "delay": 60}
    save_config(config)
    await message.answer(f"<i> 🔸 Группа добавлена: </i> {link}", parse_mode="HTML",)

# -----------------------------------
# Сообщение
# -----------------------------------
@dp.message(F.text == "💬 Изменить сообщение")
@owner_only
async def btn_setmsg(message: Message):
    # Сначала инлайн-кнопки с группами
    await message.answer("<b> Выберите группу для изменения сообщения: </b>", parse_mode="HTML", reply_markup=get_group_keyboard("msg"))
    # Затем обычная клавиатура только с кнопкой "Назад"
    back_button = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🔙 Назад")]],
        resize_keyboard=True
    )
    await message.answer("<i>Для возврата нажмите на кнопку </i><b>Назад</b>",
        parse_mode="HTML", reply_markup=back_button)

@dp.callback_query(F.data.startswith("msg:"))
async def group_msg_selected(callback: types.CallbackQuery, state: FSMContext):
    print(f"[CALLBACK] msg: from_user={callback.from_user.id}, data={callback.data}, state={await state.get_state()}")
    chat = callback.data.split("msg:")[1]
    await state.update_data(selected_group=chat)
    await callback.message.answer(f"<b> Отправьте сообщение для {chat}. Это может быть текст, медиа, текст + медиа.</b>",parse_mode="HTML",)
    await state.set_state(BotStates.waiting_for_msg)
    await callback.answer()

@dp.message(BotStates.waiting_for_msg)
@owner_only
async def handle_msg_input(message: Message, state: FSMContext):
    print(f"[FSM] Состояние: {await state.get_state()}, message: {message.text}")
    data = await state.get_data()
    chat = data["selected_group"]
    config = load_config()

    if message.photo:
        photo = message.photo[-1]
        file = await bot.get_file(photo.file_id)
        file_path = f"media/{photo.file_unique_id}.jpg"

        # создаём папку если нет
        os.makedirs("media", exist_ok=True)
        await bot.download_file(file.file_path, destination=file_path)

        config["chats"][chat]["media"] = file_path
        config["chats"][chat]["message"] = message.caption or ""
        config["chats"][chat]["caption_entities"] = [e.model_dump() for e in message.caption_entities] if message.caption_entities else None
        await message.answer(f"<i>🔸 Медиа + подпись сохранены для {chat}</i>", parse_mode="HTML",)

    elif message.document:
        file_id = message.document.file_id
        config["chats"][chat]["media"] = file_id
        config["chats"][chat]["message"] = message.caption or ""
        config["chats"][chat]["caption_entities"] = [e.model_dump() for e in message.caption_entities] if message.caption_entities else None
        await message.answer(f"<i> 🔸 Медиа + подпись сохранены для {chat} </i>",parse_mode="HTML",)
    elif message.text:
        config["chats"][chat]["message"] = message.text
        config["chats"][chat]["entities"] = [e.model_dump() for e in message.entities] if message.entities else None
        config["chats"][chat].pop("media", None)
        config["chats"][chat].pop("caption_entities", None)
        await message.answer(f"<i>🔸 Текст сохранен для {chat} </i>",parse_mode="HTML",)
    else:
        await message.answer("<b> ♦️ Не удалось распознать сообщение. Отправь текст или медиа.</b>", parse_mode="HTML",)
        return

    save_config(config)
    await message.answer("<b> 🔽 Выберите действие: </b>", parse_mode="HTML", reply_markup=main_menu)
    await state.clear()

# -----------------------------------
# Задержка Выбор единицы времени (секунды, минуты, часы)
# -----------------------------------
def format_time(seconds):
    if seconds < 60:
        return f"{seconds} сек."
    elif seconds < 3600:
        minutes = seconds // 60
        return f"{minutes} мин."
    elif seconds >= 3600:
        hours = seconds // 3600
        return f"{hours} ч."
time_unit_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="⏱ Секунды")],
        [KeyboardButton(text="🕒 Минуты")],
        [KeyboardButton(text="⏳ Часы")],
        [KeyboardButton(text="🔙 Назад")]  # Кнопка для возврата в меню
    ],
    resize_keyboard=True
)
@dp.message(F.text == "⏰ Изменить задержку")
@owner_only
async def btn_delay(message: Message, state: FSMContext):
    await message.answer("<b> Выберите группу для изменения задержки:</b>", parse_mode="HTML", reply_markup=get_group_keyboard("delay"))
    back_button = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🔙 Назад")]],
        resize_keyboard=True
    )
    await message.answer("<i>Для возврата нажмите на кнопку </i><b>Назад</b>",
        parse_mode="HTML", reply_markup=back_button)

back_button = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="🔙 Назад")]],
    resize_keyboard=True
)
@dp.callback_query(F.data.startswith("delay:"))
async def group_delay_selected(callback: types.CallbackQuery, state: FSMContext):
    print(f"[CALLBACK] delay: from_user={callback.from_user.id}, data={callback.data}, state={await state.get_state()}")
    chat = callback.data.split("delay:")[1]
    await state.update_data(selected_group=chat)
    msg = await callback.message.answer("Текущая задержка: <b>00:00:00</b>", parse_mode="HTML")
    await state.update_data(delay_hours=0, delay_minutes=0, delay_seconds=0, delay_msg_id=msg.message_id)
    ask = await callback.message.answer("<i> Введите часы: </i>", reply_markup=back_button  ,parse_mode="HTML")
    await state.update_data(ask_msg_id=ask.message_id)
    await state.set_state(BotStates.waiting_for_delay_hours)
    await callback.answer()

@dp.message(BotStates.waiting_for_delay_hours)
@owner_only
async def input_delay_hours(message: Message, state: FSMContext):
    print(f"[FSM] Состояние: {await state.get_state()}, message: {message.text}")
    if message.text == "🔙 Назад":
        return  # обработка выше
    try:
        hours = int(message.text)
        if hours < 0 or hours > 23:
            raise ValueError
    except ValueError:
        return await message.answer("<b>Введите корректное число часов (0-23) </b>", parse_mode="HTML", reply_markup=back_button)
    data = await state.get_data()
    await state.update_data(delay_hours=hours)
    msg_id = data["delay_msg_id"]
    
    # Всегда пытаемся отредактировать существующее сообщение
    try:
        await message.bot.edit_message_text(
            text=f"Текущая задержка: <b>{hours:02d}:00:00</b>",
            chat_id=message.chat.id,
            message_id=msg_id,
            parse_mode="HTML"
        )
    except Exception as e:
        print(f"[ERROR] Не удалось отредактировать сообщение (часы): {e}")
    
    await message.bot.delete_message(message.chat.id, data["ask_msg_id"])
    ask = await message.answer("<i> Введите минуты: </i>", parse_mode="HTML", reply_markup=back_button)
    await state.update_data(ask_msg_id=ask.message_id)
    await state.set_state(BotStates.waiting_for_delay_minutes)

@dp.message(BotStates.waiting_for_delay_minutes)
@owner_only
async def input_delay_minutes(message: Message, state: FSMContext):
    print(f"[FSM] Состояние: {await state.get_state()}, message: {message.text}")
    if message.text == "🔙 Назад":
        return  # обработка выше
    try:
        minutes = int(message.text)
        if minutes < 0 or minutes > 59:
            raise ValueError
    except ValueError:
        return await message.answer("<b> Введите корректное число минут (0-59) </b>", parse_mode="HTML", reply_markup=back_button)
    data = await state.get_data()
    await state.update_data(delay_minutes=minutes)
    msg_id = data["delay_msg_id"]
    hours = data["delay_hours"]
    
    # Всегда пытаемся отредактировать существующее сообщение
    try:
        await message.bot.edit_message_text(
            text=f"Текущая задержка: <b>{hours:02d}:{minutes:02d}:00</b>",
            chat_id=message.chat.id,
            message_id=msg_id,
            parse_mode="HTML"
        )
    except Exception as e:
        print(f"[ERROR] Не удалось отредактировать сообщение (минуты): {e}")
    
    await message.bot.delete_message(message.chat.id, data["ask_msg_id"])
    ask = await message.answer("<i> Введите секунды: </i>", parse_mode="HTML", reply_markup=back_button)
    await state.update_data(ask_msg_id=ask.message_id)
    await state.set_state(BotStates.waiting_for_delay_seconds)

@dp.message(BotStates.waiting_for_delay_seconds)
@owner_only
async def input_delay_seconds(message: Message, state: FSMContext):
    print(f"[FSM] Состояние: {await state.get_state()}, message: {message.text}")
    if message.text == "🔙 Назад":
        return  # обработка выше
    try:
        seconds = int(message.text)
        if seconds < 0 or seconds > 59:
            raise ValueError
    except ValueError:
        return await message.answer("<b> Введите корректное число секунд (0-59) </b> ", parse_mode="HTML", reply_markup=back_button)
    data = await state.get_data()
    hours = data["delay_hours"]
    minutes = data["delay_minutes"]
    await state.update_data(delay_seconds=seconds)
    msg_id = data["delay_msg_id"]
    
    # Всегда пытаемся отредактировать существующее сообщение
    try:
        await message.bot.edit_message_text(
            text=f"Текущая задержка: <b>{hours:02d}:{minutes:02d}:{seconds:02d}</b>",
            chat_id=message.chat.id,
            message_id=msg_id,
            parse_mode="HTML"
        )
    except Exception as e:
        print(f"[ERROR] Не удалось отредактировать сообщение (секунды): {e}")
    
    await message.bot.delete_message(message.chat.id, data["ask_msg_id"])
    total_seconds = hours * 3600 + minutes * 60 + seconds
    config = load_config()
    chat = data["selected_group"]
    config["chats"][chat]["delay"] = total_seconds
    save_config(config)
    await message.answer(f"<i>🔸 Задержка для {chat} обновлена на {hours:02d}:{minutes:02d}:{seconds:02d} </i>", parse_mode="HTML",)
    await state.clear()
    await message.answer("<b> 🔽 Выберите действие: </b>", parse_mode="HTML", reply_markup=main_menu)

# -----------------------------------
# Удаление
# -----------------------------------

@dp.message(F.text == "❌ Удалить группу")
@owner_only
async def btn_remove(message: Message):
    config = load_config()
    if not config["chats"]:
        await message.answer("<i>🔶 Список групп пуст.</i>", parse_mode="HTML")
        return
    # Сначала инлайн-кнопки с группами
    await message.answer("<b> Выберите группу для удаления: </b>", parse_mode="HTML", reply_markup=get_group_keyboard("remove"))
    # Затем обычная клавиатура только с кнопкой "Назад"
    back_button = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🔙 Назад")]],
        resize_keyboard=True
    )
    await message.answer("<i>Для возврата нажмите на кнопку </i><b>Назад</b>",
        parse_mode="HTML", reply_markup=back_button)

@dp.callback_query(F.data.startswith("remove:"))
async def handle_remove(callback: types.CallbackQuery):
    print(f"[CALLBACK] remove: from_user={callback.from_user.id}, data={callback.data}")
    chat = callback.data.split("remove:")[1]
    config = load_config()
    removed_media = []
    # Удаляем из chats
    if chat in config["chats"]:
        # Сохраняем путь к медиа для удаления
        media_path = config["chats"][chat].get("media")
        if media_path and os.path.isfile(media_path):
            removed_media.append(media_path)
        del config["chats"][chat]
    # Удаляем из scheduled
    if "scheduled" in config and chat in config["scheduled"]:
        for entry in config["scheduled"][chat]:
            media_path = entry.get("media")
            if media_path and os.path.isfile(media_path):
                removed_media.append(media_path)
        del config["scheduled"][chat]
    # Удаляем медиа-файлы
    for path in removed_media:
        with suppress(Exception):
            os.remove(path)
        save_config(config)
    await callback.message.answer(f"<i> ♦️ Группа и все связанные сообщения удалены: {chat} </i>", parse_mode="HTML",)
    if not config["chats"]:
        await callback.message.answer("<i>🔶 Список групп пуст.</i>", parse_mode="HTML")
    await callback.answer()

# -----------------------------------
# Старт/стоп/список
# -----------------------------------

@dp.message(F.text == "📒 Список групп")
@owner_only
async def btn_list_groups(message: Message):
    config = load_config()
    if not config["chats"]:
        return await message.answer("<i> 🔶 Список групп пуст. </i>", parse_mode="HTML",)
    text = "\n".join([f"{chat}" for chat in config["chats"].keys()])
    await message.answer(f"<b> Список добавленных групп:\n{text} </b>", parse_mode="HTML",)

# --- Глобальный флаг для фоновой задачи ---
schedule_broadcast_active = False

def set_schedule_active(active: bool):
    config = load_config()
    config["schedule_active"] = active
    save_config(config)

@dp.message(F.text == "🟢 Старт рассылки")
@owner_only
async def btn_launch(message: Message):
    config = load_config()
    config["active"] = True
    save_config(config)
    await message.answer("<b>✅ Рассылка включена.</b>", parse_mode="HTML")

@dp.message(F.text == "🔴 Стоп ")
@owner_only
async def btn_stop(message: Message):
    config = load_config()
    config["active"] = False
    save_config(config)
    await message.answer("<b>⛔️ Рассылка остановлена. </b>" , parse_mode="HTML")

@dp.message(F.text == "✏️ Редактировать сообщения")
@owner_only
async def schedule_edit_entry(message: Message, state: FSMContext):
    config = load_config()
    if not config.get("scheduled"):
        return await message.answer("Нет сообщений по расписанию для редактирования.")
    groups = list(config["scheduled"].keys())
    if not groups:
        return await message.answer("Нет групп с сообщениями по расписанию.")
    await message.answer(
        "<b> Выберите группу для редактирования сообщений по расписанию: </b>", parse_mode='html',
        reply_markup=get_edit_group_inline_keyboard(groups)
    )
    await state.clear()

def get_schedule_entry_preview(entry, n=20):
    text = entry.get("message", "")
    preview = text[:n] + ("..." if len(text) > n else "")
    return f"{entry['time']} | {preview}"

def get_edit_entry_inline_keyboard(entries):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=get_schedule_entry_preview(entry), callback_data=f"edit_schedule_entry:{i}")]
            for i, entry in enumerate(entries)
        ]
    )

@dp.callback_query(F.data.startswith("edit_schedule_group:"))
async def edit_schedule_group_selected(callback: types.CallbackQuery, state: FSMContext):
    print(f"[CALLBACK] edit_schedule_group: from_user={callback.from_user.id}, data={callback.data}, state={await state.get_state()}")
    group = callback.data.split(":", 1)[1]
    await state.update_data(selected_group=group)
    config = load_config()
    entries = config.get("scheduled", {}).get(group, [])
    if not entries:
        await callback.message.answer("<i>🔸 Нет сообщений по расписанию для этой группы. </i>",parse_mode='html')
        await callback.answer()
        return
    await callback.message.answer(
        "<b> Выберите сообщение для редактирования: </b>",parse_mode='html',
        reply_markup=get_edit_entry_inline_keyboard(entries)
    )
    await state.set_state(BotStates.selected_group)  # временно, далее будет отдельное состояние
    await callback.answer()

@dp.callback_query(F.data.startswith("edit_schedule_entry:"))
async def edit_schedule_entry_selected(callback: types.CallbackQuery, state: FSMContext):
    print(f"[CALLBACK] edit_schedule_entry: from_user={callback.from_user.id}, data={callback.data}, state={await state.get_state()}")
    entry_idx = int(callback.data.split(":", 1)[1])
    data = await state.get_data()
    group = data["selected_group"]
    await state.update_data(edit_entry_idx=entry_idx)
    await callback.message.answer(
        "<i> Введите новое время (ЧЧ:ММ:СС) или 0, чтобы оставить прежнее: </i>" , parse_mode="HTML")
    await state.set_state(EditScheduleStates.waiting_for_new_time)
    await callback.answer()

@dp.message(EditScheduleStates.waiting_for_new_time)
async def save_new_time(message: Message, state: FSMContext):
    print(f"[FSM] Состояние: {await state.get_state()}, message: {message.text}")
    import re
    data = await state.get_data()
    group = data["selected_group"]
    idx = data["edit_entry_idx"]
    config = load_config()
    entry = config["scheduled"][group][idx]
    if message.text.strip() != "0":
        time_pattern = r"^([01]?\d|2[0-3]):[0-5]\d:[0-5]\d$"
        if not re.match(time_pattern, message.text):
            return await message.answer("<b> Введите корректное время в формате ЧЧ:ММ:СС (например, 15:35:00) или 0, чтобы оставить прежнее </b>" , parse_mode="HTML")
        entry["time"] = message.text.strip()
    save_config(config)
    await message.answer("<i>Отправьте новое сообщение или 0, чтобы оставить прежнее сообщение:</i>",parse_mode='html')
    await state.set_state(EditScheduleStates.waiting_for_new_message)

@dp.message(EditScheduleStates.waiting_for_new_message)
async def save_new_message(message: Message, state: FSMContext):
    print(f"[FSM] Состояние: {await state.get_state()}, message: {message.text}")
    data = await state.get_data()
    group = data["selected_group"]
    idx = data["edit_entry_idx"]
    config = load_config()
    entry = config["scheduled"][group][idx]
    if message.text and message.text.strip() == "0":
        # Оставляем прежний текст/медиа
        await message.answer("<i>🔸Сообщение по расписанию обновлено!</i>", parse_mode="HTML")
        await state.clear()
        return
    if message.photo:
        photo = message.photo[-1]
        file = await bot.get_file(photo.file_id)
        file_path = f"media/{photo.file_unique_id}.jpg"
        os.makedirs("media", exist_ok=True)
        await bot.download_file(file.file_path, destination=file_path)
        entry["media"] = file_path
        entry["message"] = message.caption or ""
        entry["caption_entities"] = [e.model_dump() for e in message.caption_entities] if message.caption_entities else None
    elif message.document:
        file_id = message.document.file_id
        entry["media"] = file_id
        entry["message"] = message.caption or ""
        entry["caption_entities"] = [e.model_dump() for e in message.caption_entities] if message.caption_entities else None
    elif message.text:
        entry["message"] = message.text
        entry["entities"] = [e.model_dump() for e in message.entities] if message.entities else None
        entry.pop("media", None)
        entry.pop("caption_entities", None)
    else:
        await message.answer("<i> ♦️ Не удалось распознать сообщение. Отправьте текст или медиа, либо 0 чтобы оставить прежнее.</i>", parse_mode="HTML")
        return
    save_config(config)
    await message.answer("<i> 🔸Сообщение по расписанию обновлено! </i>", parse_mode="HTML")
    await state.clear()

@dp.callback_query(F.data == "edit_entry_back")
async def edit_entry_back(callback: types.CallbackQuery, state: FSMContext):
    print(f"[CALLBACK] edit_entry_back: from_user={callback.from_user.id}, data={callback.data}, state={await state.get_state()}")
    data = await state.get_data()
    group = data["selected_group"]
    config = load_config()
    entries = config.get("scheduled", {}).get(group, [])
    await callback.message.answer(
        "Выберите сообщение для редактирования:",
        reply_markup=get_edit_entry_inline_keyboard(entries)
    )
    await callback.answer()

# -----------------------------------
# Команды в меню Telegram
# -----------------------------------

async def set_bot_commands():
    commands = [
        BotCommand(command="start", description="Открыть меню")
    ]
    await bot.set_my_commands(commands)

# -----------------------------------
# Фоновые задачи ---
# -----------------------------------
schedule_broadcast_task = None
delay_broadcast_task = None

async def schedule_broadcast_loop():
    print("[DEBUG] schedule_broadcast_loop запущен")
    while True:
        config = load_config()
        if not config.get("schedule_active", False):
            print("[LOG] schedule_broadcast_loop: schedule_active = False, sleep 5s")
            await asyncio.sleep(5)
            continue
        now = datetime.datetime.now().time()
        for group, entries in config.get("scheduled", {}).items():
            print(f"[LOG] Проверка расписания для группы {group}, entries: {entries}")
            for entry in entries:
                try:
                    t = datetime.datetime.strptime(entry["time"], "%H:%M:%S").time()
                except Exception as e:
                    print(f"[ERROR] Некорректное время в entry: {entry}, ошибка: {e}")
                    continue
                last_sent = entry.get("last_sent_date")
                today_str = datetime.datetime.now().strftime("%Y-%m-%d")
                if last_sent == today_str:
                    print(f"[LOG] Сообщение уже отправлено сегодня для {group} в {entry['time']}")
                    continue
                now_seconds = now.hour*3600 + now.minute*60 + now.second
                t_seconds = t.hour*3600 + t.minute*60 + t.second
                if abs(now_seconds - t_seconds) <= 30:
                    print(f"[LOG] Время отправки для {group}: {entry['time']}, отправляем...")
                    await send_scheduled_message(group, entry)
                    entry["last_sent_date"] = today_str
                    save_config(config)
        await asyncio.sleep(20)

async def send_scheduled_message(group, entry):
    print(f"[DEBUG] send_scheduled_message для {group}, entry: {entry}")
    chat = group
    try:
        if entry.get("media"):
            print(f"[LOG] Отправка медиа в {chat}: {entry['media']}")
            if entry["media"].endswith(".jpg") or entry["media"].endswith(".png"):
                input_file = FSInputFile(entry["media"])
                await bot.send_photo(chat_id=chat, photo=input_file, caption=entry.get("message", ""), caption_entities=[types.MessageEntity.model_validate(e) for e in entry.get("caption_entities", [])] if entry.get("caption_entities") else None)
            else:
                input_file = FSInputFile(entry["media"])
                await bot.send_document(chat_id=chat, document=input_file, caption=entry.get("message", ""), caption_entities=[types.MessageEntity.model_validate(e) for e in entry.get("caption_entities", [])] if entry.get("caption_entities") else None)
        elif entry.get("message"):
            print(f"[LOG] Отправка текста в {chat}: {entry['message']}")
            await bot.send_message(chat_id=chat, text=entry["message"], entities=[types.MessageEntity.model_validate(e) for e in entry.get("entities", [])] if entry.get("entities") else None, parse_mode=None)
        else:
            print(f"[WARN] Нет данных для отправки в {chat}")
    except Exception as e:
        print(f"[ERROR] Не удалось отправить сообщение по расписанию в {chat}: {e}")

async def delay_broadcast_loop():
    print("[DEBUG] delay_broadcast_loop запущен")
    while True:
        config = load_config()
        if not config.get("active", False):
            print("[LOG] delay_broadcast_loop: active = False, sleep 5s")
            await asyncio.sleep(5)
            continue
        for group, data in config.get("chats", {}).items():
            print(f"[LOG] Попытка отправки в {group}, data: {data}")
            try:
                if data.get("media"):
                    print(f"[LOG] Отправка медиа в {group}: {data['media']}")
                    if data["media"].endswith(".jpg") or data["media"].endswith(".png"):
                        input_file = FSInputFile(data["media"])
                        await bot.send_photo(chat_id=group, photo=input_file, caption=data.get("message", ""), caption_entities=[types.MessageEntity.model_validate(e) for e in data.get("caption_entities", [])] if data.get("caption_entities") else None)
                    else:
                        input_file = FSInputFile(data["media"])
                        await bot.send_document(chat_id=group, document=input_file, caption=data.get("message", ""), caption_entities=[types.MessageEntity.model_validate(e) for e in data.get("caption_entities", [])] if data.get("caption_entities") else None)
                elif data.get("message"):
                    print(f"[LOG] Отправка текста в {group}: {data['message']}")
                    await bot.send_message(chat_id=group, text=data["message"], entities=[types.MessageEntity.model_validate(e) for e in data.get("entities", [])] if data.get("entities") else None, parse_mode=None)
                else:
                    print(f"[WARN] Нет данных для отправки в {group}")
            except Exception as e:
                print(f"[ERROR] Не удалось отправить сообщение по задержке в {group}: {e}")
        delays = [data.get("delay", 60) for data in config.get("chats", {}).values()]
        delay = min(delays) if delays else 60
        print(f"[LOG] Ждём {delay} секунд до следующей рассылки")
        await asyncio.sleep(delay)

# --- Запуск фоновых задач при старте ---
async def main():
    global schedule_broadcast_task, delay_broadcast_task
    print("[LOG] main() стартует")
    await set_bot_commands()
    print("[LOG] set_bot_commands выполнен")
    # Запуск фоновых задач
    schedule_broadcast_task = asyncio.create_task(schedule_broadcast_loop())
    print("[LOG] schedule_broadcast_loop запущен")
    delay_broadcast_task = asyncio.create_task(delay_broadcast_loop())
    print("[LOG] delay_broadcast_loop запущен")
    await dp.start_polling(bot)
    print("[LOG] dp.start_polling завершён")

# --- Главное меню ---
main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Добавить группу")],
        [KeyboardButton(text="🗓️ По расписанию"), KeyboardButton(text="⏳ По задержке")],
        [KeyboardButton(text="📒 Список групп")],
        [KeyboardButton(text="❌ Удалить группу")],
    ],
    resize_keyboard=True
)

# --- Меню по расписанию ---
schedule_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🟢 Старт"), KeyboardButton(text="🔴 Стоп")],
        [KeyboardButton(text="✏️ Редактировать сообщения"), KeyboardButton(text="🗑️ Удалить запись")],
        [KeyboardButton(text="🔙 Назад")],
    ],
    resize_keyboard=True
)

# --- Меню по задержке ---
spam_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="💬 Изменить сообщение"), KeyboardButton(text="⏰ Изменить задержку")],
        [KeyboardButton(text="🟢 Стаpт"), KeyboardButton(text="🔴 Cтоп")],
        [KeyboardButton(text="🔙 Назад")],
    ],
    resize_keyboard=True
)

# --- Обработчик для /start ---
@dp.message(CommandStart())
@owner_only
async def cmd_start(message: Message, state: FSMContext):
    print(f"[FSM] Состояние: {await state.get_state()}, message: {message.text}")
    await state.clear()
    await message.answer(
        "<b>🔽 Выберите действие:</b>",
        parse_mode="HTML",
        reply_markup=main_menu
    )

# --- Старт/стоп по расписанию ---
@dp.message(F.text == "🟢 Старт")
@owner_only
async def schedule_start(message: Message, state: FSMContext):
    global schedule_broadcast_active
    if schedule_broadcast_active:
        await message.answer("<i>Рассылка по расписанию уже запущена.</i>", parse_mode="HTML")
        return
    schedule_broadcast_active = True
    set_schedule_active(True)
    await message.answer("<b>🟢 Рассылка по расписанию запущена.</b>", parse_mode='html')

@dp.message(F.text == "🔴 Стоп")
@owner_only
async def schedule_stop(message: Message, state: FSMContext):
    global schedule_broadcast_active
    if not schedule_broadcast_active:
        await message.answer("<i>Рассылка по расписанию уже остановлена.</i>", parse_mode='html')
        return
    schedule_broadcast_active = False
    set_schedule_active(False)
    await message.answer("<b>🔴️ Рассылка по расписанию остановлена.</b>", parse_mode='html')

# --- Назад для меню по задержке ---
@dp.message(F.text == "🔙 Назад")
@owner_only
async def spam_back_to_main_menu(message: Message, state: FSMContext):
    # Если пользователь был в меню по задержке, возвращаем в главное меню
    # Если был в меню по расписанию, возвращаем в меню по расписанию
    # FSM: можно хранить последнее меню в state
    data = await state.get_data()
    last_menu = data.get('last_menu')
    if last_menu == 'schedule':
        await message.answer(" ", reply_markup=schedule_menu)
    else:
        await message.answer("<b>🔽 Выберите действие:</b>", parse_mode="HTML", reply_markup=main_menu)
    await state.clear()

# --- При входе в меню по расписанию сохраняем last_menu ---
@dp.message(F.text == "🗓️ По расписанию")
@owner_only
async def btn_schedule(message: Message, state: FSMContext):
    config = load_config()
    if not config["chats"] and (not config.get("scheduled") or not config["scheduled"]):
        # Останавливаем рассылку по расписанию
        config["schedule_active"] = False
        save_config(config)
        await message.answer("<i>🔶 Список групп пуст. Рассылка по расписанию остановлена.</i>", parse_mode="HTML")
        return
    await state.update_data(last_menu='schedule')
    await message.answer(
        "Выберите группу для настройки расписания:",
        reply_markup=get_group_keyboard("schedule")
    )
    await message.answer("<i>Для возврата нажмите на кнопку </i><b>Назад</b>",
        parse_mode="HTML", reply_markup=schedule_menu)
    await state.clear()

@dp.callback_query(F.data.startswith("schedule:"))
async def schedule_group_selected(callback: types.CallbackQuery, state: FSMContext):
    print(f"[CALLBACK] schedule: from_user={callback.from_user.id}, data={callback.data}, state={await state.get_state()}")
    chat = callback.data.split("schedule:")[1]
    await state.update_data(selected_group=chat)
    await callback.message.answer("<b> Введите время отправки сообщения в формате ЧЧ:ММ:СС (например, 15:30:25): </b>" , parse_mode="HTML")
    await state.set_state(ScheduleStates.waiting_for_time)
    await callback.answer()

@dp.message(ScheduleStates.waiting_for_time)
@owner_only
async def schedule_input_time(message: Message, state: FSMContext):
    print(f"[FSM] Состояние: {await state.get_state()}, message: {message.text}")
    import re
    time_pattern = r"^([01]?\d|2[0-3]):[0-5]\d:[0-5]\d$"
    if not re.match(time_pattern, message.text):
        return await message.answer("<i> Введите корректное время в формате ЧЧ:ММ:СС </i>", parse_mode="HTML")
    await state.update_data(scheduled_time=message.text)
    await message.answer("<b>Отправьте сообщение для рассылки (текст, медиа, текст+медиа):</b>",parse_mode="HTML")
    await state.set_state(ScheduleStates.waiting_for_scheduled_message)

@dp.message(ScheduleStates.waiting_for_scheduled_message)
@owner_only
async def schedule_input_message(message: Message, state: FSMContext):
    print(f"[FSM] Состояние: {await state.get_state()}, message: {message.text}")
    if not (message.text or message.photo or message.document):
        await message.answer("<i> ♦️ Не удалось распознать сообщение. Отправьте текст или медиа. </i> ", parse_mode="HTML",)
        return
    data = await state.get_data()
    chat = data["selected_group"]
    scheduled_time = data["scheduled_time"]
    config = load_config()
    # Проверка на дублирование времени
    if "scheduled" in config and chat in config["scheduled"]:
        for entry in config["scheduled"][chat]:
            if entry["time"] == scheduled_time:
                await message.answer(
                    f"<i> ♦️ На {scheduled_time} уже запланировано сообщение для этой группы. Выберите другое время. </i>",
                    parse_mode="HTML"
                )
                await message.answer(
                    "<b>Введите время отправки сообщения в формате ЧЧ:ММ:СС (например, 15:30:25):</b>",
                    parse_mode="HTML"
                )
                await state.set_state(ScheduleStates.waiting_for_time)
                return
    # Сохраняем текст и/или медиа
    entry = {"time": scheduled_time}
    if message.photo:
        photo = message.photo[-1]
        file = await bot.get_file(photo.file_id)
        file_path = f"media/{photo.file_unique_id}.jpg"
        os.makedirs("media", exist_ok=True)
        await bot.download_file(file.file_path, destination=file_path)
        entry["media"] = file_path
        entry["message"] = message.caption or ""
        entry["caption_entities"] = [e.model_dump() for e in message.caption_entities] if message.caption_entities else None
    elif message.document:
        file_id = message.document.file_id
        entry["media"] = file_id
        entry["message"] = message.caption or ""
        entry["caption_entities"] = [e.model_dump() for e in message.caption_entities] if message.caption_entities else None
    elif message.text:
        entry["message"] = message.text
        entry["entities"] = [e.model_dump() for e in message.entities] if message.entities else None
    # Сохраняем в config
    if "scheduled" not in config:
        config["scheduled"] = {}
    if chat not in config["scheduled"]:
        config["scheduled"][chat] = []
    config["scheduled"][chat].append(entry)
    save_config(config)
    await message.answer(f"<i>🔸 Сообщение по расписанию для {chat} добавлено на {scheduled_time} </i>", parse_mode="HTML")
    await state.clear()
    await message.answer("<b> 🔽 Выберите действие: </b>", parse_mode="HTML", reply_markup=main_menu)

@dp.message(F.text == "⏳ По задержке")
@owner_only
async def btn_spam_menu(message: Message, state: FSMContext):
    config = load_config()
    if not config["chats"]:
        # Останавливаем рассылку по задержке
        config["active"] = False
        save_config(config)
        await message.answer("<i>🔶 Список групп пуст. Рассылка по задержке остановлена.</i>", parse_mode="HTML")
        return
    await state.clear()
    await message.answer(
        "<i>Спам происходит по всем группам сразу.\n\nДля возврата нажмите на кнопку </i><b>Назад</b>",
        parse_mode="HTML",
        reply_markup=spam_menu
    )

@dp.message(F.text == "🟢 Стаpт")
@owner_only
async def btn_launch_spam(message: Message):
    config = load_config()
    if config.get("active", False):
        await message.answer("<i>Рассылка по задержке уже запущена.</i>", parse_mode="HTML", reply_markup=spam_menu)
        return
    config["active"] = True
    save_config(config)
    await message.answer("<b>🟢 Рассылка по задержке запущена.</b>", parse_mode="HTML", reply_markup=spam_menu)

@dp.message(F.text == "🔴 Cтоп")
@owner_only
async def btn_stop_spam(message: Message):
    config = load_config()
    if not config.get("active", False):
        await message.answer("<i>Рассылка по задержке уже остановлена.</i>", parse_mode="HTML", reply_markup=spam_menu)
        return
    config["active"] = False
    save_config(config)
    await message.answer("<b>🔴️ Рассылка по задержке остановлена.</b>", parse_mode="HTML", reply_markup=spam_menu)

@dp.message(F.text == "🗑️ Удалить запись")
@owner_only
async def delete_schedule_entry_start(message: Message, state: FSMContext):
    print(f"[FSM] Состояние: {await state.get_state()}, message: {message.text}")
    config = load_config()
    groups = list(config.get("scheduled", {}).keys())
    if not groups:
        await message.answer("<i> Нет групп с сообщениями по расписанию. </i>")
        return
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=group, callback_data=f"delete_schedule_group:{group}")] for group in groups]
    )
    await message.answer("<b> Выберите группу, из которой хотите удалить запись: </b>", reply_markup=keyboard, parse_mode="HTML")
    await state.set_state(DeleteScheduleStates.waiting_for_group)

@dp.callback_query(F.data.startswith("delete_schedule_group:"), DeleteScheduleStates.waiting_for_group)
async def delete_schedule_group_selected(callback: types.CallbackQuery, state: FSMContext):
    print(f"[CALLBACK] delete_schedule_group: from_user={callback.from_user.id}, data={callback.data}, state={await state.get_state()}")
    group = callback.data.split(":", 1)[1]
    config = load_config()
    entries = config.get("scheduled", {}).get(group, [])
    if not entries:
        await callback.message.answer("<i>🔸 В этой группе нет сообщений по расписанию. </i> ", parse_mode="HTML")
        await callback.answer()
        return
    await state.update_data(selected_group=group)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"{entry['time']} | {entry.get('message', '')[:20]}", callback_data=f"delete_schedule_entry:{i}")]
            for i, entry in enumerate(entries)
        ] + [[InlineKeyboardButton(text="🔙 Назад", callback_data="delete_schedule_back")]]
    )
    await callback.message.answer("<b> Выберите запись для удаления: </b>", reply_markup=keyboard, parse_mode="HTML")
    await state.set_state(DeleteScheduleStates.waiting_for_entry)
    await callback.answer()

@dp.callback_query(F.data.startswith("delete_schedule_entry:"), DeleteScheduleStates.waiting_for_entry)
async def delete_schedule_entry_selected(callback: types.CallbackQuery, state: FSMContext):
    print(f"[CALLBACK] delete_schedule_entry: from_user={callback.from_user.id}, data={callback.data}, state={await state.get_state()}")
    idx = int(callback.data.split(":", 1)[1])
    data = await state.get_data()
    group = data["selected_group"]
    config = load_config()
    entries = config.get("scheduled", {}).get(group, [])
    if 0 <= idx < len(entries):
        removed = entries.pop(idx)
        save_config(config)
        await callback.message.answer(f"<i>♦️ Удалена запись на {removed['time']}</i>", parse_mode="HTML")
    # После удаления — если остались записи, снова показываем выбор, иначе возвращаем к выбору группы
    if entries:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=f"{entry['time']} | {entry.get('message', '')[:20]}", callback_data=f"delete_schedule_entry:{i}")]
                for i, entry in enumerate(entries)
            ] + [[InlineKeyboardButton(text="🔙 Назад", callback_data="delete_schedule_back")]]
        )
        await callback.message.answer("<b> Выберите запись для удаления: </b>", reply_markup=keyboard, parse_mode="HTML")
        await state.set_state(DeleteScheduleStates.waiting_for_entry)
    else:
        await callback.message.answer("<i> В этой группе больше нет сообщений по расписанию. </i>", parse_mode="HTML")
        await state.set_state(DeleteScheduleStates.waiting_for_group)
        # Можно сразу показать выбор группы, если нужно
        groups = list(config.get("scheduled", {}).keys())
        if groups:
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text=group, callback_data=f"delete_schedule_group:{group}")] for group in groups]
            )
            await callback.message.answer("<b> Выберите группу, из которой хотите удалить запись: </b>", parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(F.data == "delete_schedule_back", DeleteScheduleStates.waiting_for_entry)
async def delete_schedule_back_to_group(callback: types.CallbackQuery, state: FSMContext):
    print(f"[CALLBACK] delete_schedule_back: from_user={callback.from_user.id}, data={callback.data}, state={await state.get_state()}")
    config = load_config()
    groups = list(config.get("scheduled", {}).keys())
    if not groups:
        await callback.message.answer("<i>🔸 Нет групп с сообщениями по расписанию. </i>")
        await state.clear()
        await callback.answer()
        return
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=group, callback_data=f"delete_schedule_group:{group}")] for group in groups]
    )
    await callback.message.answer("<b> Выберите группу, из которой хотите удалить запись: </b>", reply_markup=keyboard, parse_mode="HTML")
    await state.set_state(DeleteScheduleStates.waiting_for_group)
    await callback.answer()

async def run_adminbot():
    await main()