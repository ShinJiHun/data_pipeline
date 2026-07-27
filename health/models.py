from sqlalchemy import (
    Column,
    Integer,
    BigInteger,
    Float,
    String,
    Date,
    DateTime,
    Text,
    JSON,
    UniqueConstraint,
    ForeignKey,
    func,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


# =========================
# 1️⃣ 사용자 인바디 기록 (원천 데이터)
# =========================
class UserInbodyRecord(Base):
    __tablename__ = "user_inbody_record"

    id = Column(BigInteger, primary_key=True)
    user_id = Column(BigInteger, nullable=False)

    record_date = Column(Date, nullable=False)

    weight = Column(Float)
    skeletal_muscle_mass = Column(Float)
    body_fat_mass = Column(Float)
    body_fat_percentage = Column(Float)
    bmi = Column(Float)
    visceral_fat_level = Column(Integer)

    waist_circumference = Column(Float)
    thigh_circumference = Column(Float)
    arm_circumference = Column(Float)
    chest_circumference = Column(Float)

    __table_args__ = (
        UniqueConstraint("user_id", "record_date", name="uq_user_inbody_date"),
    )


# =========================
# 2️⃣ 단일 인바디 기준 AI 분석 결과
# =========================
class BodyAnalysisResult(Base):
    __tablename__ = "body_analysis_result"

    id = Column(BigInteger, primary_key=True)

    body_record_id = Column(
        BigInteger,
        ForeignKey("user_inbody_record.id", ondelete="CASCADE"),
        nullable=False,
    )


    goal_type = Column(String(30), nullable=False)     # DIET / MUSCLE / BODY_PROFILE
    body_type = Column(String(20), nullable=False)     # lean / normal / fit / obese

    model_provider = Column(String(20), nullable=False)  # openai / gemini / claude
    model_name = Column(String(50), nullable=False)

    analysis_json = Column(JSON, nullable=False)
    analysis_text = Column(Text)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
    )

    record = relationship("UserInbodyRecord")

    __table_args__ = (
        UniqueConstraint(
            "body_record_id",
            "goal_type",
            "model_provider",
            name="uq_body_goal_model",
        ),
    )


# =========================
# 3️⃣ 인생(누적) 체성분 분석 Snapshot
# =========================
class BodyAnalysisLifetimeSnapshot(Base):
    __tablename__ = "body_analysis_lifetime_snapshot"

    id = Column(BigInteger, primary_key=True)

    user_id = Column(BigInteger, nullable=False)

    from_record_date = Column(Date, nullable=False)
    to_record_date = Column(Date, nullable=False)
    record_count = Column(Integer, nullable=False)

    model_provider = Column(String(20), nullable=False)
    model_name = Column(String(50), nullable=False)

    analysis_json = Column(JSON, nullable=False)
    analysis_text = Column(Text)

    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "model_provider",
            "to_record_date",
            name="uq_lifetime_snapshot",
        ),
    )


# =========================
# 4️⃣ 최신 인생 요약 포인터
# =========================
class BodyAnalysisLifetimeCurrent(Base):
    __tablename__ = "body_analysis_lifetime_current"

    user_id = Column(BigInteger, primary_key=True)

    snapshot_id = Column(
        BigInteger,
        ForeignKey(
            "body_analysis_lifetime_snapshot.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    updated_at = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
    )

    snapshot = relationship("BodyAnalysisLifetimeSnapshot")

