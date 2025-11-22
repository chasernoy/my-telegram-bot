import json
import asyncio
import os
import builtins
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
BACKUP_DIR = os.path.join(os.path.dirname(__file__), 'backups')

# Логирование полностью отключено, чтобы не забивать дисковое пространство
DEBUG_LOGS = False
# --- Ограничение доступа по user_id ---
OWNER_ID = int(os.getenv('OWNER_ID'))


def _noop_print(*args, **kwargs):
    if DEBUG_LOGS:
        builtins.print(*args, **kwargs)


print = _noop_print

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

def private_chat_only(func):
    """Декоратор для ограничения команд только личным чатом"""
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
        if message.chat.type != "private":
            print(f"[SILENT] Игнорируем команду в группе: {message.chat.type}, text: {getattr(message, 'text', None)}")
            return  # Просто игнорируем, не отвечаем
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
    collecting_delay_media_group = State()

class ScheduleStates(StatesGroup):
    waiting_for_time = State()
    waiting_for_scheduled_message = State()
    collecting_media_group = State()

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
        if not os.path.exists(CONFIG_PATH):
            print(f"[LOG] Файл config.json не существует, создаем новый")
            default_config = {"chats": {}, "active": False, "scheduled": {}, "schedule_active": False}
            save_config(default_config)
            return default_config
        
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            content = f.read()
            if not content.strip():
                print(f"[WARN] Файл config.json пустой!")
                return {"chats": {}, "active": False, "scheduled": {}, "schedule_active": False}
            
            data = json.loads(content)
            print(f"[LOG] Загружен config: {len(data.get('chats', {}))} групп, {len(data.get('scheduled', {}))} расписаний")
            return data
    except json.JSONDecodeError as e:
        print(f"[ERROR] Ошибка парсинга JSON в config.json: {e}")
        print(f"[ERROR] Пытаемся восстановить из резервной копии...")
        # Пытаемся восстановить из последнего бэкапа
        if os.path.exists(BACKUP_DIR):
            backups = sorted([f for f in os.listdir(BACKUP_DIR) if f.startswith("config_backup_")], reverse=True)
            if backups:
                backup_path = os.path.join(BACKUP_DIR, backups[0])
                print(f"[RECOVER] Восстанавливаем из {backup_path}")
                try:
                    with open(backup_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    # Восстанавливаем файл
                    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
                        json.dump(data, f, indent=2, ensure_ascii=False)
                    print(f"[RECOVER] Конфиг восстановлен из бэкапа")
                    return data
                except Exception as restore_error:
                    print(f"[ERROR] Не удалось восстановить из бэкапа: {restore_error}")
        
        # Если восстановление не удалось, возвращаем пустой конфиг
        return {"chats": {}, "active": False, "scheduled": {}, "schedule_active": False}
    except Exception as e:
        print(f"[ERROR] Не удалось загрузить config: {e}")
        import traceback
        traceback.print_exc()
        return {"chats": {}, "active": False, "scheduled": {}, "schedule_active": False}

def create_backup():
    """Создает резервную копию config.json"""
    try:
        if not os.path.exists(BACKUP_DIR):
            os.makedirs(BACKUP_DIR, exist_ok=True)
        
        if os.path.exists(CONFIG_PATH):
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = os.path.join(BACKUP_DIR, f"config_backup_{timestamp}.json")
            with open(CONFIG_PATH, 'r') as src:
                with open(backup_path, 'w') as dst:
                    dst.write(src.read())
            print(f"[BACKUP] Создана резервная копия: {backup_path}")
            
            # Удаляем старые бэкапы (оставляем последние 10)
            backups = sorted([f for f in os.listdir(BACKUP_DIR) if f.startswith("config_backup_")])
            if len(backups) > 10:
                for old_backup in backups[:-10]:
                    os.remove(os.path.join(BACKUP_DIR, old_backup))
                    print(f"[BACKUP] Удален старый бэкап: {old_backup}")
    except Exception as e:
        print(f"[ERROR] Не удалось создать резервную копию: {e}")

def check_disk_space():
    """Проверяет свободное место на диске"""
    try:
        stat = os.statvfs(os.path.dirname(CONFIG_PATH))
        free_bytes = stat.f_bavail * stat.f_frsize
        free_mb = free_bytes / (1024 * 1024)
        print(f"[DISK] Свободно места: {free_mb:.2f} MB")
        if free_mb < 1:
            print(f"[WARN] Критически мало места на диске: {free_mb:.2f} MB")
            return False
        return True
    except Exception as e:
        print(f"[WARN] Не удалось проверить место на диске: {e}")
        return True  # Продолжаем, если не можем проверить

def save_config(data):
    """Сохраняет конфиг с атомарной записью через временный файл"""
    temp_path = None
    try:
        # Проверяем свободное место на диске
        if not check_disk_space():
            print(f"[ERROR] Недостаточно места на диске для сохранения конфига!")
            raise OSError(28, "No space left on device")
        
        # Создаем резервную копию перед сохранением (только если есть место)
        try:
            create_backup()
        except OSError as e:
            if e.errno == 28:  # No space left on device
                print(f"[WARN] Не удалось создать бэкап из-за нехватки места, продолжаем без бэкапа")
            else:
                raise
        
        # Используем временный файл для атомарной записи
        temp_path = CONFIG_PATH + ".tmp"
        
        # Записываем во временный файл
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())  # Принудительно записываем на диск
        
        # Проверяем, что временный файл не пустой
        if os.path.getsize(temp_path) == 0:
            raise OSError(28, "No space left on device - файл остался пустым")
        
        # Атомарно заменяем старый файл новым
        if os.path.exists(CONFIG_PATH):
            os.replace(temp_path, CONFIG_PATH)
        else:
            os.rename(temp_path, CONFIG_PATH)
        
        # Проверяем, что файл действительно сохранился и не пустой
        if os.path.getsize(CONFIG_PATH) == 0:
            raise OSError(28, "No space left on device - config.json остался пустым")
        
        # Проверяем, что файл действительно сохранился
        verify_data = load_config()
        if len(verify_data.get('chats', {})) != len(data.get('chats', {})):
            print(f"[ERROR] Несоответствие данных после сохранения! Ожидалось {len(data.get('chats', {}))} групп, получили {len(verify_data.get('chats', {}))}")
        
        print(f"[LOG] Сохранён config: {len(data.get('chats', {}))} групп, {len(data.get('scheduled', {}))} расписаний")
    except OSError as e:
        if e.errno == 28:  # No space left on device
            print(f"[CRITICAL] НЕТ МЕСТА НА ДИСКЕ! Не удалось сохранить конфиг.")
            print(f"[CRITICAL] Освободите место на диске и попробуйте снова.")
            # Удаляем временный файл при ошибке
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except:
                    pass
            # НЕ перезаписываем существующий файл, если он есть
            raise
        else:
            print(f"[ERROR] Не удалось сохранить config: {e}")
            import traceback
            traceback.print_exc()
            # Удаляем временный файл при ошибке
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except:
                    pass
            raise
    except Exception as e:
        print(f"[ERROR] Не удалось сохранить config: {e}")
        import traceback
        traceback.print_exc()
        # Удаляем временный файл при ошибке
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except:
                pass
        raise

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
@private_chat_only
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

