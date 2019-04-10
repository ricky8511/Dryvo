from datetime import datetime, timedelta

import pytest
from loguru import logger

from server.api.blueprints.lessons import get_lesson_data, handle_places
from server.api.database.models import (
    Lesson,
    Payment,
    Place,
    Student,
    Topic,
    WorkDay,
    LessonTopic,
)
from server.consts import DATE_FORMAT
from server.error_handling import RouteError

tomorrow = datetime.utcnow() + timedelta(days=1)


def create_lesson(teacher, student, meetup, dropoff, date, duration=40, deleted=False):
    return Lesson.create(
        teacher=teacher,
        student=student,
        creator=student.user if student else teacher.user,
        duration=duration,
        date=date,
        meetup_place=meetup,
        dropoff_place=dropoff,
        deleted=deleted,
    )


def test_lessons(auth, teacher, student, meetup, dropoff, requester):
    date = datetime(year=2018, month=11, day=27, hour=13, minute=00)
    create_lesson(teacher, student, meetup, dropoff, date)
    create_lesson(teacher, student, meetup, dropoff, date, deleted=True)
    auth.login(email=student.user.email)
    resp1 = requester.get("/lessons/?limit=1&page=1")  # no filters
    assert isinstance(resp1.json["data"], list)
    assert resp1.json["next_url"]
    resp2 = requester.get(resp1.json["next_url"])
    assert resp2.json["data"][0]["id"] != resp1.json["data"][0]["id"]
    resp = requester.get("/lessons/?student_id=gt:1")
    assert not resp.json["data"]
    resp = requester.get("/lessons/?date=2018-20-01T20")
    assert "wrong parameters" in resp.json["message"].lower()
    resp = requester.get("/lessons/?deleted=true")
    assert len(resp.json["data"]) == 2


def test_deleted_lessons(auth, teacher, student, meetup, dropoff, requester):
    date = datetime(year=2018, month=11, day=27, hour=13, minute=00)
    create_lesson(teacher, student, meetup, dropoff, date, duration=80, deleted=True)
    auth.login(email=teacher.user.email)
    resp = requester.get("/lessons/?deleted=true")
    assert resp.json["data"][0]["duration"] == 80


def test_single_lesson(auth, teacher, student, meetup, dropoff, requester):
    date = datetime(year=2018, month=11, day=27, hour=13, minute=00)
    lesson = create_lesson(teacher, student, meetup, dropoff, date)
    auth.login(email=student.user.email)
    resp = requester.get(f"/lessons/{lesson.id}")
    assert resp.json["data"]
    auth.logout()
    auth.login()
    resp = requester.get(f"/lessons/{lesson.id}")
    assert resp._status_code == 401


def test_student_new_lesson(auth, teacher, student, requester, topic):
    auth.login(email=student.user.email)
    date = (tomorrow.replace(hour=22, minute=40)).strftime(DATE_FORMAT)
    kwargs = {
        "teacher_id": teacher.id,
        "day": 1,
        "from_hour": 00,
        "from_minutes": 0,
        "to_hour": 23,
        "to_minutes": 59,
        "on_date": tomorrow.date(),
    }
    WorkDay.create(**kwargs)
    logger.debug(f"added work day for {teacher}")
    resp = requester.post(
        "/lessons/",
        json={"date": date, "meetup_place": "test", "dropoff_place": "test"},
    )
    assert not resp.json["data"]["is_approved"]
    assert resp.json["data"]["lesson_number"] == len(
        Lesson.query.filter_by(student=student).all()
    )


def test_update_topics(auth, teacher, student, requester, topic):
    auth.login(email=teacher.user.email)
    date = (tomorrow.replace(hour=13, minute=00)).strftime(DATE_FORMAT)
    resp = requester.post(
        "/lessons/",
        json={
            "date": date,
            "student_id": student.id,
            "meetup_place": "test",
            "dropoff_place": "test",
        },
    )
    lesson_id = resp.json["data"]["id"]
    resp = requester.post(
        f"/lessons/{lesson_id}/topics",
        json={"topics": {"progress": [], "finished": [topic.id]}},
    )
    assert topic.id == resp.json["data"]["topics"][0]["id"]
    assert resp.json["data"]["topics"][0]["is_finished"]
    assert len(LessonTopic.query.all()) == 1


