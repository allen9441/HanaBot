import httpx
import nonebot
import random # 導入 random 模塊
import json
from pathlib import Path # 導入 Path
from collections import defaultdict # 方便初始化字典
from nonebot import on_message, logger
from nonebot.rule import to_me
from nonebot.adapters.discord import Bot, MessageEvent, Message, MessageSegment # 確保導入 Message
from nonebot.params import EventMessage
from nonebot.plugin import PluginMetadata
from typing import Dict, Any, List, Tuple, Optional # 增加 List, Tuple, Optional

#  Plugin info
__plugin_meta__ = PluginMetadata(
    name="Chatting with Hanachan",
    description="@Hanachan to start a chat",
    usage="@Hanachan + [message]",
    type="application",
    supported_adapters={"nonebot.adapters.discord"},
)

# --- Load API from .env ---
config = nonebot.get_driver().config
OPENAI_API_KEY = getattr(config, "openai_api_key", None)
OPENAI_API_BASE = getattr(config, "openai_api_base", "https://api.openai.com/v1")
OPENAI_MODEL_NAME = getattr(config, "openai_model_name", "gpt-3.5-turbo")

if not OPENAI_API_KEY:
    logger.warning("OpenAI API Key 未在配置中設置，對話插件無法運作。")

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

# --- Persona Loading ---

_persona_data: Optional[List[Dict[str, str]]] = None # 使用底線表示內部變數

