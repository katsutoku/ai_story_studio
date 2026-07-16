"""
AIシナリオ生成サービス

仕様書 6.「AIシナリオ生成」に対応するモジュール。
プロジェクト情報・キャラクター・世界設定・過去章要約・未回収の伏線をプロンプトとして
組み立て、選択されたAIプロバイダ（openai / gemini / claude / ollama）へ送信して
章本文（Markdown）を生成する。
"""
from __future__ import annotations

from flask import current_app

from .models import Chapter, Foreshadowing, Project

# UIのセレクトボックス等で使うプロバイダ一覧
PROVIDER_CHOICES = [
    ("gemini", "Gemini（Google）"),
    ("openai", "ChatGPT（OpenAI）"),
    ("claude", "Claude（Anthropic）"),
    ("ollama", "Ollama（ローカル実行）"),
]
VALID_PROVIDERS = {key for key, _ in PROVIDER_CHOICES}


class AIGenerationError(Exception):
    """AI生成処理中に発生したエラーを表す例外。

    呼び出し側（ルート）はこの例外を捕捉して、
    章の生成ステータスを failed に更新し、エラーメッセージを表示し、
    再生成可能な状態にする。
    """


def build_prompt(project: Project, target_chapter_number: int, additional_notes: str = "") -> str:
    """プロジェクト情報・キャラクター・世界設定・過去章要約・未回収の伏線から
    次章生成用のプロンプトを組み立てる（プロバイダ非依存の共通処理）。
    """
    lines = [
        "あなたは経験豊富なノベルゲーム／アドベンチャーゲームのシナリオライターです。",
        "以下の設定情報をもとに、新しい章の本文をMarkdown形式で執筆してください。",
        "",
        f"# 作品タイトル: {project.title}",
        f"# ジャンル: {project.genre or '未設定'}",
        f"# あらすじ: {project.synopsis or '未設定'}",
    ]

    if project.constraints:
        lines.append(
            f"\n## 禁止事項・既知の事実（最優先で厳守すること。これに反する記述は絶対に書かないこと）\n"
            f"{project.constraints}"
        )

    lines.append("\n## キャラクター")
    if project.characters:
        for c in project.characters:
            lines.append(
                f"- {c.name}（{c.age or '年齢不明'} / {c.gender or '性別不明'}）"
                f" 性格: {c.personality or '未設定'} / 外見: {c.appearance or '未設定'}"
            )
    else:
        lines.append("- （未登録）")

    lines.append("\n## 世界設定")
    if project.world_settings:
        for w in project.world_settings:
            lines.append(f"- {w.name}: {w.world_view or ''} / 時代背景: {w.era or ''} / ルール: {w.rules or ''}")
    else:
        lines.append("- （未登録）")

    lines.append("\n## これまでの章の要約")
    past_chapters = [
        ch for ch in sorted(project.chapters, key=lambda c: c.chapter_number)
        if ch.chapter_number < target_chapter_number
    ]
    if past_chapters:
        for ch in past_chapters:
            lines.append(f"- 第{ch.chapter_number}章「{ch.title}」: {ch.summary or '要約未登録'}")
    else:
        lines.append("- （これが最初の章です）")

    # 全体プロット機能で章の設計図（タイトル・要約・目的）が事前に作られている場合、
    # 本文執筆時にはその設計図に従わせる（＝プロットと本文の食い違いを防ぐ）
    target_chapter = next(
        (ch for ch in project.chapters if ch.chapter_number == target_chapter_number), None
    )
    if target_chapter and (target_chapter.summary or target_chapter.goal):
        lines.append(f"\n## この章（第{target_chapter_number}章）の設計図（必ず従うこと）")
        lines.append(f"- タイトル: {target_chapter.title}")
        if target_chapter.summary:
            lines.append(f"- 要約: {target_chapter.summary}")
        if target_chapter.goal:
            lines.append(f"- この章で達成すべき目的: {target_chapter.goal}")

    future_chapters = [
        ch for ch in sorted(project.chapters, key=lambda c: c.chapter_number)
        if ch.chapter_number > target_chapter_number and (ch.summary or ch.goal)
    ]
    if future_chapters:
        lines.append(
            "\n## 今後の章の予定（ネタバレとして直接書かないこと。"
            "ただし伏線の仕込み先として活用すること）"
        )
        for ch in future_chapters:
            lines.append(f"- 第{ch.chapter_number}章「{ch.title}」: {ch.summary or ''}")

    lines.append("\n## 未回収の伏線（この章、または今後の章で意識すること）")
    pending = [f for f in project.foreshadowings if f.status == Foreshadowing.STATUS_PENDING]
    if pending:
        for f in pending:
            lines.append(f"- {f.title}: {f.plant_content or ''}")
    else:
        lines.append("- （現在、未回収の伏線はありません）")

    lines.append(
        "\n## 章構成の方針（常に厳守すること）\n"
        "この章は、それ単体で起承転結が完結する小さな事件・エピソードとして執筆してください。"
        "この章の中で発生した出来事は、この章の中で（起：事件発生 → 承：調査・展開 → 転：意外な事実 → "
        "結：この章なりの決着）まで完結させ、続きが気になる引きだけを残して終わらないようにしてください。\n"
        "同時に、この章の出来事・キャラクターの言動・小道具などの中に、物語全体の背景にある大きな謎"
        "（最終章で明かされる真相）につながる手がかりを、読者が今は気づかない程度に、さりげなく1つ以上"
        "紛れ込ませてください。この章の時点では、その手がかりが重要だと分かるように書かないでください。"
    )

    lines.append(
        "\n## セリフのト書き表記（必ず守ること。ゲーム用スクリプトへの変換に使用するため）\n"
        "本文中の会話（セリフ）の行は、必ず行頭に発言者名を【】で囲んで明記し、その直後にセリフを"
        "「」で書いてください。例：【高橋健】「本当にここでいいのか？」\n"
        "発言者名は、その章に登場する実在のキャラクター名を常に使用してください。"
        "地の文（セリフ以外の描写）にはこの表記は不要です。"
    )

    lines.append(
        "\n## 場面情報の記載（必ずこの形式で記述すること。背景画像の判別に使用するため）\n"
        "本文の一番最初（他のどの文章よりも前）に、次の2行を記述してください。\n"
        "■背景:（背景画像の判別に使う短い場所名を1つだけ書く。例：岸壁、教室、駅前。"
        "固有名詞的な短い名詞のみとし、時間帯や状況などの修飾語は含めないこと。"
        "同じ物理的な場所を指す場合は、他の章とも表記を統一すること。"
        "世界設定に該当する地名が登録されている場合はそれを優先して使うこと）\n"
        "■場面:（時間帯や状況を含めた、本文の内容から推測できる場面の呼び名を書く。"
        "例：深夜の岸壁、放課後の教室。こちらは表示・参考用の情報であり、背景画像の判別には使わない）\n"
        "この2行の後に、本文を続けてください。\n"
        "また、この章の中で場面（場所や時間帯）が転換する場合は、その都度、切り替わった直後の"
        "文章ブロックの先頭にも同じ形式で■背景:・■場面:の2行を挿入してください。"
        "1つの章の中に複数の場所・時間帯が登場する場合、場面が変わるたびに必ず新しい2行を"
        "書いてください（章の途中で場所が変わったのに2行を書き忘れる、ということがないようにすること）。"
    )

    if additional_notes:
        lines.append("\n## この章に関する追加指示")
        lines.append(additional_notes)

    lines.append(
        f"\n上記の情報と整合性を保ちながら、第{target_chapter_number}章の本文をMarkdown形式で執筆してください。"
        "本文のみを出力し、前置きや解説文は含めないでください。"
    )
    return "\n".join(lines)


