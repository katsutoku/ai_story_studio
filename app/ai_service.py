"""
AIシナリオ生成サービス（雛形）

仕様書 6.「AIシナリオ生成（開発予定）」に対応する将来実装用のモジュール。
現時点ではルートには接続していない。OpenAI APIキーを設定後、
chapters.py 等から呼び出す想定。
"""
from __future__ import annotations

from typing import Optional

from flask import current_app

from .models import Chapter, Foreshadowing, Project


def build_prompt(project: Project, target_chapter_number: int) -> str:
    """プロジェクト情報・キャラクター・世界設定・過去章要約・未回収伏線から
    次章生成用のプロンプトを組み立てる。
    """
    lines = [
        f"# 作品タイトル: {project.title}",
        f"# ジャンル: {project.genre or '未設定'}",
        f"# あらすじ: {project.synopsis or '未設定'}",
        "",
        "## キャラクター",
    ]
    for c in project.characters:
        lines.append(f"- {c.name}（{c.age or '年齢不明'} / {c.gender or '性別不明'}）: {c.personality or ''}")

    lines.append("\n## 世界設定")
    for w in project.world_settings:
        lines.append(f"- {w.name}: {w.world_view or ''}")

    lines.append("\n## これまでの章の要約")
    for ch in sorted(project.chapters, key=lambda c: c.chapter_number):
        if ch.chapter_number < target_chapter_number:
            lines.append(f"- 第{ch.chapter_number}章「{ch.title}」: {ch.summary or ''}")

    lines.append("\n## 未回収の伏線")
    pending = [f for f in project.foreshadowings if f.status == Foreshadowing.STATUS_PENDING]
    for f in pending:
        lines.append(f"- {f.title}: {f.plant_content or ''}")

    lines.append(f"\n上記の情報をもとに、第{target_chapter_number}章の本文をMarkdown形式で執筆してください。")
    return "\n".join(lines)


def generate_chapter_content(project: Project, target_chapter_number: int) -> Optional[str]:
    """OpenAI APIを呼び出して章本文を生成する（未実装・雛形）。

    実装時のイメージ:

        from openai import OpenAI
        client = OpenAI(api_key=current_app.config["OPENAI_API_KEY"])
        prompt = build_prompt(project, target_chapter_number)
        response = client.chat.completions.create(
            model=current_app.config["OPENAI_MODEL"],
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content

    通信失敗時は呼び出し側で Foreshadowing/Chapter のステータスを
    failed に更新し、エラーメッセージを表示し、再生成可能にする想定。
    """
    raise NotImplementedError("AIシナリオ生成機能は開発予定です。OPENAI_API_KEY設定後に実装してください。")
