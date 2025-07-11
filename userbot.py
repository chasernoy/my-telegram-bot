import json
import asyncio
from telethon import TelegramClient, events
import datetime
from telethon.tl.types import (
    MessageEntityBold, MessageEntityItalic, MessageEntityCode, MessageEntityPre, MessageEntityTextUrl,
    MessageEntityStrike, MessageEntityUnderline, MessageEntitySpoiler, MessageEntityMention, MessageEntityUrl,
    MessageEntityMentionName, MessageEntityHashtag, MessageEntityBotCommand, MessageEntityEmail, MessageEntityPhone,
    MessageEntityCashtag, MessageEntityBankCard, MessageEntityCustomEmoji
)
import traceback
import telethon
ENTITY_TYPE_MAP = {
    'bold': MessageEntityBold,
    'italic': MessageEntityItalic,
    'code': MessageEntityCode,
    'pre': MessageEntityPre,
    'text_link': MessageEntityTextUrl,
    'strikethrough': MessageEntityStrike,
    'underline': MessageEntityUnderline,
    'spoiler': MessageEntitySpoiler,
    'mention': MessageEntityMention,
    'url': MessageEntityUrl,
    'mention_name': MessageEntityMentionName,
    'hashtag': MessageEntityHashtag,
    'bot_command': MessageEntityBotCommand,
    'email': MessageEntityEmail,
    'phone_number': MessageEntityPhone,
    'cashtag': MessageEntityCashtag,
    'bank_card': MessageEntityBankCard,
    'custom_emoji': MessageEntityCustomEmoji,
}

def parse_entities(entities_data):
    if not entities_data:
        return None
    result = []
    for ent in entities_data:
        ent_type = ent.get('type')
        cls = ENTITY_TYPE_MAP.get(ent_type)
        if not cls:
            continue
        # Для MessageEntityTextUrl нужен аргумент url
        if ent_type == 'text_link':
            result.append(cls(offset=ent['offset'], length=ent['length'], url=ent['url']))
        elif ent_type == 'mention_name':
            result.append(cls(offset=ent['offset'], length=ent['length'], user_id=ent['user']))
        elif ent_type == 'custom_emoji':
            result.append(cls(offset=ent['offset'], length=ent['length'], document_id=ent['custom_emoji_id']))
        else:
            result.append(cls(offset=ent['offset'], length=ent['length']))
    return result

from dotenv import load_dotenv
import os

load_dotenv()
api_id = int(os.getenv('API_ID'))
api_hash = os.getenv('API_HASH')
client = TelegramClient('userbot_session', api_id, api_hash)

CONFIG_PATH = 'config.json'

def format_time(seconds):
    if seconds < 60:
        return f"{seconds} сек."
    elif seconds < 3600:
        minutes = seconds // 60
        return f"{minutes} мин."
    elif seconds >= 3600:
        hours = seconds // 3600
        return f"{hours} ч."
def load_config():
    with open(CONFIG_PATH, 'r') as f:
        return json.load(f)


async def spam_task():
    notified = False
    cycle_number = 1

    while True:
        try:
            config = load_config()

            if not config.get('active', False):
                if not notified:
                    await asyncio.sleep(10)
                await asyncio.sleep(10)
                continue
            else:
                if notified:
                    await asyncio.sleep(10)

            chats = config.get('chats', {})
            for chat, settings in chats.items():
                message = settings.get('message')
                media_path = settings.get('media')
                delay = settings.get('delay', 60)
                entities = parse_entities(settings.get('entities'))
                caption_entities = parse_entities(settings.get('caption_entities'))

                formatted_delay = format_time(delay)  # Преобразуем задержку в формат

                try:
                    if not message and not media_path:
                        await asyncio.sleep(delay)
                    elif media_path and message:
                        await client.send_file(chat, media_path, caption=message, formatting_entities=caption_entities)
                    elif media_path:
                        await client.send_file(chat, media_path)
                    else:
                        await client.send_message(chat, message, formatting_entities=entities)

                    cycle_number += 1  # Увеличиваем номер цикла
                    await asyncio.sleep(delay)  # Задержка
                except Exception as e:
                    print(f"[ERROR] spam_task: {e}")
                    traceback.print_exc()
                    await asyncio.sleep(10)  # Пауза перед повтором в случае ошибки

        except asyncio.CancelledError:
            break  # Завершаем выполнение в случае отмены задачи
        except Exception as e:
            print(f"[ERROR] spam_task outer: {e}")
            traceback.print_exc()
            await asyncio.sleep(10)  # Пауза перед повтором в случае ошибки

async def scheduled_task():
    notified = False
    while True:
        try:
            config = load_config()
            if not config.get('schedule_active', False):
                if not notified:
                    await asyncio.sleep(10)
                await asyncio.sleep(10)
                continue
            else:
                if notified:
                    await asyncio.sleep(10)

            now = datetime.datetime.now().strftime("%H:%M:%S")
            scheduled = config.get("scheduled", {})
            for chat, messages in scheduled.items():
                for entry in messages:
                    if entry["time"] == now:
                        msg = entry.get("message")
                        media = entry.get("media")
                        entities = parse_entities(entry.get('entities'))
                        caption_entities = parse_entities(entry.get('caption_entities'))
                        try:
                            if media and msg:
                                await client.send_file(chat, media, caption=msg, formatting_entities=caption_entities)
                            elif media:
                                await client.send_file(chat, media)
                            elif msg:
                                await client.send_message(chat, msg, formatting_entities=entities)
                        except Exception as e:
                            print(f"[ERROR] scheduled_task: {e}")
                            traceback.print_exc()
                            await asyncio.sleep(10)
            await asyncio.sleep(1)
        except Exception as e:
            print(f"[ERROR] scheduled_task outer: {e}")
            traceback.print_exc()
            await asyncio.sleep(10)

async def send_userbot_log(text):
    # Гарантируем, что клиент запущен
    if not client.is_connected():
        await client.start()
    await client.send_message('me', text)

# В main запускаем обе задачи
async def main():
    await client.start()
    await asyncio.gather(
        spam_task(),
        scheduled_task()
    )


async def run_userbot():
    await main()
