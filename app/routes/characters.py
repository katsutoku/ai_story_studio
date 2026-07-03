from flask import Blueprint, abort, flash, redirect, render_template, url_for

from ..extensions import db
from ..forms import CharacterForm
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