@dp.message(lambda m: m.text and (m.text.startswith("https://t.me/") or m.text.startswith("@")) and m.chat.type == "private")
@owner_only
async def handle_group_add(message: Message):
    """Добавление группы в личном чате: просто отправьте @groupname или https://t.me/groupname"""
    config = load_config()
    print(f"[ADD_GROUP] Текущий конфиг до добавления: {config}")
    link = message.text.strip()
    # Приводим к формату @groupname
    if link.startswith("https://t.me/"):
        link = "@" + link.split("https://t.me/")[-1]
    if link.startswith("@"):  # убираем лишние символы после username
        link = link.split()[0].split("/")[0]
    print(f"[ADD_GROUP] Обработанная ссылка: {link}")
    # Проверяем наличие группы только по @groupname
    groupnames = [k if k.startswith("@") else "@" + k.split("https://t.me/")[-1].split()[0].split("/")[0] for k in config["chats"].keys()]
    if link in groupnames:
        await message.answer("<i> 🔺 Данная группа уже добавлена </i>" , parse_mode="HTML",)
        return
    config["chats"][link] = {"message": None, "delay": 60}
    print(f"[ADD_GROUP] Конфиг после добавления группы: {config}")
    
    try:
        save_config(config)
    except OSError as e:
        if e.errno == 28:  # No space left on device
            print(f"[ERROR] Не удалось сохранить группу из-за нехватки места на диске")
            await message.answer(
                "<b>❌ КРИТИЧЕСКАЯ ОШИБКА!</b>\n\n"
                "<i>На сервере закончилось место на диске!\n"
                "Группа не была сохранена.\n\n"
                "Освободите место на диске и попробуйте снова.</i>",
                parse_mode="HTML"
            )
            return
        else:
            raise
    
    # Проверяем, что группа действительно сохранилась
    verify_config = load_config()
    print(f"[ADD_GROUP] Проверка после сохранения: {verify_config}")
    if link not in verify_config.get("chats", {}):
        print(f"[ERROR] Группа {link} не сохранилась! Конфиг: {verify_config}")
        await message.answer(f"<i> ⚠️ Ошибка при сохранении группы. Попробуйте еще раз.</i>", parse_mode="HTML",)
        return
    
    await message.answer(f"<i> 🔸 Группа добавлена: </i> {link}", parse_mode="HTML",)

