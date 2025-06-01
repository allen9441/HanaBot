import urllib.parse
import re

from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.adapters.discord import Bot, MessageEvent
from nonebot.adapters.discord.exception import DiscordAdapterException, NetworkError
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

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


# --- 正則表達式 ---
# 匹配 "timeout(數字, 原因);" 格式，並捕獲數字和原因部分
# 允許逗號前後有空格，原因部分可以為空
TIMEOUT_PATTERN = re.compile(r"timeout\((\d+)\s*,\s*(.*?)\s*\);")

# --- 輔助函數：檢查並處理 Timeout 回覆 ---
async def check_reply(
    bot: Bot,
    event: MessageEvent,
    ai_reply: str,
    matcher: Matcher
) -> Tuple[bool, Optional[str]]:
    """
    檢查 AI 回覆是否包含 timeout 指令，如果是則執行，發送確認消息，
    並返回一個包含處理狀態和清理後回覆的元組。

    Args:
        bot: DiscordBot 實例。
        event: 觸發的 MessageEvent。
        ai_reply: AI 生成的回覆內容。
        matcher: 當前處理事件的 Matcher 實例。

    Returns:
        Tuple[bool, Optional[str]]: (是否處理了 timeout, 清理後的回覆或 None)
    """
    match = TIMEOUT_PATTERN.search(ai_reply)
    if match:
        try:
            duration_minutes = int(match.group(1))
            reason_from_ai = match.group(2).strip() # 捕獲原因並去除前後空格
            reason_for_timeout = reason_from_ai if reason_from_ai else "AI 指令觸發" # 如果原因為空，使用預設值

            target_user_id = event.get_user_id() # 獲取觸發消息的使用者 ID
            guild_id = getattr(event, 'guild_id', None) # 嘗試獲取 guild_id

            if not guild_id:
                logger.error(f"無法從事件 {type(event)} 中獲取 guild_id，無法執行 AI 觸發的 timeout。")
                # await matcher.send("錯誤：無法獲取伺服器 ID，無法執行 AI 觸發的禁言。") # 使用 matcher.send
                return True # 雖然失敗，但也算處理了，阻止發送原消息

            logger.info(f"檢測到 AI 回覆中的 timeout 指令: timeout({duration_minutes}, '{reason_from_ai}');，目標使用者: {target_user_id}，伺服器: {guild_id}")

            # 執行 timeout
            error_message = await execute_timeout(
                bot=bot,
                guild_id=guild_id,
                user_id=int(target_user_id),
                duration_minutes=duration_minutes,
                reason=reason_for_timeout,
                operator_info="Hanachan AI"
            )

            # 發送結果通知
            if error_message:
                logger.error(f"AI 觸發的 timeout 執行失敗: {error_message}")
                # await matcher.send(f"嘗試執行 AI 觸發的禁言時出錯：\n{error_message}")
            else:
                logger.info(f"成功執行 AI 觸發的 timeout，使用者: {target_user_id}，時長: {duration_minutes} 分鐘，原因: '{reason_for_timeout}'。")
                # await matcher.send(f"已根據 AI 指令將使用者 <@{target_user_id}> 禁言 {duration_minutes} 分鐘，原因：{reason_for_timeout}。")

            # --- 清理回覆並返回 ---
            # 無論 timeout 是否成功執行，都清理回覆中的指令
            cleaned_reply = TIMEOUT_PATTERN.sub("", ai_reply).strip()
            logger.debug(f"清理後的 AI 回覆: '{cleaned_reply}'")
            return True, cleaned_reply # 返回處理成功狀態和清理後的回覆

        except ValueError:
            logger.error(f"從 AI 回覆中解析 timeout 時長失敗: '{match.group(1)}' 或原因格式錯誤")
            # await matcher.send("錯誤：AI 回覆中的禁言指令格式不正確。")
            # 解析失敗，但仍嘗試清理並返回，讓主流程決定是否發送
            cleaned_reply = TIMEOUT_PATTERN.sub("", ai_reply).strip()
            return True, cleaned_reply # 返回處理標記和清理（可能不完整）的回覆
        except Exception as e:
            logger.exception(f"處理 AI 觸發的 timeout 時發生未知錯誤")
            # await matcher.send(f"處理 AI 觸發的禁言時發生未預期錯誤。\n{e}")
            # 同上，嘗試清理並返回
            cleaned_reply = TIMEOUT_PATTERN.sub("", ai_reply).strip()
            return True, cleaned_reply # 返回處理標記和清理（可能不完整）的回覆

    logger.debug(f"沒有找到 timeout 指令")
    return False, None # 沒有找到 timeout 指令，返回 False 和 None
