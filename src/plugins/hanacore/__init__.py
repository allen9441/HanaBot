import random
import re
import sys # 新增 sys 模組
from collections import defaultdict
from nonebot import on_message, logger, get_driver, on_command # 新增 on_command
from nonebot.permission import Permission # 引入 Permission
from nonebot.matcher import Matcher
from nonebot.rule import to_me
from nonebot.adapters.discord import Bot, MessageEvent, MessageSegment
from nonebot.plugin import PluginMetadata
from typing import Dict, List, Optional

from .openai import get_openai_reply
from .script import check_reply

#  Plugin info
__plugin_meta__ = PluginMetadata(
    name="Chatting with Hanachan",
    description="@Hanachan to start a chat",
    usage="@Hanachan + [message]",
    type="application",
    supported_adapters={"nonebot.adapters.discord"},
)

# --- 狀態管理 ---
# 存儲每個頻道的計數器和下一個觸發目標
# 結構: {channel_id: {"count": 0, "target": 12}}
# 使用 defaultdict 簡化初始化
channel_counters: Dict[int, Dict[str, int]] = defaultdict(
    lambda: {"count": 0, "target": random.randint(10, 15)}
)

# 存儲每個對話 session 的歷史記錄
# 結構: {session_id: [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]}
conversation_history: Dict[str, List[Dict[str, str]]] = defaultdict(list)
MAX_HISTORY_LENGTH = 20 # 限制歷史記錄長度

# --- 處理 @ 消息的響應器 ---
# 保持原來的優先級和 block=True
at_reply_handler = on_message(rule=to_me(), priority=10, block=True)

