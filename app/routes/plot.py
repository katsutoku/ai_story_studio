import json

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for

from ..ai_service import AIGenerationError, generate_full_plot
from ..extensions import db
from ..forms import PlotGenerateForm
from ..models import Character, Chapter, Foreshadowing, Project, WorldSetting

plot_bp = Blueprint("plot", __name__, url_prefix="/projects/<int:project_id>/plot")


def _get_project_or_404(project_id):
    project = Project.query.get(project_id)
    if project is None:
        abort(404)
    return project


@plot_bp.route("/generate", methods=["GET", "POST"])
def generate(project_id):
    """AIにキャラクター・世界設定・全章の設計図・伏線をまとめて考えさせ、プレビュー画面へ渡す"""
    project = _get_project_or_404(project_id)
    form = PlotGenerateForm()
    if not form.is_submitted():
        form.provider.data = current_app.config.get("DEFAULT_AI_PROVIDER", "gemini")

    if form.validate_on_submit():
        start_number = max([c.chapter_number for c in project.chapters], default=0) + 1
        try:
            plot = generate_full_plot(
                project,
                chapter_count=form.chapter_count.data,
                start_chapter_number=start_number,
                additional_notes=form.additional_notes.data or "",
                provider=form.provider.data,
            )
        except AIGenerationError as exc:
            flash(f"AI生成に失敗しました: {exc}", "danger")
            return render_template("plot/generate.html", form=form, project=project)

        if not any(plot.get(key) for key in ("characters", "world_settings", "chapters", "foreshadowings")):
            flash("AIが候補を生成しませんでした。追加指示を変えて再試行してください。", "danger")
            return render_template("plot/generate.html", form=form, project=project)

        # 伏線プレビュー表示用：AIが返す章番号→タイトルの対応表（この時点ではまだ章はDB未登録）
        chapter_title_by_number = {
            ch.get("chapter_number"): ch.get("title")
            for ch in plot.get("chapters", [])
            if isinstance(ch, dict)
        }

        generated_json = json.dumps(plot, ensure_ascii=False)
        return render_template(
            "plot/preview.html",
            project=project,
            plot=plot,
            generated_json=generated_json,
            chapter_title_by_number=chapter_title_by_number,
        )

    return render_template("plot/generate.html", form=form, project=project)


@plot_bp.route("/generate/confirm", methods=["POST"])
def generate_confirm(project_id):
    """プレビュー画面で選択された項目のみをDBに保存する"""
    project = _get_project_or_404(project_id)

    generated_json = request.form.get("generated_json", "")
    try:
        plot = json.loads(generated_json)
    except (json.JSONDecodeError, TypeError):
        flash("生成結果の読み込みに失敗しました。もう一度生成し直してください。", "danger")
        return redirect(url_for("plot.generate", project_id=project.id))

    selected_characters = {int(i) for i in request.form.getlist("selected_characters") if i.isdigit()}
    selected_world_settings = {int(i) for i in request.form.getlist("selected_world_settings") if i.isdigit()}
    selected_chapters = {int(i) for i in request.form.getlist("selected_chapters") if i.isdigit()}
    selected_foreshadowings = {int(i) for i in request.form.getlist("selected_foreshadowings") if i.isdigit()}

    created = {"characters": 0, "world_settings": 0, "chapters": 0, "foreshadowings": 0}

    # --- キャラクター ---
    for i, c in enumerate(plot.get("characters", [])):
        if i not in selected_characters or not isinstance(c, dict):
            continue
        name = str(c.get("name") or "").strip()
        if not name:
            continue
        db.session.add(Character(
            project_id=project.id,
            name=name[:100],
            age=str(c.get("age") or "")[:20] or None,
            gender=str(c.get("gender") or "")[:20] or None,
            personality=c.get("personality") or None,
            appearance=c.get("appearance") or None,
            background=c.get("background") or None,
            notes=c.get("notes") or None,
        ))
        created["characters"] += 1

    # --- 世界設定 ---
    for i, w in enumerate(plot.get("world_settings", [])):
        if i not in selected_world_settings or not isinstance(w, dict):
            continue
        name = str(w.get("name") or "").strip()
        if not name:
            continue
        db.session.add(WorldSetting(
            project_id=project.id,
            name=name[:100],
            world_view=w.get("world_view") or None,
            era=w.get("era") or None,
            rules=w.get("rules") or None,
            terminology=w.get("terminology") or None,
            other=w.get("other") or None,
        ))
        created["world_settings"] += 1

    # --- 章の設計図（本文はまだ書かない。タイトル・要約・目的のみ登録） ---
    existing_numbers = {c.chapter_number for c in project.chapters}
    for i, ch in enumerate(plot.get("chapters", [])):
        if i not in selected_chapters or not isinstance(ch, dict):
            continue
        try:
            chapter_number = int(ch.get("chapter_number"))
        except (TypeError, ValueError):
            continue
        title = str(ch.get("title") or "").strip()
        if not title or chapter_number in existing_numbers:
            continue  # 章番号が重複する場合は安全のためスキップ
        db.session.add(Chapter(
            project_id=project.id,
            chapter_number=chapter_number,
            title=title[:200],
            summary=ch.get("summary") or None,
            goal=ch.get("goal") or None,
        ))
        existing_numbers.add(chapter_number)
        created["chapters"] += 1

    # 章を先にコミットして、伏線から章番号→章IDを引けるようにする
    db.session.commit()
    chapter_id_by_number = {
        c.chapter_number: c.id for c in Chapter.query.filter_by(project_id=project.id).all()
    }

    def _resolve_chapter_id(value):
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        return chapter_id_by_number.get(number)

    # --- 伏線 ---
    for i, f in enumerate(plot.get("foreshadowings", [])):
        if i not in selected_foreshadowings or not isinstance(f, dict):
            continue
        title = str(f.get("title") or "").strip()
        if not title:
            continue
        db.session.add(Foreshadowing(
            project_id=project.id,
            title=title[:200],
            plant_content=f.get("plant_content") or None,
            payoff_content=f.get("payoff_content") or None,
            plant_chapter_id=_resolve_chapter_id(f.get("plant_chapter_number")),
            payoff_chapter_id=_resolve_chapter_id(f.get("payoff_chapter_number")),
            status=Foreshadowing.STATUS_PENDING,
        ))
        created["foreshadowings"] += 1

    db.session.commit()

    total = sum(created.values())
    if total == 0:
        flash("登録できる項目がありませんでした。", "danger")
        return redirect(url_for("plot.generate", project_id=project.id))

    flash(
        f"全体プロットを登録しました（キャラクター{created['characters']}件・"
        f"世界設定{created['world_settings']}件・章の設計図{created['chapters']}件・"
        f"伏線{created['foreshadowings']}件）。章一覧から各章の本文を生成してください。",
        "success",
    )
    return redirect(url_for("projects.detail", project_id=project.id))
