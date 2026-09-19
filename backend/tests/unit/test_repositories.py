"""Repository 单测(内存 SQLite,architecture.md §6.2):单一实体 CRUD,无业务规则。"""

from app.models.questionnaire import Questionnaire
from app.models.questionnaire_response import QuestionnaireResponse
from app.models.user_profile import RiskLevel, UserProfile
from app.repositories.profile_repo import ProfileRepository
from app.repositories.questionnaire_repo import QuestionnaireRepository
from app.repositories.user_repo import UserRepository


class TestUserRepository:
    async def test_get_by_username_miss_and_hit(self, db_session):
        repo = UserRepository(db_session)
        assert await repo.get_by_username("alice") is None
        await repo.create("alice", "hashed")
        assert await repo.get_by_username("alice") is not None

    async def test_create_and_get_by_id(self, db_session):
        repo = UserRepository(db_session)
        user = await repo.create("bob", "hashed")
        fetched = await repo.get_by_id(user.id)
        assert fetched.username == "bob"
        assert await repo.get_by_id(9999) is None


class TestQuestionnaireRepository:
    async def test_get_latest_returns_highest_version(self, db_session):
        repo = QuestionnaireRepository(db_session)
        db_session.add(Questionnaire(title="v1", version=1, questions=[{"id": "q1"}]))
        db_session.add(Questionnaire(title="v2", version=2, questions=[{"id": "q2"}]))
        await db_session.commit()

        latest = await repo.get_latest()
        assert latest.version == 2
        assert latest.questions == [{"id": "q2"}]

    async def test_get_latest_miss(self, db_session):
        assert await QuestionnaireRepository(db_session).get_latest() is None

    async def test_create_and_get_latest_response_per_user(self, db_session):
        repo = QuestionnaireRepository(db_session)
        questionnaire = Questionnaire(title="v1", version=1, questions=[])
        db_session.add(questionnaire)
        await db_session.commit()

        await repo.create_response(
            QuestionnaireResponse(
                user_id=1, questionnaire_id=questionnaire.id, answers=[], score=60, risk_level=RiskLevel.C4
            )
        )
        latest = await repo.create_response(
            QuestionnaireResponse(
                user_id=1, questionnaire_id=questionnaire.id, answers=[], score=20, risk_level=RiskLevel.C1
            )
        )
        await db_session.commit()

        result = await repo.get_latest_response(1)
        assert result.id == latest.id  # 按 id 降序取最近一次
        assert result.risk_level is RiskLevel.C1
        # 用户隔离:其他用户无作答
        assert await repo.get_latest_response(2) is None


class TestProfileRepository:
    async def test_get_by_user_id_miss_and_hit(self, db_session):
        repo = ProfileRepository(db_session)
        assert await repo.get_by_user_id(1) is None
        await repo.save(UserProfile(user_id=1, risk_level=RiskLevel.C3, version=1))
        profile = await repo.get_by_user_id(1)
        assert profile.risk_level is RiskLevel.C3

    async def test_save_updates_existing(self, db_session):
        repo = ProfileRepository(db_session)
        profile = UserProfile(user_id=1, risk_level=RiskLevel.C1, version=1)
        await repo.save(profile)
        profile.risk_level = RiskLevel.C5
        profile.version = 2
        await repo.save(profile)
        fetched = await repo.get_by_user_id(1)
        assert fetched.risk_level is RiskLevel.C5
        assert fetched.version == 2
