import urllib.parse
import re
import os
import json
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.adapters.discord import Bot, MessageEvent
from nonebot.adapters.discord.exception import DiscordAdapterException, NetworkError
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple


# --- 正則表達式 ---
# 匹配 "timeout(數字, 原因);" 格式，並捕獲數字和原因部分
# 允許逗號前後有空格，原因部分可以為空
TIMEOUT_PATTERN = re.compile(r"timeout\((\d+)\s*,\s*(.*?)\s*\);")

# 匹配 "memory(內容);" 格式，並捕獲內容部分，允許內容跨行
MEMORY_PATTERN = re.compile(r"memory\((.*?)\);", re.DOTALL)



# --- 輔助函數：檢查並處理 Timeout 和 Memory 回覆 ---
async def check_reply(
    bot: Bot,
    event: MessageEvent,
    ai_reply: str,
    matcher: Matcher,
    timeout_on: bool
) -> Tuple[bool, Optional[str]]:
    """
    檢查 AI 回覆是否包含指令。
    如果包含，則執行相應操作，並從回覆中移除指令。
    返回一個包含處理狀態（是否處理了任一指令）和清理後回覆的元組。

    Args:
        bot: DiscordBot 實例。
        event: 觸發的 MessageEvent。
        ai_reply: AI 生成的回覆內容。
        matcher: 當前處理事件的 Matcher 實例。
        timeout_on: 是否執行 Timeout 指令。

    Returns:
        Tuple[bool, Optional[str]]: (是否處理了任一指令, 清理後的回覆或 None)
    """
    processed_timeout = False
    processed_memory = False
    cleaned_reply = ai_reply # 從原始回覆開始清理

    # 1. 檢查並處理 Timeout 指令
    match_timeout = TIMEOUT_PATTERN.search(ai_reply) # 在原始回覆中搜索
    if match_timeout and timeout_on:
        try:
            duration_minutes = int(match_timeout.group(1))
            reason_from_ai = match_timeout.group(2).strip()
            reason_for_timeout = reason_from_ai if reason_from_ai else "AI 指令觸發"

            target_user_id = event.get_user_id()
            guild_id = getattr(event, 'guild_id', None)

            if not guild_id:
                logger.error(f"無法從事件 {type(event)} 中獲取 guild_id，無法執行 AI 觸發的 timeout。")
                # 即使失敗，也標記為已處理，以便清理指令
                processed_timeout = True
            else:
                logger.info(f"檢測到 AI 回覆中的 timeout 指令: timeout({duration_minutes}, '{reason_from_ai}');，目標使用者: {target_user_id}，伺服器: {guild_id}")
                error_message = await execute_timeout(
                    bot=bot,
                    guild_id=guild_id,
                    user_id=int(target_user_id),
                    duration_minutes=duration_minutes,
                    reason=reason_for_timeout,
                    operator_info="Hanachan AI"
                )
                if error_message:
                    logger.error(f"AI 觸發的 timeout 執行失敗: {error_message}")
                else:
                    logger.info(f"成功執行 AI 觸發的 timeout，使用者: {target_user_id}，時長: {duration_minutes} 分鐘，原因: '{reason_for_timeout}'。")
                processed_timeout = True # 無論成功失敗，都標記為已處理

        except ValueError:
            logger.error(f"從 AI 回覆中解析 timeout 時長失敗: '{match_timeout.group(1)}' 或原因格式錯誤")
            processed_timeout = True # 解析失敗也標記處理，以便清理
        except Exception as e:
            logger.exception(f"處理 AI 觸發的 timeout 時發生未知錯誤")
            processed_timeout = True # 未知錯誤也標記處理，以便清理

    # 2. 檢查並處理 Memory 指令
    match_memory = MEMORY_PATTERN.search(ai_reply) # 在原始回覆中搜索
    while match_memory:
        try:
            memory_content = match_memory.group(1).strip()
            logger.info(f"檢測到 AI 回覆中的 memory 指令，內容: '{memory_content[:10]}...'")
            success = await memory_command(event, memory_content)
            if success:
                logger.info("Memory 指令處理成功。")
            else:
                logger.error("Memory 指令處理失敗。")
            cleaned_reply = cleaned_reply.replace(match_memory.group(0), "", 1).strip()
            logger.debug(f"清理 memory 指令後的回覆: '{cleaned_reply}'")
            match_memory = MEMORY_PATTERN.search(cleaned_reply)
            processed_memory = True
        except Exception as e:
            logger.exception(f"處理 AI 觸發的 memory 指令時發生未知錯誤:{e}")
            cleaned_reply = cleaned_reply.replace(match_memory.group(0), "", 1).strip()
            logger.debug(f"清理 memory 指令後的回覆: '{cleaned_reply}'")
            processed_memory

    # 3. 清理回覆
    # 注意：清理時要基於上一步清理後的結果，而不是原始 ai_reply
    if processed_timeout:
        # 使用 match_timeout.group(0) 獲取完整的匹配字串進行替換
        # 只替換第一個匹配項
        cleaned_reply = cleaned_reply.replace(match_timeout.group(0), "", 1).strip()
        logger.debug(f"清理 timeout 指令後的回覆: '{cleaned_reply}'")

    # 4. 返回結果
    processed_any = processed_timeout or processed_memory
    if processed_any:
        logger.debug(f"最終清理後的回覆: '{cleaned_reply}'")
        # 只有在處理了指令後才返回清理後的回覆
        return True, cleaned_reply if cleaned_reply else None # 如果清理後為空，返回 None
    else:
        # logger.debug(f"沒有找到 timeout 或 memory 指令")
        return False, None # 沒有處理任何指令，返回 False 和 None

