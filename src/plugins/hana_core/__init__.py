import nonebot
import random
from collections import defaultdict # 方便初始化字典
from nonebot import on_message, logger
from nonebot.rule import to_me
from nonebot.adapters.discord import Bot, MessageEvent, Message, MessageSegment
from nonebot.params import EventMessage
from nonebot.plugin import PluginMetadata
from typing import Dict, List, Optional

from .openai import get_openai_reply

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
MAX_HISTORY_LENGTH = 10 # 限制歷史記錄長度 (例如保留最近 5 組對話)


# --- 處理 @ 消息的響應器 ---
# 保持原來的優先級和 block=True
at_reply_handler = on_message(rule=to_me(), priority=10, block=True)

@at_reply_handler.handle()
async def handle_at_reply(bot: Bot, event: MessageEvent):
    # Get text content, strip() might remove mention if it's the only text
    raw_user_message = event.get_plaintext().strip()
    image_url: Optional[str] = None

    # Use event.attachments directly based on the provided event structure
    if hasattr(event, 'attachments') and event.attachments:
        for attachment in event.attachments:
            # Access attributes using dot notation for Attachment objects
            content_type = getattr(attachment, 'content_type', None) # Use getattr for safety
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

    # --- The rest of the function should execute now ---
    # 獲取用戶名
    username = event.author.global_name

    # 準備傳遞給 API 的內容
    text_content = raw_user_message
    # 格式化用於記錄的消息 (簡單表示)
    log_message_content = text_content + (" [image]" if image_url else "")

    session_id = str(event.channel_id)
    logger.info(f"收到 @ 消息, Channel: {session_id}, 用戶: {username} ({event.get_user_id()}), 內容: '{log_message_content}'")

    current_history = conversation_history[session_id]

    # 調用 API，傳入用戶名、文字內容、圖片 URL、歷史記錄和最大長度
    ai_reply, updated_history = await get_openai_reply(
        username=username,
        text_content=text_content,
        image_url=image_url,
        history=current_history,
        max_history_length=MAX_HISTORY_LENGTH
    )

    if ai_reply:
        # History is updated inside get_openai_reply
        conversation_history[session_id] = updated_history
        # Log update confirmation (using channel_id as session_id)
        logger.debug(f"Channel {session_id} 歷史記錄已更新，長度: {len(updated_history)}")

        # 回覆時也 @ 發送者 (假設 Discord 會自動處理，但實測前未知)
        # 如果需要明確 @，可以使用 f"{MessageSegment.mention(event.user_id)} {ai_reply}"
        # 但直接發送通常更好
        # 發送回覆 (如果 get_openai_reply 返回的是錯誤信息，也會在這裡發送)
        await at_reply_handler.send(MessageSegment.text(ai_reply))


# --- 處理隨機回覆的響應器 ---
# priority 設低一點，確保 @ 優先處理
# block=False 允許消息繼續被其他插件處理（如果有的話）
random_reply_handler = on_message(priority=99, block=False)

