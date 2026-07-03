import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


class Config:
    """アプリ全体の設定。環境変数から読み込む。"""

    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")

    _default_db_uri = f"sqlite:///{BASE_DIR / 'instance' / 'ai_story_studio.db'}"
    _env_db_url = os.environ.get("DATABASE_URL")
    if _env_db_url and _env_db_url.startswith("sqlite:///") and not _env_db_url.startswith("sqlite:////"):
        # 相対パス指定の場合はBASE_DIR基準の絶対パスに変換する（cwd依存の接続失敗を防ぐため）
        relative_part = _env_db_url[len("sqlite:///"):]
        _env_db_url = f"sqlite:///{(BASE_DIR / relative_part).resolve()}"
    SQLALCHEMY_DATABASE_URI = _env_db_url or _default_db_uri
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # 章本文(Markdown)の保存先ディレクトリ
    CHAPTER_MD_DIR = Path(
        os.environ.get("CHAPTER_MD_DIR", BASE_DIR / "data" / "chapters")
    )

    # OpenAI API連携（AIシナリオ生成機能用）
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
    OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    WTF_CSRF_ENABLED = True
