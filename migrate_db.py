"""
既存のDB（SQLite/MySQLどちらでも可）を、最新のアプリのスキーマに合わせて更新するスクリプト。

【2026-08 変更】
Render.comのディスクがエフェメラルであることが原因で、章本文(Markdownファイル)が
スリープ・再デプロイのたびに消えてしまう問題への対応として、chapters.content カラムを追加し、
本文をファイルではなくDBに直接保存する方式に変更した。これに伴い、本スクリプトも
SQLite専用のsqlite3モジュール直叩きから、SQLAlchemy経由（DATABASE_URLに従う）に書き換えている。
DATABASE_URL が未設定ならこれまで通りローカルのSQLiteを対象にする。

このスクリプトは、既に存在するカラムはスキップし、不足しているものだけを
ALTER TABLE で追加する。何度実行しても安全（データは消えない）。

使い方:
    # ローカル（SQLite、.envのDATABASE_URL未設定 or sqlite指定）
    python migrate_db.py

    # 本番（MySQL）に対して実行する場合は、DATABASE_URL環境変数に
    # Render/coreserverのMySQL接続文字列をセットしてから実行する
    #   例（Windows PowerShell）:
    #     $env:DATABASE_URL = "mysql+pymysql://user:password@host:3306/dbname"
    #     python migrate_db.py
    #   例（Renderのshellから直接実行する場合はDATABASE_URLは既にセットされているのでそのままでOK）
    #     python migrate_db.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv

# config.py の import 前に .env を読み込む。これをしないと DATABASE_URL が
# 環境変数に反映されず、config.py のフォールバックでローカルSQLiteが選ばれてしまう。
load_dotenv()

import ssl

from sqlalchemy import create_engine, inspect, text

from config import Config


def _build_connect_args(dialect: str) -> dict:
    """TiDB Cloud（Serverless Tier）など、TLS必須のMySQL系サービス向けに
    SSLコンテキストを明示的に渡す。

    接続URLのクエリパラメータ（?ssl_ca=...等）はSQLAlchemyのバージョンによって
    解釈がぶれやすいため、コード側で確実にTLSを有効化する。
    system標準の信頼済みCA証明書ストアを使うため、TiDB Cloud側の証明書
    （Let's Encrypt発行）は追加設定なしで検証できる。
    """
    if dialect != "mysql":
        return {}

    try:
        import certifi

        ssl_context = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        # certifi未インストールでも、OS標準のCAストアで代用してTLSは有効化する
        ssl_context = ssl.create_default_context()

    return {"ssl": ssl_context}

# (テーブル名, カラム名, SQLite用の型, MySQL用の型)
COLUMNS_TO_ENSURE = [
    ("projects", "constraints", "TEXT", "TEXT"),
    ("projects", "relationship_diagram_json", "TEXT", "TEXT"),
    ("projects", "relationship_diagram_generated_at", "DATETIME", "DATETIME"),
    ("chapters", "generation_status", "VARCHAR(20)", "VARCHAR(20)"),
    ("chapters", "generation_error", "TEXT", "TEXT"),
    ("chapters", "generation_provider", "VARCHAR(20)", "VARCHAR(20)"),
    ("chapters", "content", "TEXT", "LONGTEXT"),  # ★今回追加：本文をDBへ直接保存するため
    ("characters", "thumbnail_filename", "VARCHAR(300)", "VARCHAR(300)"),
    ("characters", "mystery_case_id", "INTEGER", "INTEGER"),
]


def main() -> None:
    import os

    if not os.environ.get("DATABASE_URL"):
        print("[警告] 環境変数 DATABASE_URL が見つかりません。.env の場所を確認してください。")
        print("       このまま続行すると、ローカルのSQLiteが対象になります。")

    db_uri = Config.SQLALCHEMY_DATABASE_URI
    # 一旦ダイアレクトだけ知るための仮エンジン（接続はしない）
    dialect = create_engine(db_uri).dialect.name  # "sqlite" または "mysql"
    connect_args = _build_connect_args(dialect)
    engine = create_engine(db_uri, connect_args=connect_args)

    print(f"対象DB: {engine.url!r} (dialect={dialect}, TLS={'有効' if connect_args else '無効/不要'})")

    inspector = inspect(engine)

    with engine.begin() as conn:
        for table, column, sqlite_type, mysql_type in COLUMNS_TO_ENSURE:
            try:
                existing_columns = {c["name"] for c in inspector.get_columns(table)}
            except Exception as exc:
                print(f"[エラー] テーブル {table} の情報取得に失敗しました: {exc}")
                continue

            if column in existing_columns:
                print(f"[スキップ] {table}.{column} は既に存在します")
                continue

            coltype = mysql_type if dialect == "mysql" else sqlite_type
            try:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}"))
                print(f"[追加しました] {table}.{column} ({coltype})")
            except Exception as exc:
                print(f"[エラー] {table}.{column} の追加に失敗しました: {exc}")

    print("完了しました。")


if __name__ == "__main__":
    main()
