"""
LifeMetrics - 통합 Watcher
여러 NAS 폴더를 동시 감시하여 자동 처리

감시 대상:
  /mnt/200gb/NAS/inbody/raw/       → 인바디 OCR → DB
  /mnt/200gb/NAS/fitdays/raw/      → FitDays OCR → DB
  /mnt/200gb/NAS/health_check/raw/ → 건강검진 OCR → DB
"""

import os
import sys
import time
import traceback
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root.parent))  # data_pipeline 패키지(…/claude_config 등) 임포트용

from services.inbody_pipeline_service import process_image_file as process_inbody
from services.fitdays_pipeline_service import process_image_file as process_fitdays
from services.health_exam_pipeline_service import process_file as process_health_exam

# ────────────────────────────────────────────
# 감시 대상 설정
# ────────────────────────────────────────────
WATCH_TARGETS = {
    "/mnt/200gb/NAS/inbody/raw": {
        "name":       "InBody",
        "extensions": (".png", ".jpg", ".jpeg"),
        "handler":    process_inbody,
    },
    "/mnt/200gb/NAS/fitdays/raw": {
        "name":       "FitDays",
        "extensions": (".png", ".jpg", ".jpeg"),
        "handler":    process_fitdays,
    },
    "/mnt/200gb/NAS/health_check/raw": {
        "name":       "HealthExam",
        "extensions": (".pdf", ".png", ".jpg", ".jpeg"),
        "handler":    process_health_exam,
    },
}


# ────────────────────────────────────────────
# 공통 처리 함수
# ────────────────────────────────────────────
def process_single_file(file_path: str, target: dict):
    name = target["name"]
    print(f"\n{'='*80}")
    print(f"[▶ {name}] {os.path.basename(file_path)}")
    print(f"[시간] {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*80}")

    try:
        result = target["handler"](file_path, user_id=1)
        if result["status"] == "success":
            print(f"\n[✅ {name}] DB 저장 완료!")
            return True
        else:
            print(f"\n[❌ {name}] {result.get('message')}")
            return False
    except Exception as e:
        print(f"\n[❌ {name}] 예외 발생: {str(e)}")
        traceback.print_exc()
        return False
    finally:
        print(f"{'='*80}\n")


# ────────────────────────────────────────────
# 이벤트 핸들러
# ────────────────────────────────────────────
class UnifiedHandler(FileSystemEventHandler):

    def __init__(self, watch_dir: str, target: dict):
        super().__init__()
        self.watch_dir  = watch_dir
        self.target     = target
        self.processing = set()

    def on_created(self, event):
        if event.is_directory:
            return

        ext = Path(event.src_path).suffix.lower()
        if ext not in self.target["extensions"]:
            return

        if event.src_path in self.processing:
            return

        # 파일 쓰기 완료 대기
        time.sleep(2)

        if not os.path.exists(event.src_path):
            return

        try:
            self.processing.add(event.src_path)
            process_single_file(event.src_path, self.target)
        finally:
            self.processing.discard(event.src_path)


# ────────────────────────────────────────────
# 시작 시 기존 파일 처리
# ────────────────────────────────────────────
def process_existing_files():
    for watch_dir, target in WATCH_TARGETS.items():
        if not os.path.exists(watch_dir):
            continue

        files = [
            f for f in os.listdir(watch_dir)
            if Path(f).suffix.lower() in target["extensions"]
        ]

        if not files:
            print(f"[{target['name']}] raw 폴더에 처리할 파일 없음")
            continue

        print(f"\n[{target['name']}] 기존 파일 {len(files)}개 발견")
        for fname in sorted(files):
            process_single_file(os.path.join(watch_dir, fname), target)
            time.sleep(3)


# ────────────────────────────────────────────
# 메인
# ────────────────────────────────────────────
def main():
    print(f"\n{'='*80}")
    print(f"🚀 LifeMetrics 통합 Watcher 시작")
    print(f"⏰ 시작 시간: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*80}")

    for watch_dir, target in WATCH_TARGETS.items():
        os.makedirs(watch_dir, exist_ok=True)
        print(f"  📂 [{target['name']}] {watch_dir}")
    print()

    # 기존 파일 먼저 처리
    process_existing_files()

    # 감시 시작
    observer = Observer()
    for watch_dir, target in WATCH_TARGETS.items():
        handler = UnifiedHandler(watch_dir, target)
        observer.schedule(handler, path=watch_dir, recursive=False)

    observer.start()
    print("[✅ 감시 중...] 파일을 업로드하면 자동 처리됩니다.")
    print("[종료: Ctrl+C]\n")

    try:
        while observer.is_alive():
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()

    observer.join()
    print("[✅ 정상 종료]")


if __name__ == "__main__":
    main()
