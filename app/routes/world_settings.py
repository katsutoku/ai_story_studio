import json

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for

from ..ai_service import AIGenerationError, generate_world_settings
from ..extensions import db
from ..forms import WorldSettingForm, WorldSettingGenerateForm
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


@world_settings_bp.route("/generate", methods=["GET", "POST"])
def generate(project_id):
    """AIに世界設定案を考えさせ、プレビュー画面へ渡す"""
    project = _get_project_or_404(project_id)
    form = WorldSettingGenerateForm()
    if not form.is_submitted():
        form.provider.data = current_app.config.get("DEFAULT_AI_PROVIDER", "gemini")

    if form.validate_on_submit():
        try:
            candidates = generate_world_settings(
                project,
                count=form.count.data,
                additional_notes=form.additional_notes.data or "",
                provider=form.provider.data,
            )
        except AIGenerationError as exc:
            flash(f"AI生成に失敗しました: {exc}", "danger")
            return render_template("world_settings/generate.html", form=form, project=project)

        if not candidates:
            flash("AIが候補を生成しませんでした。追加指示を変えて再試行してください。", "danger")
            return render_template("world_settings/generate.html", form=form, project=project)

        generated_json = json.dumps(candidates, ensure_ascii=False)
        return render_template(
            "world_settings/preview.html",
            project=project,
            candidates=candidates,
            generated_json=generated_json,
        )

    return render_template("world_settings/generate.html", form=form, project=project)


@world_settings_bp.route("/generate/confirm", methods=["POST"])
def generate_confirm(project_id):
    """プレビュー画面で選択された世界設定のみをDBに保存する"""
    project = _get_project_or_404(project_id)

    generated_json = request.form.get("generated_json", "")
    selected_indices = {int(i) for i in request.form.getlist("selected") if i.isdigit()}

    try:
        candidates = json.loads(generated_json)
    except (json.JSONDecodeError, TypeError):
        flash("生成結果の読み込みに失敗しました。もう一度生成し直してください。", "danger")
        return redirect(url_for("world_settings.generate", project_id=project.id))

    if not selected_indices:
        flash("登録する世界設定が選択されていません。", "danger")
        return redirect(url_for("world_settings.generate", project_id=project.id))

    created_count = 0
    for i, candidate in enumerate(candidates):
        if i not in selected_indices or not isinstance(candidate, dict):
            continue
        name = str(candidate.get("name") or "").strip()
        if not name:
            continue  # 設定名が空の候補は登録しない（必須項目のため）
        setting = WorldSetting(
            project_id=project.id,
            name=name[:100],
            world_view=candidate.get("world_view") or None,
            era=candidate.get("era") or None,
            rules=candidate.get("rules") or None,
            terminology=candidate.get("terminology") or None,
            other=candidate.get("other") or None,
        )
        db.session.add(setting)
        created_count += 1

    if created_count == 0:
        flash("登録できる世界設定がありませんでした（設定名が空でした）。", "danger")
        return redirect(url_for("world_settings.generate", project_id=project.id))

    db.session.commit()
    flash(f"{created_count}件の世界設定を登録しました。内容を確認・編集してください。", "success")
    return redirect(url_for("world_settings.list_world_settings", project_id=project.id))
