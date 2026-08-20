import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


class Config:
    """アプリ全体の設定。環境変数から読み込む。"""

    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")

    # .envでDATABASE_URLをmysql+pymysql://...に設定すればMySQLに接続される。
    # 未設定時はこれまで通りSQLiteにフォールバックする（開発時の後方互換のため）。
    _default_db_uri = f"sqlite:///{BASE_DIR / 'instance' / 'ai_story_studio.db'}"
    _env_db_url = os.environ.get("DATABASE_URL")
    if _env_db_url and _env_db_url.startswith("sqlite:///") and not _env_db_url.startswith("sqlite:////"):
        # 相対パス指定の場合はBASE_DIR基準の絶対パスに変換する（cwd依存の接続失敗を防ぐため）
        relative_part = _env_db_url[len("sqlite:///"):]
        _env_db_url = f"sqlite:///{(BASE_DIR / relative_part).resolve()}"
    SQLALCHEMY_DATABASE_URI = _env_db_url or _default_db_uri
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # TiDB Cloud等、TLS接続が必須なMySQL互換DBを使う場合のCA証明書パス。
    # .envのDB_SSL_CAに相対パス（例: certs/tidb_ca.pem）を指定すればBASE_DIR基準で解決する。
    # 未設定（SQLite利用時など）はSSLなしで接続する。
    SQLALCHEMY_ENGINE_OPTIONS = {}
    _db_ssl_ca = os.environ.get("DB_SSL_CA")
    if _db_ssl_ca:
        _db_ssl_ca_path = Path(_db_ssl_ca)
        if not _db_ssl_ca_path.is_absolute():
            _db_ssl_ca_path = (BASE_DIR / _db_ssl_ca_path).resolve()
        SQLALCHEMY_ENGINE_OPTIONS["connect_args"] = {"ssl": {"ca": str(_db_ssl_ca_path)}}

    # 章本文(Markdown)の保存先ディレクトリ
    CHAPTER_MD_DIR = Path(
        os.environ.get("CHAPTER_MD_DIR", BASE_DIR / "data" / "chapters")
    )

    # キャラクターサムネイル画像の保存先ディレクトリ（サーバー側のローカルディスク）
    CHARACTER_THUMBNAIL_DIR = Path(
        os.environ.get("CHARACTER_THUMBNAIL_DIR", BASE_DIR / "data" / "character_thumbnails")
    )
    CHARACTER_THUMBNAIL_MAX_SIZE_MB = int(os.environ.get("CHARACTER_THUMBNAIL_MAX_SIZE_MB", "5"))

    # AIシナリオ生成機能：利用するプロバイダと各種設定
    # PROVIDER: "openai" / "gemini" / "claude" / "ollama"
    DEFAULT_AI_PROVIDER = os.environ.get("DEFAULT_AI_PROVIDER", "gemini")

    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
    OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
    GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")

    # Ollama（ローカル実行）。APIキーは不要、エンドポイントのみ指定する。
    OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1")

    # 1回のAI呼び出しで生成できる最大トークン数（4プロバイダ共通）。
    # 章の分量を長くしたい場合はこの値を大きくする。
    # 目安：日本語は1トークン≒1〜2文字程度。20000トークンで日本語1万〜2万字程度。
    # モデル・プロバイダ側の上限を超える値を指定した場合はエラーになるので、
    # 大きくしすぎた場合は利用するモデルの仕様を確認すること。
    AI_MAX_OUTPUT_TOKENS = int(os.environ.get("AI_MAX_OUTPUT_TOKENS", "8192"))

    WTF_CSRF_ENABLED = True

    # エラーログの保存先ディレクトリ
    LOG_DIR = Path(os.environ.get("LOG_DIR", BASE_DIR / "data" / "logs"))
