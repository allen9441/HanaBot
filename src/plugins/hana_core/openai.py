import httpx
import nonebot
import json
import base64
import mimetypes
from pathlib import Path
from nonebot import logger
from typing import Dict, Any, List, Tuple, Optional, Union

# --- Load configs from .env ---
config = nonebot.get_driver().config
OPENAI_API_KEY = getattr(config, "openai_api_key", None)
OPENAI_API_BASE = getattr(config, "openai_api_base", "https://api.openai.com/v1")
OPENAI_MODEL_NAME = getattr(config, "openai_model_name", "gpt-3.5-turbo")
OPENAI_VISION_ENABLED = getattr(config, "openai_vision_enabled", False)
TEMPERATURE = getattr(config, "temperature", 0.7)
MAX_TOKENS = getattr(config, "max_tokens", 128000)

if not OPENAI_API_KEY:
    logger.warning("OpenAI API Key 未在配置中設置，對話插件可能無法運作。")
if OPENAI_VISION_ENABLED:
    logger.info("OpenAI Vision 功能已啟用")
else:
    logger.info("OpenAI Vision 功能未啟用 (若需啟用，請在.env中設置 openai_vision_enabled=True)")

# --- Persona Loading ---

_persona_data: Optional[List[Dict[str, str]]] = None
_persona_post_data: Optional[List[Dict[str, str]]] = None

def load_persona() -> Optional[List[Dict[str, str]]]:
    """
    載入 persona.json 文件 (包含多個消息的列表) 並處理可能的錯誤。
    """
    global _persona_data
    if _persona_data is not None: # 如果已載入，直接返回
        return _persona_data

    try:
        # 使用 pathlib 建立相對於目前檔案的路徑
        # 注意：路徑是相對於 openai.py 的位置
        persona_path = Path(__file__).parent.parent.parent.parent / 'persona.json'
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

# 在模塊載入時嘗試載入 Persona
_persona_data = load_persona()

def load_persona_post() -> Optional[List[Dict[str, str]]]:
    """
    首次調用時載入 persona_post.json 文件並緩存結果。
    後續調用直接返回緩存的結果。
    """
    global _persona_post_data
    if _persona_post_data is not None: # 如果已載入，直接返回緩存結果
        return _persona_post_data

    persona_post_path = Path(__file__).parent.parent.parent.parent / 'persona_post.json'
    try:
        if persona_post_path.is_file():
            with open(persona_post_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list) and all(
                    isinstance(item, dict) and "role" in item and "content" in item
                    for item in data
                ):
                    _persona_post_data = data
                    logger.info(f"成功載入附加 Persona (Post): {persona_post_path} ({len(data)} 條消息)")
                else:
                    logger.warning(f"附加 Persona (Post) 文件格式不符預期: {persona_post_path}")
                    _persona_post_data = None # 格式錯誤視為無效
        else:
             # 文件不存在是正常情況，設為 None
             _persona_post_data = None
             # logger.debug(f"附加 Persona (Post) 文件未找到 (可選): {persona_post_path}")

    except json.JSONDecodeError:
        logger.exception(f"解析附加 Persona (Post) 文件時發生 JSON 錯誤: {persona_post_path}")
        _persona_post_data = None
    except Exception:
        logger.exception(f"載入附加 Persona (Post) 文件時發生未知錯誤: {persona_post_path}")
        _persona_post_data = None

    return _persona_post_data

# 在模塊載入時嘗試載入並緩存 Persona Post
load_persona_post() # 調用一次以觸發載入和緩存


