from pathlib import Path

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for

from ..ai_service import (
    AIGenerationError,
    generate_chapter_content,
    generate_interrogation,
    revise_chapter_content,
)
from ..extensions import db
from ..forms import (
    ChapterContentForm,
    ChapterForm,
    ChapterReviseForm,
    GenerateChapterForm,
    InterrogationGenerateForm,
)
from ..models import Chapter, Project

chapters_bp = Blueprint("chapters", __name__, url_prefix="/projects/<int:project_id>/chapters")


def _get_project_or_404(project_id):
    project = Project.query.get(project_id)
    if project is None:
        abort(404)
    return project


def _get_chapter_or_404(project_id, chapter_id):
    chapter = Chapter.query.filter_by(id=chapter_id, project_id=project_id).first()
    if chapter is None:
        abort(404)
    return chapter


def _chapter_md_path(project_id: int, chapter_id: int) -> Path:
    directory = Path(current_app.config["CHAPTER_MD_DIR"])
    return directory / f"project_{project_id}_chapter_{chapter_id}.md"


def _normalize_newlines(text: str) -> str:
    """改行コードを \\n に統一する。

    ブラウザはtextarea送信時に内部の改行(\\n)をすべて\\r\\n(CRLF)に変換して送信するため、
    正規化せずに保存すると、編集→保存を繰り返すたびに\\rが本文に蓄積し、
    表示環境によっては余分な改行に見えてしまう。保存前に必ずこれを通すことで防止する。
    """
    if not text:
        return text
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _read_chapter_content(path: Path) -> str:
    """Markdownファイルが存在しない場合は空文字を返し、システム停止を防止する"""
    if not path.exists():
        return ""
    try:
        return _normalize_newlines(path.read_text(encoding="utf-8"))
    except OSError:
        return ""


@chapters_bp.route("/")
def list_chapters(project_id):
    project = _get_project_or_404(project_id)
    chapters = (
        Chapter.query.filter_by(project_id=project.id).order_by(Chapter.chapter_number).all()
    )
    return render_template("chapters/list.html", project=project, chapters=chapters)


@chapters_bp.route("/new", methods=["GET", "POST"])
def create(project_id):
    project = _get_project_or_404(project_id)
    form = ChapterForm()
    if form.validate_on_submit():
        chapter = Chapter(
            project_id=project.id,
            chapter_number=form.chapter_number.data,
            title=form.title.data,
            summary=form.summary.data,
            goal=form.goal.data,
        )
        db.session.add(chapter)
        db.session.commit()

        # 本文用の空Markdownファイルパスを登録
        md_path = _chapter_md_path(project.id, chapter.id)
        chapter.content_path = str(md_path)
        db.session.commit()

        flash("章を作成しました。", "success")
        return redirect(url_for("chapters.list_chapters", project_id=project.id))
    return render_template("chapters/form.html", form=form, project=project, is_edit=False)


@chapters_bp.route("/<int:chapter_id>/edit", methods=["GET", "POST"])
def edit(project_id, chapter_id):
    project = _get_project_or_404(project_id)
    chapter = _get_chapter_or_404(project_id, chapter_id)

    form = ChapterForm(obj=chapter)
    if form.validate_on_submit():
        chapter.chapter_number = form.chapter_number.data
        chapter.title = form.title.data
        chapter.summary = form.summary.data
        chapter.goal = form.goal.data
        db.session.commit()
        flash("章情報を更新しました。", "success")
        return redirect(url_for("chapters.list_chapters", project_id=project.id))
    return render_template(
        "chapters/form.html", form=form, project=project, is_edit=True, chapter=chapter
    )


