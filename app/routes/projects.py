from flask import Blueprint, abort, flash, redirect, render_template, url_for

from ..extensions import db
from ..forms import ProjectForm
from ..models import Project

projects_bp = Blueprint("projects", __name__, url_prefix="/projects")


@projects_bp.route("/")
def list_projects():
    projects = Project.query.order_by(Project.updated_at.desc()).all()
    return render_template("projects/list.html", projects=projects)


@projects_bp.route("/<int:project_id>")
def detail(project_id):
    project = Project.query.get(project_id)
    if project is None:
        abort(404)
    return render_template("projects/detail.html", project=project)


@projects_bp.route("/new", methods=["GET", "POST"])
def create():
    form = ProjectForm()
    if form.validate_on_submit():
        project = Project(
            title=form.title.data,
            genre=form.genre.data,
            synopsis=form.synopsis.data,
            constraints=form.constraints.data,
        )
        db.session.add(project)
        db.session.commit()
        flash("作品を作成しました。", "success")
        return redirect(url_for("projects.detail", project_id=project.id))
    return render_template("projects/form.html", form=form, is_edit=False)


@projects_bp.route("/<int:project_id>/edit", methods=["GET", "POST"])
def edit(project_id):
    project = Project.query.get(project_id)
    if project is None:
        abort(404)

    form = ProjectForm(obj=project)
    if form.validate_on_submit():
        project.title = form.title.data
        project.genre = form.genre.data
        project.synopsis = form.synopsis.data
        project.constraints = form.constraints.data
        db.session.commit()
        flash("作品情報を更新しました。", "success")
        return redirect(url_for("projects.detail", project_id=project.id))
    return render_template("projects/form.html", form=form, is_edit=True, project=project)


@projects_bp.route("/<int:project_id>/delete", methods=["POST"])
def delete(project_id):
    project = Project.query.get(project_id)
    if project is None:
        abort(404)
    db.session.delete(project)
    db.session.commit()
    flash("作品を削除しました。", "success")
    return redirect(url_for("projects.list_projects"))