def generate_chapter_content(
    project: Project,
    target_chapter_number: int,
    additional_notes: str = "",
    provider: str = "",
) -> str:
    """選択されたAIプロバイダを呼び出して章本文（Markdown）を生成する。

    provider: "openai" / "gemini" / "claude" / "ollama"。
    未指定の場合は設定のデフォルトプロバイダ（DEFAULT_AI_PROVIDER）を使用する。

    認証エラー・通信エラー・レスポンス不正などが発生した場合は
    AIGenerationError を送出する。呼び出し側で章の生成ステータスを
    failed に更新し、エラーメッセージを表示し、再生成可能にする。
    """
    prompt = build_prompt(project, target_chapter_number, additional_notes)
    return _dispatch(prompt, provider)


def build_revision_prompt(
    project: Project, chapter: Chapter, existing_content: str, revision_instructions: str
) -> str:
    """既存の章本文に対する部分修正用プロンプトを組み立てる。

    ゼロから書き直すのではなく、指示された変更点だけを反映させ、
    それ以外の文章・トーンはできるだけ元のまま維持させることを狙いとする。
    """
    lines = [
        "あなたは経験豊富なノベルゲーム／アドベンチャーゲームのシナリオ編集者です。",
        "以下は、あるノベルゲームの一章として、既に執筆済みの本文です。",
        "この本文に対して、下記の「修正指示」で指定された変更点だけを反映するように書き換えてください。",
        "修正指示で触れられていない部分の展開・文体・トーンは、できる限り元の本文のまま維持してください。",
        "",
        f"# 作品タイトル: {project.title}",
        f"# ジャンル: {project.genre or '未設定'}",
    ]

    if project.constraints:
        lines.append(f"\n## 禁止事項・既知の事実（最優先で厳守すること）\n{project.constraints}")

    lines.append(
        f"\n## 修正対象の本文（第{chapter.chapter_number}章「{chapter.title}」）\n"
        f"```markdown\n{existing_content}\n```"
    )
    lines.append(f"\n## 修正指示（これに従って本文を書き換えること）\n{revision_instructions}")
    lines.append(
        "\n## 場面情報の記載（必ずこの形式で保つこと）\n"
        "本文の一番最初（他のどの文章よりも前）に、次の2行が必要です。\n"
        "■背景:（背景画像の判別に使う短い場所名。固有名詞的な短い名詞のみとし、時間帯や状況の修飾語は含めない）\n"
        "■場面:（時間帯や状況を含めた、内容から推測できる場面の呼び名）\n"
        "さらに、本文中で場面（場所や時間帯）が転換する箇所があれば、その都度、切り替わった直後の"
        "文章ブロックの先頭にも同じ形式で■背景:・■場面:の2行が必要です。\n"
        "修正対象の本文に既にこれらの行がある場合は、修正後の内容と矛盾しないよう必要に応じて更新してください"
        "（例：場所や時間帯が変わる修正をした場合は、該当する2行もそれに合わせて書き換える）。"
        "まだこれらの行がない場合（章の先頭、または途中の場面転換部分）は、本文の内容から推測して"
        "新たに追加してください。"
    )
    lines.append(
        "\n修正後の本文全体をMarkdown形式で出力してください。"
        "本文以外の説明・前置き・「修正しました」等のコメントは一切含めないこと。"
    )
    return "\n".join(lines)


