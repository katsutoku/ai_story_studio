import json

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for

from ..ai_service import AIGenerationError, generate_foreshadowings
from ..extensions import db
from ..forms import ForeshadowingForm, ForeshadowingGenerateForm
from ..models import Chapter, Foreshadowing, Project

foreshadowings_bp = Blueprint(
    "foreshadowings", __name__, url_prefix="/projects/<int:project_id>/foreshadowings"
)


def _get_project_or_404(project_id):
    project = Project.query.get(project_id)
    if project is None:
        abort(404)
    return project


def _chapter_choices(project_id):
    chapters = Chapter.query.filter_by(project_id=project_id).order_by(Chapter.chapter_number).all()
    choices = [(0, "未設定")] + [(c.id, f"第{c.chapter_number}章 {c.title}") for c in chapters]
    return choices


def _populate_form_choices(form, project_id):
    choices = _chapter_choices(project_id)
    form.plant_chapter_id.choices = choices
    form.payoff_chapter_id.choices = choices


@foreshadowings_bp.route("/")
def list_foreshadowings(project_id):
    project = _get_project_or_404(project_id)
    items = (
        Foreshadowing.query.filter_by(project_id=project.id).order_by(Foreshadowing.id).all()
    )
    return render_template("foreshadowings/list.html", project=project, items=items)


@foreshadowings_bp.route("/new", methods=["GET", "POST"])
def create(project_id):
    project = _get_project_or_404(project_id)
    form = ForeshadowingForm()
    _populate_form_choices(form, project.id)

    if form.validate_on_submit():
        item = Foreshadowing(
            project_id=project.id,
            title=form.title.data,
            plant_content=form.plant_content.data,
            payoff_content=form.payoff_content.data,
            plant_chapter_id=form.plant_chapter_id.data or None,
            payoff_chapter_id=form.payoff_chapter_id.data or None,
            status=form.status.data,
        )
        db.session.add(item)
        db.session.commit()
        flash("伏線を登録しました。", "success")
        return redirect(url_for("foreshadowings.list_foreshadowings", project_id=project.id))
    return render_template("foreshadowings/form.html", form=form, project=project, is_edit=False)


@foreshadowings_bp.route("/<int:item_id>/edit", methods=["GET", "POST"])
def edit(project_id, item_id):
    project = _get_project_or_404(project_id)
    item = Foreshadowing.query.filter_by(id=item_id, project_id=project.id).first()
    if item is None:
        abort(404)

    form = ForeshadowingForm(obj=item)
    _populate_form_choices(form, project.id)

    if form.validate_on_submit():
        item.title = form.title.data
        item.plant_content = form.plant_content.data
        item.payoff_content = form.payoff_content.data
        item.plant_chapter_id = form.plant_chapter_id.data or None
        item.payoff_chapter_id = form.payoff_chapter_id.data or None
        item.status = form.status.data
        db.session.commit()
        flash("伏線情報を更新しました。", "success")
        return redirect(url_for("foreshadowings.list_foreshadowings", project_id=project.id))

    if not form.is_submitted():
        form.plant_chapter_id.data = item.plant_chapter_id or 0
        form.payoff_chapter_id.data = item.payoff_chapter_id or 0

    return render_template(
        "foreshadowings/form.html", form=form, project=project, is_edit=True, item=item
    )


@foreshadowings_bp.route("/<int:item_id>/delete", methods=["POST"])
def delete(project_id, item_id):
    project = _get_project_or_404(project_id)
    item = Foreshadowing.query.filter_by(id=item_id, project_id=project.id).first()
    if item is None:
        abort(404)
    db.session.delete(item)
    db.session.commit()
    flash("伏線を削除しました。", "success")
    return redirect(url_for("foreshadowings.list_foreshadowings", project_id=project.id))


@foreshadowings_bp.route("/delete_all", methods=["POST"])
def delete_all(project_id):
    """この作品の伏線をすべて削除する（全体プロットの伏線だけ再生成したい場合などに使用）"""
    project = _get_project_or_404(project_id)
    items = Foreshadowing.query.filter_by(project_id=project.id).all()

    if not items:
        flash("削除対象の伏線がありません。", "danger")
        return redirect(url_for("foreshadowings.list_foreshadowings", project_id=project.id))

    deleted_count = len(items)
    for item in items:
        db.session.delete(item)
    db.session.commit()
    flash(f"伏線を{deleted_count}件すべて削除しました。", "success")
    return redirect(url_for("foreshadowings.list_foreshadowings", project_id=project.id))


