# =========================================
# 🧱 Lotto Parser Dockerfile
# =========================================
FROM python:3.11-slim

# 기본 패키지 업데이트 + 필수 의존성 설치
RUN apt-get update && apt-get install -y \
    build-essential \
    libmariadb-dev \
    && rm -rf /var/lib/apt/lists/*

# 작업 디렉토리 설정
WORKDIR /app/lotto_parser

# 파이썬 라이브러리 복사 및 설치
COPY requirements.txt /app/lotto_parser/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Lotto Parser 코드 복사
COPY . /app/lotto_parser

# 환경 변수
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# 기본 실행 명령 (lotto_main.py 실행)
CMD ["python3", "-u", "main/lotto_main.py"]

