"""
AIシナリオ生成サービス

仕様書 6.「AIシナリオ生成」に対応するモジュール。
プロジェクト情報・キャラクター・世界設定・過去章要約・未回収の伏線をプロンプトとして
組み立て、選択されたAIプロバイダ（openai / gemini / claude / ollama）へ送信して
章本文（Markdown）を生成する。
"""
from __future__ import annotations

from flask import current_app

from .models import Chapter, Character, Foreshadowing, MysteryCase, Project

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


def _mystery_cases_for_chapter_number(project: Project, chapter_number: int) -> list:
    """指定した章番号が、いずれかのMysteryCaseの事件範囲
    （trigger_chapter_id〜resolution_chapter_id、章番号ベース）に含まれるかを判定する。

    range両端が章番号ベースなのは、Chapterがmajor_number/sub_number構成に将来拡張されても
    FK参照先（Chapter.id）自体は変わらないため（設計書5.1節）。
    """
    matched = []
    for case in project.mystery_cases:
        trigger_number = case.trigger_chapter.chapter_number if case.trigger_chapter else None
        resolution_number = case.resolution_chapter.chapter_number if case.resolution_chapter else None
        if trigger_number is not None and resolution_number is not None:
            in_range = trigger_number <= chapter_number <= resolution_number
        elif trigger_number is not None:
            in_range = chapter_number == trigger_number
        elif resolution_number is not None:
            in_range = chapter_number == resolution_number
        else:
            in_range = False
        if in_range:
            matched.append(case)
    return matched