@chapters_bp.route("/<int:chapter_id>/content", methods=["GET", "POST"])
def edit_content(project_id, chapter_id):
    """章本文(Markdown)の編集画面"""
    project = _get_project_or_404(project_id)
    chapter = _get_chapter_or_404(project_id, chapter_id)

    md_path = _chapter_md_path(project.id, chapter.id)
    form = ChapterContentForm()

    if form.validate_on_submit():
        try:
            md_path.parent.mkdir(parents=True, exist_ok=True)
            md_path.write_text(_normalize_newlines(form.content.data or ""), encoding="utf-8")
        except OSError:
            # ファイル保存失敗時はDB更新を中止し、エラーメッセージを表示する
            flash("本文の保存に失敗しました。もう一度お試しください。", "danger")
            return render_template(
                "chapters/edit_content.html", form=form, project=project, chapter=chapter
            )

        chapter.content_path = str(md_path)
        db.session.commit()
        flash("本文を保存しました。", "success")
        return redirect(url_for("chapters.list_chapters", project_id=project.id))

    # GET時：ファイルが存在しない場合は空文字として表示する
    form.content.data = _read_chapter_content(md_path)
    return render_template(
        "chapters/edit_content.html", form=form, project=project, chapter=chapter
    )


@chapters_bp.route("/<int:chapter_id>/delete", methods=["POST"])
def delete(project_id, chapter_id):
    project = _get_project_or_404(project_id)
    chapter = _get_chapter_or_404(project_id, chapter_id)

    md_path = _chapter_md_path(project.id, chapter.id)
    if md_path.exists():
        try:
            md_path.unlink()
        except OSError:
            pass  # ファイル削除失敗はDB削除を妨げない

    db.session.delete(chapter)
    db.session.commit()
    flash("章を削除しました。", "success")
    return redirect(url_for("chapters.list_chapters", project_id=project.id))


@chapters_bp.route("/delete_all", methods=["POST"])
def delete_all(project_id):
    """この作品の章をすべて削除する（全体プロットの章だけ再生成したい場合などに使用）"""
    project = _get_project_or_404(project_id)
    chapters = Chapter.query.filter_by(project_id=project.id).all()

    if not chapters:
        flash("削除対象の章がありません。", "danger")
        return redirect(url_for("chapters.list_chapters", project_id=project.id))

    # 伏線が削除対象の章を参照していると不整合になるため、先に参照を外しておく
    from ..models import Foreshadowing

    chapter_ids = [c.id for c in chapters]
    Foreshadowing.query.filter(
        Foreshadowing.project_id == project.id, Foreshadowing.plant_chapter_id.in_(chapter_ids)
    ).update({"plant_chapter_id": None}, synchronize_session=False)
    Foreshadowing.query.filter(
        Foreshadowing.project_id == project.id, Foreshadowing.payoff_chapter_id.in_(chapter_ids)
    ).update({"payoff_chapter_id": None}, synchronize_session=False)

    deleted_count = 0
    for chapter in chapters:
        md_path = _chapter_md_path(project.id, chapter.id)
        if md_path.exists():
            try:
                md_path.unlink()
            except OSError:
                pass
        db.session.delete(chapter)
        deleted_count += 1

    db.session.commit()
    flash(f"章を{deleted_count}件すべて削除しました。", "success")
    return redirect(url_for("chapters.list_chapters", project_id=project.id))


@chapters_bp.route("/generate", methods=["GET", "POST"])
def generate(project_id):
    """AIで次章を生成する（新規章番号を指定するケース）"""
    project = _get_project_or_404(project_id)

    next_number = (
        max([c.chapter_number for c in project.chapters], default=0) + 1
    )
    form = GenerateChapterForm()
    if form.chapter_number.data is None:
        form.chapter_number.data = next_number
    if not form.is_submitted():
        form.provider.data = current_app.config.get("DEFAULT_AI_PROVIDER", "gemini")

    if form.validate_on_submit():
        return _run_generation(
            project,
            chapter_number=form.chapter_number.data,
            title_hint=form.title.data,
            additional_notes=form.additional_notes.data,
            provider=form.provider.data,
        )

    return render_template(
        "chapters/generate.html", form=form, project=project, chapter=None
    )


