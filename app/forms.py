from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField, FileSize
from wtforms import BooleanField, IntegerField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, NumberRange, Optional


class ProjectForm(FlaskForm):
    """作品情報フォーム"""

    title = StringField(
        "プロジェクト名", validators=[DataRequired(message="プロジェクト名は必須です。"), Length(max=200)]
    )
    genre = StringField("ジャンル", validators=[Optional(), Length(max=100)])
    synopsis = TextAreaField("あらすじ", validators=[Optional()])
    constraints = TextAreaField(
        "禁止事項・既知の事実（任意）",
        validators=[Optional()],
        description=(
            "AI生成のたびに毎回自動で反映される。例：「主人公が真犯人であることは"
            "最終章まで読者に悟らせないこと」「被害者Aの死亡推定時刻は22時、主人公は"
            "その時刻は駅前の喫茶店にいたことにする」など"
        ),
    )


class PlotGenerateForm(FlaskForm):
    """AI全体プロット生成フォーム（おまかせ生成モード）"""

    chapter_count = IntegerField(
        "章数",
        default=5,
        validators=[DataRequired(message="章数は必須です。"), NumberRange(min=1, max=20, message="1〜20章の範囲で指定してください。")],
    )
    provider = SelectField(
        "利用するAI",
        choices=[
            ("gemini", "Gemini（Google）"),
            ("openai", "ChatGPT（OpenAI）"),
            ("claude", "Claude（Anthropic）"),
            ("ollama", "Ollama（ローカル実行）"),
        ],
        validators=[DataRequired()],
    )
    additional_notes = TextAreaField(
        "作品への大まかな要望（任意）",
        validators=[Optional()],
        description="例：ヒロインが実は最終章の黒幕とつながっている、というミステリー要素を入れてほしい、など",
    )


class RelationshipGenerateForm(FlaskForm):
    """AI人物相関図生成フォーム"""

    provider = SelectField(
        "利用するAI",
        choices=[
            ("gemini", "Gemini（Google）"),
            ("openai", "ChatGPT（OpenAI）"),
            ("claude", "Claude（Anthropic）"),
            ("ollama", "Ollama（ローカル実行）"),
        ],
        validators=[DataRequired()],
    )
    additional_notes = TextAreaField(
        "追加指示（任意）",
        validators=[Optional()],
        description="例：主人公を中心に、恋愛関係と敵対関係を分けて考えてほしい、など",
    )


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
    thumbnail = FileField(
        "サムネイル画像（任意）",
        validators=[
            FileAllowed(["jpg", "jpeg", "png", "gif", "webp"], "画像ファイル（jpg/png/gif/webp）のみアップロードできます。"),
            FileSize(max_size=5 * 1024 * 1024, message="ファイルサイズは5MB以下にしてください。"),
        ],
    )
    remove_thumbnail = BooleanField("サムネイル画像を削除する")


class CharacterGenerateForm(FlaskForm):
    """AIキャラクター生成フォーム"""

    count = IntegerField(
        "生成する人数",
        default=3,
        validators=[DataRequired(message="人数は必須です。"), NumberRange(min=1, max=10, message="1〜10人の範囲で指定してください。")],
    )
    provider = SelectField(
        "利用するAI",
        choices=[
            ("gemini", "Gemini（Google）"),
            ("openai", "ChatGPT（OpenAI）"),
            ("claude", "Claude（Anthropic）"),
            ("ollama", "Ollama（ローカル実行）"),
        ],
        validators=[DataRequired()],
    )
    additional_notes = TextAreaField(
        "追加指示（任意）",
        validators=[Optional()],
        description="例：主人公の幼馴染となる女性キャラクターを含めてほしい、など",
    )


class WorldSettingForm(FlaskForm):
    """世界設定フォーム"""

    name = StringField("設定名", validators=[DataRequired(message="設定名は必須です。"), Length(max=100)])
    world_view = TextAreaField("世界設定", validators=[Optional()])
    era = TextAreaField("時代背景", validators=[Optional()])
    rules = TextAreaField("ルール", validators=[Optional()])
    terminology = TextAreaField("用語", validators=[Optional()])
    other = TextAreaField("その他設定", validators=[Optional()])


