from sqlalchemy.dialects.mysql import LONGTEXT

from datetime import datetime

from .extensions import db

class Project(db.Model):
    """作品（プロジェクト）"""

    __tablename__ = "projects"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    synopsis = db.Column(db.Text, nullable=True)  # あらすじ
    genre = db.Column(db.String(100), nullable=True)
    # 禁止事項・既知の事実（例：「主人公が犯人だと悟らせない」「被害者の死亡推定時刻は22時」など）
    # AI生成のたびに毎回プロンプトへ差し込まれる
    constraints = db.Column(db.Text, nullable=True)
    # 人物相関図のキャッシュ（JSON文字列）。関連コンテンツの更新有無を判定するため生成日時も保持する。
    relationship_diagram_json = db.Column(db.Text, nullable=True)
    relationship_diagram_generated_at = db.Column(db.DateTime, nullable=True)
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

    # サムネイル画像（サーバー側のディスクにファイルとして保存し、ファイル名のみDBに持たせる）
    thumbnail_filename = db.Column(db.String(300), nullable=True)

    # NULL = 通常のメインキャスト（キャラクター管理一覧に表示）
    # 値あり = その事件専用のモブキャラ（キャラクター管理一覧には表示せず、事件詳細から管理する）
    mystery_case_id = db.Column(db.Integer, db.ForeignKey("mystery_cases.id"), nullable=True)

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

    # 本文（旧: Markdownファイル保存 → Renderのディスク消失問題によりDB直接保存に変更）
    # MySQLではTextの既定がTEXT型（最大64KB）になり長編の章で不足する可能性があるため、
    # MySQLのときだけLONGTEXTを使うよう明示する
    content = db.Column(db.Text().with_variant(LONGTEXT, "mysql"), nullable=True)
    
    # 本文はMarkdownファイルとして保存し、DBには相対パスのみ保持する
    # →旧仕様の名残。互換性のため残すが、新規書き込みはしない（将来的に削除可）
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


class MysteryCase(db.Model):
    """事件（ミステリー・トリック生成モジュール）

    1作品に複数の事件（章単位のミステリー）を持てる。真相（trick_json）は秘匿情報のため、
    通常の章生成プロンプトへは絶対に自動注入しない（app/ai_service.pyのbuild_prompt()を参照）。
    """

    __tablename__ = "mystery_cases"

    STATUS_DRAFT = "draft"
    STATUS_TRICK_READY = "trick_ready"
    STATUS_ENVIRONMENT_READY = "environment_ready"
    STATUS_EVALUATED = "evaluated"

    CAST_STATUS_PENDING = "pending"
    CAST_STATUS_COMPLETE = "complete"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)

    title = db.Column(db.String(200), nullable=False)  # 事件名（例：離島館連続殺人事件）

    # --- Phase 1: ユーザー入力（構想） ---
    case_world_setting = db.Column(db.Text, nullable=True)  # 事件固有の舞台設定（補足）
    climax_twist = db.Column(db.Text, nullable=True)  # 絶対条件・オチ
    fixed_role_hints_json = db.Column(db.Text, nullable=True)
    # 任意。「探偵役はこのキャラクターに固定したい」等、強い希望がある役割のみユーザーが指定。
    # [{"character_id": 12, "role": "detective"}]

    # --- Phase 2: トリック（真相）+ 抽象配役表 ---
    trick_json = db.Column(db.Text, nullable=True)  # 真相・矛盾セットを含む秘匿情報
    required_cast_json = db.Column(db.Text, nullable=True)
    # トリックが要求する役割の一覧（配役前は抽象状態）。
    # fixed_character_id はPhase1から引き継いだ確定分。
    # assigned_character_id はPhase2.5（配役）で確定するまではnull。

    # --- Phase 2.5: 配役（キャスティング）確定結果 ---
    cast_status = db.Column(db.String(20), default=CAST_STATUS_PENDING)
    # required_cast_jsonの各要素のassigned_character_idを埋めていく形で記録するため、
    # 別テーブルは持たずrequired_cast_jsonを正とする。

    # --- Phase 3: 環境・小道具 ---
    environment_json = db.Column(db.Text, nullable=True)  # timeline / location / weather / items

    # --- Phase 5: 判定結果 ---
    latest_evaluation_json = db.Column(db.Text, nullable=True)
    latest_evaluation_source = db.Column(db.String(20), nullable=True)  # "chapter" | "interrogation"
    latest_evaluation_target_chapter_id = db.Column(
        db.Integer, db.ForeignKey("chapters.id"), nullable=True
    )

    # 章との紐付け（この事件がどの章で発生するか。任意・未設定可。常にChapter.id単位で参照する）
    trigger_chapter_id = db.Column(db.Integer, db.ForeignKey("chapters.id"), nullable=True)
    resolution_chapter_id = db.Column(db.Integer, db.ForeignKey("chapters.id"), nullable=True)

    status = db.Column(db.String(20), default=STATUS_DRAFT)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    project = db.relationship("Project", backref="mystery_cases")

    # この事件専用のモブキャラ一覧（Character.mystery_case_idの逆参照）。
    # 削除時はルート側で明示的に連動削除する（サムネイルファイルの削除も伴うため、
    # ORMのcascadeには任せない）。
    mob_characters = db.relationship(
        "Character", backref="mystery_case", lazy=True, foreign_keys="Character.mystery_case_id"
    )

    trigger_chapter = db.relationship("Chapter", foreign_keys=[trigger_chapter_id])
    resolution_chapter = db.relationship("Chapter", foreign_keys=[resolution_chapter_id])
    latest_evaluation_target_chapter = db.relationship(
        "Chapter", foreign_keys=[latest_evaluation_target_chapter_id]
    )

    def __repr__(self):
        return f"<MysteryCase {self.id} {self.title} ({self.status})>"
