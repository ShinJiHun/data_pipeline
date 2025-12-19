# ==========================================
# 🧠 RidingInformation Base Python Image
# ==========================================
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    TZ=Asia/Seoul \
    PYTHONPATH=/app

# ✅ 시스템 패키지 설치
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libmariadb-dev \
    tesseract-ocr \
    tesseract-ocr-kor \
    fonts-nanum \
    && rm -rf /var/lib/apt/lists/*

# ✅ 기본 Python 패키지 세팅 (공통 requirements)
COPY cycle_data_parser/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# ✅ 폰트 및 로그 설정
ENV MPLCONFIGDIR=/tmp/matplotlib
RUN mkdir -p $MPLCONFIGDIR && chmod -R 777 $MPLCONFIGDIR && mkdir -p /app/logs

WORKDIR /app

CMD ["python3"]

