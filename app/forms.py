from flask_wtf import FlaskForm
from wtforms import IntegerField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional


class ProjectForm(FlaskForm):
    """作品情報フォーム"""

    title = StringField(
        "プロジェクト名", validators=[DataRequired(message="プロジェクト名は必須です。"), Length(max=200)]
    )
    genre = StringField("ジャンル", validators=[Optional(), Length(max=100)])
    synopsis = TextAreaField("あらすじ", validators=[Optional()])


class CharacterForm(FlaskForm):
    """キャラクターフォーム"""

    name = StringField(
        "名前", validators=[DataRequired(message="キャラクター名は必須です。"), Length(max=100)]
    )
    age = StringField("年齢", validators=[Optional(), Length(max=20)])
    gender = StringField("性別", validators=[Optional(), Length(max=20)])
    personality = TextAreaField("性格", validators=[Optional()])
    appearance = TextAreaField("外見", validators=[Optional()])
    background = TextAreaField("背景設定", validators=[Optional()])
    notes = TextAreaField("備考", validators=[Optional()])


class WorldSettingForm(FlaskForm):
    """世界設定フォーム"""

    name = StringField("設定名", validators=[DataRequired(message="設定名は必須です。"), Length(max=100)])
    world_view = TextAreaField("世界設定", validators=[Optional()])
    era = TextAreaField("時代背景", validators=[Optional()])
    rules = TextAreaField("ルール", validators=[Optional()])
    terminology = TextAreaField("用語", validators=[Optional()])
    other = TextAreaField("その他設定", validators=[Optional()])


class ChapterForm(FlaskForm):
    """章フォーム（本文以外のメタ情報）"""

    chapter_number = IntegerField(
        "章番号", validators=[DataRequired(message="章番号は必須です。")]
    )
    title = StringField(
        "タイトル", validators=[DataRequired(message="タイトルは必須です。"), Length(max=200)]
    )
    summary = TextAreaField("要約", validators=[Optional()])
    goal = TextAreaField("この章で達成すべき目的", validators=[Optional()])


class ChapterContentForm(FlaskForm):
    """章本文(Markdown)編集フォーム"""

    content = TextAreaField("本文(Markdown)", validators=[Optional()])


class ForeshadowingForm(FlaskForm):
    """伏線フォーム"""

    title = StringField(
        "タイトル", validators=[DataRequired(message="タイトルは必須です。"), Length(max=200)]
    )
    plant_content = TextAreaField("配置内容", validators=[Optional()])
    payoff_content = TextAreaField("回収内容", validators=[Optional()])
    plant_chapter_id = SelectField("配置章", coerce=int, validators=[Optional()])
    payoff_chapter_id = SelectField("回収章", coerce=int, validators=[Optional()])
    status = SelectField(
        "状態",
        choices=[
            ("pending", "未回収"),
            ("processing", "処理中（予定）"),
            ("completed", "回収済み"),
            ("failed", "失敗（予定）"),
        ],
        validators=[DataRequired()],
    )
