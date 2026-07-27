#!/bin/bash

# ============================================
# 새로 저장된 라이딩 활동 AI 분석 배치
# ============================================

API_BASE="http://localhost:8080"
USER_ID=1
LOGDIR="/mnt/200gb/project/data_pipeline/cycle_data_parser/main/logs"
LOGFILE="$LOGDIR/ai_analysis.log"

mkdir -p "$LOGDIR"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] AI 분석 배치 시작" | tee -a "$LOGFILE"

# Spring Boot 헬스체크
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$API_BASE/api/activity/list?userId=1" 2>/dev/null)
if [ "$HTTP_STATUS" != "200" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ❌ Spring Boot 응답 없음 (status: $HTTP_STATUS)" | tee -a "$LOGFILE"
    exit 1
fi

echo "  ✅ Spring Boot 연결 확인" | tee -a "$LOGFILE"

# 미분석 활동 목록 조회 (분석 안된 것만)
RESPONSE=$(curl -s "$API_BASE/api/activity/list?userId=$USER_ID&limit=100")

if [ -z "$RESPONSE" ] || [ "$RESPONSE" = "[]" ]; then
    echo "  ℹ️  활동 없음" | tee -a "$LOGFILE"
    exit 0
fi

# activity ID 목록 추출 (jq 사용)
ACTIVITY_IDS=$(echo "$RESPONSE" | jq -r '.[].id' 2>/dev/null)

if [ -z "$ACTIVITY_IDS" ]; then
    echo "  ❌ ID 파싱 실패 (jq 설치 필요: apt install jq)" | tee -a "$LOGFILE"
    exit 1
fi

SUCCESS=0
SKIP=0
FAIL=0

for ID in $ACTIVITY_IDS; do
    # 기존 분석 결과 확인
    EXISTING=$(curl -s "$API_BASE/api/ai/analysis/activity/$ID?userId=$USER_ID")

    if [ "$EXISTING" != "null" ] && [ -n "$EXISTING" ] && echo "$EXISTING" | jq -e '.id' > /dev/null 2>&1; then
        echo "  ⏭️  이미 분석됨: $ID" | tee -a "$LOGFILE"
        SKIP=$((SKIP + 1))
        continue
    fi

    # Claude 분석 요청
    HTTP_CODE=$(curl -s -o /tmp/analysis_result.json -w "%{http_code}" \
        -X POST "$API_BASE/api/ai/analysis/activity/$ID?userId=$USER_ID")

    if [ "$HTTP_CODE" = "200" ]; then
        SUMMARY=$(cat /tmp/analysis_result.json | jq -r '.summary // "분석완료"' 2>/dev/null)
        echo "  ✅ 분석 완료: $ID - $SUMMARY" | tee -a "$LOGFILE"
        SUCCESS=$((SUCCESS + 1))
        sleep 2  # API 레이트 리밋 방지
    else
        echo "  ❌ 분석 실패: $ID (HTTP $HTTP_CODE)" | tee -a "$LOGFILE"
        FAIL=$((FAIL + 1))
    fi
done

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 완료 - 성공: ${SUCCESS}, 스킵: ${SKIP}, 실패: ${FAIL}" | tee -a "$LOGFILE"
