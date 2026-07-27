"""
LifeMetrics - 건강 체크 입력 CLI
혈압, 심박수, 산소포화도를 입력해서 DB에 저장

사용법:
    # 대화형 입력 (오늘 날짜)
    python health_check_batch.py

    # 날짜 지정
    python health_check_batch.py --date 2025-11-15

    # 값 직접 지정
    python health_check_batch.py --date 2025-11-15 --sbp 120 --dbp 80 --hr 62 --spo2 98.0 --source hospital --note "건강검진"

    # 최근 기록 조회
    python health_check_batch.py --list
    python health_check_batch.py --list --days 7
"""

import os
import sys
import argparse
from datetime import date
from dotenv import load_dotenv

load_dotenv("/mnt/200gb/apps/.env")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.health_check_service import save_health_check, get_recent_health_checks


def input_int(prompt):
    while True:
        val = input(prompt).strip()
        if not val:
            return None
        try:
            return int(val)
        except ValueError:
            print("  숫자를 입력하세요.")


def input_float(prompt):
    while True:
        val = input(prompt).strip()
        if not val:
            return None
        try:
            return float(val)
        except ValueError:
            print("  숫자를 입력하세요.")


def interactive_input(target_date: str):
    print(f"\n{'='*50}")
    print(f"  건강 체크 입력: {target_date}")
    print(f"  (입력 생략 시 Enter)")
    print(f"{'='*50}\n")

    sbp    = input_int("수축기 혈압 (최고혈압, mmHg): ")
    dbp    = input_int("이완기 혈압 (최저혈압, mmHg): ")
    hr     = input_int("심박수 (bpm): ")
    spo2   = input_float("산소포화도 (%): ")
    source = input("출처 (manual/hospital/device, 기본 manual): ").strip() or "manual"
    note   = input("메모 (생략 가능): ").strip() or None

    if not any([sbp, dbp, hr, spo2]):
        print("\n⚠️  입력된 값이 없습니다. 종료합니다.")
        return

    print(f"\n[입력 확인]")
    if sbp and dbp: print(f"  혈압: {sbp}/{dbp} mmHg")
    if hr:          print(f"  심박수: {hr} bpm")
    if spo2:        print(f"  산소포화도: {spo2}%")
    print(f"  출처: {source}")
    if note:        print(f"  메모: {note}")

    confirm = input("\n저장할까요? (y/N): ").strip().lower()
    if confirm != "y":
        print("취소됐습니다.")
        return

    save_health_check(
        measure_date=target_date,
        systolic_bp=sbp,
        diastolic_bp=dbp,
        heart_rate=hr,
        oxygen_saturation=spo2,
        source=source,
        note=note,
    )
    print("✅ 저장 완료!")


def show_list(days: int):
    records = get_recent_health_checks(days=days)
    if not records:
        print(f"\n최근 {days}일간 기록이 없습니다.")
        return

    print(f"\n{'='*70}")
    print(f"  최근 {days}일 건강 체크 기록")
    print(f"{'='*70}")
    print(f"{'날짜':<12} {'혈압':^12} {'심박':^6} {'산소포화도':^10} {'출처':<10} {'메모'}")
    print(f"{'-'*70}")
    for r in records:
        bp   = f"{r['systolic_bp']}/{r['diastolic_bp']}" if r['systolic_bp'] else "-"
        hr   = str(r['heart_rate']) if r['heart_rate'] else "-"
        spo2 = f"{r['oxygen_saturation']}%" if r['oxygen_saturation'] else "-"
        src  = r['source'] or "-"
        note = r['note'] or ""
        print(f"{str(r['measure_date']):<12} {bp:^12} {hr:^6} {spo2:^10} {src:<10} {note}")
    print(f"{'='*70}\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date",   default=str(date.today()), help="측정 날짜 (YYYY-MM-DD)")
    parser.add_argument("--sbp",    type=int,   help="수축기 혈압")
    parser.add_argument("--dbp",    type=int,   help="이완기 혈압")
    parser.add_argument("--hr",     type=int,   help="심박수")
    parser.add_argument("--spo2",   type=float, help="산소포화도")
    parser.add_argument("--source", choices=["manual", "hospital", "device"],
                        default="manual", help="입력 출처")
    parser.add_argument("--note",   type=str,   help="메모")
    parser.add_argument("--list",   action="store_true", help="최근 기록 조회")
    parser.add_argument("--days",   type=int,   default=30, help="조회 기간 (기본 30일)")
    args = parser.parse_args()

    if args.list:
        show_list(args.days)
        return

    # 비대화형: 인자로 직접 입력
    if any([args.sbp, args.dbp, args.hr, args.spo2]):
        save_health_check(
            measure_date=args.date,
            systolic_bp=args.sbp,
            diastolic_bp=args.dbp,
            heart_rate=args.hr,
            oxygen_saturation=args.spo2,
            source=args.source,
            note=args.note,
        )
        print(f"✅ {args.date} 저장 완료!")
        return

    # 대화형 입력
    interactive_input(args.date)


if __name__ == "__main__":
    main()