@pytest.mark.parametrize(
    ("student_id", "topics", "error"),
    (
        (None, {}, "Lesson must have a student assigned."),
        (1, {"progress": [5]}, "Topic does not exist."),
    ),
)
def test_invalid_update_topics(
    auth, meetup, dropoff, teacher, requester, topic, student_id, topics, error
):
    auth.login(email=teacher.user.email)
    date = tomorrow.replace(hour=13, minute=00)
    student = Student.get_by_id(student_id) if student_id else None
    lesson = create_lesson(teacher, student, meetup, dropoff, date)
    resp = requester.post(f"/lessons/{lesson.id}/topics", json={"topics": topics})
    assert resp.status_code == 400
    assert resp.json["message"] == error


def test_hour_not_available(auth, teacher, student, requester):
    auth.login(email=student.user.email)
    date = (tomorrow.replace(hour=12, minute=00)).strftime(DATE_FORMAT)
    kwargs = {
        "teacher_id": teacher.id,
        "day": 1,
        "from_hour": 13,
        "from_minutes": 0,
        "to_hour": 17,
        "to_minutes": 0,
        "on_date": tomorrow.date(),
    }
    WorkDay.create(**kwargs)
    logger.debug(f"added work day for {teacher}")
    resp = requester.post(
        "/lessons/",
        json={"date": date, "meetup_place": "test", "dropoff_place": "test"},
    )
    assert "not available" in resp.json["message"]


def test_teacher_new_lesson_without_student(auth, teacher, student, requester):
    auth.login(email=teacher.user.email)
    date = (tomorrow.replace(hour=13, minute=00)).strftime(DATE_FORMAT)
    resp = requester.post("/lessons/", json={"date": date})
    assert "does not exist" in resp.json["message"]


def test_teacher_new_lesson_with_student(auth, teacher, student, requester):
    auth.login(email=teacher.user.email)
    date = (tomorrow.replace(hour=13, minute=00)).strftime(DATE_FORMAT)
    resp = requester.post(
        "/lessons/",
        json={
            "date": date,
            "student_id": student.id,
            "meetup_place": "test",
            "dropoff_place": "test",
        },
    )
    assert resp.json["data"]["is_approved"]


def test_delete_lesson(auth, teacher, student, meetup, dropoff, requester):
    lesson = create_lesson(teacher, student, meetup, dropoff, datetime.utcnow())
    auth.login(email=student.user.email)
    resp = requester.delete(f"/lessons/{lesson.id}")
    assert "successfully" in resp.json["message"]


def test_approve_lesson(auth, teacher, student, meetup, dropoff, requester):
    lesson = create_lesson(teacher, student, meetup, dropoff, datetime.utcnow())
    auth.login(email=teacher.user.email)
    resp = requester.get(f"/lessons/{lesson.id}/approve")
    assert "approved" in resp.json["message"]
    resp = requester.get(f"/lessons/7/approve")
    assert "not exist" in resp.json["message"]
    assert lesson.is_approved


def test_user_edit_lesson(app, auth, student, teacher, meetup, dropoff, requester):
    """ test that is_approved turns false when user edits lesson"""
    lesson = create_lesson(teacher, student, meetup, dropoff, datetime.utcnow())
    auth.login(email=student.user.email)
    resp = requester.post(f"/lessons/{lesson.id}", json={"meetup_place": "no"})
    assert "successfully" in resp.json["message"]
    assert "no" == resp.json["data"]["meetup_place"]["name"]
    assert not resp.json["data"]["is_approved"]


def test_handle_places(student: Student, meetup: Place):
    assert handle_places("t", "tst", None) == (None, None)
    assert handle_places(meetup.name, "", student) == (meetup, None)
    new_meetup, new_dropoff = handle_places("aa", "bb", student)
    assert new_meetup.name == "aa"
    assert new_meetup.times_used == 1
    assert new_dropoff.times_used == 1


@pytest.mark.parametrize(
    ("data_dict", "error"),
    (
        (
            {"date": (datetime.utcnow() - timedelta(minutes=2)).strftime(DATE_FORMAT)},
            "Date is not valid.",
        ),
        ({"date": (tomorrow.strftime(DATE_FORMAT))}, "This hour is not available."),
    ),
)
def test_student_invalid_get_lesson_data(student, data_dict: dict, error: str):
    with pytest.raises(RouteError) as e:
        get_lesson_data(data_dict, student.user)
    assert e.value.description == error


