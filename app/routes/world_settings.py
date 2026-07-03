from flask import Blueprint, abort, flash, redirect, render_template, url_for

from ..extensions import db
from ..forms import WorldSettingForm
from ..models import Project, WorldSetting

world_settings_bp = Blueprint(
    "world_settings", __name__, url_prefix="/projects/<int:project_id>/world-settings"
)


def _get_project_or_404(project_id):
    project = Project.query.get(project_id)
    if project is None:
        abort(404)
    return project


@world_settings_bp.route("/")
def list_world_settings(project_id):
    project = _get_project_or_404(project_id)
    settings = WorldSetting.query.filter_by(project_id=project.id).order_by(WorldSetting.id).all()
    return render_template("world_settings/list.html", project=project, settings=settings)


@world_settings_bp.route("/new", methods=["GET", "POST"])
def create(project_id):
    project = _get_project_or_404(project_id)
    form = WorldSettingForm()
    if form.validate_on_submit():
        setting = WorldSetting(
            project_id=project.id,
            name=form.name.data,
            world_view=form.world_view.data,
            era=form.era.data,
            rules=form.rules.data,
            terminology=form.terminology.data,
            other=form.other.data,
        )
        db.session.add(setting)
        db.session.commit()
        flash("世界設定を登録しました。", "success")
        return redirect(url_for("world_settings.list_world_settings", project_id=project.id))
    return render_template("world_settings/form.html", form=form, project=project, is_edit=False)


@world_settings_bp.route("/<int:setting_id>/edit", methods=["GET", "POST"])
def edit(project_id, setting_id):
    project = _get_project_or_404(project_id)
    setting = WorldSetting.query.filter_by(id=setting_id, project_id=project.id).first()
    if setting is None:
        abort(404)

    form = WorldSettingForm(obj=setting)
    if form.validate_on_submit():
        setting.name = form.name.data
        setting.world_view = form.world_view.data
        setting.era = form.era.data
        setting.rules = form.rules.data
        setting.terminology = form.terminology.data
        setting.other = form.other.data
        db.session.commit()
        flash("世界設定を更新しました。", "success")
        return redirect(url_for("world_settings.list_world_settings", project_id=project.id))
    return render_template(
        "world_settings/form.html", form=form, project=project, is_edit=True, setting=setting
    )


@world_settings_bp.route("/<int:setting_id>/delete", methods=["POST"])
def delete(project_id, setting_id):
    project = _get_project_or_404(project_id)
    setting = WorldSetting.query.filter_by(id=setting_id, project_id=project.id).first()
    if setting is None:
        abort(404)
    db.session.delete(setting)
    db.session.commit()
    flash("世界設定を削除しました。", "success")
    return redirect(url_for("world_settings.list_world_settings", project_id=project.id))
