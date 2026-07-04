import json

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for

from ..ai_service import AIGenerationError, generate_characters
from ..extensions import db
from ..forms import CharacterForm, CharacterGenerateForm
from ..models import Character, Project

characters_bp = Blueprint("characters", __name__, url_prefix="/projects/<int:project_id>/characters")


def _get_project_or_404(project_id):
    project = Project.query.get(project_id)
    if project is None:
        abort(404)
    return project


@characters_bp.route("/")
def list_characters(project_id):
    project = _get_project_or_404(project_id)
    characters = Character.query.filter_by(project_id=project.id).order_by(Character.id).all()
    return render_template("characters/list.html", project=project, characters=characters)


@characters_bp.route("/new", methods=["GET", "POST"])
def create(project_id):
    project = _get_project_or_404(project_id)
    form = CharacterForm()
    if form.validate_on_submit():
        character = Character(
            project_id=project.id,
            name=form.name.data,
            age=form.age.data,
            gender=form.gender.data,
            personality=form.personality.data,
            appearance=form.appearance.data,
            background=form.background.data,
            notes=form.notes.data,
        )
        db.session.add(character)
        db.session.commit()
        flash("キャラクターを登録しました。", "success")
        return redirect(url_for("characters.list_characters", project_id=project.id))
    return render_template("characters/form.html", form=form, project=project, is_edit=False)


@characters_bp.route("/<int:character_id>/edit", methods=["GET", "POST"])
def edit(project_id, character_id):
    project = _get_project_or_404(project_id)
    character = Character.query.filter_by(id=character_id, project_id=project.id).first()
    if character is None:
        abort(404)

    form = CharacterForm(obj=character)
    if form.validate_on_submit():
        character.name = form.name.data
        character.age = form.age.data
        character.gender = form.gender.data
        character.personality = form.personality.data
        character.appearance = form.appearance.data
        character.background = form.background.data
        character.notes = form.notes.data
        db.session.commit()
        flash("キャラクター情報を更新しました。", "success")
        return redirect(url_for("characters.list_characters", project_id=project.id))
    return render_template(
        "characters/form.html", form=form, project=project, is_edit=True, character=character
    )


@characters_bp.route("/<int:character_id>/delete", methods=["POST"])
def delete(project_id, character_id):
    project = _get_project_or_404(project_id)
    character = Character.query.filter_by(id=character_id, project_id=project.id).first()
    if character is None:
        abort(404)
    db.session.delete(character)
    db.session.commit()
    flash("キャラクターを削除しました。", "success")
    return redirect(url_for("characters.list_characters", project_id=project.id))


@characters_bp.route("/generate", methods=["GET", "POST"])
def generate(project_id):
    """AIにキャラクター案を考えさせ、プレビュー画面へ渡す"""
    project = _get_project_or_404(project_id)
    form = CharacterGenerateForm()
    if not form.is_submitted():
        form.provider.data = current_app.config.get("DEFAULT_AI_PROVIDER", "gemini")

    if form.validate_on_submit():
        try:
            candidates = generate_characters(
                project,
                count=form.count.data,
                additional_notes=form.additional_notes.data or "",
                provider=form.provider.data,
            )
        except AIGenerationError as exc:
            flash(f"AI生成に失敗しました: {exc}", "danger")
            return render_template(
                "characters/generate.html", form=form, project=project
            )

        if not candidates:
            flash("AIが候補を生成しませんでした。追加指示を変えて再試行してください。", "danger")
            return render_template(
                "characters/generate.html", form=form, project=project
            )

        # プレビュー画面へ、生成結果をJSONとして引き渡す（この時点ではまだDBに保存しない）
        generated_json = json.dumps(candidates, ensure_ascii=False)
        return render_template(
            "characters/preview.html",
            project=project,
            candidates=candidates,
            generated_json=generated_json,
        )

    return render_template("characters/generate.html", form=form, project=project)


@characters_bp.route("/generate/confirm", methods=["POST"])
def generate_confirm(project_id):
    """プレビュー画面で選択されたキャラクターのみをDBに保存する"""
    project = _get_project_or_404(project_id)

    generated_json = request.form.get("generated_json", "")
    selected_indices = {int(i) for i in request.form.getlist("selected") if i.isdigit()}

    try:
        candidates = json.loads(generated_json)
    except (json.JSONDecodeError, TypeError):
        flash("生成結果の読み込みに失敗しました。もう一度生成し直してください。", "danger")
        return redirect(url_for("characters.generate", project_id=project.id))

    if not selected_indices:
        flash("登録するキャラクターが選択されていません。", "danger")
        return redirect(url_for("characters.generate", project_id=project.id))

    created_count = 0
    for i, candidate in enumerate(candidates):
        if i not in selected_indices or not isinstance(candidate, dict):
            continue
        name = str(candidate.get("name") or "").strip()
        if not name:
            continue  # 名前が空の候補は登録しない（必須項目のため）
        character = Character(
            project_id=project.id,
            name=name[:100],
            age=str(candidate.get("age") or "")[:20] or None,
            gender=str(candidate.get("gender") or "")[:20] or None,
            personality=candidate.get("personality") or None,
            appearance=candidate.get("appearance") or None,
            background=candidate.get("background") or None,
            notes=candidate.get("notes") or None,
        )
        db.session.add(character)
        created_count += 1

    if created_count == 0:
        flash("登録できるキャラクターがありませんでした（名前が空でした）。", "danger")
        return redirect(url_for("characters.generate", project_id=project.id))

    db.session.commit()
    flash(f"{created_count}件のキャラクターを登録しました。内容を確認・編集してください。", "success")
    return redirect(url_for("characters.list_characters", project_id=project.id))
