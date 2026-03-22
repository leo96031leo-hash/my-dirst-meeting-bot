from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage
import os
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)
line_bot_api = LineBotApi(os.getenv('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('LINE_CHANNEL_SECRET'))

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    print(f"收到訊息類型: {event.source.type}") # 加這行測試看看
    if event.source.type == 'group':
        print("\n" + "🔥"*20)
        print("🎉 成功抓到群組 ID 啦：")
        print(event.source.group_id)
        print("🔥"*20 + "\n")

if __name__ == "__main__":
    app.run(port=5000)