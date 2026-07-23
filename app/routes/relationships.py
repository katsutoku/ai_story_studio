import json
from datetime import datetime

from flask import Blueprint, abort, current_app, flash, redirect, render_template, url_for

from ..ai_service import AIGenerationError, generate_relationships
from ..diagram_utils import build_vis_network_data
from ..extensions import db
from ..forms import RelationshipGenerateForm
from ..models import Project

relationships_bp = Blueprint(
    "relationships", __name__, url_prefix="/projects/<int:project_id>/relationships"
)


def _get_project_or_404(project_id):
    project = Project.query.get(project_id)
    if project is None:
        abort(404)
    return project


def _content_last_updated_at(project):
    """相関図に影響しうる項目（キャラクター・世界設定・章・伏線）の
    最終更新日時のうち、最も新しいものを返す。

    注意：Project自体のupdated_atは意図的に含めない。相関図データ
    （relationship_diagram_json等）もProjectの列であるため、含めてしまうと
    「保存した直後の自分自身の更新」を「内容が古くなった」と誤検知してしまう。
    作品名・ジャンル・あらすじ・禁止事項欄を直接編集した場合の検知は
    今回は対象外とする（キャラクター等の編集に比べて頻度が低いため）。
    """
    timestamps = []
    for c in project.characters:
        timestamps.append(c.updated_at)
    for w in project.world_settings:
        timestamps.append(w.updated_at)
    for ch in project.chapters:
        timestamps.append(ch.updated_at)
    for f in project.foreshadowings:
        timestamps.append(f.updated_at)
    timestamps = [t for t in timestamps if t is not None]
    return max(timestamps) if timestamps else None


def _is_diagram_stale(project) -> bool:
    if not project.relationship_diagram_generated_at:
        return False  # キャッシュ自体がない場合は「古い」ではなく「未生成」として別扱いする
    last_updated = _content_last_updated_at(project)
    if last_updated is None:
        return False
    return last_updated > project.relationship_diagram_generated_at


@relationships_bp.route("/")
def view(project_id):
    """保存済みの相関図があればAIを呼ばずに表示する。なければ生成画面へ誘導する。"""
    project = _get_project_or_404(project_id)

    if not project.relationship_diagram_json:
        return redirect(url_for("relationships.generate", project_id=project.id))

    try:
        relationships = json.loads(project.relationship_diagram_json)
    except (json.JSONDecodeError, TypeError):
        relationships = []

    vis_data = build_vis_network_data(relationships, characters=project.characters)
    return render_template(
        "relationships/result.html",
        project=project,
        vis_data_json=json.dumps(vis_data, ensure_ascii=False),
        relationships=relationships,
        is_stale=_is_diagram_stale(project),
        generated_at=project.relationship_diagram_generated_at,
    )


@relationships_bp.route("/generate", methods=["GET", "POST"])
def generate(project_id):
    """AIに人物同士の関係性を考えさせ、相関図としてDBに保存する"""
    project = _get_project_or_404(project_id)

    if not project.characters:
        flash("人物相関図を生成するには、先にキャラクターを登録してください。", "danger")
        return render_template("relationships/generate.html", form=None, project=project)

    form = RelationshipGenerateForm()
    if not form.is_submitted():
        form.provider.data = current_app.config.get("DEFAULT_AI_PROVIDER", "gemini")

    if form.validate_on_submit():
        try:
            relationships = generate_relationships(
                project,
                additional_notes=form.additional_notes.data or "",
                provider=form.provider.data,
            )
        except AIGenerationError as exc:
            flash(f"AI生成に失敗しました: {exc}", "danger")
            return render_template("relationships/generate.html", form=form, project=project)

        vis_data = build_vis_network_data(relationships, characters=project.characters)
        if not vis_data["nodes"]:
            flash(
                "関係性データを取得できませんでした。追加指示を変えて再試行してください。", "danger"
            )
            return render_template("relationships/generate.html", form=form, project=project)

        # 生成結果をDBに保存する（次回以降はAIを呼ばずにこのキャッシュを表示する）
        project.relationship_diagram_json = json.dumps(relationships, ensure_ascii=False)
        project.relationship_diagram_generated_at = datetime.utcnow()
        db.session.commit()

        flash("人物相関図を生成しました。", "success")
        return redirect(url_for("relationships.view", project_id=project.id))

    return render_template("relationships/generate.html", form=form, project=project)
