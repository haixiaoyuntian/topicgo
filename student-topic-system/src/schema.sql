-- 学生选题系统数据库脚本（SQLite）
-- 所有表统一包含 CREATED_BY / CREATED_AT / UPDATED_BY / UPDATED_AT / FLAG 五个公共字段

CREATE TABLE IF NOT EXISTS USERS (
    ID             INTEGER PRIMARY KEY AUTOINCREMENT,
    USER_NO        TEXT NOT NULL UNIQUE,              -- 学号/工号
    USER_NAME      TEXT NOT NULL UNIQUE,              -- 登录名
    PASSWORD_HASH  TEXT NOT NULL,                     -- 密码哈希
    REAL_NAME      TEXT NOT NULL,
    CLASS_NAME     TEXT,
    GPA            REAL DEFAULT 0.0,                  -- 绩点
    ROLE           TEXT NOT NULL DEFAULT 'student',   -- student / teacher
    DEPT           TEXT,
    CREATED_BY     TEXT,
    CREATED_AT     TEXT,
    UPDATED_BY     TEXT,
    UPDATED_AT     TEXT,
    FLAG           INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS TOPICS (
    ID             INTEGER PRIMARY KEY AUTOINCREMENT,
    TOPIC_CODE     TEXT NOT NULL UNIQUE,              -- 题目编号 T01...
    TOPIC_NAME     TEXT NOT NULL,
    TOPIC_DESC     TEXT,
    TAG            TEXT,
    DIFFICULTY     TEXT DEFAULT '中等',                -- 较易/中等/较难
    MAX_NUM        INTEGER NOT NULL DEFAULT 4,        -- 人数上限
    TEACHER_ID     INTEGER,
    STATUS         TEXT DEFAULT 'open',               -- open / closed
    CREATED_BY     TEXT,
    CREATED_AT     TEXT,
    UPDATED_BY     TEXT,
    UPDATED_AT     TEXT,
    FLAG           INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS ROUNDS (
    ID             INTEGER PRIMARY KEY AUTOINCREMENT,
    ROUND_NAME     TEXT NOT NULL,
    RULE           TEXT DEFAULT 'first_come',         -- first_come / gpa_priority
    START_AT       TEXT,
    END_AT         TEXT,
    WAITLIST_ON    INTEGER DEFAULT 1,                 -- 候补开关
    STATUS         TEXT DEFAULT 'open',               -- open / closed
    CREATED_BY     TEXT,
    CREATED_AT     TEXT,
    UPDATED_BY     TEXT,
    UPDATED_AT     TEXT,
    FLAG           INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS SELECTIONS (
    ID             INTEGER PRIMARY KEY AUTOINCREMENT,
    ROUND_ID       INTEGER NOT NULL,
    TOPIC_ID       INTEGER NOT NULL,
    USER_ID        INTEGER NOT NULL,
    STATUS         TEXT NOT NULL DEFAULT 'confirmed', -- confirmed / waiting / cancelled
    QUEUE_NO       INTEGER,                           -- 候补序号
    SELECTED_AT    TEXT,
    CANCELLED_AT   TEXT,
    CREATED_BY     TEXT,
    CREATED_AT     TEXT,
    UPDATED_BY     TEXT,
    UPDATED_AT     TEXT,
    FLAG           INTEGER DEFAULT 1
);

-- 同一轮次中，一个用户只能有一条 confirmed 记录（并发防重双保险）
CREATE UNIQUE INDEX IF NOT EXISTS UK_SELECTION_USER
    ON SELECTIONS (ROUND_ID, USER_ID) WHERE STATUS = 'confirmed';

CREATE INDEX IF NOT EXISTS IDX_SELECTION_TOPIC ON SELECTIONS (TOPIC_ID, STATUS);

CREATE TABLE IF NOT EXISTS ANNOUNCEMENTS (
    ID             INTEGER PRIMARY KEY AUTOINCREMENT,
    TITLE          TEXT NOT NULL,
    CONTENT        TEXT,
    PUBLISHED_AT   TEXT,
    CREATED_BY     TEXT,
    CREATED_AT     TEXT,
    UPDATED_BY     TEXT,
    UPDATED_AT     TEXT,
    FLAG           INTEGER DEFAULT 1
);