@at_reply_handler.handle()
async def handle_at_reply(bot: Bot, event: MessageEvent, matcher: Matcher):

    # # 檢查頻道 ID 是否在黑名單內
    # channel_id = event.channel_id
    # blackchannel = getattr(get_driver().config, "blackchannels", None)
    # if channel_id in blackchannel:
    #     logger.debug(f"消息來自黑名單頻道：{channel_id}，不做出回應。")
    #     return
    
    raw_user_message = event.get_message()
    image_url: Optional[str] = None

    # Use event.attachments directly based on the provided event structure
    if hasattr(event, 'attachments') and event.attachments:
        for attachment in event.attachments:
            # Access attributes using dot notation for Attachment objects
            content_type = getattr(attachment, 'content_type', None)
            url = getattr(attachment, 'url', None)
            if content_type and content_type.startswith('image/') and url:
                image_url = url
                logger.debug(f"檢測到圖片附件 (@ 消息): {image_url}")
                break # 只處理第一個圖片

    # If there's no text AND no image, then ignore.
    # This allows messages with only an image (and the @mention) to proceed.
    if not raw_user_message and not image_url:
        logger.debug("消息無文字內容也無圖片附件，已忽略 (@ 消息)")
        return

    # 獲取用戶名
    username = event.author.global_name if event.author.global_name else event.author.username

    # --- 替換@格式 ---
    processed_message_content = str(raw_user_message) 
    if hasattr(event, 'mentions') and event.mentions:
        logger.debug(f"Found {len(event.mentions)} mentions in @ message, attempting replacement.")
        mention_map = {}
        for mention in event.mentions:
            if hasattr(mention, 'id') and (hasattr(mention, 'global_name') or hasattr(mention, 'username')):
                 mention_id = str(mention.id)
                 mention_name = mention.global_name if mention.global_name else mention.username
                 if mention_name:
                     mention_map[mention_id] = mention_name

        if mention_map:
            def replace_mention(match):
                user_id = match.group(1)
                if user_id in mention_map:
                    username_mention = mention_map[user_id]
                    return f"<@{user_id}>({username_mention})"
                else:
                    return match.group(0)

            processed_message_content = re.sub(r"<@!?(\d+)>", replace_mention, processed_message_content)
            logger.debug(f"替換提及後的訊息：'{processed_message_content[:50]}...'")
        else:
            logger.debug("提及列表為空。")
    # else:
    #     logger.debug("無提及用戶消息或其他未知問題。")

    # 準備傳遞給 API 的內容
    text_content = processed_message_content

    # 格式化用於記錄的消息 (簡單表示)
    log_message_content = str(raw_user_message) + (" [image]" if image_url else "")

    session_id = str(event.channel_id)
    logger.info(f"收到 @ 消息, Channel: {session_id}, 用戶: {username} ({event.get_user_id()}), 內容: '{log_message_content}'")

    current_history = conversation_history[session_id]

    # --- 發送 typing 指示 ---
    try:
        await bot.trigger_typing_indicator(channel_id=event.channel_id)
        logger.debug(f"已為 Channel {session_id} 發送 typing 指示")
    except Exception as e:
        logger.warning(f"為 Channel {session_id} 發送 typing 指示時出錯: {e}")

    # 調用 API，傳入用戶名、文字內容、圖片 URL、歷史記錄和最大長度
    ai_reply, updated_history = await get_openai_reply(
        username=username,
        text_content=text_content,
        image_url=image_url,
        history=current_history,
        max_history_length=MAX_HISTORY_LENGTH,
        channel_id=str(event.channel_id)
    )

    if ai_reply:
        # History is updated inside get_openai_reply
        conversation_history[session_id] = updated_history
        logger.debug(f"Channel {session_id} 歷史記錄已更新，長度: {len(updated_history)}")

        # --- 檢查 AI 回覆是否包含 timeout 指令 ---
        timeout_handled, final_reply = await check_reply(bot, event, ai_reply, matcher)

        # --- 確定最終要發送的回覆 ---
        # 如果 timeout 被處理且有清理後的回覆，使用清理後的回覆
        # 否則（未處理 timeout），使用原始 ai_reply
        # 注意：如果 timeout_handled 為 True 但 final_reply 為 None (例如解析錯誤)，則不發送任何 AI 回覆
        message_to_send = final_reply if timeout_handled and final_reply is not None else (ai_reply if not timeout_handled else None)

        # --- 發送最終回覆 ---
        if message_to_send is not None:
            try:
                await matcher.send(MessageSegment.text(message_to_send), reply_message=event.id)
                logger.debug(f"已成功回覆 Channel {session_id} 中的消息 {event.id}")
            except Exception as e:
                logger.error(f"在 Channel {session_id} 回覆消息 {event.id} 時發生錯誤: {e}")
                # 如果回覆失敗，嘗試直接發送
                try:
                    # 確保直接發送時也使用最終確定的回覆
                    await matcher.send(MessageSegment.text(f"回覆時出錯，嘗試直接發送：\n{message_to_send}"))
                except Exception as fallback_e:
                     logger.error(f"在 Channel {session_id} 直接發送消息也失敗: {fallback_e}")


# --- 處理隨機回覆的響應器 ---
# priority 設低一點，確保 @ 優先處理
# block=False 允許消息繼續被其他插件處理（如果有的話）
random_reply_handler = on_message(priority=99, block=False)