@foreshadowings_bp.route("/generate", methods=["GET", "POST"])
def generate(project_id):
    """AIに伏線案を考えさせ、プレビュー画面へ渡す"""
    project = _get_project_or_404(project_id)
    form = ForeshadowingGenerateForm()
    if not form.is_submitted():
        form.provider.data = current_app.config.get("DEFAULT_AI_PROVIDER", "gemini")

    if form.validate_on_submit():
        try:
            candidates = generate_foreshadowings(
                project,
                count=form.count.data,
                additional_notes=form.additional_notes.data or "",
                provider=form.provider.data,
            )
        except AIGenerationError as exc:
            current_app.logger.exception("伏線のAI生成に失敗しました (project_id=%s)", project.id)
            flash(f"AI生成に失敗しました: {exc}", "danger")
            return render_template("foreshadowings/generate.html", form=form, project=project)

        if not candidates:
            flash("AIが候補を生成しませんでした。追加指示を変えて再試行してください。", "danger")
            return render_template("foreshadowings/generate.html", form=form, project=project)

        # プレビュー表示用に、AIが返した章番号から実際の章タイトルを引けるようにしておく
        chapter_by_number = {c.chapter_number: c for c in project.chapters}

        generated_json = json.dumps(candidates, ensure_ascii=False)
        return render_template(
            "foreshadowings/preview.html",
            project=project,
            candidates=candidates,
            generated_json=generated_json,
            chapter_by_number=chapter_by_number,
        )

    return render_template("foreshadowings/generate.html", form=form, project=project)


@foreshadowings_bp.route("/generate/confirm", methods=["POST"])
def generate_confirm(project_id):
    """プレビュー画面で選択された伏線のみをDBに保存する"""
    project = _get_project_or_404(project_id)

    generated_json = request.form.get("generated_json", "")
    selected_indices = {int(i) for i in request.form.getlist("selected") if i.isdigit()}

    try:
        candidates = json.loads(generated_json)
    except (json.JSONDecodeError, TypeError):
        flash("生成結果の読み込みに失敗しました。もう一度生成し直してください。", "danger")
        return redirect(url_for("foreshadowings.generate", project_id=project.id))

    if not selected_indices:
        flash("登録する伏線が選択されていません。", "danger")
        return redirect(url_for("foreshadowings.generate", project_id=project.id))

    # AIが返す章番号 → 実際のChapter.id への変換用マップ
    chapter_id_by_number = {
        c.chapter_number: c.id for c in Chapter.query.filter_by(project_id=project.id).all()
    }

    def _resolve_chapter_id(value):
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        return chapter_id_by_number.get(number)

    created_count = 0
    for i, candidate in enumerate(candidates):
        if i not in selected_indices or not isinstance(candidate, dict):
            continue
        title = str(candidate.get("title") or "").strip()
        if not title:
            continue  # タイトルが空の候補は登録しない（必須項目のため）
        item = Foreshadowing(
            project_id=project.id,
            title=title[:200],
            plant_content=candidate.get("plant_content") or None,
            payoff_content=candidate.get("payoff_content") or None,
            plant_chapter_id=_resolve_chapter_id(candidate.get("plant_chapter_number")),
            payoff_chapter_id=_resolve_chapter_id(candidate.get("payoff_chapter_number")),
            status=Foreshadowing.STATUS_PENDING,
        )
        db.session.add(item)
        created_count += 1

    if created_count == 0:
        flash("登録できる伏線がありませんでした（タイトルが空でした）。", "danger")
        return redirect(url_for("foreshadowings.generate", project_id=project.id))

    db.session.commit()
    flash(f"{created_count}件の伏線を登録しました。内容を確認・編集してください。", "success")
    return redirect(url_for("foreshadowings.list_foreshadowings", project_id=project.id))
