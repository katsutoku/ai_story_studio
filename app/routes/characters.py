import json
import uuid
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from werkzeug.utils import secure_filename

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


def _thumbnail_dir() -> Path:
    directory = Path(current_app.config["CHARACTER_THUMBNAIL_DIR"])
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _delete_thumbnail_file(filename):
    """サムネイル画像ファイルを削除する（存在しない・失敗しても処理は止めない）"""
    if not filename:
        return
    path = _thumbnail_dir() / filename
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass


def _save_thumbnail_file(character_id, file_storage) -> str:
    """アップロードされた画像をサーバー側のディスクに保存し、保存したファイル名を返す。

    ファイル名は「character_{id}_{ランダム文字列}.{拡張子}」の形式にし、
    利用者が元々付けていたファイル名は使わない（パス操作等を防ぐため、
    secure_filenameで拡張子だけを安全に取り出す）。
    """
    original_name = secure_filename(file_storage.filename or "")
    ext = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else "jpg"
    filename = f"character_{character_id}_{uuid.uuid4().hex[:8]}.{ext}"
    file_storage.save(str(_thumbnail_dir() / filename))
    return filename


def _find_duplicate_character(project_id, name, exclude_id=None):
    """同一プロジェクト内で、前後の空白・大文字小文字を無視して同名のキャラクターを探す"""
    normalized = (name or "").strip().lower()
    if not normalized:
        return None
    query = Character.query.filter_by(project_id=project_id)
    if exclude_id is not None:
        query = query.filter(Character.id != exclude_id)
    for c in query.all():
        if c.name.strip().lower() == normalized:
            return c
    return None


@characters_bp.route("/")
def list_characters(project_id):
    project = _get_project_or_404(project_id)
    # 事件専用のモブキャラ（Character.mystery_case_idが設定されているもの）は、
    # メインのキャラクター管理一覧には出さない（事件詳細画面から管理する）
    characters = (
        Character.query.filter_by(project_id=project.id)
        .filter(Character.mystery_case_id.is_(None))
        .order_by(Character.id)
        .all()
    )
    return render_template("characters/list.html", project=project, characters=characters)


@characters_bp.route("/new", methods=["GET", "POST"])
def create(project_id):
    project = _get_project_or_404(project_id)
    form = CharacterForm()

    if form.validate_on_submit():
        duplicate = _find_duplicate_character(project.id, form.name.data)
        action = request.form.get("action", "save")

        if duplicate and action != "update_existing":
            # 同名キャラクターが既にいる場合、確認なしでは保存せず警告を表示する
            flash(
                f"同名のキャラクター「{duplicate.name}」が既に登録されています。"
                "内容を確認し、下のボタンから既存のキャラクターを更新するか、"
                "名前を変えて別のキャラクターとして登録してください。",
                "danger",
            )
            return render_template(
                "characters/form.html", form=form, project=project, is_edit=False,
                duplicate=duplicate,
            )

        if duplicate and action == "update_existing":
            # 新規作成はせず、既存キャラクターの内容を上書き更新する
            duplicate.age = form.age.data
            duplicate.gender = form.gender.data
            duplicate.personality = form.personality.data
            duplicate.appearance = form.appearance.data
            duplicate.background = form.background.data
            duplicate.notes = form.notes.data
            if form.remove_thumbnail.data:
                _delete_thumbnail_file(duplicate.thumbnail_filename)
                duplicate.thumbnail_filename = None
            elif form.thumbnail.data:
                _delete_thumbnail_file(duplicate.thumbnail_filename)
                duplicate.thumbnail_filename = _save_thumbnail_file(duplicate.id, form.thumbnail.data)
            db.session.commit()
            flash(f"既存のキャラクター「{duplicate.name}」を更新しました。", "success")
            return redirect(url_for("characters.list_characters", project_id=project.id))

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
        db.session.commit()  # サムネイルのファイル名にIDを使うため、先にコミットしてIDを確定させる

        if form.thumbnail.data:
            character.thumbnail_filename = _save_thumbnail_file(character.id, form.thumbnail.data)
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
        duplicate = _find_duplicate_character(project.id, form.name.data, exclude_id=character.id)
        if duplicate:
            flash(
                f"同名のキャラクター「{duplicate.name}」が既に別に登録されています。"
                "名前を変えるか、不要な方を削除してください。",
                "danger",
            )
            return render_template(
                "characters/form.html", form=form, project=project, is_edit=True,
                character=character, duplicate=duplicate,
            )

        character.name = form.name.data
        character.age = form.age.data
        character.gender = form.gender.data
        character.personality = form.personality.data
        character.appearance = form.appearance.data
        character.background = form.background.data
        character.notes = form.notes.data
        if form.remove_thumbnail.data:
            _delete_thumbnail_file(character.thumbnail_filename)
            character.thumbnail_filename = None
        elif form.thumbnail.data:
            _delete_thumbnail_file(character.thumbnail_filename)
            character.thumbnail_filename = _save_thumbnail_file(character.id, form.thumbnail.data)
        db.session.commit()
        flash("キャラクター情報を更新しました。", "success")
        return redirect(url_for("characters.list_characters", project_id=project.id))
    return render_template(
        "characters/form.html", form=form, project=project, is_edit=True, character=character
    )


@characters_bp.route("/<int:character_id>/thumbnail")
def thumbnail(project_id, character_id):
    """キャラクターのサムネイル画像を配信する"""
    project = _get_project_or_404(project_id)
    character = Character.query.filter_by(id=character_id, project_id=project.id).first()
    if character is None or not character.thumbnail_filename:
        abort(404)
    return send_from_directory(_thumbnail_dir(), character.thumbnail_filename)


@characters_bp.route("/<int:character_id>/delete", methods=["POST"])
def delete(project_id, character_id):
    project = _get_project_or_404(project_id)
    character = Character.query.filter_by(id=character_id, project_id=project.id).first()
    if character is None:
        abort(404)
    _delete_thumbnail_file(character.thumbnail_filename)
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

    existing_names = {
        c.name.strip().lower() for c in Character.query.filter_by(project_id=project.id).all()
    }
    seen_in_batch = set()

    created_count = 0
    skipped_names = []
    for i, candidate in enumerate(candidates):
        if i not in selected_indices or not isinstance(candidate, dict):
            continue
        name = str(candidate.get("name") or "").strip()
        if not name:
            continue  # 名前が空の候補は登録しない（必須項目のため）
        key = name.lower()
        if key in existing_names or key in seen_in_batch:
            # 既存キャラクター、または今回選択した候補同士での重複はスキップする
            skipped_names.append(name)
            continue
        seen_in_batch.add(key)
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

    if created_count == 0 and not skipped_names:
        flash("登録できるキャラクターがありませんでした（名前が空でした）。", "danger")
        return redirect(url_for("characters.generate", project_id=project.id))

    db.session.commit()

    if created_count:
        flash(f"{created_count}件のキャラクターを登録しました。内容を確認・編集してください。", "success")
    if skipped_names:
        flash(
            "以下のキャラクターは、既存または選択内で名前が重複していたため登録をスキップしました： "
            + "、".join(skipped_names)
            + "。必要であれば、キャラクター編集画面から手動で内容を反映してください。",
            "warning",
        )
    return redirect(url_for("characters.list_characters", project_id=project.id))