def revise_chapter_content(
    project: Project,
    chapter: Chapter,
    existing_content: str,
    revision_instructions: str,
    provider: str = "",
) -> str:
    """既存の章本文を、指示された変更点だけ反映する形でAIに書き換えさせる。

    generate_chapter_content() がゼロから新しい本文を書くのに対し、
    こちらは既存の本文をプロンプトに含め、部分的な修正を依頼する。
    """
    prompt = build_revision_prompt(project, chapter, existing_content, revision_instructions)
    return _dispatch(prompt, provider)


CHARACTER_JSON_INSTRUCTIONS = (
    "\n出力は必ず次のJSON配列形式のみとし、前置き・説明文・Markdownのコードフェンスは一切含めないこと。\n"
    '[{"name": "名前", "age": "年齢（不明なら空文字）", "gender": "性別", '
    '"personality": "性格", "appearance": "外見", "background": "背景設定", "notes": "備考"}, ...]'
)


def build_character_prompt(project: Project, count: int, additional_notes: str = "") -> str:
    """作品情報と既存キャラクターから、新規キャラクター案をAIに考えさせるプロンプトを組み立てる。"""
    lines = [
        "あなたは経験豊富なノベルゲーム／アドベンチャーゲームのシナリオライターです。",
        f"以下の作品情報をもとに、新しく登場させるキャラクターを{count}人分考えてください。",
        "",
        f"# 作品タイトル: {project.title}",
        f"# ジャンル: {project.genre or '未設定'}",
        f"# あらすじ: {project.synopsis or '未設定'}",
    ]

    if project.constraints:
        lines.append(f"\n## 禁止事項・既知の事実（最優先で厳守すること）\n{project.constraints}")

    if project.characters:
        lines.append("\n## 既に登録済みのキャラクター（名前や役割が重複しないようにすること）")
        for c in project.characters:
            lines.append(f"- {c.name}: {c.personality or ''}")

    if project.world_settings:
        lines.append("\n## 世界設定（キャラクターの背景と整合させること）")
        for w in project.world_settings:
            lines.append(f"- {w.name}: {w.world_view or ''}")

    if additional_notes:
        lines.append("\n## 追加指示")
        lines.append(additional_notes)

    lines.append(CHARACTER_JSON_INSTRUCTIONS)
    return "\n".join(lines)