# --- OpenAI API Call Logic ---
async def get_openai_reply(
    username: str,
    text_content: str,
    image_url: Optional[str],
    history: List[Dict[str, str]],
    max_history_length: int
) -> Tuple[Optional[str], List[Dict[str, str]]]:
    """
    調用 OpenAI 相容 API (根據配置決定是否啟用 Vision) 並返回回覆文本和更新後的歷史記錄。
    失敗則返回 (None, 原始歷史記錄)。
    """
    if not OPENAI_API_KEY:
        logger.warning("嘗試調用 OpenAI 相容 API，但 API Key 未配置")
        return None, history

    # --- 構建當前用戶消息 ---
    current_user_content: Union[str, List[Dict[str, Any]]]
    history_user_message: str # 用於存儲歷史的消息格式

    # 檢查 Vision 是否啟用以及是否有圖片 URL
    if image_url and OPENAI_VISION_ENABLED:
        logger.debug(f"Vision 已啟用，嘗試下載並編碼圖片: {image_url}")
        base64_image_data = None
        mime_type = "image/png" # Default MIME type

        try:
            async with httpx.AsyncClient() as client:
                # Follow redirects, set a reasonable timeout
                img_response = await client.get(image_url, follow_redirects=True, timeout=30.0)
                img_response.raise_for_status() # Raise exception for bad status codes
                image_bytes = await img_response.aread()

                # Try to get MIME type from response header first
                content_type_header = img_response.headers.get("content-type")
                if content_type_header:
                    mime_type = content_type_header.split(";")[0] # Get the main part like 'image/jpeg'
                else:
                    # Fallback: Guess MIME type from URL
                    guessed_type, _ = mimetypes.guess_type(image_url)
                    if guessed_type:
                        mime_type = guessed_type
                    # If still unknown, keep the default

                # Encode image to Base64
                encoded_bytes = base64.b64encode(image_bytes)
                base64_image_data = encoded_bytes.decode('utf-8')
                logger.debug(f"圖片下載並編碼成功 (Type: {mime_type}, Base64 長度: {len(base64_image_data)})")

        except httpx.HTTPStatusError as e:
            logger.error(f"下載圖片時發生 HTTP 狀態錯誤: {e.response.status_code} - URL: {image_url}")
        except httpx.RequestError as e:
            logger.error(f"下載圖片時發生網路錯誤: {e} - URL: {image_url}")
        except Exception as e:
            logger.exception(f"下載或編碼圖片時發生未知錯誤: {e} - URL: {image_url}")

        # --- 組合 Base64 訊息，傳回 user content ---
        if base64_image_data:
            data_uri = f"data:{mime_type};base64,{base64_image_data}"
            current_user_content = [
                {"type": "text", "text": f"{username}: {text_content}" if text_content else f"{username} 發送了一張圖片:"},
                {"type": "image_url", "image_url": {"url": data_uri}}
            ]
            history_user_message = f"{username}: {text_content} [image]" if text_content else f"{username}: [image]"
            logger.debug(f"成功構建 Base64 Vision 請求內容")
        else:
            # Fallback to text-only if image download/encoding failed
            logger.warning("圖片處理失敗，將僅發送文字內容。")
            current_user_content = f"{username}: {text_content}" if text_content else f"{username}: [圖片處理失敗]"
            history_user_message = current_user_content # History reflects the failure

    else:
        # Standard text format (Vision disabled or no image URL)
        if image_url and not OPENAI_VISION_ENABLED:
             logger.debug("Vision 未啟用，已忽略圖片。")
        current_user_content = f"{username}: {text_content}" if text_content else f"{username}: [image]" # Keep [image] tag if only image and vision disabled
        history_user_message = current_user_content

    # --- 組合 Persona, 歷史記錄和當前用戶消息 ---
    messages = []

    # 1. 添加 Persona Data (前置)
    persona_list = load_persona()
    if persona_list:
        messages.extend(persona_list)

    # 2. 添加 History
    messages.extend(history)

    # 3. 添加 Persona Post Data
    persona_post_list = load_persona_post()
    if persona_post_list:
        messages.extend(persona_post_list)
        logger.debug(f"已附加 {len(persona_post_list)} 條 Persona Post 消息")

    # 4. 添加當前用戶消息
    messages.append({"role": "user", "content": current_user_content})

    # --- 設定 API URL, Headers, Payload ---
    api_url = f"{OPENAI_API_BASE.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    # 確保 messages 列表不為空
    if not messages:
        logger.error("嘗試調用API，但消息列表為空")
        return None, history

    payload = {
        "model": OPENAI_MODEL_NAME,
        "messages": messages,
        "temperature": TEMPERATURE,
        "max_completion_tokens": MAX_TOKENS
    }

    # logger.debug(payload)
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(api_url, headers=headers, json=payload, timeout=60.0)
            response.raise_for_status()
        api_result = response.json()
        ai_reply = api_result["choices"][0]["message"]["content"].strip()
        if ai_reply:
            log_user_input = history_user_message # 使用包含圖片標記的歷史消息進行記錄
            logger.info(f"API 調用成功，用戶: {username}, 輸入: '{log_user_input[:30]}...', 回覆: '{ai_reply[:30]}...'")
            # 更新歷史記錄 (使用 history_user_message)
            updated_history = history + [
                {"role": "user", "content": history_user_message}, # 存儲簡化版用戶消息
                {"role": "assistant", "content": ai_reply}
            ]
            # 限制歷史記錄長度
            if len(updated_history) > max_history_length:
                updated_history = updated_history[-max_history_length:]
            return ai_reply, updated_history
        else:
            logger.warning("API 返回了空的回覆")
            return None, history # 返回 None 和未修改的歷史
    except httpx.HTTPStatusError as e:
        logger.error(f"請求 API 時發生狀態錯誤: {e.response.status_code} - {e.response.text}")
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