# --- Timeout 核心邏輯 ---

async def execute_timeout(
    bot: Bot,
    guild_id: int,
    user_id: int,
    duration_minutes: int,
    reason: str = "無原因",
    operator_info: str = "系統自動觸發"
) -> Optional[str]:
    """
    執行 Discord Timeout 操作的核心函數。

    Args:
        bot: DiscordBot 實例。
        guild_id: 伺服器 ID。
        user_id: 目標使用者 ID。
        duration_minutes: 禁言持續時間（分鐘）。
        reason: 禁言原因。
        operator_info: 執行操作的使用者或系統資訊。

    Returns:
        如果成功，返回 None。如果失敗，返回錯誤訊息字串。
    """
    # 驗證持續時間
    if duration_minutes <= 0:
        logger.warning(f"無效的禁言持續時間: {duration_minutes} 分鐘")
        return "錯誤：持續時間必須是正整數（分鐘）。"
    # Discord timeout 上限是 28 天
    max_duration_minutes = 28 * 24 * 60 - 1
    if duration_minutes > max_duration_minutes:
        logger.warning(f"禁言持續時間 {duration_minutes} 分鐘超過上限 {max_duration_minutes} 分鐘")
        return f"錯誤：持續時間不能超過 {(max_duration_minutes + 1) // (24*60)} 天 ({max_duration_minutes} 分鐘)。"

    # 計算禁言結束時間 (UTC)
    now_utc = datetime.now(timezone.utc)
    timeout_until = now_utc + timedelta(minutes=duration_minutes)
    # 格式化為 Discord API 要求的 ISO 8601 格式
    communication_disabled_until = timeout_until.isoformat()

    try:
        logger.info(f"嘗試在伺服器 {guild_id} 中將使用者 {user_id} 禁言至 {communication_disabled_until}，原因：{reason}，操作者：{operator_info}")

        # 調用 Discord API 修改伺服器成員
        # 對 Reason 進行 URL 編碼
        encoded_reason = urllib.parse.quote(f"由 {operator_info} 執行禁言操作: {reason}")

        await bot.modify_guild_member(
            guild_id=guild_id,
            user_id=user_id,
            communication_disabled_until=communication_disabled_until,
            reason=encoded_reason
        )

        logger.info(f"成功將使用者 {user_id} 禁言。")
        return None # 表示成功

    except (DiscordAdapterException, NetworkError) as e:
        error_code = getattr(e, 'code', None)
        error_msg = getattr(e, 'message', str(e))
        logger.error(f"禁言使用者 {user_id} 時發生 API 錯誤: {error_msg} (Code: {error_code or 'N/A'})")
        error_message = f"禁言使用者 <@{user_id}> 失敗。\n錯誤：{error_msg}"
        # 檢查錯誤碼（如果存在且是數字）
        if isinstance(error_code, int):
            if error_code == 50013: # Missing Permissions
                error_message += "\n請檢查 Bot 是否擁有 'Moderate Members' (管理成員) 權限。"
            elif error_code == 50001: # Missing Access
                error_message += "\n請檢查 Bot 是否能訪問該伺服器或頻道。"
            elif error_code == 10007: # Unknown Member
                error_message += "\n找不到指定的使用者。"
        elif isinstance(e, NetworkError):
             error_message += "\n(可能是網路連線問題或 Discord API 暫時無法訪問)"
        return error_message
    except Exception as e:
        logger.exception(f"禁言使用者 {user_id} 時發生未知錯誤")
        return f"禁言使用者 <@{user_id}> 時發生未預期的錯誤: {e}。請查看日誌。"
    
    
# --- 處理 Memory 指令 ---
async def memory_command(event: MessageEvent, memory_content: str) -> bool:
    """
    處理 memory 指令，將內容寫入對應頻道的 JSON 檔案。

    Args:
        event: 觸發的 MessageEvent。
        memory_content: 從指令中提取的記憶內容。

    Returns:
        bool: 是否成功處理。
    """
    channel_id = getattr(event, 'channel_id', None)
    guild_id = getattr(event, 'guild_id', None) # 也記錄 guild_id 以供參考

    if not channel_id:
        logger.error(f"無法從事件 {type(event)} 中獲取 channel_id，無法儲存記憶。")
        return False

    try:
        memories_dir = "memories"
        file_path = os.path.join(memories_dir, f"{channel_id}.json")

        # 確保 memories 資料夾存在
        os.makedirs(memories_dir, exist_ok=True)

        # 讀取現有數據或初始化
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, list): # 確保是列表
                logger.warning(f"記憶檔案 {file_path} 格式不正確（非列表），將重新初始化。")
                data = []
        except (FileNotFoundError, json.JSONDecodeError):
            data = [] # 如果檔案不存在或格式錯誤，則初始化為空列表

        # 添加新記憶 (可以考慮添加時間戳或其他元數據)
        timestamp = datetime.now(timezone.utc).isoformat()
        user_id = event.get_user_id()
        data.append({
            "timestamp": timestamp,
            "user_id": user_id,
            "guild_id": guild_id, # 記錄伺服器 ID
            "content": memory_content
        })

        # 寫回 JSON 檔案
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

        logger.info(f"成功將記憶 '{memory_content[:50]}...' 儲存到頻道 {channel_id} 的檔案 {file_path} 中。")
        return True

    except OSError as e:
        logger.error(f"處理記憶檔案 {file_path} 時發生 OS 錯誤: {e}")
        return False
    except Exception as e:
        logger.exception(f"儲存記憶到頻道 {channel_id} 時發生未知錯誤")
        return False