@random_reply_handler.handle()
async def handle_random_reply(bot: Bot, event: MessageEvent):
    # 1. 檢查是否是機器人自己的消息，避免自我觸發和計數
    if str(event.get_user_id()) == str(bot.self_id):
         return

    # 2. 檢查是否是 @ 消息，如果是，則由 at_reply_handler 處理，這裡忽略
    #    使用 event.is_tome() 可以判斷
    if event.is_tome():
        return

    # 3. 獲取頻道 ID 和消息內容
    #    伺服器頻道和私訊皆使用 channel_id 作為識別符
    if not hasattr(event, 'channel_id') or not event.channel_id:
        # logger.debug("無法獲取 channel_id，跳過隨機回覆計數")
        return 

    channel_id = event.channel_id
    raw_user_message = event.get_plaintext().strip()
    image_url: Optional[str] = None

    # Use event.attachments directly
    if hasattr(event, 'attachments') and event.attachments:
        for attachment in event.attachments:
            # Access attributes using dot notation for Attachment objects
            content_type = getattr(attachment, 'content_type', None) # Use getattr for safety
            url = getattr(attachment, 'url', None)
            if content_type and content_type.startswith('image/') and url:
                image_url = url
                logger.debug(f"檢測到圖片附件 (隨機回覆計數): {image_url}")
                break # 只處理第一個圖片

    # If there's no text AND no image, then don't count or trigger
    if not raw_user_message and not image_url:
        logger.debug("消息無文字內容也無圖片附件，不計數 (隨機回覆)")
        return

    # --- The rest of the function proceeds ---
    # 獲取用戶名
    username = event.member.nick if event.member and event.member.nick else event.author.username

    # 準備傳遞給 API 的內容和記錄
    text_content = raw_user_message
    log_message_content = text_content + (" [image]" if image_url else "")
    # 用於存儲歷史的格式化消息
    history_formatted_message = f"{username}: {log_message_content}" # 包含用戶名和圖片標記

    # 4. 更新計數器
    counter_data = channel_counters[channel_id]
    counter_data["count"] += 1
    logger.debug(f"頻道 {channel_id} 消息計數: {counter_data['count']}/{counter_data['target']}")

    # 5. 檢查是否達到觸發閾值
    if counter_data["count"] >= counter_data["target"]:
        # 使用 channel_id 作為歷史記錄的 key
        session_id = str(channel_id) # 使用 channel_id
        logger.info(f"頻道 {channel_id} 達到隨機回覆閾值 ({counter_data['count']}/{counter_data['target']}), Channel: {session_id}, 觸發者: {username}, 消息: '{log_message_content[:30]}...'")

        # 重置計數器並設定下一個目標
        counter_data["count"] = 0
        counter_data["target"] = random.randint(10, 15) # 設定下一個 10-15 之間的隨機目標
        logger.debug(f"頻道 {channel_id} 計數器已重置，下一個目標: {counter_data['target']}")

        # 獲取當前 channel 的歷史記錄
        current_history = conversation_history[session_id]

        # 調用 OpenAI API，傳入用戶名、文字內容、圖片 URL、歷史記錄和最大長度
        ai_reply, updated_history = await get_openai_reply(
            username=username,
            text_content=text_content, # 傳遞原始文本
            image_url=image_url,       # 傳遞圖片 URL
            history=current_history,
            max_history_length=MAX_HISTORY_LENGTH
        )

        if ai_reply:
            # History is updated inside get_openai_reply (using history_formatted_message)
            conversation_history[session_id] = updated_history
            logger.debug(f"Channel {session_id} 歷史記錄已通過隨機回覆更新，長度: {len(updated_history)}")

            # 檢查返回的是否是錯誤信息
            if "請求 AI 服務時出錯" in ai_reply or "連接 AI 服務時網路出錯" in ai_reply or "處理 AI 請求時發生了預料外的錯誤" in ai_reply:
                 logger.warning(f"隨機回覆 API 調用返回錯誤信息: {ai_reply}")
                 # 即使是錯誤信息，也嘗試發送給用戶
                 try:
                     await random_reply_handler.send(MessageSegment.text(ai_reply))
                 except Exception as e:
                     logger.error(f"在頻道 {channel_id} 發送隨機回覆錯誤信息失敗: {e}")
            else:
                # 發送正常的 AI 回覆
                try:
                    await random_reply_handler.send(MessageSegment.text(ai_reply))
                    logger.info(f"已在頻道 {channel_id} 發送帶有歷史記錄的隨機回覆")
                except Exception as e:
                    logger.error(f"在頻道 {channel_id} 發送隨機回覆失敗: {e}")
        else:
             # get_openai_reply 在 API Key 未配置時可能返回 None
             logger.warning(f"隨機回覆 API 調用未返回有效內容 (可能 API Key 未配置)")
             # 可以選擇在這裡發送一個通用錯誤消息，或者不發送

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
