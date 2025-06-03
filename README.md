# Hanachan

- 基於Nonebot2構建的Discord AI聊天機器人，使用OpenAI 相容 API。

## How to start

1. 執行`pipx install nb-cli`安裝nonebot2腳手架。
2. generate project using `nb create` .
3. install plugins using `nb plugin install` .

4. 新增`.env`檔案，填入OpenAI 相容 API配置及Discord bot配置。
```dotenv
# OpenAI API 配置
OPENAI_API_KEY="[在這裡填入你的key]"
OPENAI_API_BASE="[填入反代地址，結尾需加/v1]"
OPENAI_MODEL_NAME="[模型名稱]"
OPENAI_VISION_ENABLED=False

# Discord Bot 設定
DISCORD_BOTS='[{"token": "[填入Discord Bot的token]","intent": {"guilds": true, "guild_messages": true,"message_content": true, "presence": true}}]'

# 其他環境參數
TEMPERATURE=1.15
MAX_TOKENS=131072
PERSONA='persona.json'
PERSONA_POST='persona_post.json'
BLACKCHANNELS=[123456]
```
5. 在Discord Developer Portal中勾選[Presence Intent],[Server Members Intent],[Message Content Intent]。
6. 新增`persona.json`在專案根目錄中，作為預設提示詞使用。
   (可選)：新增`persona_post.json`作為Post History提示詞
```json
[
  {
    "role": "system", // role 可填入 system/assisstant/user
    "content": "You are a helpful assisstant, type 'OK' if you recieve the message."
  },
  {
    "role": "assistant",
    "content": "OK"
  }
]
```

7. run your bot using `nb run` .

## 本專案基於CC BY-NC 4.0授權開源

## Documentation

See [Docs](https://nonebot.dev/)
