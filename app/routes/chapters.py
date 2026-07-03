from pathlib import Path

from flask import Blueprint, abort, current_app, flash, redirect, render_template, url_for

from ..extensions import db
from ..forms import ChapterContentForm, ChapterForm
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


def _read_chapter_content(path: Path) -> str:
    """Markdownファイルが存在しない場合は空文字を返し、システム停止を防止する"""
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
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
            md_path.write_text(form.content.data or "", encoding="utf-8")
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
