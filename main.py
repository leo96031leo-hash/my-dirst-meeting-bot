import os
import datetime
import socket

# 針對 LINE API 強制使用 IPv4，不影響 Google (解決剛剛的 oauth2 timeout 災情)
_orig_getaddrinfo = socket.getaddrinfo

def patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    if host == 'api.line.me':
        family = socket.AF_INET
    return _orig_getaddrinfo(host, port, family, type, proto, flags)

socket.getaddrinfo = patched_getaddrinfo

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
    LINE_GROUP_ID_STR = os.getenv('LINE_GROUP_ID', '')
    # 支援單一或多個群組 ID（用逗號分隔），並將字串轉換成陣列
    line_group_ids = [gid.strip() for gid in LINE_GROUP_ID_STR.split(',') if gid.strip()]
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
    
    # 動態尋找「是否已發送」所在的欄位索引，避免新增欄位後寫死覆蓋錯誤
    headers = worksheet.row_values(1)
    status_col_index = headers.index('是否已發送') + 1 if '是否已發送' in headers else 4
    
    # === 欄位名稱設定區 (對應你提供的 Excel) ===
    COL_DATE = '日期'
    COL_TIME = '時間'
    COL_LOCATION = '地點'
    COL_M1_CONTENT = '碩一內容'
    COL_M2_CONTENT = '碩二內容'
    COL_MEETING = '是否 meeting'
    COL_STATUS = '是否已發送'
    # ============================================

    pending_items = []
    cancel_items = []
    update_rows = []
    tomorrow_datetime = datetime.datetime.now() + datetime.timedelta(days=1)
    
    for i, row in enumerate(records):
        # 把欄位內容轉成字串並去除空白
        date_str = str(row.get(COL_DATE, '')).strip()
        status_val = str(row.get(COL_STATUS, '')).strip()
        is_meeting = str(row.get(COL_MEETING, '')).strip()
        
        # 只要沒有填寫日期，或者已經發送過了，就直接略過
        if not date_str or status_val:
            continue
            
        # 嘗試解析 Excel 裡面的日期格式 (支援 2026/03/24 或 2026/3/24)
        try:
            date_clean = date_str.replace('-', '/')
            meeting_date = datetime.datetime.strptime(date_clean, '%Y/%m/%d').date()
        except ValueError:
            print(f"警告：第 {i+2} 列的日期 '{date_str}' 格式錯誤（需為 YYYY/M/D），略過。")
            continue
            
        # 核心判斷：只有「開會日期 = 明天」才納入本次推播任務
        if meeting_date == tomorrow_datetime.date():
            if is_meeting == '是':
                pending_items.append(row)
                update_rows.append(i + 2)
            elif is_meeting == '否':
                cancel_items.append(row)
                update_rows.append(i + 2)
    
    if not pending_items and not cancel_items:
        print("沒有新的會議事項需要通知。")
        return
        
    draft_message = ""
    
    # 若被標記為不開會
    if cancel_items:
        draft_message = "各位夥伴好，本週暫停 meeting 一次！大家辛苦了 ☕"
    else:
        # 2. 重新啟用 Gemini AI，讓它幫忙整理重點與加上 Emoji
        prompt_text = (
            "請幫我整理成一篇相對正式、嚴肅的「本周 meeting 通知」。\n"
            "【重要排版指令與格式要求】：\n"
            "1. 開頭第一句請直接講重點「各位夥伴好，請準備本周 meeting」，絕對不要發明像康樂股長打招呼的贅字。\n"
            "2. 語氣請冷靜、專業，只需適度加入幾個標示用的基礎 Emoji（例如 📅、📍）即可。\n"
            "3. 你可以把以下內容進行專業潤飾，但**嚴格保持**下面這個換行結構樣貌，絕對不能擅自偏離這個排版：\n"
            "   日期：[代入日期]\n"
            "   時間：[代入時間]\n"
            "   地點：[代入地點]\n"
            "   碩一15分鐘\n"
            "   [你潤飾整理過的碩一學術重點]\n"
            "   碩二20分鐘\n"
            "   [你潤飾整理過的碩二學術重點]\n"
            "4. 絕對不要輸出 [請填寫...] 或 [你的名字] 這種需要人工補齊的假佔位符，不需要署名。\n\n"
            "以下為本次會議的實際原始資訊：\n"
        )
        for item in pending_items:
            prompt_text += (
                f"實際日期：{item.get(COL_DATE, '')}\n"
                f"實際時間：{item.get(COL_TIME, '')}\n"
                f"實際地點：{item.get(COL_LOCATION, '')}\n"
                f"碩一內容：{item.get(COL_M1_CONTENT, '')}\n"
                f"碩二內容：{item.get(COL_M2_CONTENT, '')}\n"
                "---------------------------\n"
            )
            
        # 呼叫 Gemini AI
        try:
            genai.configure(api_key=GEMINI_API_KEY)
            model = genai.GenerativeModel('gemini-2.5-flash')
            response = model.generate_content(prompt_text)
            
            # 把 Gemini 誤加的標記字眼清掉
            draft_message = response.text.replace('@All', '').replace('@all', '').strip()
            
        except Exception as e:
            print(f"呼叫 Gemini API 失敗: {e}")
            return
            
    if not line_group_ids:
        print("未設定 LINE_GROUP_ID，無法發送任何通知。")
        return
    
    # 3. 發送至 LINE 群組 (支援多群組 Multicast 廣播)
    import requests
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {LINE_CHANNEL_ACCESS_TOKEN}'
    }
    
    # 依照群組數量決定打「單發(push)」還是「群發(multicast)」的 API
    if len(line_group_ids) == 1:
        api_url = 'https://api.line.me/v2/bot/message/push'
        payload = {
            "to": line_group_ids[0],
            "messages": [{"type": "text", "text": draft_message}]
        }
    else:
        api_url = 'https://api.line.me/v2/bot/message/multicast'
        payload = {
            "to": line_group_ids,
            "messages": [{"type": "text", "text": draft_message}]
        }
        
    try:
        res = requests.post(api_url, headers=headers, json=payload)
        res.raise_for_status()
    except Exception as e:
        print(f"發送 LINE 訊息失敗: {e}")
        return
        
    # 4. 更新 Google Sheets (寫入今天的日期)
    try:
        today_str = datetime.datetime.now().strftime('%Y-%m-%d')
        # 改回動態欄位寫入，不怕表單順序變動
        cells_to_update = [gspread.Cell(row=r, col=status_col_index, value=today_str) for r in update_rows]
        worksheet.update_cells(cells_to_update)
        print(f"成功發送通知並更新了 {len(update_rows)} 筆資料。")
    except Exception as e:
        print(f"更新 Google Sheets 失敗: {e}")

if __name__ == "__main__":
    main()