def generate_characters(
    project: Project, count: int = 3, additional_notes: str = "", provider: str = ""
) -> list[dict]:
    """AIにキャラクター案を考えさせ、パース済みの辞書のリストを返す。

    各要素は {"name", "age", "gender", "personality", "appearance", "background", "notes"} を持つ。
    AIの応答がJSONとして解釈できない場合は AIGenerationError を送出する。
    """
    prompt = build_character_prompt(project, count, additional_notes)
    raw_text = _dispatch(prompt, provider)
    return _parse_json_array(raw_text)


WORLD_SETTING_JSON_INSTRUCTIONS = (
    "\n出力は必ず次のJSON配列形式のみとし、前置き・説明文・Markdownのコードフェンスは一切含めないこと。\n"
    '[{"name": "設定名", "world_view": "世界設定", "era": "時代背景", '
    '"rules": "ルール", "terminology": "用語", "other": "その他設定"}, ...]'
)


def build_world_setting_prompt(project: Project, count: int, additional_notes: str = "") -> str:
    """作品情報と既存の世界設定・キャラクターから、新規の世界設定案をAIに考えさせるプロンプトを組み立てる。"""
    lines = [
        "あなたは経験豊富なノベルゲーム／アドベンチャーゲームのシナリオライターです。",
        f"以下の作品情報をもとに、世界観を構成する設定を{count}件考えてください。",
        "",
        f"# 作品タイトル: {project.title}",
        f"# ジャンル: {project.genre or '未設定'}",
        f"# あらすじ: {project.synopsis or '未設定'}",
    ]

    if project.constraints:
        lines.append(f"\n## 禁止事項・既知の事実（最優先で厳守すること）\n{project.constraints}")

    if project.world_settings:
        lines.append("\n## 既に登録済みの世界設定（内容が重複しないようにすること）")
        for w in project.world_settings:
            lines.append(f"- {w.name}: {w.world_view or ''}")

    if project.characters:
        lines.append("\n## キャラクター（世界設定との整合を意識すること）")
        for c in project.characters:
            lines.append(f"- {c.name}: {c.background or c.personality or ''}")

    if additional_notes:
        lines.append("\n## 追加指示")
        lines.append(additional_notes)

    lines.append(WORLD_SETTING_JSON_INSTRUCTIONS)
    return "\n".join(lines)


