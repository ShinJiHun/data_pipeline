def build_body_analysis_prompt(
    inbody: dict,
    deltas: dict,
    goal: str,
    body_type: str,
):
    return f"""
당신은 체성분 분석 전문 AI입니다.

다음은 한 사용자의 인바디 기록입니다:
- 체중: {inbody["weight"]} kg
- 골격근량: {inbody["skeletal_muscle_mass"]} kg
- 체지방률: {inbody["body_fat_percentage"]} %
- BMI: {inbody["bmi"]}
- 내장지방 레벨: {inbody["visceral_fat_level"]}

이전 기록 대비 변화:
- 체중: {deltas.get("weight")}
- 체지방률: {deltas.get("body_fat_percentage")}
- 골격근량: {deltas.get("skeletal_muscle_mass")}

사용자의 현재 체형 타입: {body_type}
사용자의 목표: {goal}

⚠️ 출력 규칙:
- 반드시 JSON만 출력
- 아래 스키마를 정확히 따를 것
- 키 이름 변경 금지
- 누락 금지
- 추가 필드 금지

출력 스키마:
{{
  "summary": string,
  "current_state": string,
  "body_type_analysis": string,
  "training_recommendation": {{
    "strength": string,
    "cardio": string
  }},
  "diet_recommendation": {{
    "general": string,
    "convenience_store": string
  }},
  "warning": string | null
}}
"""

