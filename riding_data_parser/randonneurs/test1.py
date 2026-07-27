import requests

session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0"})

# 어디로 리다이렉트 되는지 확인
res = session.get("http://www.korearandonneurs.kr/reg/login.jsp", allow_redirects=True)
print("최종 URL:", res.url)
print("상태코드:", res.status_code)

res2 = session.get("http://www.korearandonneurs.kr/reg/", allow_redirects=True)
print("루트 URL:", res2.url)
print("루트 상태:", res2.status_code)
