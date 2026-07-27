from sqlalchemy.orm import Session
from health.models import UserInbodyRecord
from health.services.body_analysis_service import get_or_create_body_analysis


def run_full_body_analysis(
    db: Session,
    user_id: int,
    providers: list[str],
    goal_type: str = "DIET",
):
    records = (
        db.query(UserInbodyRecord)
        .filter_by(user_id=user_id)
        .order_by(UserInbodyRecord.record_date)
        .all()
    )

    for record in records:
        body_type = "overweight"  # resolveBodyType(record)

        for provider in providers:
            get_or_create_body_analysis(
                db=db,
                user_id=user_id,
                body_record=record,
                goal_type=goal_type,
                body_type=body_type,
                provider=provider,
            )