def generate_world_settings(
    project: Project, count: int = 3, additional_notes: str = "", provider: str = ""
) -> list[dict]:
    """AIに世界設定案を考えさせ、パース済みの辞書のリストを返す。

    各要素は {"name", "world_view", "era", "rules", "terminology", "other"} を持つ。
    """
    prompt = build_world_setting_prompt(project, count, additional_notes)
    raw_text = _dispatch(prompt, provider)
    return _parse_json_array(raw_text)


FORESHADOWING_JSON_INSTRUCTIONS = (
    "\n出力は必ず次のJSON配列形式のみとし、前置き・説明文・Markdownのコードフェンスは一切含めないこと。\n"
    '[{"title": "伏線のタイトル", "plant_content": "配置内容（何をどのように示唆するか）", '
    '"payoff_content": "回収内容（どう回収されるか）", '
    '"plant_chapter_number": 配置する章番号（既存の章番号から選ぶ。適切な章がなければnull）, '
    '"payoff_chapter_number": 回収する章番号（既存の章番号から選ぶ。まだ書かれていない先の章なら大きめの番号、'
    '適切な章がなければnull）}, ...]\n'
    "章番号は数値（整数）またはnullのみとし、文字列にはしないこと。"
)


def build_foreshadowing_prompt(project: Project, count: int, additional_notes: str = "") -> str:
    """作品情報・キャラクター・世界設定・既存の章・既存の伏線から、新規の伏線案を考えさせるプロンプトを組み立てる。"""
    lines = [
        "あなたは経験豊富なノベルゲーム／アドベンチャーゲームのシナリオライターです。",
        f"以下の作品情報をもとに、物語に仕込む伏線を{count}件考えてください。",
        "",
        f"# 作品タイトル: {project.title}",
        f"# ジャンル: {project.genre or '未設定'}",
        f"# あらすじ: {project.synopsis or '未設定'}",
    ]

    if project.constraints:
        lines.append(f"\n## 禁止事項・既知の事実（最優先で厳守すること）\n{project.constraints}")

    if project.characters:
        lines.append("\n## キャラクター")
        for c in project.characters:
            lines.append(f"- {c.name}: {c.personality or ''}")

    if project.world_settings:
        lines.append("\n## 世界設定")
        for w in project.world_settings:
            lines.append(f"- {w.name}: {w.world_view or ''}")

    if project.chapters:
        lines.append("\n## 既存の章一覧（plant_chapter_number / payoff_chapter_number はこの中から選ぶこと）")
        for ch in sorted(project.chapters, key=lambda c: c.chapter_number):
            lines.append(f"- 第{ch.chapter_number}章「{ch.title}」: {ch.summary or ''}")
    else:
        lines.append("\n## 既存の章はまだ登録されていません（plant_chapter_number / payoff_chapter_number は null にすること）")

    if project.foreshadowings:
        lines.append("\n## 既に登録済みの伏線（内容が重複しないようにすること）")
        for f in project.foreshadowings:
            lines.append(f"- {f.title}: {f.plant_content or ''}")

    if additional_notes:
        lines.append("\n## 追加指示")
        lines.append(additional_notes)

    lines.append(FORESHADOWING_JSON_INSTRUCTIONS)
    return "\n".join(lines)


def generate_foreshadowings(
    project: Project, count: int = 3, additional_notes: str = "", provider: str = ""
) -> list[dict]:
    """AIに伏線案を考えさせ、パース済みの辞書のリストを返す。

    各要素は {"title", "plant_content", "payoff_content",
    "plant_chapter_number", "payoff_chapter_number"} を持つ。
    """
    prompt = build_foreshadowing_prompt(project, count, additional_notes)
    raw_text = _dispatch(prompt, provider)
    return _parse_json_array(raw_text)


