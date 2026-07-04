from datetime import datetime

from .extensions import db


class Project(db.Model):
    """作品（プロジェクト）"""

    __tablename__ = "projects"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    synopsis = db.Column(db.Text, nullable=True)  # あらすじ
    genre = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    characters = db.relationship(
        "Character", backref="project", cascade="all, delete-orphan", lazy=True
    )
    world_settings = db.relationship(
        "WorldSetting", backref="project", cascade="all, delete-orphan", lazy=True
    )
    chapters = db.relationship(
        "Chapter",
        backref="project",
        cascade="all, delete-orphan",
        lazy=True,
        order_by="Chapter.chapter_number",
    )
    foreshadowings = db.relationship(
        "Foreshadowing", backref="project", cascade="all, delete-orphan", lazy=True
    )

    def __repr__(self):
        return f"<Project {self.id} {self.title}>"


class Character(db.Model):
    """キャラクター"""

    __tablename__ = "characters"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)

    name = db.Column(db.String(100), nullable=False)
    age = db.Column(db.String(20), nullable=True)  # 「不明」等の文字列も許容
    gender = db.Column(db.String(20), nullable=True)
    personality = db.Column(db.Text, nullable=True)
    appearance = db.Column(db.Text, nullable=True)
    background = db.Column(db.Text, nullable=True)
    notes = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<Character {self.id} {self.name}>"


class WorldSetting(db.Model):
    """世界設定"""

    __tablename__ = "world_settings"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)

    name = db.Column(db.String(100), nullable=False)  # 設定名（例：異界駅）
    world_view = db.Column(db.Text, nullable=True)  # 世界設定
    era = db.Column(db.Text, nullable=True)  # 時代背景
    rules = db.Column(db.Text, nullable=True)  # ルール
    terminology = db.Column(db.Text, nullable=True)  # 用語
    other = db.Column(db.Text, nullable=True)  # その他設定

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<WorldSetting {self.id} {self.name}>"


class Chapter(db.Model):
    """章"""

    __tablename__ = "chapters"

    STATUS_PENDING = "pending"
    STATUS_PROCESSING = "processing"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)

    chapter_number = db.Column(db.Integer, nullable=False)
    title = db.Column(db.String(200), nullable=False)
    summary = db.Column(db.Text, nullable=True)
    goal = db.Column(db.Text, nullable=True)  # この章で達成すべき目的

    # 本文はMarkdownファイルとして保存し、DBには相対パスのみ保持する
    content_path = db.Column(db.String(300), nullable=True)

    # AIシナリオ生成のステータス（未生成の場合はNULL）
    generation_status = db.Column(db.String(20), nullable=True)
    generation_error = db.Column(db.Text, nullable=True)
    generation_provider = db.Column(db.String(20), nullable=True)  # openai / gemini / claude / ollama

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<Chapter {self.id} #{self.chapter_number} {self.title}>"


class Foreshadowing(db.Model):
    """伏線"""

    __tablename__ = "foreshadowings"

    STATUS_PENDING = "pending"
    STATUS_PROCESSING = "processing"  # 予定
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"  # 予定

    STATUS_CHOICES = [
        (STATUS_PENDING, "未回収"),
        (STATUS_PROCESSING, "処理中（予定）"),
        (STATUS_COMPLETED, "回収済み"),
        (STATUS_FAILED, "失敗（予定）"),
    ]

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)

    title = db.Column(db.String(200), nullable=False)
    plant_content = db.Column(db.Text, nullable=True)  # 配置内容
    payoff_content = db.Column(db.Text, nullable=True)  # 回収内容

    plant_chapter_id = db.Column(db.Integer, db.ForeignKey("chapters.id"), nullable=True)
    payoff_chapter_id = db.Column(db.Integer, db.ForeignKey("chapters.id"), nullable=True)

    status = db.Column(db.String(20), nullable=False, default=STATUS_PENDING)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    plant_chapter = db.relationship("Chapter", foreign_keys=[plant_chapter_id])
    payoff_chapter = db.relationship("Chapter", foreign_keys=[payoff_chapter_id])

    def __repr__(self):
        return f"<Foreshadowing {self.id} {self.title} ({self.status})>"