class WorldSettingGenerateForm(FlaskForm):
    """AI世界設定生成フォーム"""

    count = IntegerField(
        "生成する件数",
        default=3,
        validators=[DataRequired(message="件数は必須です。"), NumberRange(min=1, max=10, message="1〜10件の範囲で指定してください。")],
    )
    provider = SelectField(
        "利用するAI",
        choices=[
            ("gemini", "Gemini（Google）"),
            ("openai", "ChatGPT（OpenAI）"),
            ("claude", "Claude（Anthropic）"),
            ("ollama", "Ollama（ローカル実行）"),
        ],
        validators=[DataRequired()],
    )
    additional_notes = TextAreaField(
        "追加指示（任意）",
        validators=[Optional()],
        description="例：現代パートと異界パートの2つの時間軸がある設定にしてほしい、など",
    )


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


class ChapterReviseForm(FlaskForm):
    """AI本文部分修正フォーム"""

    revision_instructions = TextAreaField(
        "修正指示",
        validators=[DataRequired(message="修正指示は必須です。")],
        description="例：被害者を〇〇から△△に変更してください。主人公と被害者が会うのは当日ではなく前日の出来事に変更してください。",
    )
    provider = SelectField(
        "利用するAI",
        choices=[
            ("gemini", "Gemini（Google）"),
            ("openai", "ChatGPT（OpenAI）"),
            ("claude", "Claude（Anthropic）"),
            ("ollama", "Ollama（ローカル実行）"),
        ],
        validators=[DataRequired()],
    )


class InterrogationGenerateForm(FlaskForm):
    """AI尋問シナリオ生成フォーム（探偵役・容疑者役の独立セッション方式）"""

    detective_name = StringField(
        "探偵役の名前", validators=[DataRequired(message="探偵役の名前は必須です。"), Length(max=100)]
    )
    suspect_name = StringField(
        "容疑者役の名前", validators=[DataRequired(message="容疑者役の名前は必須です。"), Length(max=100)]
    )
    public_context = TextAreaField(
        "公開情報（探偵と容疑者、双方が知っている事件の概要）",
        validators=[Optional()],
        description="例：〇〇邸で被害者Aが刺殺された。凶器のナイフが現場から見つかっている。容疑者は事件当夜、邸内にいたと証言している。",
    )
    suspect_secret = TextAreaField(
        "容疑者だけが知っている秘密（DBには保存されません。生成のたびに入力してください）",
        validators=[DataRequired(message="容疑者の秘密は必須です。")],
        description="例：本当は被害者と口論していたが、それを隠すため別の場所にいたと嘘をついている。凶器には触れていない。",
    )
    turn_count = IntegerField(
        "ターン数（探偵の発言1回＋容疑者の返答1回で1ターン）",
        default=10,
        validators=[DataRequired(message="ターン数は必須です。"), NumberRange(min=3, max=20, message="3〜20ターンの範囲で指定してください。")],
    )
    provider = SelectField(
        "利用するAI",
        choices=[
            ("gemini", "Gemini（Google）"),
            ("openai", "ChatGPT（OpenAI）"),
            ("claude", "Claude（Anthropic）"),
            ("ollama", "Ollama（ローカル実行）"),
        ],
        validators=[DataRequired()],
    )


class GenerateChapterForm(FlaskForm):
    """AI章生成フォーム"""

    chapter_number = IntegerField(
        "章番号", validators=[DataRequired(message="章番号は必須です。")]
    )
    title = StringField("タイトル（任意・仮タイトルとして使用）", validators=[Optional(), Length(max=200)])
    provider = SelectField(
        "利用するAI",
        choices=[
            ("gemini", "Gemini（Google）"),
            ("openai", "ChatGPT（OpenAI）"),
            ("claude", "Claude（Anthropic）"),
            ("ollama", "Ollama（ローカル実行）"),
        ],
        validators=[DataRequired()],
    )
    additional_notes = TextAreaField(
        "この章に関する追加指示（任意）",
        validators=[Optional()],
        description="例：この章では〇〇との出会いを描いてほしい、など",
    )
    include_case_environment = BooleanField(
        "この章が関わる事件の客観的事実（タイムライン・場所・天候・小道具）を注入する",
        description="事件の真相そのものではなく、作中で自然に描写してよい情報のみが渡されます（デフォルトOFF）。",
    )
    reveal_case_truth = BooleanField(
        "この章で事件の真相を開示する（解決編に指定した章でのみ有効）",
        description="この章がその事件の「解決編」として紐付けられている場合に限り、真相が注入されます（デフォルトOFF）。",
    )


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


