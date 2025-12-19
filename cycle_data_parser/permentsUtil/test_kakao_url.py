import requests
import re

def expand_kakao_url(short_url):
    try:
        res = requests.get(short_url, allow_redirects=False, timeout=5)
        print(f"Status: {res.status_code}")
        print(f"Headers: {res.headers}")
        if "Location" in res.headers:
            redirect_url = res.headers["Location"]
            print(f"Redirect URL: {redirect_url}")
            m = re.search(r"urlX=([\d.]+)&urlY=([\d.]+)", redirect_url)
            if m:
                lon, lat = float(m.group(1)), float(m.group(2))
                print(f"Extracted coords: lat={lat}, lon={lon}")
                return lon, lat
    except Exception as e:
        print(f"Error: {e}")
    return None, None


if __name__ == "__main__":
    test_url = "http://kko.to/YSYwyMj0M"  # PT-02 예시
    expand_kakao_url(test_url)

