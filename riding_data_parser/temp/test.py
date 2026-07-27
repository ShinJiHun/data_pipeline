"""
FIT 파일 진단: 기어 데이터가 들어있는지 확인.

사용:
  /mnt/200gb/project/data_pipeline/venv/bin/python inspect_fit.py 22749254619.fit
"""
import sys
from collections import Counter, defaultdict

import fitparse

if len(sys.argv) < 2:
    print("사용: inspect_fit.py <fit파일>")
    sys.exit(1)

path = sys.argv[1]
fit = fitparse.FitFile(path)

msg_counter = Counter()
fields_by_msg = defaultdict(set)
gear_hits = []          # 기어 관련 메시지 샘플 (앞부분 몇 개만)
event_types = Counter() # event 메시지의 event_type 분포

print(f"=== 파일: {path} ===\n")

for msg in fit.get_messages():
    name = msg.name
    msg_counter[name] += 1

    fields = {d.name: d.value for d in msg}
    fields_by_msg[name].update(fields.keys())

    # event 메시지의 event_type 통계 (gear_change_event는 event 타입으로 들어옴)
    if name == "event":
        et = fields.get("event")
        if et is not None:
            event_types[str(et)] += 1

    # 기어 관련 흔적 찾기 - 메시지명 또는 필드명에 'gear' 포함
    name_has_gear = "gear" in name.lower()
    field_has_gear = any("gear" in f.lower() for f in fields.keys())
    if (name_has_gear or field_has_gear) and len(gear_hits) < 10:
        gear_hits.append((name, fields))

print("--- 메시지 타입별 개수 ---")
for n, c in msg_counter.most_common():
    print(f"  {n}: {c}")

print("\n--- event 메시지의 event_type 분포 ---")
if event_types:
    for et, c in event_types.most_common():
        print(f"  {et}: {c}")
else:
    print("  (event 메시지 없음)")

print("\n--- 기어 관련 메시지/필드 발견 ---")
if gear_hits:
    for i, (n, f) in enumerate(gear_hits, 1):
        print(f"\n[{i}] {n}")
        for k, v in f.items():
            print(f"    {k}: {v}")
else:
    print("  (기어 관련 메시지/필드 없음 → 전자식 변속 데이터 미기록)")

print("\n--- record 메시지의 필드 목록 (1초 단위 데이터) ---")
if "record" in fields_by_msg:
    for f in sorted(fields_by_msg["record"]):
        print(f"  {f}")
