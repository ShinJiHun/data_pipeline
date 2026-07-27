import requests
from bs4 import BeautifulSoup
import re
import time
from datetime import datetime

COOKIE = "JSESSIONID=166C0DD313B9D506C6C54BC34A7EDA04; lang=ko; PHPSESSID=2hd99fetikslkt6eqk2lsi4uo2"
URL = "http://www.korearandonneurs.kr/reg/register.php"
CHECK_INTERVAL = 60

WATCH_LIST = {
    "2261": "서울 300K-서 (4월 11일)"
}

FULL_MSG = "이 대회의 제한 인원이 다 찼습니다"
TELEGRAM_TOKEN = "8522278796:AAHlEkShQ9pBC265FC-CA8xqgY_7Yzh3bSk"
TELEGRAM_CHAT_ID = "8711100447"

def send_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
    except Exception as e:
        print(f"[텔레그램 오류] {e}", flush=True)

def fetch_status():
    headers = {
        "Cookie": COOKIE,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/146.0.0.0",
        "Referer": "http://www.korearandonneurs.kr:8080/"
    }
    res = requests.get(URL, headers=headers, timeout=10)
    res.encoding = 'utf-8'
    soup = BeautifulSoup(res.text, "html.parser")

    results = {}
    for event_id in WATCH_LIST:
        anchor = soup.find("a", {"name": event_id})
        if not anchor:
            continue
        msg = ""
        count = None
        next_el = anchor.next_sibling
        while next_el:
            if hasattr(next_el, 'get') and next_el.get("name"):
                break
            if hasattr(next_el, 'get'):
                classes = next_el.get("class", [])
                if "event-info" in classes:
                    msg = next_el.get_text(" ", strip=True)
                    count_match = re.search(r"현재 신청 인원 (\d+)명", msg)
                    if count_match:
                        count = int(count_match.group(1))
            next_el = next_el.next_sibling
        results[event_id] = {"msg": msg, "count": count}
    return results

def monitor():
    prev = {}
    start_msg = "🚴 브레베 모니터링 시작\n"
    for eid, name in WATCH_LIST.items():
        start_msg += f"  → {name} (ID: {eid})\n"
    print(start_msg, flush=True)
    send_telegram(start_msg)

    while True:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 체크 중...", flush=True)
        try:
            current = fetch_status()
            for eid, data in current.items():
                name = WATCH_LIST[eid]
                msg = data["msg"]
                count = data["count"]
                is_full = FULL_MSG in msg
                status = "마감" if is_full else "신청가능"
                count_str = f"{count}명" if count is not None else "-"

                if eid not in prev:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] {name} | {status} | 신청인원: {count_str}", flush=True)
                else:
                    prev_full = FULL_MSG in prev[eid]["msg"]
                    prev_count = prev[eid]["count"]
                    if prev_full and not is_full:
                        alert = f"🎉 자리 났다!\n{name}\n상태: 마감 → 신청가능\n신청인원: {count_str}\nhttp://www.korearandonneurs.kr/reg/register.php"
                        print(f"\n{'='*50}\n{alert}\n{'='*50}\n", flush=True)
                        send_telegram(alert)
                    elif not prev_full and is_full:
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] {name} | 마감됨", flush=True)
                    elif prev_count != count and count is not None:
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] {name} | 인원변동: {prev_count}명 → {count_str}", flush=True)
                prev[eid] = data

        except Exception as e:
            print(f"[오류] {e}", flush=True)

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    monitor()