@random_reply_handler.handle()
async def handle_random_reply(bot: Bot, event: MessageEvent, matcher: Matcher):

    # 1. 獲取頻道 ID 和消息內容
    #    伺服器頻道和私訊皆使用 channel_id 作為識別符
    if not hasattr(event, 'channel_id') or not event.channel_id:
        # logger.debug("無法獲取 channel_id，跳過隨機回覆計數")
        return 

    # 2. 檢查頻道 ID 是否在黑名單內
    channel_id = event.channel_id
    blackchannel = getattr(get_driver().config, "blackchannels", None)
    if channel_id in blackchannel:
        logger.debug(f"消息來自黑名單頻道：{channel_id}，不做出回應。")
        return
    
    # 3. 檢查是否是機器人自己的消息，避免自我觸發和計數
    if str(event.get_user_id()) == str(bot.self_id):
         return

    # 4. 檢查是否是 @ 消息，如果是，則由 at_reply_handler 處理，這裡忽略
    if event.is_tome():
        return

    raw_user_message = event.get_message()
    image_url: Optional[str] = None

    # Use event.attachments directly
    if hasattr(event, 'attachments') and event.attachments:
        for attachment in event.attachments:
            # Access attributes using dot notation for Attachment objects
            content_type = getattr(attachment, 'content_type', None)
            url = getattr(attachment, 'url', None)
            if content_type and content_type.startswith('image/') and url:
                image_url = url
                logger.debug(f"檢測到圖片附件 (隨機回覆計數): {image_url}")
                break # 只處理第一個圖片

    # If there's no text AND no image, then don't count or trigger
    if not raw_user_message and not image_url:
        logger.debug("消息無文字內容也無圖片附件，不計數 (隨機回覆)")
        return

    # 獲取用戶名
    username = event.author.global_name if event.author.global_name else event.author.username

    # --- 替換@格式 ---
    processed_message_content = str(raw_user_message)
    if hasattr(event, 'mentions') and event.mentions:
        logger.debug(f"找到 {len(event.mentions)} 個提及對象，嘗試替換。")
        mention_map = {}
        for mention in event.mentions:
            # 確保提及對象有id及username
            if hasattr(mention, 'id') and (hasattr(mention, 'global_name') or hasattr(mention, 'username')):
                 mention_id = str(mention.id)
                 mention_name = mention.global_name if mention.global_name else mention.username
                 if mention_name:
                     mention_map[mention_id] = mention_name

        if mention_map:
            # 使用正則替換
            def replace_mention(match):
                user_id = match.group(1)
                if user_id in mention_map:
                    username_mention = mention_map[user_id]
                    return f"<@{user_id}>({username_mention})"
                else:
                    return match.group(0)

            # 正則如下
            processed_message_content = re.sub(r"<@!?(\d+)>", replace_mention, processed_message_content)
            # logger.debug(f"替換提及後的格式：'{processed_message_content[:50]}...'")
        # else:
        #     logger.debug("提及列表中沒有用戶。")
    # else:
    #     logger.debug("No mentions found or event structure doesn't support mentions attribute (random reply trigger).")

    # 準備傳遞給 API 的內容和記錄
    text_content = processed_message_content 

    log_message_content = str(raw_user_message) + (" [image]" if image_url else "")

    history_formatted_message = f"{username}: {processed_message_content}" + (" [image]" if image_url else "") # 包含用戶名和圖片標記 (使用處理後內容)

    # 5. 更新計數器
    counter_data = channel_counters[channel_id]
    counter_data["count"] += 1
    logger.debug(f"頻道 {channel_id} 消息計數: {counter_data['count']}/{counter_data['target']}")

    # 6. 檢查是否達到觸發閾值
    if counter_data["count"] >= counter_data["target"]:
        # 使用 channel_id 作為歷史記錄的 key
        session_id = str(channel_id)
        logger.info(f"頻道 {channel_id} 達到隨機回覆閾值 ({counter_data['count']}/{counter_data['target']}), Channel: {session_id}, 觸發者: {username}, 消息: '{log_message_content[:30]}...'")

        # 重置計數器並設定下一個目標
        counter_data["count"] = 0
        counter_data["target"] = random.randint(10, 15) # 設定下一個 10-15 之間的隨機目標
        logger.debug(f"頻道 {channel_id} 計數器已重置，下一個目標: {counter_data['target']}")

        # 獲取當前 channel 的歷史記錄
        current_history = conversation_history[session_id]

        # --- 發送 typing 指示 ---
        try:
            await bot.trigger_typing_indicator(channel_id=event.channel_id)
            logger.debug(f"已為 Channel {session_id} (隨機回覆) 發送 typing 指示")
        except Exception as e:
            logger.warning(f"為 Channel {session_id} (隨機回覆) 發送 typing 指示時出錯: {e}")

        # 調用 OpenAI API，傳入用戶名、文字內容、圖片 URL、歷史記錄和最大長度
        ai_reply, updated_history = await get_openai_reply(
            username=username,
            text_content=text_content,
            image_url=image_url,
            history=current_history,
            max_history_length=MAX_HISTORY_LENGTH,
            channel_id=str(event.channel_id)
        )

        if ai_reply:
            
            # 去除 timeout(); 部分，但不執行動作
            # logger.debug(f"原訊息：{ai_reply}")
            cleaned_reply = re.compile(r"timeout\(\s*.*?\s*\);").sub("", ai_reply).strip()
            
            # History is updated inside get_openai_reply
            conversation_history[session_id] = updated_history
            logger.debug(f"Channel {session_id} 歷史記錄已通過隨機回覆更新，長度: {len(updated_history)}")

            # 發送回覆 (如果 get_openai_reply 返回的是錯誤信息，也會在這裡發送)
            try:
                await at_reply_handler.send(MessageSegment.text(cleaned_reply), reply_message=event.id)
                logger.debug(f"已成功回覆 Channel {session_id} 中的消息 {event.id}")
            except Exception as e:
                logger.error(f"在 Channel {session_id} 回覆消息 {event.id} 時發生錯誤: {e}")
                # 如果回覆失敗，嘗試直接發送
                try:
                    await at_reply_handler.send(MessageSegment.text(f"回覆時出錯，嘗試直接發送：\n{cleaned_reply}"))
                except Exception as fallback_e:
                    logger.error(f"在 Channel {session_id} 直接發送消息也失敗: {fallback_e}")

    else:
        # 如果未達到閾值，仍然將包含用戶名和圖片標記的消息記錄到歷史中
        session_id = str(channel_id) # 同樣使用 channel_id 作為 key
        current_history = conversation_history[session_id]
        # 只添加格式化後的用戶消息 (包含圖片標記)
        updated_history = current_history + [{"role": "user", "content": history_formatted_message}]
        # 限制歷史記錄長度
        if len(updated_history) > MAX_HISTORY_LENGTH:
            updated_history = updated_history[-MAX_HISTORY_LENGTH:]
        conversation_history[session_id] = updated_history
        logger.debug(f"Channel {session_id}: 未達閾值，已記錄用戶消息 ({username}): '{log_message_content[:20]}...'，歷史長度: {len(updated_history)}")