# -----------------------------------
# Сообщение
# -----------------------------------
@dp.message(F.text == "💬 Изменить сообщение")
@private_chat_only
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
    
    # Если это медиа-группа, переключаемся на специальный обработчик
    if message.media_group_id:
        await state.set_state(BotStates.collecting_delay_media_group)
        # Обрабатываем первое сообщение медиа-группы
        await handle_delay_media_group(message, state)
        return
    
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
    elif message.video:
        video = message.video
        file = await bot.get_file(video.file_id)
        os.makedirs("media", exist_ok=True)
        file_path = f"media/{video.file_unique_id}.mp4"
        await bot.download_file(file.file_path, destination=file_path)
        config["chats"][chat]["media"] = file_path
        config["chats"][chat]["message"] = message.caption or ""
        config["chats"][chat]["caption_entities"] = [e.model_dump() for e in message.caption_entities] if message.caption_entities else None
        await message.answer(f"<i>🔸 Видео + подпись сохранены для {chat}</i>", parse_mode="HTML",)
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

# Обработчик для медиа-групп в режиме задержки
@dp.message(BotStates.collecting_delay_media_group)
@owner_only
async def handle_delay_media_group(message: Message, state: FSMContext):
    """Обработка медиа-групп для режима задержки"""
    print(f"[FSM] Обработка медиа-группы задержки: {message.media_group_id}")
    
    data = await state.get_data()
    chat = data["selected_group"]
    config = load_config()
    
    # Собираем медиа-группу
    media_group_id = message.media_group_id
    if media_group_id not in data.get("media_groups", {}):
        data["media_groups"] = {media_group_id: []}
        await state.update_data(media_groups=data["media_groups"])
    
    # Добавляем текущее медиа в группу
    media_item = {}
    if message.photo:
        photo = message.photo[-1]
        file = await bot.get_file(photo.file_id)
        file_path = f"media/{photo.file_unique_id}.jpg"
        os.makedirs("media", exist_ok=True)
        await bot.download_file(file.file_path, destination=file_path)
        media_item = {"type": "photo", "file_path": file_path}
    elif message.video:
        video = message.video
        file = await bot.get_file(video.file_id)
        file_path = f"media/{video.file_unique_id}.mp4"
        os.makedirs("media", exist_ok=True)
        await bot.download_file(file.file_path, destination=file_path)
        media_item = {"type": "video", "file_path": file_path}
    elif message.document:
        file_id = message.document.file_id
        media_item = {"type": "document", "file_id": file_id}
    
    if media_item:
        data["media_groups"][media_group_id].append(media_item)
        await state.update_data(media_groups=data["media_groups"])
    
    # Если это последнее сообщение в группе (нет caption или это текстовое сообщение)
    if message.caption or (message.text and not message.photo and not message.video and not message.document):
        # Сохраняем медиа-группу
        config["chats"][chat]["media_group"] = data["media_groups"][media_group_id]
        config["chats"][chat]["message"] = message.caption or message.text or ""
        config["chats"][chat]["caption_entities"] = [e.model_dump() for e in message.caption_entities] if message.caption_entities else None
        config["chats"][chat]["entities"] = [e.model_dump() for e in message.entities] if message.entities else None
        config["chats"][chat].pop("media", None)  # Удаляем старый медиа
        print(f"[DEBUG] Сохраняем медиа-группу задержки: {data['media_groups'][media_group_id]}")
        
        save_config(config)
        await message.answer(f"<i>🔸 Медиа-группа сохранена для {chat}</i>", parse_mode="HTML")
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
@private_chat_only
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
@private_chat_only
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
    print(f"[DELETE] Удаляем группу: {chat}")
    print(f"[DELETE] Конфиг до удаления: {config}")
    
    removed_media = []
    # Удаляем из chats
    if chat in config["chats"]:
        print(f"[DELETE] Удаляем из chats: {chat}")
        # Сохраняем путь к медиа для удаления
        media_path = config["chats"][chat].get("media")
        if media_path and os.path.isfile(media_path):
            removed_media.append(media_path)
            print(f"[DELETE] Добавлен медиа-файл для удаления: {media_path}")
        
        # Удаляем медиа-группу из chats
        media_group = config["chats"][chat].get("media_group")
        if media_group:
            print(f"[DELETE] Найдена медиа-группа в chats: {len(media_group)} элементов")
            for media_item in media_group:
                if media_item.get("type") in ["photo", "video"]:
                    file_path = media_item.get("file_path")
                    if file_path and os.path.isfile(file_path):
                        removed_media.append(file_path)
                        print(f"[DELETE] Добавлен файл из медиа-группы для удаления: {file_path}")
        
        del config["chats"][chat]
    else:
        print(f"[DELETE] Группа {chat} не найдена в chats")
    
    # Удаляем из scheduled
    if "scheduled" in config and chat in config["scheduled"]:
        print(f"[DELETE] Удаляем из scheduled: {chat}")
        for entry in config["scheduled"][chat]:
            media_path = entry.get("media")
            if media_path and os.path.isfile(media_path):
                removed_media.append(media_path)
                print(f"[DELETE] Добавлен медиа-файл из scheduled для удаления: {media_path}")
            
            # Удаляем медиа-группу из scheduled
            media_group = entry.get("media_group")
            if media_group:
                print(f"[DELETE] Найдена медиа-группа в scheduled: {len(media_group)} элементов")
                for media_item in media_group:
                    if media_item.get("type") in ["photo", "video"]:
                        file_path = media_item.get("file_path")
                        if file_path and os.path.isfile(file_path):
                            removed_media.append(file_path)
                            print(f"[DELETE] Добавлен файл из медиа-группы scheduled для удаления: {file_path}")
        del config["scheduled"][chat]
    else:
        print(f"[DELETE] Группа {chat} не найдена в scheduled")
    
    # Удаляем медиа-файлы
    for path in removed_media:
        with suppress(Exception):
            os.remove(path)
            print(f"[DELETE] Удален медиа-файл: {path}")
    
    # Сохраняем конфиг только один раз после всех операций удаления
    print(f"[DELETE] Конфиг после удаления: {config}")
    save_config(config)
    print(f"[DELETE] Конфиг сохранен")
    
    await callback.message.answer(f"<i> ♦️ Группа и все связанные сообщения удалены: {chat} </i>", parse_mode="HTML",)
    if not config["chats"]:
        await callback.message.answer("<i>🔶 Список групп пуст.</i>", parse_mode="HTML")
    await callback.answer()

