"""
AIシナリオ生成サービス

仕様書 6.「AIシナリオ生成」に対応するモジュール。
プロジェクト情報・キャラクター・世界設定・過去章要約・未回収の伏線をプロンプトとして
組み立て、選択されたAIプロバイダ（openai / gemini / claude / ollama）へ送信して
章本文（Markdown）を生成する。
"""
from __future__ import annotations

from flask import current_app

from .models import Foreshadowing, Project

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
        "",
        "## キャラクター",
    ]
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
