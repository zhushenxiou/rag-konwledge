"""初始化数据库：创建 rag_kb（及测试库）并启用 pgvector 扩展。

用法:
    conda run -n langchain python scripts/init_db.py
之后执行:
    conda run -n langchain alembic upgrade head
"""
import sys
from urllib.parse import urlparse

import psycopg2
from psycopg2 import sql

sys.path.insert(0, ".")
from app.config import settings  # noqa: E402


def _info(url: str) -> dict:
    u = urlparse(url)
    return {
        "host": u.hostname or "localhost",
        "port": u.port or 5432,
        "user": u.username or "postgres",
        "password": u.password or "",
        "dbname": (u.path or "/postgres")[1:] or "postgres",
    }


def ensure_db(admin_conn, dbname: str) -> None:
    admin_conn.autocommit = True
    with admin_conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
        if cur.fetchone() is None:
            cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
            print(f"[init_db] created database: {dbname}")
        else:
            print(f"[init_db] database exists: {dbname}")


def ensure_extension(info: dict) -> None:
    conn = psycopg2.connect(**info)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute("SELECT extversion FROM pg_extension WHERE extname='vector'")
        version = cur.fetchone()[0]
        print(f"[init_db] pgvector ready on {info['dbname']}: v{version}")
    conn.close()


def main() -> None:
    urls = [settings.database_url]
    if settings.test_database_url:
        urls.append(settings.test_database_url)

    admin = _info(urls[0])
    admin["dbname"] = "postgres"  # 连接到管理库
    admin_conn = psycopg2.connect(**admin)
    try:
        for url in urls:
            info = _info(url)
            ensure_db(admin_conn, info["dbname"])
            ensure_extension(info)
    finally:
        admin_conn.close()

    print("[init_db] 完成。下一步执行:  conda run -n langchain alembic upgrade head")


if __name__ == "__main__":
    main()