class MysteryCaseForm(FlaskForm):
    """事件Phase1入力フォーム（世界観補足・オチ・固定配役の希望）"""

    title = StringField(
        "事件名", validators=[DataRequired(message="事件名は必須です。"), Length(max=200)]
    )
    case_world_setting = TextAreaField(
        "この事件固有の舞台設定（補足・任意）",
        validators=[Optional()],
        description="作品全体の世界設定とは別に、この事件だけで使う補足情報があれば入力してください。",
    )
    climax_twist = TextAreaField(
        "オチ・絶対条件（任意）",
        validators=[Optional()],
        description=(
            "例：実は被害者は双子で、事件当夜に入れ替わっていた。"
            "空欄の場合、オチ自体もAIが考案します（トリック生成時に自動で決定されます）。"
        ),
    )
    fixed_role_hints_text = TextAreaField(
        "固定配役の希望（任意）",
        validators=[Optional()],
        description=(
            "強い希望がある役割だけ「役割: キャラクター名」の形式で1行ずつ入力してください"
            "（例：detective: 名探偵コナン）。役割はdetective/victim/culprit/suspect/witness/"
            "accomplice/otherのいずれか。指定しない役割はすべてAIが自由に設計します。"
            "キャラクター名はこの作品に登録済みのものと一致させてください。"
        ),
    )


class MysteryGenerateWithProviderForm(FlaskForm):
    """トリック生成・環境生成で共用する、プロバイダ選択のみのフォーム"""

    provider = SelectField(
        "利用するAI",
        choices=[
            ("gemini", "Gemini（Google）"),
            ("openai", "ChatGPT（OpenAI）"),
            ("claude", "Claude（Anthropic）"),
            ("ollama", "Ollama（ローカル実行）"),
        ],
        validators=[DataRequired()],
    )


class MysteryMobGenerateForm(FlaskForm):
    """配役ステップでの、事件専用モブキャラAI生成フォーム"""

    count = IntegerField(
        "生成する候補数",
        default=3,
        validators=[DataRequired(message="候補数は必須です。"), NumberRange(min=1, max=5, message="1〜5件の範囲で指定してください。")],
    )
    provider = SelectField(
        "利用するAI",
        choices=[
            ("gemini", "Gemini（Google）"),
            ("openai", "ChatGPT（OpenAI）"),
            ("claude", "Claude（Anthropic）"),
            ("ollama", "Ollama（ローカル実行）"),
        ],
        validators=[DataRequired()],
    )


class MysteryEvaluateForm(FlaskForm):
    """Phase5 判定AI（矛盾検知）フォーム"""

    source_type = SelectField(
        "検証対象",
        choices=[
            ("chapter", "既存の章本文から選ぶ"),
            ("text", "本文/尋問ログを直接貼り付ける"),
        ],
        validators=[DataRequired()],
    )
    chapter_id = SelectField("対象の章（検証対象が「章本文」の場合）", coerce=int, validators=[Optional()])
    manual_text = TextAreaField(
        "本文/尋問ログを直接貼り付け（検証対象が「直接貼り付け」の場合）", validators=[Optional()]
    )
    provider = SelectField(
        "利用するAI",
        choices=[
            ("gemini", "Gemini（Google）"),
            ("openai", "ChatGPT（OpenAI）"),
            ("claude", "Claude（Anthropic）"),
            ("ollama", "Ollama（ローカル実行）"),
        ],
        validators=[DataRequired()],
    )


class ForeshadowingGenerateForm(FlaskForm):
    """AI伏線生成フォーム"""

    count = IntegerField(
        "生成する件数",
        default=3,
        validators=[DataRequired(message="件数は必須です。"), NumberRange(min=1, max=10, message="1〜10件の範囲で指定してください。")],
    )
    provider = SelectField(
        "利用するAI",
        choices=[
            ("gemini", "Gemini（Google）"),
            ("openai", "ChatGPT（OpenAI）"),
            ("claude", "Claude（Anthropic）"),
            ("ollama", "Ollama（ローカル実行）"),
        ],
        validators=[DataRequired()],
    )
    additional_notes = TextAreaField(
        "追加指示（任意）",
        validators=[Optional()],
        description="例：ヒロインの正体に関わる伏線を1つ含めてほしい、など",
    )