def build_prompt(
    project: Project,
    target_chapter_number: int,
    additional_notes: str = "",
    include_case_environment: bool = False,
    reveal_case_truth: bool = False,
) -> str:
    """プロジェクト情報・キャラクター・世界設定・過去章要約・未回収の伏線から
    次章生成用のプロンプトを組み立てる（プロバイダ非依存の共通処理）。

    ミステリー・トリック生成モジュール（app/models.py MysteryCase）との連携はここに限定する。
    本編生成のルール自体（この関数の基本構造・書式規約）には手を入れず、事件範囲内の章にのみ
    「材料」を追加供給する（詳細はdocs/mystery_trick_module_design.md 0.1節・5章、CLAUDE.md「9.」）。

    include_case_environment: Trueの場合のみ、事件範囲内の章にenvironment_json（客観的事実。
        真相そのものではない）を注入する（デフォルトOFF）。
    reveal_case_truth: Trueかつ、この章が該当事件のresolution_chapter（解決編）である場合のみ、
        trick_json.true_mechanism（真相）を注入する（デフォルトOFF。誤って地の文生成プロンプトに
        真相が混入すると伏線が台無しになるため、二重に明示的なオプトインを要求する）。
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
    # 事件専用のモブキャラ（mystery_case_idが設定されているもの）は、事件範囲外の章では
    # 存在しないものとして扱うため、通常のキャラクター一覧には含めない（下の事件連携セクションで
    # 範囲内の章にのみ別途追加する）
    main_characters = [c for c in project.characters if c.mystery_case_id is None]
    if main_characters:
        for c in main_characters:
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

    mystery_cases_in_range = _mystery_cases_for_chapter_number(project, target_chapter_number)
    if mystery_cases_in_range:
        target_chapter_row = next(
            (ch for ch in project.chapters if ch.chapter_number == target_chapter_number), None
        )
        mob_characters = [c for case in mystery_cases_in_range for c in case.mob_characters]
        if mob_characters:
            lines.append(
                "\n## この章に関わる事件専用のモブキャラ（この事件の範囲内の章にのみ登場する端役）"
            )
            for c in mob_characters:
                lines.append(
                    f"- {c.name}（{c.age or '年齢不明'} / {c.gender or '性別不明'}）"
                    f" 性格: {c.personality or '未設定'} / 外見: {c.appearance or '未設定'}"
                )

        if include_case_environment:
            for case in mystery_cases_in_range:
                environment = _load_json_dict_or_none(case.environment_json)
                if not environment:
                    continue
                lines.append(
                    f"\n## 事件「{case.title}」の客観的事実（作中で自然に描写してよい情報。"
                    "真相そのものではないため、これだけを頼りに真相を書かないこと）"
                )
                if environment.get("timeline"):
                    lines.append(
                        "タイムライン: "
                        + "、".join(
                            f"{t.get('time', '')} {t.get('location', '')} {t.get('event', '')}"
                            for t in environment.get("timeline", [])
                        )
                    )
                if environment.get("location"):
                    lines.append(
                        "場所: "
                        + "、".join(
                            f"{l.get('name', '')}（{l.get('description', '')}）"
                            for l in environment.get("location", [])
                        )
                    )
                if environment.get("weather"):
                    lines.append(f"天候: {environment.get('weather', '')}")
                if environment.get("items"):
                    lines.append(
                        "小道具: "
                        + "、".join(item.get("name", "") for item in environment.get("items", []))
                    )

        if reveal_case_truth and target_chapter_row is not None:
            for case in mystery_cases_in_range:
                if case.resolution_chapter_id != target_chapter_row.id:
                    continue
                trick = _load_json_dict_or_none(case.trick_json)
                if trick and trick.get("true_mechanism"):
                    lines.append(
                        f"\n## 事件「{case.title}」の真相（この章は解決編として明示指定されたため注入。"
                        "この章で真相を開示する描写をしてよい）\n"
                        f"{trick.get('true_mechanism', '')}"
                    )

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
    include_case_environment: bool = False,
    reveal_case_truth: bool = False,
) -> str:
    """選択されたAIプロバイダを呼び出して章本文（Markdown）を生成する。

    provider: "openai" / "gemini" / "claude" / "ollama"。
    未指定の場合は設定のデフォルトプロバイダ（DEFAULT_AI_PROVIDER）を使用する。

    include_case_environment / reveal_case_truth: build_prompt()を参照
    （ミステリー・トリック生成モジュールとの連携。ともにデフォルトOFF）。

    認証エラー・通信エラー・レスポンス不正などが発生した場合は
    AIGenerationError を送出する。呼び出し側で章の生成ステータスを
    failed に更新し、エラーメッセージを表示し、再生成可能にする。
    """
    prompt = build_prompt(
        project,
        target_chapter_number,
        additional_notes,
        include_case_environment=include_case_environment,
        reveal_case_truth=reveal_case_truth,
    )
    return _dispatch(prompt, provider)


def _format_transcript_line(speaker_name: str, line: str) -> str:
    """1ターン分のセリフを、本文の書式規約（【名前】「セリフ」）に整形する"""
    line = (line or "").strip()
    # AIが誤って「」や【】を含めて返してきた場合に備え、素の発言部分だけを取り出す
    line = line.strip("「」").strip()
    return f"【{speaker_name}】「{line}」"


def build_detective_turn_prompt(
    project: Project,
    detective_name: str,
    suspect_name: str,
    public_context: str,
    transcript_text: str,
    turn_number: int,
    total_turns: int,
) -> str:
    """探偵役の1ターン分のセリフを生成するプロンプトを組み立てる。

    重要：ここには project.constraints（真相・犯人などの秘密情報）を一切含めない。
    探偵は「公開情報」と「これまでの会話」だけから、次の質問・指摘を考える。
    これにより、探偵が最初から答えを知っている状態になることを防ぐ
    （カンニング防止。設計判断として明示的に決定した制約）。
    """
    lines = [
        "あなたは、ミステリー・推理ゲームのシナリオに登場する「探偵役」のキャラクターです。",
        f"あなたの名前は「{detective_name}」です。今、容疑者「{suspect_name}」を尋問しています。",
        "",
        f"# 事件の公開情報（あなたが知っていること。これ以外の事実を勝手に決めつけないこと）\n{public_context or '（特に追加情報なし）'}",
    ]
    if transcript_text:
        lines.append(f"\n# これまでの尋問のやり取り\n{transcript_text}")
    else:
        lines.append("\n# これまでの尋問のやり取り\n（まだ会話は始まっていません。これが最初の質問です）")

    lines.append(
        f"\n# 指示\n"
        f"あなたは今{turn_number}ターン目（全{total_turns}ターン中）の発言をする番です。"
        "公開情報とこれまでの会話だけを根拠に、鋭く、しかし決めつけすぎない質問・指摘を"
        "1つだけ発言してください。あなたがまだ知らないはずの事実（容疑者の内心や、"
        "公開情報に含まれていない事件の真相）を、知っているかのように話さないでください。\n"
        "出力は発言内容のみとし、地の文・前置き・鉤括弧（「」）は付けずに、セリフの中身だけを"
        "1〜2文で出力してください。"
    )
    return "\n".join(lines)


def build_suspect_turn_prompt(
    project: Project,
    suspect_name: str,
    suspect_secret: str,
    public_context: str,
    transcript_text: str,
    detective_line: str,
) -> str:
    """容疑者役の1ターン分の返答を生成するプロンプトを組み立てる。

    suspect_secret（この容疑者だけが知っている秘密：本当のアリバイ、隠したい動機など）は、
    DBには保存せず、生成のたびに利用者が入力した内容をそのまま使う。
    他の容疑者の秘密は一切渡さない（独立セッション方式）。
    """
    character = next(
        (c for c in project.characters if c.name.strip() == suspect_name.strip()), None
    )

    lines = [
        "あなたは、ミステリー・推理ゲームのシナリオに登場する「容疑者」のキャラクターです。",
        f"あなたの名前は「{suspect_name}」です。今、探偵から尋問を受けています。",
    ]
    if character is not None:
        lines.append(
            f"あなたの性格: {character.personality or '未設定'} / "
            f"背景: {character.background or '未設定'}"
        )

    lines.append(f"\n# あなただけが知っている秘密（絶対に他人には教えない情報）\n{suspect_secret}")
    lines.append(f"\n# 事件の公開情報（探偵と共有している一般的な情報）\n{public_context or '（特に追加情報なし）'}")

    if project.constraints:
        lines.append(f"\n# 世界の既知の事実（矛盾しないよう意識すること）\n{project.constraints}")

    if transcript_text:
        lines.append(f"\n# これまでの尋問のやり取り\n{transcript_text}")

    lines.append(
        f"\n# 探偵の今の発言\n【{suspect_name}への質問】{detective_line}"
    )
    lines.append(
        "\n# 指示\n"
        "あなたの性格と、あなただけが知っている秘密を踏まえて、この質問に返答してください。"
        "秘密を守ろうとして嘘をついたり、はぐらかしたり、動揺を見せたりしてよく、"
        "逆に追い詰められて一部を認めることもあってよい。あなたの性格に合った反応にすること。\n"
        "出力は発言内容のみとし、地の文・前置き・鉤括弧（「」）は付けずに、セリフの中身だけを"
        "1〜2文で出力してください。"
    )
    return "\n".join(lines)


def generate_interrogation(
    project: Project,
    detective_name: str,
    suspect_name: str,
    suspect_secret: str,
    public_context: str = "",
    total_turns: int = 10,
    provider: str = "",
) -> str:
    """探偵AIと容疑者AIを交互に呼び出し、1本道の尋問シナリオを生成する。

    探偵側のプロンプトには真相（project.constraints）を含めない。
    容疑者側のプロンプトには、その容疑者専用の秘密（suspect_secret）と
    project.constraintsのみを含め、他のキャラクターの秘密は一切渡さない
    （独立セッション方式によるカンニング防止）。

    戻り値は、本文の書式規約（【名前】「セリフ」）に整形済みのMarkdownテキスト。
    """
    if total_turns < 1:
        raise AIGenerationError("ターン数は1以上を指定してください。")

    transcript_lines: list[str] = []  # 表示用（整形済み）
    formatted_lines: list[str] = []  # 最終的な本文として返す行

    for turn in range(1, total_turns + 1):
        transcript_text = "\n".join(transcript_lines)

        detective_prompt = build_detective_turn_prompt(
            project, detective_name, suspect_name, public_context,
            transcript_text, turn, total_turns,
        )
        detective_line = _dispatch(detective_prompt, provider).strip()
        transcript_lines.append(_format_transcript_line(detective_name, detective_line))
        formatted_lines.append(_format_transcript_line(detective_name, detective_line))

        transcript_text = "\n".join(transcript_lines)
        suspect_prompt = build_suspect_turn_prompt(
            project, suspect_name, suspect_secret, public_context,
            transcript_text, detective_line,
        )
        suspect_line = _dispatch(suspect_prompt, provider).strip()
        transcript_lines.append(_format_transcript_line(suspect_name, suspect_line))
        formatted_lines.append(_format_transcript_line(suspect_name, suspect_line))

    return "\n".join(formatted_lines)


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


RELATIONSHIP_JSON_INSTRUCTIONS = (
    "\n出力は必ず次のJSON配列形式のみとし、前置き・説明文・Markdownのコードフェンスは一切含めないこと。\n"
    '[{"from": "キャラクターA", "to": "キャラクターB", "relationship": "AからBへの関係性（短く）"}, ...]\n'
    "「from」「to」は、渡されたキャラクター一覧に実在する名前を必ずそのまま使うこと（表記ゆれ禁止）。"
    "関係が双方向的なもの（友人、幼馴染など）は1本の矢印で表現してよい。"
    "一方的・非対称な関係（疑惑、片思い、恨みなど）は「from」を起点として表現すること。"
)


def build_relationship_prompt(project: Project, additional_notes: str = "") -> str:
    """登録済みのキャラクター・世界設定・章の要約・伏線から、人物相関図の元になる
    関係性データをAIに考えさせるプロンプトを組み立てる。
    """
    lines = [
        "あなたは経験豊富なノベルゲーム／アドベンチャーゲームのシナリオ分析アシスタントです。",
        "以下の作品情報をもとに、登場人物同士の人間関係を整理してください。",
        "制作者がストーリー全体の人物相関を俯瞰的に把握するための相関図の元データとして使います。",
        "",
        f"# 作品タイトル: {project.title}",
        f"# ジャンル: {project.genre or '未設定'}",
        f"# あらすじ: {project.synopsis or '未設定'}",
    ]

    if project.constraints:
        lines.append(f"\n## 禁止事項・既知の事実（この内容と矛盾する関係性を書かないこと）\n{project.constraints}")

    lines.append("\n## キャラクター一覧（relationshipのfrom/toはこの名前のみを使うこと）")
    if project.characters:
        for c in project.characters:
            lines.append(
                f"- {c.name}（{c.age or '年齢不明'} / {c.gender or '性別不明'}）"
                f" 性格: {c.personality or '未設定'} / 背景: {c.background or '未設定'}"
            )
    else:
        lines.append("- （キャラクターが登録されていません）")

    if project.world_settings:
        lines.append("\n## 世界設定")
        for w in project.world_settings:
            lines.append(f"- {w.name}: {w.world_view or ''}")

    if project.chapters:
        lines.append("\n## 各章の要約（人物同士の関わりが分かる場合は参考にすること）")
        for ch in sorted(project.chapters, key=lambda c: c.chapter_number):
            lines.append(f"- 第{ch.chapter_number}章「{ch.title}」: {ch.summary or ''}")

    if project.foreshadowings:
        lines.append("\n## 伏線（人物間の隠された関係性のヒントになる場合がある）")
        for f in project.foreshadowings:
            lines.append(f"- {f.title}: {f.plant_content or ''}")

    if additional_notes:
        lines.append("\n## 追加指示")
        lines.append(additional_notes)

    lines.append(RELATIONSHIP_JSON_INSTRUCTIONS)
    return "\n".join(lines)


def generate_relationships(
    project: Project, additional_notes: str = "", provider: str = ""
) -> list[dict]:
    """AIに人物同士の関係性を考えさせ、パース済みの辞書のリストを返す。

    各要素は {"from", "to", "relationship"} を持つ。
    """
    prompt = build_relationship_prompt(project, additional_notes)
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


def _format_world_settings(project: Project) -> str:
    """WorldSetting一覧を、プロンプトに埋め込む簡潔なテキストに整形する（複数の生成関数で共用）"""
    if not project.world_settings:
        return "（未設定）"
    return "\n".join(f"- {w.name}: {w.world_view or ''}" for w in project.world_settings)


def _resolve_fixed_role_hints(case: MysteryCase, project: Project) -> str:
    """Phase1でユーザーが指定した「固定配役の希望」(fixed_role_hints_json)を、
    キャラクターIDだけでなく実名を含む人間可読なテキストに整形する。
    """
    import json

    try:
        hints = json.loads(case.fixed_role_hints_json or "[]")
    except (json.JSONDecodeError, TypeError):
        hints = []
    if not hints:
        return "（特になし。すべての役割をこのAIが自由に設計してよい）"

    character_lookup = {c.id: c for c in project.characters}
    lines = []
    for hint in hints:
        if not isinstance(hint, dict):
            continue
        character = character_lookup.get(hint.get("character_id"))
        name = character.name if character else "（不明なキャラクター）"
        lines.append(f"- role: {hint.get('role')} → {name}")
    return "\n".join(lines) if lines else "（特になし）"


def build_trick_prompt(case: MysteryCase, project: Project) -> str:
    """事件のトリック（真相）・矛盾セット・抽象配役表をAIに考えさせるプロンプトを組み立てる（Phase 2）。

    この時点のrequired_castは抽象状態（名前を持たない役割の集合）。
    実際のキャラクターへの割り当ては、この後の配役ステップ（Phase 2.5）で行う。
    """
    lines = [
        "あなたは本格ミステリーのトリック設計専門AIです。",
        "以下の制約を守り、JSON形式のみでトリック構造を出力してください（前置き・Markdown装飾は禁止）。",
        "",
        "# 制約",
        "1. 超自然現象・SF的ガジェット・偶然任せのトリックは禁止（ノックスの十戒に準拠）。",
        "2. プレイヤーが証言と証拠品を突きつけて破綻させられる、明確な矛盾(contradiction_set)を"
        "必ず1つ以上作ること。",
        "3. プレイヤーを別人物へ誘導するミスディレクションを最低1つ含めること。",
        "4. 「禁止事項・既知の事実」に反する設定を作らないこと。",
        "",
        f"# 作品タイトル: {project.title}",
        f"# ジャンル: {project.genre or '未設定'}",
        f"# 世界観\n{_format_world_settings(project)}",
    ]
    if case.case_world_setting:
        lines.append(f"\n# この事件固有の舞台設定（補足）\n{case.case_world_setting}")

    if project.constraints:
        lines.append(f"\n# 禁止事項・既知の事実（最優先で厳守すること）\n{project.constraints}")

    if case.climax_twist and case.climax_twist.strip():
        lines.append(
            "\n# オチ（絶対条件。この結末に矛盾なく到達するトリックを設計すること）\n"
            f"{case.climax_twist}"
        )
    else:
        lines.append(
            "\n# オチについて\n"
            "オチ（事件の結末・意外性のある真相の要点）は指定されていません。"
            "あなたがこの事件にふさわしいオチを自由に考案し、出力JSONの "
            "generated_conclusion フィールドに、後から読み返しても分かるよう簡潔な文章"
            "（1〜3文程度）で明記してください。"
        )
    lines.append(
        "\n# 固定配役の希望（指定があれば必ずその役割で使うこと。指定がない役割は自由に設計してよい）\n"
        f"{_resolve_fixed_role_hints(case, project)}"
    )

    lines.append(
        "\n# 出力JSONスキーマ\n"
        "{\n"
        '  "trick_type": "string",\n'
        '  "true_mechanism": "string（真相の仕組み。秘匿情報）",\n'
        '  "misdirection": "string",\n'
        '  "generated_conclusion": "string（オチが未指定だった場合のみ、あなたが考案したオチを'
        "記載。オチが指定済みだった場合は空文字でよい）\",\n"
        '  "contradiction_set": {\n'
        '    "witness_statement": "string",\n'
        '    "evidence_fact": "string",\n'
        '    "key_evidence": "string"\n'
        "  },\n"
        '  "required_cast": [\n'
        "    {\n"
        '      "role_key": "string（例: witness_1）",\n'
        '      "role_type": "detective|victim|culprit|suspect|witness|accomplice|other",\n'
        '      "public_trait": "string（この役割に求められる表面的な特徴。配役の判断材料）",\n'
        '      "necessity_reason": "string（トリック上、なぜこの役割が何人必要なのかの理由）"\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "固定配役の希望で指定された役割は required_cast にも含め、role_key を対応させること。"
        "それ以外の役割は、トリックの成立に本当に必要な人数だけを過不足なく設計すること"
        "（矛盾セットの成立に不要な役割を水増ししない）。"
    )
    return "\n".join(lines)


def generate_trick(case: MysteryCase, project: Project, provider: str = "") -> dict:
    """トリック（真相）・矛盾セット・抽象配役表をAIに生成させ、パース済みの辞書を返す（Phase 2）。

    戻り値はまだDBに保存しない（既存の「生成→プレビュー→選択保存」パターンに合わせ、
    確定保存はルート側の generate-trick/confirm で行う）。
    """
    prompt = build_trick_prompt(case, project)
    raw_text = _dispatch(prompt, provider)
    data = _parse_json_object(raw_text)

    if not isinstance(data.get("contradiction_set"), dict):
        data["contradiction_set"] = {}
    if not isinstance(data.get("required_cast"), list):
        data["required_cast"] = []
    if not isinstance(data.get("generated_conclusion"), str):
        data["generated_conclusion"] = ""
    return data


def auto_assign_cast(
    case: MysteryCase, project: Project, required_cast: list[dict], provider: str = ""
) -> tuple[list[dict], list[dict], list[Character]]:
    """お任せモード専用：fixed_character_id（固定配役の希望に由来するもの）が設定されていない
    役割すべてに、AI生成モブキャラを1件だけ生成して自動的に割り当てる。

    既存のモブキャラ生成（cast_generate_mobルートが使うgenerate_characters）を候補数1で
    呼び出す。DBへのCharacter保存はここでは行わない（お任せモードは「Phase2・配役・Phase3の
    すべてが成功し、一括プレビューでユーザーが確定したタイミングでまとめて保存する」という
    設計のため。呼び出し側でPhase3成功後にdb.session.add_all() → flush()して確定する）。

    戻り値は (更新後のrequired_cast, 環境生成プロンプト用に解決済みのcast一覧,
    未保存のCharacterインスタンス一覧)。

    名前が既存キャラクター・今回生成した他のモブキャラと重複した場合はAIGenerationErrorを
    送出する（何もDBに保存していない時点でのエラーなので、呼び出し側は安全に中断できる）。
    """
    fixed_lookup = {c.id: c for c in project.characters}
    fixed_lookup.update({c.id: c for c in case.mob_characters})

    existing_names = {c.name.strip().lower() for c in project.characters}

    updated_cast: list[dict] = []
    resolved_cast: list[dict] = []
    pending_characters: list[Character] = []

    for original_entry in required_cast:
        entry = dict(original_entry)
        fixed_id = entry.get("fixed_character_id") or entry.get("assigned_character_id")
        if fixed_id:
            character = fixed_lookup.get(fixed_id)
            entry["assigned_character_id"] = fixed_id
            updated_cast.append(entry)
            resolved_cast.append(
                {
                    "role_key": entry.get("role_key"),
                    "role_type": entry.get("role_type"),
                    "public_trait": entry.get("public_trait"),
                    "character_name": character.name if character else "（不明なキャラクター）",
                    "character_personality": character.personality if character else "",
                }
            )
            continue

        notes = (
            f"この事件専用のモブキャラクターを考えてください。役割: {entry.get('role_type')}。"
            f"求められる特徴: {entry.get('public_trait') or '（特になし）'}"
        )
        candidates = generate_characters(project, count=1, additional_notes=notes, provider=provider)
        if not candidates:
            raise AIGenerationError(f"役割「{entry.get('role_key')}」のモブキャラ生成に失敗しました。")

        candidate = candidates[0]
        name = str(candidate.get("name") or "").strip()
        if not name:
            raise AIGenerationError(f"役割「{entry.get('role_key')}」のモブキャラの名前が空でした。")
        if name.lower() in existing_names:
            raise AIGenerationError(
                f"役割「{entry.get('role_key')}」用に生成された「{name}」は、既存または他の役割の"
                "候補と名前が重複しています。個別の配役画面からやり直してください。"
            )
        existing_names.add(name.lower())

        character = Character(
            project_id=project.id,
            mystery_case_id=case.id,
            name=name[:100],
            age=str(candidate.get("age") or "")[:20] or None,
            gender=str(candidate.get("gender") or "")[:20] or None,
            personality=candidate.get("personality") or None,
            appearance=candidate.get("appearance") or None,
            background=candidate.get("background") or None,
            notes=candidate.get("notes") or None,
        )
        pending_characters.append(character)
        entry["_pending_character_index"] = len(pending_characters) - 1
        updated_cast.append(entry)
        resolved_cast.append(
            {
                "role_key": entry.get("role_key"),
                "role_type": entry.get("role_type"),
                "public_trait": entry.get("public_trait"),
                "character_name": character.name,
                "character_personality": character.personality or "",
            }
        )

    return updated_cast, resolved_cast, pending_characters


def _resolve_cast_list(case: MysteryCase, project: Project) -> list[dict]:
    """required_cast_json（配役結果を含む）を、実際のCharacterの実名・特徴に解決する。

    assigned_character_idは、通常のメインキャスト（project.characters）と
    この事件専用のモブキャラ（case.mob_characters）のどちらも参照しうる。
    """
    import json

    try:
        required_cast = json.loads(case.required_cast_json or "[]")
    except (json.JSONDecodeError, TypeError):
        required_cast = []

    character_lookup = {c.id: c for c in project.characters}
    character_lookup.update({c.id: c for c in case.mob_characters})

    resolved = []
    for entry in required_cast:
        if not isinstance(entry, dict):
            continue
        character = character_lookup.get(entry.get("assigned_character_id"))
        resolved.append(
            {
                "role_key": entry.get("role_key"),
                "role_type": entry.get("role_type"),
                "public_trait": entry.get("public_trait"),
                "character_name": character.name if character else "（未配役）",
                "character_personality": character.personality if character else "",
            }
        )
    return resolved


def build_environment_prompt(case: MysteryCase, project: Project, resolved_cast: list[dict]) -> str:
    """配役済み（Phase 2.5完了後）の実名一覧をもとに、矛盾のない現場データ（タイムライン・
    場所・天候・小道具）を実名でAIに生成させるプロンプトを組み立てる（Phase 3）。

    trick_json（真相を含む）はここで初めてAIに渡すが、生成対象はあくまで客観的な現場データであり、
    真相そのものを本編の地の文プロンプトへそのまま横流ししないことは呼び出し側の責務
    （app/routes/mystery.py・build_prompt()側で担保する。詳細はCLAUDE.md「8.」「9.」）。
    """
    cast_lines = [
        f"- {c['role_key']}（{c['role_type']}）: {c['character_name']} / {c['public_trait'] or ''}"
        for c in resolved_cast
    ]
    cast_text = "\n".join(cast_lines) if cast_lines else "（配役情報なし）"

    lines = [
        "あなたは本格ミステリーの現場状況・小道具設計専門AIです。",
        "以下の真相（トリック）と、配役が確定した登場人物の実名一覧をもとに、矛盾のない現場の",
        "タイムライン・場所・天候・小道具をJSON形式のみで出力してください（前置き・Markdown装飾は",
        "禁止。抽象的な役割名ではなく実名で描写すること）。",
        "",
        f"# 世界観\n{_format_world_settings(project)}\n{case.case_world_setting or ''}",
    ]
    if project.constraints:
        lines.append(f"\n# 禁止事項・既知の事実（最優先で厳守すること）\n{project.constraints}")

    lines.append(
        "\n# 真相（トリック。矛盾なく現場データに反映すること。プレイヤーには開示しない前提で"
        f"設計してよい）\n{case.trick_json or ''}"
    )
    lines.append(f"\n# 配役済みの登場人物一覧（この実名を使って描写すること）\n{cast_text}")
    lines.append(
        "\n# 出力JSONスキーマ\n"
        "{\n"
        '  "timeline": [{"time": "string", "location": "string", "event": "string", '
        '"characters": ["string", ...]}, ...],\n'
        '  "location": [{"name": "string", "description": "string"}, ...],\n'
        '  "weather": "string",\n'
        '  "items": [{"name": "string", "description": "string", "location_found": "string"}, ...],\n'
        '  "additional_required_cast": [{"role_key": "string", "role_type": '
        '"witness|accomplice|other", "public_trait": "string", "necessity_reason": "string"}]\n'
        "}\n"
        "additional_required_castは、この現場データを作る中で新たに必要だと判明した役割がある"
        "場合のみ含めること（例：見張り役がもう1人必要、等）。不要な場合は空配列にすること。"
    )
    return "\n".join(lines)


def generate_environment(
    case: MysteryCase, project: Project, provider: str = "", resolved_cast: list[dict] | None = None
) -> dict:
    """配役済みの実名一覧をもとに、現場データ（timeline/location/weather/items）を生成する（Phase 3）。

    追加の役割が必要と判明した場合はadditional_required_castに含まれる
    （ルート側でrequired_cast_jsonへの追記・cast_statusの差し戻しに使う）。

    resolved_cast: 通常はNoneのまま呼び出し、case.required_cast_json（DB保存済み）を
        _resolve_cast_list()で解決する。お任せモード（auto_assign_cast）のように、
        配役結果がまだDBに保存されていない（Character未コミット）場合は、呼び出し側で
        解決済みのcast一覧を直接渡す。
    """
    if resolved_cast is None:
        resolved_cast = _resolve_cast_list(case, project)
    prompt = build_environment_prompt(case, project, resolved_cast)
    raw_text = _dispatch(prompt, provider)
    data = _parse_json_object(raw_text)

    for key in ("timeline", "location", "items", "additional_required_cast"):
        if not isinstance(data.get(key), list):
            data[key] = []
    data.setdefault("weather", "")
    return data


def build_evaluation_prompt(case: MysteryCase, target_text: str, source_label: str) -> str:
    """正解データ（trick_json・environment_json）と実際の本文/尋問ログを比較させ、
    矛盾やプレイアビリティを評価させるプロンプトを組み立てる（Phase 5）。
    """
    return (
        "あなたはゲームシナリオの監修者・クオリティアナライザーAIです。\n"
        "【正解の設定データ】と【実際の本文/ログ】を比較し、JSON形式で評価してください。\n"
        "\n"
        "【正解の設定データ（外部に漏らしてはいけない真相を含む）】\n"
        f"trick: {case.trick_json or ''}\n"
        f"environment: {case.environment_json or ''}\n"
        "\n"
        f"【検証対象（{source_label}）】\n"
        f"{target_text}\n"
        "\n"
        "【検証項目】\n"
        "1. logical_flaws: キャラが知るはずのない情報を話していないか。タイムライン・天候との矛盾。\n"
        "2. contradiction_playability: 探偵役が提示証拠で嘘を論破できる構成になっているか。\n"
        "3. narrative_quality: ドラマ性（自然な言い逃れ、駆け引き）。\n"
        "\n"
        "【出力JSONスキーマ】\n"
        "{\n"
        '  "overall_grade": "S|A|B|C",\n'
        '  "logical_flaws": [{"description": "string", "location_hint": "string", '
        '"suggestion": "string"}],\n'
        '  "contradiction_playability": "string",\n'
        '  "narrative_quality": "string",\n'
        '  "notable_excerpts": ["string"]\n'
        "}\n"
    )


def generate_evaluation(
    case: MysteryCase, target_text: str, source_label: str, provider: str = ""
) -> dict:
    """本文/尋問ログを正解データと突き合わせ、矛盾検出・評価結果を生成する（Phase 5）。

    Phase 5はボタン起動の都度実行し、章生成のたびに自動実行はしない
    （相関図キャッシュ更新方針と同じ「コスト意識」。CLAUDE.md「7.」参照）。
    """
    prompt = build_evaluation_prompt(case, target_text, source_label)
    raw_text = _dispatch(prompt, provider)
    data = _parse_json_object(raw_text)

    if not isinstance(data.get("logical_flaws"), list):
        data["logical_flaws"] = []
    if not isinstance(data.get("notable_excerpts"), list):
        data["notable_excerpts"] = []
    data.setdefault("overall_grade", "")
    data.setdefault("contradiction_playability", "")
    data.setdefault("narrative_quality", "")
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


def _load_json_dict_or_none(text: str):
    """DBに保存済みのJSON文字列を辞書として読み込む（章生成プロンプトへの任意注入用）。

    AIの生応答を厳格にパースする_parse_json_objectとは異なり、こちらは既に一度保存された
    データを読み込むだけなので、壊れていた場合は例外を送出せずNoneを返す
    （章生成そのものを止めないため）。
    """
    import json

    if not text:
        return None
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None


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
            max_tokens=current_app.config.get("AI_MAX_OUTPUT_TOKENS", 8192),
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
            config={"max_output_tokens": current_app.config.get("AI_MAX_OUTPUT_TOKENS", 8192)},
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
            max_tokens=current_app.config.get("AI_MAX_OUTPUT_TOKENS", 8192),
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
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "num_predict": current_app.config.get("AI_MAX_OUTPUT_TOKENS", 8192),
                },
            },
            timeout=300.0,
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
