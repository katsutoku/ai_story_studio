"""
既存の instance/ai_story_studio.db を、最新のアプリのスキーマに合わせて更新するスクリプト。

これまでの機能追加で、以下のカラムが不足している可能性があります。
  - projects.constraints
  - projects.relationship_diagram_json
  - projects.relationship_diagram_generated_at
  - chapters.generation_status
  - chapters.generation_error
  - chapters.generation_provider
  - characters.thumbnail_filename
  - characters.mystery_case_id

このスクリプトは、既に存在するカラムはスキップし、不足しているものだけを
ALTER TABLE で追加します。何度実行しても安全です（データは消えません）。

なお、mystery_cases テーブル自体（ミステリー・トリック生成モジュール）は新規テーブルのため、
python run.py を実行すれば db.create_all() により自動的に作成されます
（このスクリプトでの対応は不要です。CLAUDE.md「DBマイグレーションは手動」の方針どおり、
既存テーブルへのカラム追加のみをこのスクリプトで扱います）。

使い方（Windows）:
    1. このファイル（migrate_db.py）を、AI Story Studioのプロジェクトフォルダ
       （run.py や config.py がある場所）に置く
    2. コマンドプロンプトまたはPowerShellでそのフォルダに移動する
       例: cd C:\\Users\\あなたの名前\\ai_story_studio
    3. 次のコマンドを実行する
       python migrate_db.py
    4. 「追加しました」「スキップ」等のログが表示されれば完了
"""
import sqlite3
from pathlib import Path

# DBファイルのパス（.envでDATABASE_URLを変更していない場合はこのままでOK）
DB_PATH = Path(__file__).resolve().parent / "instance" / "ai_story_studio.db"

# (テーブル名, カラム名, 型) のリスト
COLUMNS_TO_ENSURE = [
    ("projects", "constraints", "TEXT"),
    ("projects", "relationship_diagram_json", "TEXT"),
    ("projects", "relationship_diagram_generated_at", "DATETIME"),
    ("chapters", "generation_status", "VARCHAR(20)"),
    ("chapters", "generation_error", "TEXT"),
    ("chapters", "generation_provider", "VARCHAR(20)"),
    ("characters", "thumbnail_filename", "VARCHAR(300)"),
    ("characters", "mystery_case_id", "INTEGER"),
]


def main() -> None:
    if not DB_PATH.exists():
        print(f"DBファイルが見つかりません: {DB_PATH}")
        print("先に一度 python run.py を実行してDBを作成してから、再度このスクリプトを実行してください。")
        return

    print(f"対象DB: {DB_PATH}")
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    for table, column, coltype in COLUMNS_TO_ENSURE:
        cur.execute(f"PRAGMA table_info({table})")
        existing_columns = {row[1] for row in cur.fetchall()}

        if column in existing_columns:
            print(f"[スキップ] {table}.{column} は既に存在します")
            continue

        try:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
            print(f"[追加しました] {table}.{column} ({coltype})")
        except sqlite3.OperationalError as exc:
            print(f"[エラー] {table}.{column} の追加に失敗しました: {exc}")

    conn.commit()
    conn.close()
    print("完了しました。")


if __name__ == "__main__":
    main()