# -----------------------------------
# Старт/стоп/список
# -----------------------------------

@dp.message(F.text == "📒 Список групп")
@private_chat_only
@owner_only
async def btn_list_groups(message: Message):
    config = load_config()
    print(f"[LIST] Загружен конфиг для списка групп: {config}")
    print(f"[LIST] Группы в chats: {list(config.get('chats', {}).keys())}")
    if not config["chats"]:
        return await message.answer("<i> 🔶 Список групп пуст. </i>", parse_mode="HTML",)
    text = "\n".join([f"{chat}" for chat in config["chats"].keys()])
    print(f"[LIST] Отправляем список: {text}")
    await message.answer(f"<b> Список добавленных групп:\n{text} </b>", parse_mode="HTML",)

# --- Глобальный флаг для фоновой задачи ---
schedule_broadcast_active = False

def set_schedule_active(active: bool):
    config = load_config()
    config["schedule_active"] = active
    save_config(config)

@dp.message(F.text == "🟢 Старт рассылки")
@private_chat_only
@owner_only
async def btn_launch(message: Message):
    config = load_config()
    config["active"] = True
    save_config(config)
    await message.answer("<b>✅ Рассылка включена.</b>", parse_mode="HTML")

@dp.message(F.text == "🔴 Стоп ")
@private_chat_only
@owner_only
async def btn_stop(message: Message, state: FSMContext):
    config = load_config()
    config["active"] = False
    save_config(config)
    await message.answer("<b>⛔️ Рассылка остановлена. </b>" , parse_mode="HTML")