def load_persona() -> Optional[List[Dict[str, str]]]:
    """
    載入 persona.json 文件 (包含多個消息的列表) 並處理可能的錯誤。
    """
    global _persona_data
    if _persona_data is not None: # 如果已載入，直接返回
        return _persona_data

    try:
        # 使用 pathlib 建立相對於目前檔案的路徑
        persona_path = Path(__file__).parent.parent.parent / 'persona.json'
        if not persona_path.is_file():
            logger.warning(f"Persona 文件未找到: {persona_path}")
            return None

        with open(persona_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # 驗證 persona.json 是否為包含字典的列表，且每個字典都有 'role' 和 'content'
            if isinstance(data, list) and all(
                isinstance(item, dict) and "role" in item and "content" in item
                for item in data
            ):
                 _persona_data = data
                 logger.info(f"成功載入 Persona 列表: {persona_path} ({len(data)} 條消息)")
                 return _persona_data
            else:
                logger.warning(f"Persona 文件格式不符預期 (需要是包含 'role' 和 'content' 字典的列表): {persona_path}")
                return None
    except json.JSONDecodeError:
        logger.exception(f"解析 Persona 文件時發生 JSON 錯誤: {persona_path}")
        return None
    except Exception:
        logger.exception(f"載入 Persona 文件時發生未知錯誤: {persona_path}")
        return None

# 在插件載入時嘗試載入 Persona
_persona_data = load_persona()


# --- 提取 OpenAI API 調用邏輯 ---
async def get_openai_reply(
    formatted_user_message: str, # Renamed parameter for clarity
    history: List[Dict[str, str]]
) -> Tuple[Optional[str], List[Dict[str, str]]]:
    """
    調用 OpenAI API (使用已格式化的用戶消息) 並返回回覆文本和更新後的歷史記錄。
    失敗則返回 (None, 原始歷史記錄)。
    """
    if not OPENAI_API_KEY:
        logger.warning("嘗試調用，但 API Key 未配置")
        return None, history # 返回 None 和未修改的歷史

    # 組合 Persona 列表, 歷史記錄和當前用戶消息
    messages = []
    persona_list = load_persona() # 獲取已載入的 persona 列表
    if persona_list:
        messages.extend(persona_list) # 添加 persona 列表中的所有消息

    messages.extend(history) # 添加歷史記錄
    # 添加已格式化的用戶消息 (包含 username)
    messages.append({"role": "user", "content": formatted_user_message})

    # --- 設定 API URL, Headers, Payload ---
    api_url = f"{OPENAI_API_BASE.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    # 確保 messages 列表不為空
    if not messages:
        logger.error("嘗試調用 API，但消息列表為空")
        return None, history

    payload = {
        "model": OPENAI_MODEL_NAME,
        "messages": messages,
        "temperature": 0.7,
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(api_url, headers=headers, json=payload, timeout=60.0)
            response.raise_for_status()
        api_result = response.json()
        ai_reply = api_result["choices"][0]["message"]["content"].strip()
        if ai_reply:
            # Log using the formatted message
            logger.info(f"OpenAI API 調用成功，用戶消息: '{formatted_user_message[:30]}...', 回覆: '{ai_reply[:30]}...'")
            # 更新歷史記錄 (使用格式化後的用戶消息)
            updated_history = history + [
                {"role": "user", "content": formatted_user_message},
                {"role": "assistant", "content": ai_reply}
            ]
            # 限制歷史記錄長度
            if len(updated_history) > MAX_HISTORY_LENGTH:
                updated_history = updated_history[-MAX_HISTORY_LENGTH:] # 只保留最後 N 條
            return ai_reply, updated_history
        else:
            logger.warning("OpenAI API 返回了空的回覆")
            return None, history # 返回 None 和未修改的歷史
    except httpx.HTTPStatusError as e:
        logger.error(f"請求 OpenAI API 時發生狀態錯誤: {e.response.status_code} - {e.response.text}")
        error_msg = f"請求 AI 服務時出錯 (狀態碼: {e.response.status_code})。"
        # 注意：這裡返回錯誤消息作為 AI 回覆，但不更新歷史記錄
        return error_msg, history
    except httpx.RequestError as e:
        logger.error(f"請求 OpenAI API 時發生網路錯誤: {e}")
        error_msg = f"連接 AI 服務時網路出錯。"
        return error_msg, history
    except Exception as e:
        logger.exception("調用 OpenAI API 時發生未知錯誤")
        error_msg = f"處理 AI 請求時發生了預料外的錯誤。"
        return error_msg, history

# --- 處理 @ 消息的響應器 ---
# 保持原來的優先級和 block=True
at_reply_handler = on_message(rule=to_me(), priority=10, block=True)

@at_reply_handler.handle()
async def handle_at_reply(bot: Bot, event: MessageEvent):
    raw_user_message = event.get_plaintext().strip() # Get raw message
    if not raw_user_message:
        return

    # Get username (Guild Nickname > Username)
    # 檢查 event.member 是否存在及其 nick 屬性，否則回退到 event.author.username
    username = event.author.global_name
    # Format message for history and API
    formatted_user_message = f"{username}: {raw_user_message}"

    # 使用 channel_id 作為歷史記錄的 key，確保每個頻道有獨立歷史
    session_id = str(event.channel_id)
    # Log raw message for clarity, include username
    logger.info(f"收到 @ 消息, Channel: {session_id}, 用戶: {username} ({event.get_user_id()}), 內容: {raw_user_message}")

    # 獲取當前 channel 的歷史記錄
    current_history = conversation_history[session_id]

    # 調用 API，傳入格式化後的用戶消息和歷史記錄
    ai_reply, updated_history = await get_openai_reply(formatted_user_message, current_history)

    if ai_reply:
        # History is updated inside get_openai_reply using the formatted message
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
    #    注意：Discord適配器中，私訊可能沒有 channel_id，需要處理
    #    對於伺服器器頻道，event.channel_id 應該是有的
    #    對於私訊，可以使用 event.get_session_id() 作為唯一標識符
    #    這裡假設主要在伺服器頻道使用
    if not hasattr(event, 'channel_id') or not event.channel_id:
        # logger.debug("消息來自私訊或無法獲取 channel_id，跳過隨機回覆計數")
        return # 或者為私聊實現單獨的計數邏輯

    channel_id = event.channel_id
    raw_user_message = event.get_plaintext().strip() # Get raw message

    # 如果消息為空（例如只有圖片或表情），則不計數也不觸發
    # TODO : 添加 Vision Support，回傳圖片網址
    if not raw_user_message:
        return

    # Get username (Guild Nickname > Username)
    # 檢查 event.member 是否存在及其 nick 屬性，否則回退到 event.author.username
    username = event.member.nick if event.member and event.member.nick else event.author.username
    # Format message for history and API
    formatted_user_message = f"{username}: {raw_user_message}"

    # 4. 更新計數器
    counter_data = channel_counters[channel_id]
    counter_data["count"] += 1
    logger.debug(f"頻道 {channel_id} 消息計數: {counter_data['count']}/{counter_data['target']}")

    # 5. 檢查是否達到觸發閾值
    if counter_data["count"] >= counter_data["target"]:
        # 使用 channel_id 作為歷史記錄的 key
        session_id = str(event.channel_id)
        # Log raw message and username
        logger.info(f"頻道 {channel_id} 達到隨機回覆閾值 ({counter_data['count']}/{counter_data['target']}), Channel: {session_id}, 觸發者: {username}, 消息: '{raw_user_message[:30]}...'")

        # 獲取當前 channel 的歷史記錄
        current_history = conversation_history[session_id]

        # 調用 OpenAI API，傳入格式化後的觸發消息和歷史記錄
        ai_reply, updated_history = await get_openai_reply(formatted_user_message, current_history)

        if ai_reply:
            # History is updated inside get_openai_reply using the formatted message
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

        # 6. 無論 API 調用是否成功，都重置計數器並設定下一個目標
        counter_data["count"] = 0
        counter_data["target"] = random.randint(10, 15) # 設定下一個 10-15 之間的隨機目標
        logger.debug(f"頻道 {channel_id} 計數器已重置，下一個目標: {counter_data['target']}")

    else:
        # 如果未達到閾值，仍然將格式化後的用戶消息記錄到歷史中
        session_id = str(event.channel_id) # 同樣使用 channel_id 作為 key
        current_history = conversation_history[session_id]
        # 只添加格式化後的用戶消息，不添加 AI 回覆
        updated_history = current_history + [{"role": "user", "content": formatted_user_message}]
        # 限制歷史記錄長度
        if len(updated_history) > MAX_HISTORY_LENGTH:
            updated_history = updated_history[-MAX_HISTORY_LENGTH:]
        conversation_history[session_id] = updated_history
        # Log raw message and username
        logger.debug(f"Channel {session_id}: 未達閾值，已記錄用戶消息 ({username}): '{raw_user_message[:20]}...'，歷史長度: {len(updated_history)}")
        # defaultdict 會自動保持計數器更新，無需額外操作
