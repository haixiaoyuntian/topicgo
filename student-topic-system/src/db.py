"""数据库连接、初始化与种子数据"""
import os
import sqlite3
from datetime import datetime

from werkzeug.security import generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "topicgo.db")


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_conn():
    """返回连接：WAL 模式 + 立即事务，保证并发写安全"""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.isolation_level = None  # 手动控制事务
    return conn


def init_db():
    with open(os.path.join(BASE_DIR, "schema.sql"), encoding="utf-8") as f:
        schema = f.read()
    conn = get_conn()
    conn.executescript(schema)
    if conn.execute("SELECT COUNT(*) c FROM USERS").fetchone()["c"] == 0:
        seed(conn)
    conn.close()


def seed(conn):
    ts = now_str()
    users = [
        ("20230101", "zhangsan", "123456", "张明", "软工2301", 3.8, "student"),
        ("20230102", "lisi", "123456", "李琪", "软工2301", 3.2, "student"),
        ("20230103", "wangwu", "123456", "王浩", "软工2302", 3.9, "student"),
        ("20230104", "zhaoliu", "123456", "赵雪", "软工2302", 3.5, "student"),
        ("20230105", "chenqi", "123456", "陈琦", "软工2301", 2.9, "student"),
        ("T2001", "teacher", "123456", "刘老师", None, 0, "teacher"),
    ]
    for no, name, pwd, real, cls, gpa, role in users:
        conn.execute(
            "INSERT INTO USERS(USER_NO,USER_NAME,PASSWORD_HASH,REAL_NAME,CLASS_NAME,GPA,ROLE,DEPT,CREATED_BY,CREATED_AT,FLAG)"
            " VALUES(?,?,?,?,?,?,?,'软件学院','system',?,1)",
            (no, name, generate_password_hash(pwd), real, cls, gpa, role, ts),
        )
    topics = [
        ("T01", "基于Flask的图书管理系统", "实现图书的增删改查、借阅管理与读者统计", "Web开发", "较易", 3),
        ("T02", "学生选题系统（TopicGo）", "公正公开的课设在线选题平台，支持候补递补", "Web开发", "中等", 3),
        ("T03", "基于YOLOv5的课堂专注度识别", "摄像头实时检测学生课堂行为并统计专注度", "人工智能", "较难", 2),
        ("T04", "电商销售数据分析与可视化", "爬取公开销售数据，完成清洗、分析与大屏展示", "数据分析", "中等", 4),
        ("T05", "区块链五子棋积分结算游戏", "游戏结束时用区块链结算积分，降低链上事务负载", "区块链", "较难", 2),
        ("T06", "校园二手交易小程序", "二手商品发布、聊天议价与线下交付管理", "Web开发", "中等", 4),
    ]
    for code, name, desc, tag, diff, max_num in topics:
        conn.execute(
            "INSERT INTO TOPICS(TOPIC_CODE,TOPIC_NAME,TOPIC_DESC,TAG,DIFFICULTY,MAX_NUM,TEACHER_ID,STATUS,CREATED_BY,CREATED_AT,FLAG)"
            " VALUES(?,?,?,?,?,?,6,'open','system',?,1)",
            (code, name, desc, tag, diff, max_num, ts),
        )
    conn.execute(
        "INSERT INTO ROUNDS(ROUND_NAME,RULE,START_AT,END_AT,WAITLIST_ON,STATUS,CREATED_BY,CREATED_AT,FLAG)"
        " VALUES('2026春季课设选题·第一轮','first_come','2026-03-16 08:00:00','2026-06-30 18:00:00',1,'open','system',?,1)",
        (ts,),
    )
    conn.execute(
        "INSERT INTO ANNOUNCEMENTS(TITLE,CONTENT,PUBLISHED_AT,CREATED_BY,CREATED_AT,FLAG)"
        " VALUES('选题须知','每人限选1题；满员题目可加入候补；退选后名额自动递补给候补首位。',?,'system',?,1)",
        (ts, ts),
    )
    conn.commit()