PLOT_JSON_INSTRUCTIONS = (
    "\n出力は必ず次のJSONオブジェクト形式のみとし、前置き・説明文・Markdownのコードフェンスは"
    "一切含めないこと。章番号は指定された開始番号から連番にすること。\n"
    "{\n"
    '  "characters": [{"name": "名前", "age": "年齢", "gender": "性別", "personality": "性格", '
    '"appearance": "外見", "background": "背景設定", "notes": "備考"}, ...],\n'
    '  "world_settings": [{"name": "設定名", "world_view": "世界設定", "era": "時代背景", '
    '"rules": "ルール", "terminology": "用語", "other": "その他設定"}, ...],\n'
    '  "chapters": [{"chapter_number": 章番号（整数）, "title": "章タイトル", '
    '"summary": "この章で起きる出来事の要約", "goal": "この章で達成すべき目的"}, ...],\n'
    '  "foreshadowings": [{"title": "伏線タイトル", "plant_content": "配置内容", '
    '"payoff_content": "回収内容", "plant_chapter_number": 配置する章番号またはnull, '
    '"payoff_chapter_number": 回収する章番号またはnull}, ...]\n'
    "}"
)


def build_full_plot_prompt(
    project: Project, chapter_count: int, start_chapter_number: int, additional_notes: str = ""
) -> str:
    """作品の大まかなコンセプトから、キャラクター・世界設定・全章の設計図・伏線を
    まとめて一括生成させるためのプロンプトを組み立てる（「おまかせ生成モード」用）。
    """
    end_chapter_number = start_chapter_number + chapter_count - 1
    lines = [
        "あなたは経験豊富なノベルゲーム／アドベンチャーゲームのシナリオライター兼編集者です。",
        "以下の大まかな作品情報だけから、結末までを見据えた作品全体の設計図（プロット）を",
        "一括で考えてください。キャラクター・世界設定・全章のあらすじ・伏線を、すべて矛盾なく",
        "連動させることが最も重要です。",
        "",
        f"# 作品タイトル: {project.title}",
        f"# ジャンル: {project.genre or '未設定'}",
        f"# あらすじ: {project.synopsis or '（あらすじも含めて自由に構想してよい）'}",
        f"# 章数: {chapter_count}章（第{start_chapter_number}章〜第{end_chapter_number}章として採番すること）",
    ]

    if project.constraints:
        lines.append(
            f"\n## 禁止事項・既知の事実（最優先で厳守すること。これに反する設計は絶対にしないこと）\n"
            f"{project.constraints}"
        )

    if project.characters:
        lines.append("\n## 既に登録済みのキャラクター（重複させず、これらと整合させること）")
        for c in project.characters:
            lines.append(f"- {c.name}: {c.personality or ''}")

    if project.world_settings:
        lines.append("\n## 既に登録済みの世界設定（重複させず、これらと整合させること）")
        for w in project.world_settings:
            lines.append(f"- {w.name}: {w.world_view or ''}")

    if additional_notes:
        lines.append("\n## 作品への大まかな要望（最優先で反映すること）")
        lines.append(additional_notes)

    lines.append(
        "\n## 章構成の方針（すべての章に適用すること）\n"
        "各章は、それ単体で起承転結が完結する小さな事件・エピソードとして設計してください"
        "（「起：事件発生 → 承：調査・展開 → 転：意外な事実 → 結：この章なりの決着」）。"
        "同時に、各章の出来事の中に、最終章で明かされる作品全体の大きな謎につながる手がかりを"
        "1つ以上仕込み、最終章でそれらが収束して真相が明かされるように、全体を逆算して設計してください。"
    )

    lines.append(PLOT_JSON_INSTRUCTIONS)
    return "\n".join(lines)


