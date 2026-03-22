import os
import datetime
import gspread
from dotenv import load_dotenv
import google.generativeai as genai
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    PushMessageRequest,
    TextMessage
)

def main():
    # 載入環境變數
    load_dotenv()
    
    LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
    LINE_GROUP_ID = os.getenv('LINE_GROUP_ID')
    GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
    GOOGLE_SHEET_URL = os.getenv('GOOGLE_SHEET_URL')
    GOOGLE_CREDENTIALS_FILE = os.getenv('GOOGLE_CREDENTIALS_FILE', 'service_account.json')

    # 1. 讀取 Google Sheets (使用 gspread 套件)
    try:
        gc = gspread.service_account(filename=GOOGLE_CREDENTIALS_FILE)
        sh = gc.open_by_url(GOOGLE_SHEET_URL)
        worksheet = sh.sheet1
    except Exception as e:
        print(f"讀取 Google Sheets 失敗: {e}")
        return
        
    # 取得所有資料 (假設第一列是標題)
    records = worksheet.get_all_records()
    
    pending_items = []
    update_rows = []
    
    for i, row in enumerate(records):
        # 尋找 "是否已發送" 為空值的項目
        if not row.get('是否已發送'):
            pending_items.append(row)
            # 紀錄在試算表中的實際列號 (i 從 0 開始，+2 因為有標題列且索引從 1 開始)
            update_rows.append(i + 2)
            
    if not pending_items:
        print("沒有新的會議事項需要通知。")
        return
        
    # 2. 整理文字給 Gemini
    prompt_text = (
        "請把以下這週收集到的會議事項，整理成一篇專業、帶有條列式、語氣親切的 LINE 開會通知草稿。"
        "開會時間是明天（週四）。\n\n會議事項如下：\n"
    )
    for item in pending_items:
        prompt_text += f"- 時間：{item.get('時間', '未定')} / 提議者：{item.get('提議者', '未定')} / 議題：{item.get('開會議題', '未定')}\n"
        
    # 呼叫 Gemini AI
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content(prompt_text)
        draft_message = response.text
    except Exception as e:
        print(f"呼叫 Gemini API 失敗: {e}")
        return
    
    # 3. 發送至 LINE 群組
    try:
        configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
        with ApiClient(configuration) as api_client:
            line_api = MessagingApi(api_client)
            line_api.push_message(
                PushMessageRequest(
                    to=LINE_GROUP_ID,
                    messages=[TextMessage(text=draft_message)]
                )
            )
    except Exception as e:
        print(f"發送 LINE 訊息失敗: {e}")
        return
        
    # 4. 更新 Google Sheets (寫入今天的日期)
    try:
        today_str = datetime.datetime.now().strftime('%Y-%m-%d')
        # 假設 "是否已發送" 固定在第 4 欄 (D 欄)
        cells_to_update = [gspread.Cell(row=r, col=4, value=today_str) for r in update_rows]
        worksheet.update_cells(cells_to_update)
        print(f"成功發送通知並更新了 {len(update_rows)} 筆資料。")
    except Exception as e:
        print(f"更新 Google Sheets 失敗: {e}")

if __name__ == "__main__":
    main()
