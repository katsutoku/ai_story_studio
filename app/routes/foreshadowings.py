from flask import Blueprint, abort, flash, redirect, render_template, url_for

from ..extensions import db
from ..forms import ForeshadowingForm
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