def generate_full_plot(
    project: Project,
    chapter_count: int = 5,
    start_chapter_number: int = 1,
    additional_notes: str = "",
    provider: str = "",
) -> dict:
    """「おまかせ生成モード」：キャラクター・世界設定・全章の設計図・伏線を一括生成する。

    戻り値は {"characters": [...], "world_settings": [...], "chapters": [...],
    "foreshadowings": [...]} の辞書。各リストの要素は対応する個別生成関数と同じ形式。
    """
    prompt = build_full_plot_prompt(project, chapter_count, start_chapter_number, additional_notes)
    raw_text = _dispatch(prompt, provider)
    data = _parse_json_object(raw_text)

    for key in ("characters", "world_settings", "chapters", "foreshadowings"):
        if key not in data or not isinstance(data[key], list):
            data[key] = []
    return data


def _dispatch(prompt: str, provider: str = "") -> str:
    """指定（または既定）のプロバイダにプロンプトを送信し、生テキストを返す共通処理。"""
    provider = (provider or current_app.config.get("DEFAULT_AI_PROVIDER", "gemini")).lower()
    if provider not in VALID_PROVIDERS:
        raise AIGenerationError(f"不明なAIプロバイダが指定されました: {provider}")

    generators = {
        "openai": _generate_with_openai,
        "gemini": _generate_with_gemini,
        "claude": _generate_with_claude,
        "ollama": _generate_with_ollama,
    }
    return generators[provider](prompt)


def _parse_json_array(text: str) -> list[dict]:
    """AIの応答テキストからJSON配列を抽出してパースする。

    コードフェンス（```json ... ```）で囲まれていても、前後に説明文が
    付いていても、できる範囲で救済してパースを試みる。
    """
    import json
    import re

    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.MULTILINE).strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", cleaned, re.S)
        if not match:
            raise AIGenerationError(
                "AIの応答からJSON配列を検出できませんでした。プロンプトや追加指示を調整して再試行してください。"
            )
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise AIGenerationError(f"AIの応答をJSONとして解析できませんでした: {exc}") from exc

    if not isinstance(data, list):
        raise AIGenerationError("AIの応答がJSON配列ではありませんでした。")
    return data


def _parse_json_object(text: str) -> dict:
    """AIの応答テキストからJSONオブジェクトを抽出してパースする（全体プロット生成用）。"""
    import json
    import re

    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.MULTILINE).strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.S)
        if not match:
            raise AIGenerationError(
                "AIの応答からJSONオブジェクトを検出できませんでした。プロンプトや追加指示を調整して再試行してください。"
            )
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise AIGenerationError(f"AIの応答をJSONとして解析できませんでした: {exc}") from exc

    if not isinstance(data, dict):
        raise AIGenerationError("AIの応答がJSONオブジェクトではありませんでした。")
    return data


