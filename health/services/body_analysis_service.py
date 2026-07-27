from sqlalchemy.orm import Session
from health.models import BodyAnalysisResult
from health.llm.client import run_llm_analysis


def get_or_create_body_analysis(
    db: Session,
    *,
    user_id: int,
    body_record,
    goal_type: str,
    body_type: str,
    provider: str,
):
    # 1️⃣ 기존 결과 확인
    existing = (
        db.query(BodyAnalysisResult)
        .filter_by(
            body_record_id=body_record.id,
            goal_type=goal_type,
            model_provider=provider,
        )
        .first()
    )

    if existing:
        return existing

    # 2️⃣ AI 분석
    llm_result = run_llm_analysis(
        provider=provider,
        record=[body_record],
        goal_type=goal_type,
        body_type=body_type,
    )

    # 3️⃣ 저장
    result = BodyAnalysisResult(
        user_id=user_id,
        body_record_id=body_record.id,
        goal_type=goal_type,
        body_type=body_type,
        model_provider=provider,
        model_name=llm_result["model"],
        analysis_json=llm_result["analysis_json"],
        analysis_text=llm_result.get("analysis_text"),
    )

    db.add(result)
    db.commit()
    db.refresh(result)

    return result