@chapters_bp.route("/<int:chapter_id>/regenerate", methods=["GET", "POST"])
def regenerate(project_id, chapter_id):
    """既存の章（主にfailed状態）を再生成する"""
    project = _get_project_or_404(project_id)
    chapter = _get_chapter_or_404(project_id, chapter_id)

    form = GenerateChapterForm(chapter_number=chapter.chapter_number, title=chapter.title)
    if not form.is_submitted():
        form.provider.data = chapter.generation_provider or current_app.config.get(
            "DEFAULT_AI_PROVIDER", "gemini"
        )

    if form.validate_on_submit():
        return _run_generation(
            project,
            chapter_number=form.chapter_number.data,
            title_hint=form.title.data,
            additional_notes=form.additional_notes.data,
            provider=form.provider.data,
            existing_chapter=chapter,
        )

    return render_template(
        "chapters/generate.html", form=form, project=project, chapter=chapter
    )


def _run_generation(
    project, chapter_number, title_hint, additional_notes, provider, existing_chapter=None
):
    """AI生成の共通処理：章レコードの用意 → API呼び出し → 保存/エラー処理"""
    chapter = existing_chapter or Chapter.query.filter_by(
        project_id=project.id, chapter_number=chapter_number
    ).first()

    if chapter is None:
        chapter = Chapter(
            project_id=project.id,
            chapter_number=chapter_number,
            title=title_hint or f"第{chapter_number}章（生成中）",
        )
        db.session.add(chapter)

    chapter.generation_status = Chapter.STATUS_PROCESSING
    chapter.generation_error = None
    chapter.generation_provider = provider
    db.session.commit()

    try:
        content = generate_chapter_content(
            project, chapter_number, additional_notes=additional_notes or "", provider=provider
        )
    except AIGenerationError as exc:
        # AI生成エラー時：エラーメッセージ表示、ステータスをfailedへ更新、再生成可能とする
        chapter.generation_status = Chapter.STATUS_FAILED
        chapter.generation_error = str(exc)
        db.session.commit()
        flash(f"AI生成に失敗しました: {exc}", "danger")
        return redirect(
            url_for("chapters.regenerate", project_id=project.id, chapter_id=chapter.id)
        )

    md_path = _chapter_md_path(project.id, chapter.id)
    try:
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(_normalize_newlines(content), encoding="utf-8")
    except OSError as exc:
        # ファイル保存失敗時はDB更新を中止し、エラーメッセージを表示する
        chapter.generation_status = Chapter.STATUS_FAILED
        chapter.generation_error = f"本文の保存に失敗しました: {exc}"
        db.session.commit()
        flash("生成された本文の保存に失敗しました。もう一度お試しください。", "danger")
        return redirect(
            url_for("chapters.regenerate", project_id=project.id, chapter_id=chapter.id)
        )

    if title_hint:
        chapter.title = title_hint
    chapter.content_path = str(md_path)
    chapter.generation_status = Chapter.STATUS_COMPLETED
    chapter.generation_error = None
    db.session.commit()

    flash(f"第{chapter_number}章を{provider}で生成しました。内容を確認・編集してください。", "success")
    return redirect(
        url_for("chapters.edit_content", project_id=project.id, chapter_id=chapter.id)
    )


@chapters_bp.route("/<int:chapter_id>/revise", methods=["GET", "POST"])
def revise(project_id, chapter_id):
    """既存の本文に対して、指示した変更点だけをAIに反映させる（部分修正）"""
    project = _get_project_or_404(project_id)
    chapter = _get_chapter_or_404(project_id, chapter_id)

    md_path = _chapter_md_path(project.id, chapter.id)
    existing_content = _read_chapter_content(md_path)

    if not existing_content:
        flash("この章にはまだ本文がありません。先に本文を生成してください。", "danger")
        return redirect(url_for("chapters.list_chapters", project_id=project.id))

    form = ChapterReviseForm()
    if not form.is_submitted():
        form.provider.data = chapter.generation_provider or current_app.config.get(
            "DEFAULT_AI_PROVIDER", "gemini"
        )

    if form.validate_on_submit():
        try:
            revised_content = revise_chapter_content(
                project,
                chapter,
                existing_content=existing_content,
                revision_instructions=form.revision_instructions.data,
                provider=form.provider.data,
            )
            revised_content = _normalize_newlines(revised_content)
        except AIGenerationError as exc:
            flash(f"AIによる修正に失敗しました: {exc}", "danger")
            return render_template(
                "chapters/revise.html", form=form, project=project, chapter=chapter,
                existing_content=existing_content,
            )

        return render_template(
            "chapters/revise_preview.html",
            project=project,
            chapter=chapter,
            existing_content=existing_content,
            revised_content=revised_content,
        )

    return render_template(
        "chapters/revise.html", form=form, project=project, chapter=chapter,
        existing_content=existing_content,
    )


