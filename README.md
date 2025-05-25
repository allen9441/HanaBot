# Hanachan

- 基於Nonebot2構建的Discord AI聊天機器人，使用OpenAI 相容 API。

# 本專案基於CC BY-NC 4.0授權開源，需遵守以下規則
- 您必須給出適當的署名，提供指向本協議的鏈接，並指明是否（對原作）作了修改。您可以以任何合理方式進行，但不得以任何方式暗示授權人認可您或您的使用。
- 您不得將本作品用於商業目的，包括但不限於任何形式的商業倒賣、SaaS、API 付費介面、二次銷售、打包出售、收費分發或其他直接或間接獲利行為。

## How to start

1. 執行`pipx install nb-cli`安裝nonebot2腳手架。
1. generate project using `nb create` .
2. install plugins using `nb plugin install` .

3. 新增`.env`檔案，填入OpenAI 相容 API配置及Discord bot配置。
```json
# OpenAI API 配置
OPENAI_API_KEY="[在這裡填入你的key]"
OPENAI_API_BASE="[填入反代地址，結尾需加/v1]"
OPENAI_MODEL_NAME="[模型名稱]"

# Discord Bot 設定
DISCORD_BOTS='[{"token": "[填入Discord Bot的token]",
                "intent": {"guilds": true, "guild_messages": true,"message_content": true, "presence": true}}]'
```
4. 在Discord Developer Portal中勾選[Presence Intent],[Server Members Intent],[Message Content Intent]。
5. run your bot using `nb run` .

## Documentation

See [Docs](https://nonebot.dev/)