def _generate_with_openai(prompt: str) -> str:
    api_key = current_app.config.get("OPENAI_API_KEY")
    if not api_key:
        raise AIGenerationError(
            "OPENAI_API_KEYが設定されていません。.envファイルにAPIキーを設定してください。"
        )
    try:
        from openai import OpenAI
        import openai as openai_module
    except ImportError as exc:
        raise AIGenerationError(f"openaiパッケージがインストールされていません: {exc}") from exc

    try:
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=current_app.config.get("OPENAI_MODEL", "gpt-4o-mini"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.9,
        )
    except openai_module.AuthenticationError as exc:
        raise AIGenerationError(f"OpenAI APIの認証に失敗しました: {exc}") from exc
    except openai_module.RateLimitError as exc:
        raise AIGenerationError(f"OpenAI APIのレート制限に達しました: {exc}") from exc
    except openai_module.APIConnectionError as exc:
        raise AIGenerationError(f"OpenAI APIとの通信に失敗しました: {exc}") from exc
    except openai_module.APIStatusError as exc:
        raise AIGenerationError(f"OpenAI APIがエラーを返しました（status={exc.status_code}）: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise AIGenerationError(f"OpenAI呼び出し中に予期しないエラーが発生しました: {exc}") from exc

    if not response.choices or not response.choices[0].message.content:
        raise AIGenerationError("OpenAI APIから本文を取得できませんでした（空のレスポンス）。")
    return response.choices[0].message.content


def _generate_with_gemini(prompt: str) -> str:
    api_key = current_app.config.get("GEMINI_API_KEY")
    if not api_key:
        raise AIGenerationError(
            "GEMINI_API_KEYが設定されていません。.envファイルにAPIキーを設定してください。"
        )
    try:
        from google import genai
        from google.genai import errors as genai_errors
    except ImportError as exc:
        raise AIGenerationError(f"google-genaiパッケージがインストールされていません: {exc}") from exc

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=current_app.config.get("GEMINI_MODEL", "gemini-2.5-flash"),
            contents=prompt,
        )
    except genai_errors.ClientError as exc:
        raise AIGenerationError(f"Gemini APIへのリクエストが拒否されました（認証情報等を確認してください）: {exc}") from exc
    except genai_errors.ServerError as exc:
        raise AIGenerationError(f"Gemini APIが一時的にエラーを返しました。時間をおいて再試行してください: {exc}") from exc
    except genai_errors.APIError as exc:
        raise AIGenerationError(f"Gemini APIとの通信でエラーが発生しました: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise AIGenerationError(f"Gemini呼び出し中に予期しないエラーが発生しました: {exc}") from exc

    text = getattr(response, "text", None)
    if not text:
        raise AIGenerationError("Gemini APIから本文を取得できませんでした（空のレスポンス）。")
    return text


def _generate_with_claude(prompt: str) -> str:
    api_key = current_app.config.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise AIGenerationError(
            "ANTHROPIC_API_KEYが設定されていません。.envファイルにAPIキーを設定してください。"
        )
    try:
        import anthropic
    except ImportError as exc:
        raise AIGenerationError(f"anthropicパッケージがインストールされていません: {exc}") from exc

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=current_app.config.get("CLAUDE_MODEL", "claude-sonnet-4-6"),
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.AuthenticationError as exc:
        raise AIGenerationError(f"Claude APIの認証に失敗しました: {exc}") from exc
    except anthropic.RateLimitError as exc:
        raise AIGenerationError(f"Claude APIのレート制限に達しました: {exc}") from exc
    except anthropic.APIConnectionError as exc:
        raise AIGenerationError(f"Claude APIとの通信に失敗しました: {exc}") from exc
    except anthropic.APIStatusError as exc:
        raise AIGenerationError(f"Claude APIがエラーを返しました（status={exc.status_code}）: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise AIGenerationError(f"Claude呼び出し中に予期しないエラーが発生しました: {exc}") from exc

    if not response.content or not getattr(response.content[0], "text", None):
        raise AIGenerationError("Claude APIから本文を取得できませんでした（空のレスポンス）。")
    return response.content[0].text


def _generate_with_ollama(prompt: str) -> str:
    base_url = current_app.config.get("OLLAMA_BASE_URL", "http://localhost:11434")
    model = current_app.config.get("OLLAMA_MODEL", "llama3.1")

    try:
        import httpx
    except ImportError as exc:
        raise AIGenerationError(f"httpxパッケージがインストールされていません: {exc}") from exc

    try:
        resp = httpx.post(
            f"{base_url.rstrip('/')}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=120.0,
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.ConnectError as exc:
        raise AIGenerationError(
            f"Ollamaサーバー（{base_url}）へ接続できませんでした。Ollamaが起動しているか確認してください: {exc}"
        ) from exc
    except httpx.TimeoutException as exc:
        raise AIGenerationError(f"Ollamaサーバーへのリクエストがタイムアウトしました: {exc}") from exc
    except httpx.HTTPStatusError as exc:
        raise AIGenerationError(f"Ollamaサーバーがエラーを返しました（status={exc.response.status_code}）: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise AIGenerationError(f"Ollama呼び出し中に予期しないエラーが発生しました: {exc}") from exc

    text = data.get("response")
    if not text:
        raise AIGenerationError("Ollamaから本文を取得できませんでした（空のレスポンス）。")
    return text