@chapters_bp.route("/<int:chapter_id>/revise/confirm", methods=["POST"])
def revise_confirm(project_id, chapter_id):
    """修正結果プレビューで確認された本文で、既存のMarkdownファイルを上書きする"""
    project = _get_project_or_404(project_id)
    chapter = _get_chapter_or_404(project_id, chapter_id)

    revised_content = request.form.get("revised_content", "")
    if not revised_content.strip():
        flash("本文が空のため保存できませんでした。", "danger")
        return redirect(url_for("chapters.revise", project_id=project.id, chapter_id=chapter.id))

    md_path = _chapter_md_path(project.id, chapter.id)
    try:
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(_normalize_newlines(revised_content), encoding="utf-8")
    except OSError as exc:
        flash(f"修正後の本文の保存に失敗しました: {exc}", "danger")
        return redirect(url_for("chapters.revise", project_id=project.id, chapter_id=chapter.id))

    chapter.content_path = str(md_path)
    db.session.commit()

    flash("修正後の本文を保存しました。", "success")
    return redirect(url_for("chapters.edit_content", project_id=project.id, chapter_id=chapter.id))


@chapters_bp.route("/<int:chapter_id>/interrogation", methods=["GET", "POST"])
def interrogation(project_id, chapter_id):
    """探偵役・容疑者役の独立セッション方式で、1本道の尋問シナリオを生成する"""
    project = _get_project_or_404(project_id)
    chapter = _get_chapter_or_404(project_id, chapter_id)

    form = InterrogationGenerateForm()
    if not form.is_submitted():
        form.provider.data = current_app.config.get("DEFAULT_AI_PROVIDER", "gemini")

    if form.validate_on_submit():
        try:
            transcript = generate_interrogation(
                project,
                detective_name=form.detective_name.data,
                suspect_name=form.suspect_name.data,
                suspect_secret=form.suspect_secret.data,
                public_context=form.public_context.data or "",
                total_turns=form.turn_count.data,
                provider=form.provider.data,
            )
        except AIGenerationError as exc:
            flash(f"尋問シナリオの生成に失敗しました: {exc}", "danger")
            return render_template(
                "chapters/interrogation.html", form=form, project=project, chapter=chapter
            )

        transcript = _normalize_newlines(transcript)
        existing_content = _read_chapter_content(_chapter_md_path(project.id, chapter.id))

        return render_template(
            "chapters/interrogation_preview.html",
            project=project,
            chapter=chapter,
            transcript=transcript,
            existing_content=existing_content,
        )

    return render_template(
        "chapters/interrogation.html", form=form, project=project, chapter=chapter
    )


@chapters_bp.route("/<int:chapter_id>/interrogation/confirm", methods=["POST"])
def interrogation_confirm(project_id, chapter_id):
    """尋問シナリオのプレビューで確認された内容を、章本文に反映する（上書き／追記を選択）"""
    project = _get_project_or_404(project_id)
    chapter = _get_chapter_or_404(project_id, chapter_id)

    final_content = request.form.get("final_content", "")
    if not final_content.strip():
        flash("本文が空のため保存できませんでした。", "danger")
        return redirect(url_for("chapters.interrogation", project_id=project.id, chapter_id=chapter.id))

    md_path = _chapter_md_path(project.id, chapter.id)
    try:
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(_normalize_newlines(final_content), encoding="utf-8")
    except OSError as exc:
        flash(f"本文の保存に失敗しました: {exc}", "danger")
        return redirect(url_for("chapters.interrogation", project_id=project.id, chapter_id=chapter.id))

    chapter.content_path = str(md_path)
    db.session.commit()

    flash("尋問シナリオを本文に反映しました。", "success")
    return redirect(url_for("chapters.edit_content", project_id=project.id, chapter_id=chapter.id))