@pytest.mark.parametrize(
    ("data_dict", "error"),
    (
        (
            {
                "date": (datetime.utcnow() + timedelta(days=2))
                .replace(hour=10, minute=0)
                .strftime(DATE_FORMAT),
                "student_id": 0,
            },
            "Student does not exist.",
        ),
    ),
)
def test_teacher_invalid_get_lesson_data(teacher, data_dict: dict, error: str):
    with pytest.raises(RouteError) as e:
        get_lesson_data(data_dict, teacher.user)
    assert e.value.description == error


def test_valid_get_lesson_data(student):
    date = ((tomorrow + timedelta(days=1)).replace(hour=00, minute=00)).strftime(
        DATE_FORMAT
    )
    data_dict = {"date": date, "meetup_place": "test", "dropoff_place": "test"}
    get_lesson_data(data_dict, student.user)


def test_lesson_number(teacher, student, meetup, dropoff):
    lessons = []
    for _ in range(2):
        lessons.append(
            create_lesson(teacher, student, meetup, dropoff, datetime.utcnow())
        )
    assert lessons[1].lesson_number == student.new_lesson_number - 1


def test_topics_for_lesson(app):
    topic = Topic.create(
        title="not important", min_lesson_number=1, max_lesson_number=2
    )
    assert topic in Topic.for_lesson(1)


def test_payments(auth, teacher, student, requester):
    payments = []
    for x in range(4):
        payments.append(
            Payment.create(teacher=teacher, student=student, amount=x * 100)
        )

    auth.login(email=teacher.user.email)
    resp = requester.get("/lessons/payments?limit=2")
    assert len(resp.json["data"]) == 2
    assert resp.json["data"][0]["id"] == payments[-1].id

    Payment.create(
        teacher=teacher,
        student=student,
        amount=100_000,
        created_at=datetime.utcnow().replace(month=datetime.utcnow().month + 1),
    )
    start_of_month = datetime.today().replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    start_next_month = start_of_month.replace(month=(start_of_month.month + 1))
    end_next_month = start_next_month.replace(month=(start_next_month.month + 1))
    start_next_month = start_next_month.strftime(DATE_FORMAT)
    start_of_month = start_of_month.strftime(DATE_FORMAT)
    end_next_month = end_next_month.strftime(DATE_FORMAT)
    resp = requester.get(
        f"/lessons/payments?created_at=ge:{start_next_month}&created_at=lt:{end_next_month}"
    )
    assert resp.json["data"][0]["amount"] == 100_000
    resp = requester.get(
        f"/lessons/payments?created_at=ge:{start_of_month}&created_at=lt:{start_of_month}"
    )
    assert not resp.json["data"]


def test_lesson_topics(auth, requester, student, meetup, dropoff, topic, teacher):
    """test for:
    1. lesson without topics
    2. lesson with finished topics
    3. lesson without topics, but user with topics"""
    lesson = create_lesson(teacher, student, meetup, dropoff, datetime.utcnow())
    auth.login(email=teacher.user.email)
    resp = requester.get(f"/lessons/{lesson.id}/topics")
    assert resp.json["data"][0]["id"] == topic.id
    another_topic = Topic.create(
        title="test3", min_lesson_number=20, max_lesson_number=22
    )
    requester.post(
        f"/lessons/{lesson.id}/topics",
        json={"topics": {"progress": [another_topic.id], "finished": [topic.id]}},
    )
    resp = requester.get(f"/lessons/{lesson.id}/topics")
    assert another_topic.id in [topic["id"] for topic in resp.json["data"]]

    another_lesson = create_lesson(teacher, student, meetup, dropoff, datetime.utcnow())
    resp = requester.get(f"/lessons/{another_lesson.id}/topics")
    assert another_topic.id == resp.json["data"][0]["id"]
    assert len(resp.json["data"]) == 1


def test_new_lesson_topics(
    auth, requester, student, meetup, dropoff, topic, lesson, teacher
):
    auth.login(email=teacher.user.email)
    resp = requester.get(f"/lessons/0/topics")
    assert "Lesson does not exist" in resp.json["message"]
    resp = requester.get(f"/lessons/0/topics?student_id=1000")
    assert "Lesson does not exist" in resp.json["message"]
    another_topic = Topic.create(
        title="test3", min_lesson_number=20, max_lesson_number=22
    )
    requester.post(
        f"/lessons/{lesson.id}/topics",
        json={"topics": {"progress": [another_topic.id], "finished": [topic.id]}},
    )
    requester.get(f"/lessons/{lesson.id}/topics")
    resp = requester.get(f"/lessons/0/topics?student_id={student.id}")
    assert resp.json["data"][0]["id"] == another_topic.id

