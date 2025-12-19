FROM riding-base:latest

WORKDIR /app/cycle_data_parser
COPY . /app/cycle_data_parser

# ✅ requirements.txt 올바른 경로 지정
RUN pip install --no-cache-dir -r /app/cycle_data_parser/requirements.txt && pip install polyline

ENV PYTHONPATH=/app
CMD ["python3", "-u", "main/main_reader.py"]