@dp.message(F.text == "✏️ Редактировать сообщения")
@private_chat_only
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
    
    # Сортируем записи по времени от меньшего к большему
    entries_sorted = sorted(entries, key=lambda x: x.get("time", "00:00:00"))
    
    await callback.message.answer(
        "<b> Выберите сообщение для редактирования: </b>",parse_mode='html',
        reply_markup=get_edit_entry_inline_keyboard(entries_sorted)
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
    if message.text == "🔙 Назад":
        # Вернуться к списку сообщений для редактирования
        data = await state.get_data()
        group = data["selected_group"]
        config = load_config()
        entries = config.get("scheduled", {}).get(group, [])
        # Сортируем записи по времени от меньшего к большему
        entries_sorted = sorted(entries, key=lambda x: x.get("time", "00:00:00"))
        await message.answer(
            "Выберите сообщение для редактирования:",
            reply_markup=get_edit_entry_inline_keyboard(entries_sorted)
        )
        await state.set_state(BotStates.selected_group)
        return
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
    # Сброс last_sent_date при изменении времени
    if "last_sent_date" in entry:
        del entry["last_sent_date"]
    save_config(config)
    await message.answer("<i>Отправьте новое сообщение или 0, чтобы оставить прежнее сообщение:</i>",parse_mode='html')
    await state.set_state(EditScheduleStates.waiting_for_new_message)

@dp.message(EditScheduleStates.waiting_for_new_message)
async def save_new_message(message: Message, state: FSMContext):
    if message.text == "🔙 Назад":
        # Вернуться к списку сообщений для редактирования
        data = await state.get_data()
        group = data["selected_group"]
        config = load_config()
        entries = config.get("scheduled", {}).get(group, [])
        # Сортируем записи по времени от меньшего к большему
        entries_sorted = sorted(entries, key=lambda x: x.get("time", "00:00:00"))
        await message.answer(
            "Выберите сообщение для редактирования:",
            reply_markup=get_edit_entry_inline_keyboard(entries_sorted)
        )
        await state.set_state(BotStates.selected_group)
        return
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
    elif message.video:
        video = message.video
        file = await bot.get_file(video.file_id)
        file_path = f"media/{video.file_unique_id}.mp4"
        os.makedirs("media", exist_ok=True)
        await bot.download_file(file.file_path, destination=file_path)
        entry["media"] = file_path
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
    # Сброс last_sent_date при изменении сообщения
    if "last_sent_date" in entry:
        del entry["last_sent_date"]
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
    # Сортируем записи по времени от меньшего к большему
    entries_sorted = sorted(entries, key=lambda x: x.get("time", "00:00:00"))
    await callback.message.answer(
        "Выберите сообщение для редактирования:",
        reply_markup=get_edit_entry_inline_keyboard(entries_sorted)
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
                    print(f"[INFO] Пропуск: уже отправлено сегодня для {group} в {entry['time']} (last_sent_date={last_sent})")
                    continue
                now_seconds = now.hour*3600 + now.minute*60 + now.second
                t_seconds = t.hour*3600 + t.minute*60 + t.second
                if 0 <= now_seconds - t_seconds <= 300:
                    print(f"[LOG] Время отправки для {group}: {entry['time']}, отправляем... (опоздание: {now_seconds - t_seconds} сек)")
                    try:
                        await send_scheduled_message(group, entry)
                        entry["last_sent_date"] = today_str
                        save_config(config)
                    except Exception as e:
                        print(f"[ERROR] Не удалось отправить сообщение по расписанию в {group}: {e}")
        await asyncio.sleep(5)


async def send_scheduled_message(group, entry):
    print(f"[DEBUG] send_scheduled_message для {group}, entry: {entry}")
    chat = group
    try:
        # Проверяем права бота в группе
        try:
            chat_member = await bot.get_chat_member(chat_id=chat, user_id=bot.id)
            if chat_member.status not in ['administrator', 'creator']:
                print(f"[WARN] Бот не является администратором в {chat}, статус: {chat_member.status}")
        except Exception as e:
            print(f"[WARN] Не удалось проверить права бота в {chat}: {e}")
        
        if entry.get("media_group"):
            print(f"[LOG] Отправка медиа-группы в {chat}: {len(entry['media_group'])} элементов")
            print(f"[DEBUG] Содержимое медиа-группы: {entry['media_group']}")
            media_group = []
            for media_item in entry["media_group"]:
                print(f"[DEBUG] Обработка элемента: {media_item}")
                if media_item["type"] == "photo":
                    input_file = FSInputFile(media_item["file_path"])
                    media_group.append(types.InputMediaPhoto(media=input_file))
                    print(f"[DEBUG] Добавлено фото: {media_item['file_path']}")
                elif media_item["type"] == "video":
                    input_file = FSInputFile(media_item["file_path"])
                    media_group.append(types.InputMediaVideo(media=input_file))
                    print(f"[DEBUG] Добавлено видео: {media_item['file_path']}")
                elif media_item["type"] == "document":
                    media_group.append(types.InputMediaDocument(media=media_item["file_id"]))
                    print(f"[DEBUG] Добавлен документ: {media_item['file_id']}")
            
            # Добавляем подпись к первому элементу
            if media_group and entry.get("message"):
                media_group[0].caption = entry["message"]
                if entry.get("caption_entities"):
                    media_group[0].caption_entities = [types.MessageEntity.model_validate(e) for e in entry["caption_entities"]]
            
            await bot.send_media_group(chat_id=chat, media=media_group, disable_notification=True)
        elif entry.get("media"):
            print(f"[LOG] Отправка медиа в {chat}: {entry['media']}")
            media_path = entry["media"]
            if media_path.endswith(".jpg") or media_path.endswith(".png"):
                input_file = FSInputFile(media_path)
                await bot.send_photo(
                    chat_id=chat, 
                    photo=input_file, 
                    caption=entry.get("message", ""), 
                    caption_entities=[types.MessageEntity.model_validate(e) for e in entry.get("caption_entities", [])] if entry.get("caption_entities") else None,
                    disable_notification=True
                )
            elif media_path.endswith(".mp4") or media_path.endswith(".mov") or media_path.endswith(".m4v"):
                input_file = FSInputFile(media_path)
                await bot.send_video(
                    chat_id=chat,
                    video=input_file,
                    caption=entry.get("message", ""),
                    caption_entities=[types.MessageEntity.model_validate(e) for e in entry.get("caption_entities", [])] if entry.get("caption_entities") else None,
                    disable_notification=True
                )
            else:
                input_file = FSInputFile(media_path)
                await bot.send_document(
                    chat_id=chat, 
                    document=input_file, 
                    caption=entry.get("message", ""), 
                    caption_entities=[types.MessageEntity.model_validate(e) for e in entry.get("caption_entities", [])] if entry.get("caption_entities") else None,
                    disable_notification=True
                )
        elif entry.get("message"):
            print(f"[LOG] Отправка текста в {chat}: {entry['message']}")
            await bot.send_message(
                chat_id=chat, 
                text=entry["message"], 
                entities=[types.MessageEntity.model_validate(e) for e in entry.get("entities", [])] if entry.get("entities") else None, 
                parse_mode=None,
                disable_notification=True,  # Отключаем уведомления
                disable_web_page_preview=True  # Отключаем превью ссылок
            )
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
                # Проверяем права бота в группе
                try:
                    chat_member = await bot.get_chat_member(chat_id=group, user_id=bot.id)
                    if chat_member.status not in ['administrator', 'creator']:
                        print(f"[WARN] Бот не является администратором в {group}, статус: {chat_member.status}")
                except Exception as e:
                    print(f"[WARN] Не удалось проверить права бота в {group}: {e}")
                
                if data.get("media_group"):
                    print(f"[LOG] Отправка медиа-группы в {group}: {len(data['media_group'])} элементов")
                    print(f"[DEBUG] Содержимое медиа-группы задержки: {data['media_group']}")
                    media_group = []
                    for media_item in data["media_group"]:
                        print(f"[DEBUG] Обработка элемента задержки: {media_item}")
                        if media_item["type"] == "photo":
                            input_file = FSInputFile(media_item["file_path"])
                            media_group.append(types.InputMediaPhoto(media=input_file))
                            print(f"[DEBUG] Добавлено фото задержки: {media_item['file_path']}")
                        elif media_item["type"] == "video":
                            input_file = FSInputFile(media_item["file_path"])
                            media_group.append(types.InputMediaVideo(media=input_file))
                            print(f"[DEBUG] Добавлено видео задержки: {media_item['file_path']}")
                        elif media_item["type"] == "document":
                            media_group.append(types.InputMediaDocument(media=media_item["file_id"]))
                            print(f"[DEBUG] Добавлен документ задержки: {media_item['file_id']}")
                    
                    # Добавляем подпись к первому элементу
                    if media_group and data.get("message"):
                        media_group[0].caption = data["message"]
                        if data.get("caption_entities"):
                            media_group[0].caption_entities = [types.MessageEntity.model_validate(e) for e in data["caption_entities"]]
                    
                    await bot.send_media_group(chat_id=group, media=media_group, disable_notification=True)
                elif data.get("media"):
                    print(f"[LOG] Отправка медиа в {group}: {data['media']}")
                    media_path = data["media"]
                    if media_path.endswith(".jpg") or media_path.endswith(".png"):
                        input_file = FSInputFile(media_path)
                        await bot.send_photo(
                            chat_id=group, 
                            photo=input_file, 
                            caption=data.get("message", ""), 
                            caption_entities=[types.MessageEntity.model_validate(e) for e in data.get("caption_entities", [])] if data.get("caption_entities") else None,
                            disable_notification=True
                        )
                    elif media_path.endswith(".mp4") or media_path.endswith(".mov") or media_path.endswith(".m4v"):
                        input_file = FSInputFile(media_path)
                        await bot.send_video(
                            chat_id=group,
                            video=input_file,
                            caption=data.get("message", ""),
                            caption_entities=[types.MessageEntity.model_validate(e) for e in data.get("caption_entities", [])] if data.get("caption_entities") else None,
                            disable_notification=True
                        )
                    else:
                        input_file = FSInputFile(media_path)
                        await bot.send_document(
                            chat_id=group, 
                            document=input_file, 
                            caption=data.get("message", ""), 
                            caption_entities=[types.MessageEntity.model_validate(e) for e in data.get("caption_entities", [])] if data.get("caption_entities") else None,
                            disable_notification=True
                        )
                elif data.get("message"):
                    print(f"[LOG] Отправка текста в {group}: {data['message']}")
                    await bot.send_message(
                        chat_id=group, 
                        text=data["message"], 
                        entities=[types.MessageEntity.model_validate(e) for e in data.get("entities", [])] if data.get("entities") else None, 
                        parse_mode=None,
                        disable_notification=True,  # Отключаем уведомления
                        disable_web_page_preview=True  # Отключаем превью ссылок
                    )
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
@private_chat_only
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
@private_chat_only
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
@private_chat_only
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
@private_chat_only
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
@private_chat_only
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
        "<b> Выберите группу для настройки расписания: </b>",
        reply_markup=get_group_keyboard("schedule"), parse_mode="HTML"
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
    if not (message.text or message.photo or message.document or message.video):
        await message.answer("<i> ♦️ Не удалось распознать сообщение. Отправьте текст или медиа. </i> ", parse_mode="HTML",)
        return
    
    # Если это медиа-группа, переключаемся на специальный обработчик
    if message.media_group_id:
        print(f"[DEBUG] Обнаружена медиа-группа: {message.media_group_id}")
        await state.set_state(ScheduleStates.collecting_media_group)
        # Обрабатываем первое сообщение медиа-группы
        await handle_media_group(message, state)
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
    elif message.video:
        video = message.video
        file = await bot.get_file(video.file_id)
        file_path = f"media/{video.file_unique_id}.mp4"
        os.makedirs("media", exist_ok=True)
        await bot.download_file(file.file_path, destination=file_path)
        entry["media"] = file_path
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

# Обработчик для медиа-групп (несколько фото/видео в одном сообщении)
@dp.message(ScheduleStates.collecting_media_group)
@owner_only
async def handle_media_group(message: Message, state: FSMContext):
    """Обработка медиа-групп для расписания"""
    print(f"[FSM] Обработка медиа-группы: {message.media_group_id}")
    
    data = await state.get_data()
    chat = data["selected_group"]
    scheduled_time = data["scheduled_time"]
    config = load_config()
    
    # Проверяем, есть ли уже запись с таким временем
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
    
    # Собираем медиа-группу
    media_group_id = message.media_group_id
    if media_group_id not in data.get("media_groups", {}):
        data["media_groups"] = {media_group_id: []}
        await state.update_data(media_groups=data["media_groups"])
    
    # Добавляем текущее медиа в группу
    media_item = {}
    if message.photo:
        photo = message.photo[-1]
        file = await bot.get_file(photo.file_id)
        file_path = f"media/{photo.file_unique_id}.jpg"
        os.makedirs("media", exist_ok=True)
        await bot.download_file(file.file_path, destination=file_path)
        media_item = {"type": "photo", "file_path": file_path}
    elif message.video:
        video = message.video
        file = await bot.get_file(video.file_id)
        file_path = f"media/{video.file_unique_id}.mp4"
        os.makedirs("media", exist_ok=True)
        await bot.download_file(file.file_path, destination=file_path)
        media_item = {"type": "video", "file_path": file_path}
    elif message.document:
        file_id = message.document.file_id
        media_item = {"type": "document", "file_id": file_id}
    
    if media_item:
        data["media_groups"][media_group_id].append(media_item)
        await state.update_data(media_groups=data["media_groups"])
    
    # Если это последнее сообщение в группе (нет caption или это текстовое сообщение)
    if message.caption or (message.text and not message.photo and not message.video and not message.document):
        # Сохраняем медиа-группу
        entry = {
            "time": scheduled_time,
            "media_group": data["media_groups"][media_group_id],
            "message": message.caption or message.text or "",
            "caption_entities": [e.model_dump() for e in message.caption_entities] if message.caption_entities else None,
            "entities": [e.model_dump() for e in message.entities] if message.entities else None
        }
        print(f"[DEBUG] Сохраняем медиа-группу: {entry}")
        
        # Сохраняем в config
        if "scheduled" not in config:
            config["scheduled"] = {}
        if chat not in config["scheduled"]:
            config["scheduled"][chat] = []
        config["scheduled"][chat].append(entry)
        save_config(config)
        
        await message.answer(f"<i>🔸 Медиа-группа по расписанию для {chat} добавлена на {scheduled_time} </i>", parse_mode="HTML")
        await state.clear()
        await message.answer("<b> 🔽 Выберите действие: </b>", parse_mode="HTML", reply_markup=main_menu)

@dp.message(F.text == "⏳ По задержке")
@private_chat_only
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
@private_chat_only
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
@private_chat_only
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
@private_chat_only
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
    # Сортируем записи по времени, сохраняя исходные индексы
    entries_sorted = sorted(
        list(enumerate(entries)),
        key=lambda item: item[1].get("time", "00:00:00")
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text=f"{entry['time']} | {entry.get('message', '')[:20]}",
                callback_data=f"delete_schedule_entry:{idx}"
            )]
            for idx, entry in entries_sorted
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
        
        # Удаляем медиа-файлы удаленной записи
        removed_media = []
        
        # Удаляем одиночный медиа-файл
        media_path = removed.get("media")
        if media_path and os.path.isfile(media_path):
            removed_media.append(media_path)
            print(f"[DELETE] Добавлен медиа-файл для удаления: {media_path}")
        
        # Удаляем медиа-группу
        media_group = removed.get("media_group")
        if media_group:
            print(f"[DELETE] Найдена медиа-группа в удаляемой записи: {len(media_group)} элементов")
            for media_item in media_group:
                if media_item.get("type") in ["photo", "video"]:
                    file_path = media_item.get("file_path")
                    if file_path and os.path.isfile(file_path):
                        removed_media.append(file_path)
                        print(f"[DELETE] Добавлен файл из медиа-группы для удаления: {file_path}")
        
        # Удаляем все найденные медиа-файлы
        for path in removed_media:
            with suppress(Exception):
                os.remove(path)
                print(f"[DELETE] Удален медиа-файл: {path}")
        
        save_config(config)
        await callback.message.answer(f"<i>♦️ Удалена запись на {removed['time']}</i>", parse_mode="HTML")
    # После удаления — если остались записи, снова показываем выбор, иначе возвращаем к выбору группы
    if entries:
        # Сортируем записи по времени от меньшего к большему, сохраняя индексы
        entries_sorted = sorted(
            list(enumerate(entries)),
            key=lambda item: item[1].get("time", "00:00:00")
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(
                    text=f"{entry['time']} | {entry.get('message', '')[:20]}",
                    callback_data=f"delete_schedule_entry:{idx}"
                )]
                for idx, entry in entries_sorted
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