# --- 檢查是否為擁有者 ---
SPECIFIC_USER_ID = "473722884573888522" # 您的使用者 ID

async def _is_specific_user(event: MessageEvent) -> bool:
    """檢查事件觸發者是否為擁有者 ID"""
    return event.get_user_id() == SPECIFIC_USER_ID

IS_SPECIFIC_USER = Permission(_is_specific_user)


# --- !wack 指令：清除當前頻道短期記憶 (僅限擁有者) ---
wack_handler = on_command("wack", aliases={"清除記憶"}, permission=IS_SPECIFIC_USER, priority=5, block=True)

@wack_handler.handle()
async def handle_wack(event: MessageEvent, matcher: Matcher):
    session_id = str(event.channel_id)
    if session_id in conversation_history:
        del conversation_history[session_id]
        logger.info(f"指定用戶 {event.get_user_id()} 在頻道 {session_id} 清除了短期記憶。")
        await matcher.send("操你媽敲沙小，我腦袋都空了。", reply_message=event.id)
    else:
        logger.debug(f"指定用戶 {event.get_user_id()} 嘗試清除頻道 {session_id} 的記憶，但該頻道無歷史記錄。")
        await matcher.send("腦袋沒東西了啦，敲啥。", reply_message=event.id)


# --- !down 指令：關閉 Bot (僅限擁有者) ---
reset_handler = on_command("down", aliases={"關閉"}, permission=IS_SPECIFIC_USER, priority=5, block=True)

@reset_handler.handle()
async def handle_reset(event: MessageEvent, matcher: Matcher): # 加入 event 參數以供日誌記錄
    logger.warning(f"收到指定用戶 {event.get_user_id()} 的指令，準備關閉 Bot...")
    await matcher.send("小睡一下，等等回來")
    # 使用 sys.exit() 來觸發退出，依賴外部管理器 (如 pm2, systemd) 重啟
    sys.exit(0)
