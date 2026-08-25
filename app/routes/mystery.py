import json
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for

from ..ai_service import (
    AIGenerationError,
    apply_cast_replacements_to_trick,
    auto_assign_cast,
    generate_characters,
    generate_environment,
    generate_evaluation,
    generate_trick,
    revise_environment,
    revise_trick,
    warn_unexpected_names,
    _resolve_trick_json_placeholders,
)
from ..extensions import db
from ..forms import (
    MysteryCaseForm,
    MysteryEnvironmentReviseForm,
    MysteryEvaluateForm,
    MysteryGenerateWithProviderForm,
    MysteryMobGenerateForm,
    MysteryTrickReviseForm,
)
from ..models import Character, Chapter, MysteryCase, Project

mystery_bp = Blueprint(
    "mystery", __name__, url_prefix="/projects/<int:project_id>/mystery-cases"
)

ROLE_TYPE_CHOICES = ["detective", "victim", "culprit", "suspect", "witness", "accomplice", "other"]

# 固定配役の希望欄で、英単語の代わりに日本語ラベルでも指定できるようにするための対応表
# （内部的に使うrole_typeの値は英語のまま変更しない）
ROLE_LABEL_TO_KEY = {
    "探偵": "detective",
    "被害者": "victim",
    "犯人": "culprit",
    "容疑者": "suspect",
    "目撃者": "witness",
    "共犯者": "accomplice",
    "その他": "other",
}


def _get_project_or_404(project_id):
    project = Project.query.get(project_id)
    if project is None:
        abort(404)
    return project


def _get_case_or_404(project_id, case_id):
    case = MysteryCase.query.filter_by(id=case_id, project_id=project_id).first()
    if case is None:
        abort(404)
    return case


def _load_json_list(text):
    try:
        data = json.loads(text or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    return data if isinstance(data, list) else []


def _load_json_dict(text):
    try:
        data = json.loads(text or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _recompute_cast_status(case, project) -> list:
    """required_cast_jsonを読み込み、全役割に配役済みかどうかでcast_statusを更新する。

    全役割の配役が完了したタイミングで、trick_json内に残っている可能性がある
    【CAST:role_key】トークンを実名に解決する（_resolve_trick_json_placeholders。
    冪等なので、既に解決済み・トークンがそもそもない場合は何もしない）。

    戻り値はパース済みのrequired_castリスト（呼び出し側での再利用のため）。
    """
    required_cast = _load_json_list(case.required_cast_json)
    if required_cast and all(entry.get("assigned_character_id") for entry in required_cast):
        case.cast_status = MysteryCase.CAST_STATUS_COMPLETE
        _resolve_trick_json_placeholders(case, project)
    else:
        case.cast_status = MysteryCase.CAST_STATUS_PENDING
    return required_cast


def _parse_fixed_role_hints(project, text):
    """Phase1フォームの「role: キャラクター名」形式のテキストを、
    fixed_role_hints_json相当のリスト（[{"character_id":.., "role":..}]）に変換する。

    一致しないキャラクター名・不正な行はスキップし、呼び出し側で警告表示に使えるよう
    スキップした行のリストも一緒に返す。
    """
    if not text or not text.strip():
        return [], []

    character_by_name = {c.name.strip().lower(): c for c in project.characters}
    hints = []
    skipped_lines = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if ":" not in line and "：" not in line:
            skipped_lines.append(line)
            continue
        separator = ":" if ":" in line else "："
        role, _, name = line.partition(separator)
        role = role.strip().lower()
        role = ROLE_LABEL_TO_KEY.get(role, role)
        name = name.strip()
        character = character_by_name.get(name.lower())
        if role not in ROLE_TYPE_CHOICES or character is None:
            skipped_lines.append(line)
            continue
        hints.append({"character_id": character.id, "role": role})
    return hints, skipped_lines


def _merge_fixed_hints_into_cast(required_cast, fixed_hints):
    """トリック生成結果のrequired_castに、Phase1で指定された固定配役を割り当てる。

    role_typeが一致し、まだ配役されていない最初の役割に割り当てる。一致する役割が
    required_castに存在しない場合は、新しい役割として末尾に追加する。
    """
    used_role_keys = {entry.get("role_key") for entry in required_cast}
    for hint in fixed_hints:
        target = next(
            (
                entry
                for entry in required_cast
                if entry.get("role_type") == hint["role"] and not entry.get("assigned_character_id")
            ),
            None,
        )
        if target is not None:
            target["assigned_character_id"] = hint["character_id"]
            target["fixed_character_id"] = hint["character_id"]
            continue

        role_key = f"{hint['role']}_fixed"
        suffix = 1
        while role_key in used_role_keys:
            suffix += 1
            role_key = f"{hint['role']}_fixed_{suffix}"
        used_role_keys.add(role_key)
        required_cast.append(
            {
                "role_key": role_key,
                "role_type": hint["role"],
                "public_trait": "（ユーザー指定の固定配役）",
                "necessity_reason": "ユーザーがPhase1で固定配役として指定した役割",
                "assigned_character_id": hint["character_id"],
                "fixed_character_id": hint["character_id"],
            }
        )
    return required_cast


def _chapter_choices_with_content(project_id):
    chapters = (
        Chapter.query.filter_by(project_id=project_id)
        .filter(Chapter.content.isnot(None), Chapter.content != "")
        .order_by(Chapter.chapter_number)
        .all()
    )
    return [(c.id, f"第{c.chapter_number}章 {c.title}") for c in chapters]

def _read_chapter_text(project_id, chapter_id) -> str:
    """章本文を読み込む（DBのchapters.contentから直接取得）"""
    chapter = Chapter.query.filter_by(id=chapter_id, project_id=project_id).first()
    return chapter.content or "" if chapter else ""

def _delete_mob_character(character: Character):
    """モブキャラを削除する（サムネイルファイルも連動削除。characters.pyの_delete_thumbnail_fileと同じ規則）"""
    if character.thumbnail_filename:
        thumb_dir = Path(current_app.config["CHARACTER_THUMBNAIL_DIR"])
        path = thumb_dir / character.thumbnail_filename
        if path.exists():
            try:
                path.unlink()
            except OSError:
                pass
    db.session.delete(character)


@mystery_bp.route("/")
def list_cases(project_id):
    project = _get_project_or_404(project_id)
    cases = (
        MysteryCase.query.filter_by(project_id=project.id).order_by(MysteryCase.id).all()
    )
    return render_template("mystery/list.html", project=project, cases=cases)


@mystery_bp.route("/new", methods=["GET", "POST"])
def create(project_id):
    """Phase1：事件の構想（世界観補足・オチ・固定配役の希望）を入力し、MysteryCase(draft)を作成する"""
    project = _get_project_or_404(project_id)
    form = MysteryCaseForm()

    if form.validate_on_submit():
        fixed_hints, skipped_lines = _parse_fixed_role_hints(project, form.fixed_role_hints_text.data)

        case = MysteryCase(
            project_id=project.id,
            title=form.title.data,
            case_world_setting=form.case_world_setting.data,
            climax_twist=form.climax_twist.data,
            fixed_role_hints_json=json.dumps(fixed_hints, ensure_ascii=False),
            status=MysteryCase.STATUS_DRAFT,
        )
        db.session.add(case)
        db.session.commit()

        if skipped_lines:
            flash(
                "固定配役の希望のうち、以下の行は形式が不正または該当するキャラクターが"
                "見つからなかったため無視されました： " + "、".join(skipped_lines),
                "warning",
            )
        flash("事件を作成しました。次にAIでトリックを生成してください。", "success")
        return redirect(url_for("mystery.detail", project_id=project.id, case_id=case.id))

    return render_template("mystery/form.html", form=form, project=project)


@mystery_bp.route("/<int:case_id>")
def detail(project_id, case_id):
    project = _get_project_or_404(project_id)
    case = _get_case_or_404(project_id, case_id)

    trick = _load_json_dict(case.trick_json)
    required_cast = _load_json_list(case.required_cast_json)
    environment = _load_json_dict(case.environment_json)
    evaluation = _load_json_dict(case.latest_evaluation_json)

    character_lookup = {c.id: c for c in project.characters}
    character_lookup.update({c.id: c for c in case.mob_characters})
    for entry in required_cast:
        character = character_lookup.get(entry.get("assigned_character_id"))
        entry["assigned_character_name"] = character.name if character else None

    chapter_choices = [(c.id, f"第{c.chapter_number}章 {c.title}") for c in project.chapters]

    return render_template(
        "mystery/detail.html",
        project=project,
        case=case,
        trick=trick,
        required_cast=required_cast,
        environment=environment,
        evaluation=evaluation,
        chapter_choices=chapter_choices,
    )


@mystery_bp.route("/<int:case_id>/set-chapters", methods=["POST"])
def set_chapters(project_id, case_id):
    """この事件が発生する章の範囲（trigger_chapter_id〜resolution_chapter_id）を設定する。

    章生成プロンプトへのモブキャラ・客観的事実の注入は、この範囲（章番号ベース）を
    もとに判定される（app/ai_service.pyの_mystery_cases_for_chapter_number参照）。
    """
    project = _get_project_or_404(project_id)
    case = _get_case_or_404(project_id, case_id)

    valid_chapter_ids = {c.id for c in project.chapters}

    def _parse(raw):
        if not raw:
            return None
        try:
            value = int(raw)
        except ValueError:
            return None
        return value if value in valid_chapter_ids else None

    case.trigger_chapter_id = _parse(request.form.get("trigger_chapter_id"))
    case.resolution_chapter_id = _parse(request.form.get("resolution_chapter_id"))
    db.session.commit()
    flash("章との紐付けを更新しました。", "success")
    return redirect(url_for("mystery.detail", project_id=project.id, case_id=case.id))


@mystery_bp.route("/<int:case_id>/delete", methods=["POST"])
def delete(project_id, case_id):
    project = _get_project_or_404(project_id)
    case = _get_case_or_404(project_id, case_id)

    for character in list(case.mob_characters):
        _delete_mob_character(character)

    db.session.delete(case)
    db.session.commit()
    flash("事件を削除しました（この事件専用のモブキャラも合わせて削除しました）。", "success")
    return redirect(url_for("mystery.list_cases", project_id=project.id))


@mystery_bp.route("/<int:case_id>/generate-trick", methods=["GET", "POST"])
def generate_trick_view(project_id, case_id):
    """Phase2：トリック（真相）・矛盾セット・抽象配役表をAIに生成させる（プレビューのみ、DB未保存）"""
    project = _get_project_or_404(project_id)
    case = _get_case_or_404(project_id, case_id)

    form = MysteryGenerateWithProviderForm()
    if not form.is_submitted():
        form.provider.data = current_app.config.get("DEFAULT_AI_PROVIDER", "gemini")

    if form.validate_on_submit():
        try:
            trick_data = generate_trick(case, project, provider=form.provider.data)
        except AIGenerationError as exc:
            current_app.logger.exception("トリックのAI生成に失敗しました (case_id=%s)", case.id)
            flash(f"トリックの生成に失敗しました: {exc}", "danger")
            return render_template("mystery/generate_trick.html", form=form, project=project, case=case)

        generated_json = json.dumps(trick_data, ensure_ascii=False)
        return render_template(
            "mystery/trick_preview.html",
            project=project,
            case=case,
            trick=trick_data,
            generated_json=generated_json,
        )

    return render_template("mystery/generate_trick.html", form=form, project=project, case=case)


@mystery_bp.route("/<int:case_id>/generate-trick/confirm", methods=["POST"])
def generate_trick_confirm(project_id, case_id):
    """Phase2確定：プレビューされたトリックを保存し、Phase1の固定配役希望をrequired_castに反映する"""
    project = _get_project_or_404(project_id)
    case = _get_case_or_404(project_id, case_id)

    generated_json = request.form.get("generated_json", "")
    try:
        trick_data = json.loads(generated_json)
    except (json.JSONDecodeError, TypeError):
        flash("生成結果の読み込みに失敗しました。もう一度生成し直してください。", "danger")
        return redirect(url_for("mystery.generate_trick_view", project_id=project.id, case_id=case.id))

    required_cast = trick_data.get("required_cast")
    required_cast = required_cast if isinstance(required_cast, list) else []

    fixed_hints = _load_json_list(case.fixed_role_hints_json)
    required_cast = _merge_fixed_hints_into_cast(required_cast, fixed_hints)

    # オチが未入力だった場合のみ、AIが考案したオチ（generated_conclusion）をclimax_twistへ書き戻す
    if not case.climax_twist or not case.climax_twist.strip():
        generated_conclusion = str(trick_data.get("generated_conclusion") or "").strip()
        if generated_conclusion:
            case.climax_twist = generated_conclusion

    case.trick_json = json.dumps(
        {
            "trick_type": trick_data.get("trick_type", ""),
            "true_mechanism": trick_data.get("true_mechanism", ""),
            "misdirection": trick_data.get("misdirection", ""),
            "contradiction_set": trick_data.get("contradiction_set", {}),
        },
        ensure_ascii=False,
    )
    case.required_cast_json = json.dumps(required_cast, ensure_ascii=False)
    case.status = MysteryCase.STATUS_TRICK_READY
    _recompute_cast_status(case, project)
    db.session.commit()

    flash("トリックを保存しました。続けて配役を行ってください。", "success")
    return redirect(url_for("mystery.cast", project_id=project.id, case_id=case.id))


@mystery_bp.route("/<int:case_id>/auto-generate", methods=["GET", "POST"])
def auto_generate(project_id, case_id):
    """お任せモード：Phase2（トリック）→Phase2.5（配役）→Phase3（環境）を1回の操作で一括生成する。

    固定配役の希望以外の役割は、すべてAI生成のモブキャラを1件だけ自動採用する（既存キャラからの
    手動選択は行わない）。DB書き込みは、すべてのフェーズが成功し一括プレビューで確定した時点で
    まとめて行う（途中経過は残さない）。draft状態の事件にのみ使用できる。
    """
    project = _get_project_or_404(project_id)
    case = _get_case_or_404(project_id, case_id)

    if case.status != MysteryCase.STATUS_DRAFT:
        flash("すでにトリック生成以降が進んでいる事件には、お任せモードは使用できません。", "danger")
        return redirect(url_for("mystery.detail", project_id=project.id, case_id=case.id))

    form = MysteryGenerateWithProviderForm()
    if not form.is_submitted():
        form.provider.data = current_app.config.get("DEFAULT_AI_PROVIDER", "gemini")

    if form.validate_on_submit():
        provider = form.provider.data

        try:
            trick_data = generate_trick(case, project, provider=provider)
        except AIGenerationError as exc:
            current_app.logger.exception("お任せモード：トリックのAI生成に失敗しました (case_id=%s)", case.id)
            flash(f"トリックの生成に失敗しました: {exc}", "danger")
            return render_template("mystery/auto_generate.html", form=form, project=project, case=case)

        required_cast = trick_data.get("required_cast")
        required_cast = required_cast if isinstance(required_cast, list) else []
        fixed_hints = _load_json_list(case.fixed_role_hints_json)
        required_cast = _merge_fixed_hints_into_cast(required_cast, fixed_hints)

        try:
            updated_cast, resolved_cast, pending_characters = auto_assign_cast(
                case, project, required_cast, provider=provider
            )
        except AIGenerationError as exc:
            current_app.logger.exception("お任せモード：配役のAI生成に失敗しました (case_id=%s)", case.id)
            flash(
                f"配役（AIモブキャラ生成）に失敗しました: {exc} "
                "トリックの生成はまだ保存されていません。もう一度お試しになるか、"
                "個別のフローでやり直してください。",
                "danger",
            )
            return render_template("mystery/auto_generate.html", form=form, project=project, case=case)

        # トリック本文中の【CAST:role_key】トークンを、配役結果（既存キャラ・新規モブどちらも
        # resolved_castの時点で実名が確定している）で置換する。DB保存前のこの時点で解決しておく
        # ことで、環境生成プロンプトにも、確定保存されるtrick_jsonにも創作名が残らないようにする。
        cast_name_replacements = {
            f"【CAST:{c['role_key']}】": c["character_name"]
            for c in resolved_cast
            if c.get("role_key") and c.get("character_name")
        }
        apply_cast_replacements_to_trick(trick_data, cast_name_replacements)
        trick_json_text = json.dumps(
            {
                "trick_type": trick_data.get("trick_type", ""),
                "true_mechanism": trick_data.get("true_mechanism", ""),
                "misdirection": trick_data.get("misdirection", ""),
                "contradiction_set": trick_data.get("contradiction_set", {}),
            },
            ensure_ascii=False,
        )

        try:
            environment_data = generate_environment(
                case,
                project,
                provider=provider,
                resolved_cast=resolved_cast,
                trick_json_text=trick_json_text,
            )
        except AIGenerationError as exc:
            current_app.logger.exception("お任せモード：環境・小道具のAI生成に失敗しました (case_id=%s)", case.id)
            flash(
                f"環境・小道具の生成に失敗しました: {exc} "
                "ここまでの生成結果はまだ保存されていません。もう一度お試しください。",
                "danger",
            )
            return render_template("mystery/auto_generate.html", form=form, project=project, case=case)

        preview_payload = {
            "trick_data": trick_data,
            "required_cast": updated_cast,
            "pending_characters": [
                {
                    "name": c.name,
                    "age": c.age,
                    "gender": c.gender,
                    "personality": c.personality,
                    "appearance": c.appearance,
                    "background": c.background,
                    "notes": c.notes,
                }
                for c in pending_characters
            ],
            "environment_data": environment_data,
        }
        generated_json = json.dumps(preview_payload, ensure_ascii=False)

        return render_template(
            "mystery/auto_generate_preview.html",
            project=project,
            case=case,
            trick=trick_data,
            required_cast=updated_cast,
            pending_characters=pending_characters,
            environment=environment_data,
            generated_json=generated_json,
        )

    return render_template("mystery/auto_generate.html", form=form, project=project, case=case)


@mystery_bp.route("/<int:case_id>/auto-generate/confirm", methods=["POST"])
def auto_generate_confirm(project_id, case_id):
    """お任せモード確定：トリック・配役（新規モブキャラの保存込み）・環境をまとめて保存する"""
    project = _get_project_or_404(project_id)
    case = _get_case_or_404(project_id, case_id)

    generated_json = request.form.get("generated_json", "")
    try:
        payload = json.loads(generated_json)
    except (json.JSONDecodeError, TypeError):
        flash("生成結果の読み込みに失敗しました。もう一度生成し直してください。", "danger")
        return redirect(url_for("mystery.auto_generate", project_id=project.id, case_id=case.id))

    trick_data = payload.get("trick_data") or {}
    required_cast = payload.get("required_cast") or []
    pending_characters_data = payload.get("pending_characters") or []
    environment_data = payload.get("environment_data") or {}

    # オチが未入力だった場合のみ、AIが考案したオチをclimax_twistへ書き戻す（Phase2確定と同じ処理）
    if not case.climax_twist or not case.climax_twist.strip():
        generated_conclusion = str(trick_data.get("generated_conclusion") or "").strip()
        if generated_conclusion:
            case.climax_twist = generated_conclusion

    case.trick_json = json.dumps(
        {
            "trick_type": trick_data.get("trick_type", ""),
            "true_mechanism": trick_data.get("true_mechanism", ""),
            "misdirection": trick_data.get("misdirection", ""),
            "contradiction_set": trick_data.get("contradiction_set", {}),
        },
        ensure_ascii=False,
    )

    # 新規モブキャラをまとめて保存し、idを確定させてからrequired_castに反映する
    new_characters = []
    for c_data in pending_characters_data:
        if not isinstance(c_data, dict):
            continue
        character = Character(
            project_id=project.id,
            mystery_case_id=case.id,
            name=str(c_data.get("name") or "")[:100],
            age=str(c_data.get("age") or "")[:20] or None,
            gender=str(c_data.get("gender") or "")[:20] or None,
            personality=c_data.get("personality") or None,
            appearance=c_data.get("appearance") or None,
            background=c_data.get("background") or None,
            notes=c_data.get("notes") or None,
        )
        db.session.add(character)
        new_characters.append(character)
    db.session.flush()  # assigned_character_idに使うためIDを確定させる

    for entry in required_cast:
        if not isinstance(entry, dict):
            continue
        pending_index = entry.pop("_pending_character_index", None)
        if pending_index is not None and 0 <= pending_index < len(new_characters):
            entry["assigned_character_id"] = new_characters[pending_index].id

    case.required_cast_json = json.dumps(required_cast, ensure_ascii=False)
    case.environment_json = json.dumps(
        {
            "timeline": environment_data.get("timeline", []),
            "location": environment_data.get("location", []),
            "weather": environment_data.get("weather", ""),
            "items": environment_data.get("items", []),
        },
        ensure_ascii=False,
    )
    case.cast_status = MysteryCase.CAST_STATUS_COMPLETE
    case.status = MysteryCase.STATUS_ENVIRONMENT_READY
    db.session.commit()

    additional_required_cast = environment_data.get("additional_required_cast") or []
    if additional_required_cast:
        flash(
            "お任せモードで、トリック・配役・環境をまとめて生成しました。"
            "ただし環境生成の過程で追加の役割が必要と示唆されました。この役割は自動反映されて"
            "いないため、必要であれば配役画面から手動で追加・再生成してください。",
            "warning",
        )
    else:
        flash("お任せモードで、トリック・配役・環境をまとめて生成しました。", "success")
    return redirect(url_for("mystery.detail", project_id=project.id, case_id=case.id))


@mystery_bp.route("/<int:case_id>/trick/revise", methods=["GET", "POST"])
def trick_revise(project_id, case_id):
    """確定済みのトリック（真相）に対して、指示した変更点だけをAIに反映させる（部分修正）。

    配役が完了していない（trick_json内に【CAST:role_key】トークンが残っている）場合は対象外。
    """
    project = _get_project_or_404(project_id)
    case = _get_case_or_404(project_id, case_id)

    if not case.trick_json:
        flash("先にトリックを生成・確定してください。", "danger")
        return redirect(url_for("mystery.generate_trick_view", project_id=project.id, case_id=case.id))
    if "【CAST:" in case.trick_json:
        flash(
            "配役が完了していないため、部分修正はできません。先に配役を完了するか、"
            "トリックを再生成してください。",
            "danger",
        )
        return redirect(url_for("mystery.cast", project_id=project.id, case_id=case.id))

    form = MysteryTrickReviseForm()
    if not form.is_submitted():
        form.provider.data = current_app.config.get("DEFAULT_AI_PROVIDER", "gemini")

    if form.validate_on_submit():
        try:
            revised_trick = revise_trick(
                case, project, revision_instructions=form.revision_instructions.data, provider=form.provider.data
            )
        except AIGenerationError as exc:
            current_app.logger.exception("トリックのAI修正に失敗しました (case_id=%s)", case.id)
            flash(f"AIによる修正に失敗しました: {exc}", "danger")
            return render_template("mystery/trick_revise.html", form=form, project=project, case=case)

        warnings = warn_unexpected_names(revised_trick, case, project)
        generated_json = json.dumps(revised_trick, ensure_ascii=False)
        return render_template(
            "mystery/trick_revise_preview.html",
            project=project,
            case=case,
            trick=revised_trick,
            warnings=warnings,
            generated_json=generated_json,
        )

    return render_template("mystery/trick_revise.html", form=form, project=project, case=case)


@mystery_bp.route("/<int:case_id>/trick/revise/confirm", methods=["POST"])
def trick_revise_confirm(project_id, case_id):
    """修正結果プレビューで確認されたトリックで、trick_jsonを上書きする"""
    project = _get_project_or_404(project_id)
    case = _get_case_or_404(project_id, case_id)

    generated_json = request.form.get("generated_json", "")
    try:
        revised_trick = json.loads(generated_json)
    except (json.JSONDecodeError, TypeError):
        flash("修正結果の読み込みに失敗しました。もう一度修正し直してください。", "danger")
        return redirect(url_for("mystery.trick_revise", project_id=project.id, case_id=case.id))

    case.trick_json = json.dumps(
        {
            "trick_type": revised_trick.get("trick_type", ""),
            "true_mechanism": revised_trick.get("true_mechanism", ""),
            "misdirection": revised_trick.get("misdirection", ""),
            "contradiction_set": revised_trick.get("contradiction_set", {}),
        },
        ensure_ascii=False,
    )
    db.session.commit()

    flash("修正後のトリックを保存しました。", "success")
    return redirect(url_for("mystery.detail", project_id=project.id, case_id=case.id))


@mystery_bp.route("/<int:case_id>/cast")
def cast(project_id, case_id):
    """Phase2.5：抽象配役表の各役割に、既存キャラクターまたは新規モブキャラを割り当てる画面"""
    project = _get_project_or_404(project_id)
    case = _get_case_or_404(project_id, case_id)

    if not case.trick_json:
        flash("先にトリックを生成・確定してください。", "danger")
        return redirect(url_for("mystery.generate_trick_view", project_id=project.id, case_id=case.id))

    required_cast = _load_json_list(case.required_cast_json)
    character_lookup = {c.id: c for c in project.characters}
    character_lookup.update({c.id: c for c in case.mob_characters})
    for entry in required_cast:
        character = character_lookup.get(entry.get("assigned_character_id"))
        entry["assigned_character_name"] = character.name if character else None

    # 配役候補として選べるキャラクター（メインキャスト＋この事件専用のモブ。他事件のモブは含めない）
    assignable_characters = list(project.characters) + list(case.mob_characters)

    return render_template(
        "mystery/cast.html",
        project=project,
        case=case,
        required_cast=required_cast,
        assignable_characters=assignable_characters,
    )


@mystery_bp.route("/<int:case_id>/cast/assign", methods=["POST"])
def cast_assign(project_id, case_id):
    """既存キャラクター（メインキャストまたはこの事件のモブ）を、指定した役割に割り当てる"""
    project = _get_project_or_404(project_id)
    case = _get_case_or_404(project_id, case_id)

    role_key = request.form.get("role_key", "")
    character_id_raw = request.form.get("character_id", "")

    required_cast = _load_json_list(case.required_cast_json)
    target = next((entry for entry in required_cast if entry.get("role_key") == role_key), None)
    if target is None:
        flash("指定された役割が見つかりませんでした。", "danger")
        return redirect(url_for("mystery.cast", project_id=project.id, case_id=case.id))

    if not character_id_raw:
        target["assigned_character_id"] = None
    else:
        try:
            character_id = int(character_id_raw)
        except ValueError:
            flash("配役の指定が不正です。", "danger")
            return redirect(url_for("mystery.cast", project_id=project.id, case_id=case.id))

        valid_ids = {c.id for c in project.characters} | {c.id for c in case.mob_characters}
        if character_id not in valid_ids:
            flash("この作品に登録されていないキャラクターは配役できません。", "danger")
            return redirect(url_for("mystery.cast", project_id=project.id, case_id=case.id))
        target["assigned_character_id"] = character_id

    case.required_cast_json = json.dumps(required_cast, ensure_ascii=False)
    _recompute_cast_status(case, project)
    db.session.commit()

    if case.cast_status == MysteryCase.CAST_STATUS_COMPLETE:
        flash("全ての役割の配役が完了しました。続けて環境・小道具を生成できます。", "success")
    else:
        flash("配役を保存しました。", "success")
    return redirect(url_for("mystery.cast", project_id=project.id, case_id=case.id))


@mystery_bp.route("/<int:case_id>/cast/<string:role_key>/generate-mob", methods=["GET", "POST"])
def cast_generate_mob(project_id, case_id, role_key):
    """指定した役割用に、この事件専用のモブキャラ候補をAIに生成させる（既存のキャラクター生成と同じ
    プレビュー→選択保存の流れを再利用する。プレビューのみ、DB未保存）
    """
    project = _get_project_or_404(project_id)
    case = _get_case_or_404(project_id, case_id)

    required_cast = _load_json_list(case.required_cast_json)
    role = next((entry for entry in required_cast if entry.get("role_key") == role_key), None)
    if role is None:
        abort(404)

    form = MysteryMobGenerateForm()
    if not form.is_submitted():
        form.provider.data = current_app.config.get("DEFAULT_AI_PROVIDER", "gemini")

    if form.validate_on_submit():
        notes = (
            f"この事件専用のモブキャラクターを考えてください。役割: {role.get('role_type')}。"
            f"求められる特徴: {role.get('public_trait') or '（特になし）'}"
        )
        try:
            candidates = generate_characters(
                project, count=form.count.data, additional_notes=notes, provider=form.provider.data
            )
        except AIGenerationError as exc:
            current_app.logger.exception("配役モブキャラのAI生成に失敗しました (case_id=%s)", case.id)
            flash(f"AI生成に失敗しました: {exc}", "danger")
            return render_template(
                "mystery/cast_generate_mob.html", form=form, project=project, case=case, role=role
            )

        if not candidates:
            flash("AIが候補を生成しませんでした。もう一度試してください。", "danger")
            return render_template(
                "mystery/cast_generate_mob.html", form=form, project=project, case=case, role=role
            )

        generated_json = json.dumps(candidates, ensure_ascii=False)
        return render_template(
            "mystery/cast_mob_preview.html",
            project=project,
            case=case,
            role=role,
            candidates=candidates,
            generated_json=generated_json,
        )

    return render_template(
        "mystery/cast_generate_mob.html", form=form, project=project, case=case, role=role
    )


@mystery_bp.route("/<int:case_id>/cast/<string:role_key>/generate-mob/confirm", methods=["POST"])
def cast_generate_mob_confirm(project_id, case_id, role_key):
    """選択された1件のモブキャラ候補をCharacterとして保存し、その役割に自動で割り当てる"""
    project = _get_project_or_404(project_id)
    case = _get_case_or_404(project_id, case_id)

    generated_json = request.form.get("generated_json", "")
    selected_raw = request.form.get("selected", "")
    try:
        candidates = json.loads(generated_json)
        selected_index = int(selected_raw)
        candidate = candidates[selected_index]
    except (json.JSONDecodeError, TypeError, ValueError, IndexError):
        flash("候補の読み込みに失敗しました。もう一度生成し直してください。", "danger")
        return redirect(
            url_for("mystery.cast_generate_mob", project_id=project.id, case_id=case.id, role_key=role_key)
        )

    name = str(candidate.get("name") or "").strip()
    if not name:
        flash("候補の名前が空のため登録できませんでした。", "danger")
        return redirect(
            url_for("mystery.cast_generate_mob", project_id=project.id, case_id=case.id, role_key=role_key)
        )

    # 名前の重複チェックはプロジェクト全体で統一（モブキャラも例外にしない。CLAUDE.md方針）
    normalized = name.strip().lower()
    duplicate = next(
        (c for c in Character.query.filter_by(project_id=project.id).all() if c.name.strip().lower() == normalized),
        None,
    )
    if duplicate:
        flash(
            f"同名のキャラクター「{duplicate.name}」が既に登録されているため、この候補は登録できませんでした。"
            "名前を変えて再生成するか、既存のキャラクターを配役画面から選んでください。",
            "danger",
        )
        return redirect(url_for("mystery.cast", project_id=project.id, case_id=case.id))

    character = Character(
        project_id=project.id,
        mystery_case_id=case.id,
        name=name[:100],
        age=str(candidate.get("age") or "")[:20] or None,
        gender=str(candidate.get("gender") or "")[:20] or None,
        personality=candidate.get("personality") or None,
        appearance=candidate.get("appearance") or None,
        background=candidate.get("background") or None,
        notes=candidate.get("notes") or None,
    )
    db.session.add(character)
    db.session.flush()  # assigned_character_idに使うためIDを確定させる

    required_cast = _load_json_list(case.required_cast_json)
    target = next((entry for entry in required_cast if entry.get("role_key") == role_key), None)
    if target is not None:
        target["assigned_character_id"] = character.id
    case.required_cast_json = json.dumps(required_cast, ensure_ascii=False)
    _recompute_cast_status(case, project)
    db.session.commit()

    flash(f"事件専用のモブキャラ「{character.name}」を登録し、役割に割り当てました。", "success")
    return redirect(url_for("mystery.cast", project_id=project.id, case_id=case.id))


@mystery_bp.route("/<int:case_id>/generate-environment", methods=["GET", "POST"])
def generate_environment_view(project_id, case_id):
    """Phase3：配役済みの実名一覧をもとに、現場データ（タイムライン・場所・天候・小道具）を生成する"""
    project = _get_project_or_404(project_id)
    case = _get_case_or_404(project_id, case_id)

    if case.cast_status != MysteryCase.CAST_STATUS_COMPLETE:
        flash("先にすべての役割の配役を完了してください。", "danger")
        return redirect(url_for("mystery.cast", project_id=project.id, case_id=case.id))

    form = MysteryGenerateWithProviderForm()
    if not form.is_submitted():
        form.provider.data = current_app.config.get("DEFAULT_AI_PROVIDER", "gemini")

    if form.validate_on_submit():
        try:
            environment_data = generate_environment(case, project, provider=form.provider.data)
        except AIGenerationError as exc:
            current_app.logger.exception("環境・小道具のAI生成に失敗しました (case_id=%s)", case.id)
            flash(f"環境・小道具の生成に失敗しました: {exc}", "danger")
            return render_template(
                "mystery/generate_environment.html", form=form, project=project, case=case
            )

        generated_json = json.dumps(environment_data, ensure_ascii=False)
        return render_template(
            "mystery/environment_preview.html",
            project=project,
            case=case,
            environment=environment_data,
            generated_json=generated_json,
        )

    return render_template("mystery/generate_environment.html", form=form, project=project, case=case)


@mystery_bp.route("/<int:case_id>/generate-environment/confirm", methods=["POST"])
def generate_environment_confirm(project_id, case_id):
    """Phase3確定：現場データを保存する。追加の役割が判明した場合は配役ステップへ差し戻す"""
    project = _get_project_or_404(project_id)
    case = _get_case_or_404(project_id, case_id)

    generated_json = request.form.get("generated_json", "")
    try:
        environment_data = json.loads(generated_json)
    except (json.JSONDecodeError, TypeError):
        flash("生成結果の読み込みに失敗しました。もう一度生成し直してください。", "danger")
        return redirect(
            url_for("mystery.generate_environment_view", project_id=project.id, case_id=case.id)
        )

    case.environment_json = json.dumps(
        {
            "timeline": environment_data.get("timeline", []),
            "location": environment_data.get("location", []),
            "weather": environment_data.get("weather", ""),
            "items": environment_data.get("items", []),
        },
        ensure_ascii=False,
    )

    additional_required_cast = environment_data.get("additional_required_cast") or []
    if additional_required_cast:
        required_cast = _load_json_list(case.required_cast_json)
        existing_keys = {entry.get("role_key") for entry in required_cast}
        for entry in additional_required_cast:
            if not isinstance(entry, dict):
                continue
            role_key = entry.get("role_key") or f"extra_{len(required_cast) + 1}"
            if role_key in existing_keys:
                role_key = f"{role_key}_extra"
            existing_keys.add(role_key)
            required_cast.append(
                {
                    "role_key": role_key,
                    "role_type": entry.get("role_type", "other"),
                    "public_trait": entry.get("public_trait", ""),
                    "necessity_reason": entry.get("necessity_reason", ""),
                    "assigned_character_id": None,
                }
            )
        case.required_cast_json = json.dumps(required_cast, ensure_ascii=False)
        _recompute_cast_status(case, project)
        db.session.commit()
        flash(
            "環境・小道具を生成しましたが、追加で必要な役割が見つかりました。"
            "配役をやり直してから、環境・小道具を再生成してください。",
            "warning",
        )
        return redirect(url_for("mystery.cast", project_id=project.id, case_id=case.id))

    case.status = MysteryCase.STATUS_ENVIRONMENT_READY
    db.session.commit()
    flash("環境・小道具を保存しました。", "success")
    return redirect(url_for("mystery.detail", project_id=project.id, case_id=case.id))


@mystery_bp.route("/<int:case_id>/environment/revise", methods=["GET", "POST"])
def environment_revise(project_id, case_id):
    """確定済みの環境・小道具データに対して、指示した変更点だけをAIに反映させる（部分修正）"""
    project = _get_project_or_404(project_id)
    case = _get_case_or_404(project_id, case_id)

    if not case.environment_json:
        flash("先に環境・小道具を生成・確定してください。", "danger")
        return redirect(url_for("mystery.generate_environment_view", project_id=project.id, case_id=case.id))

    form = MysteryEnvironmentReviseForm()
    if not form.is_submitted():
        form.provider.data = current_app.config.get("DEFAULT_AI_PROVIDER", "gemini")

    if form.validate_on_submit():
        try:
            revised_environment = revise_environment(
                case, project, revision_instructions=form.revision_instructions.data, provider=form.provider.data
            )
        except AIGenerationError as exc:
            current_app.logger.exception("環境・小道具のAI修正に失敗しました (case_id=%s)", case.id)
            flash(f"AIによる修正に失敗しました: {exc}", "danger")
            return render_template("mystery/environment_revise.html", form=form, project=project, case=case)

        warnings = warn_unexpected_names(revised_environment, case, project)
        generated_json = json.dumps(revised_environment, ensure_ascii=False)
        return render_template(
            "mystery/environment_revise_preview.html",
            project=project,
            case=case,
            environment=revised_environment,
            warnings=warnings,
            generated_json=generated_json,
        )

    return render_template("mystery/environment_revise.html", form=form, project=project, case=case)


@mystery_bp.route("/<int:case_id>/environment/revise/confirm", methods=["POST"])
def environment_revise_confirm(project_id, case_id):
    """修正結果プレビューで確認された環境・小道具データで、environment_jsonを上書きする"""
    project = _get_project_or_404(project_id)
    case = _get_case_or_404(project_id, case_id)

    generated_json = request.form.get("generated_json", "")
    try:
        revised_environment = json.loads(generated_json)
    except (json.JSONDecodeError, TypeError):
        flash("修正結果の読み込みに失敗しました。もう一度修正し直してください。", "danger")
        return redirect(url_for("mystery.environment_revise", project_id=project.id, case_id=case.id))

    case.environment_json = json.dumps(
        {
            "timeline": revised_environment.get("timeline", []),
            "location": revised_environment.get("location", []),
            "weather": revised_environment.get("weather", ""),
            "items": revised_environment.get("items", []),
        },
        ensure_ascii=False,
    )
    db.session.commit()

    flash("修正後の環境・小道具を保存しました。", "success")
    return redirect(url_for("mystery.detail", project_id=project.id, case_id=case.id))


@mystery_bp.route("/<int:case_id>/evaluate", methods=["GET", "POST"])
def evaluate(project_id, case_id):
    """Phase5：正解データ（trick_json・environment_json）と実際の本文/ログを比較し、矛盾を検出する"""
    project = _get_project_or_404(project_id)
    case = _get_case_or_404(project_id, case_id)

    if not case.trick_json:
        flash("先にトリックを生成・確定してください。", "danger")
        return redirect(url_for("mystery.generate_trick_view", project_id=project.id, case_id=case.id))

    form = MysteryEvaluateForm()
    form.chapter_id.choices = [(0, "選択してください")] + _chapter_choices_with_content(project.id)
    if not form.is_submitted():
        form.provider.data = current_app.config.get("DEFAULT_AI_PROVIDER", "gemini")

    if form.validate_on_submit():
        if form.source_type.data == "chapter":
            if not form.chapter_id.data:
                flash("検証対象の章を選択してください。", "danger")
                return render_template("mystery/evaluate.html", form=form, project=project, case=case)
            target_text = _read_chapter_text(project.id, form.chapter_id.data)
            source_label = "chapter"
            target_chapter_id = form.chapter_id.data
        else:
            target_text = form.manual_text.data or ""
            source_label = "interrogation"
            target_chapter_id = None

        if not target_text.strip():
            flash("検証対象の本文が空です。", "danger")
            return render_template("mystery/evaluate.html", form=form, project=project, case=case)

        try:
            evaluation_data = generate_evaluation(
                case, target_text, source_label, provider=form.provider.data
            )
        except AIGenerationError as exc:
            current_app.logger.exception("評価（Phase5）のAI生成に失敗しました (case_id=%s)", case.id)
            flash(f"評価の生成に失敗しました: {exc}", "danger")
            return render_template("mystery/evaluate.html", form=form, project=project, case=case)

        generated_json = json.dumps(evaluation_data, ensure_ascii=False)
        return render_template(
            "mystery/evaluate_preview.html",
            project=project,
            case=case,
            evaluation=evaluation_data,
            generated_json=generated_json,
            source_label=source_label,
            target_chapter_id=target_chapter_id or 0,
        )

    return render_template("mystery/evaluate.html", form=form, project=project, case=case)


@mystery_bp.route("/<int:case_id>/evaluate/confirm", methods=["POST"])
def evaluate_confirm(project_id, case_id):
    """Phase5確定：評価結果をlatest_evaluation_*に保存する"""
    project = _get_project_or_404(project_id)
    case = _get_case_or_404(project_id, case_id)

    generated_json = request.form.get("generated_json", "")
    try:
        evaluation_data = json.loads(generated_json)
    except (json.JSONDecodeError, TypeError):
        flash("評価結果の読み込みに失敗しました。もう一度評価し直してください。", "danger")
        return redirect(url_for("mystery.evaluate", project_id=project.id, case_id=case.id))

    source_label = request.form.get("source_label", "")
    target_chapter_id = request.form.get("target_chapter_id", "0")

    case.latest_evaluation_json = json.dumps(evaluation_data, ensure_ascii=False)
    case.latest_evaluation_source = source_label or None
    case.latest_evaluation_target_chapter_id = (
        int(target_chapter_id) if target_chapter_id and target_chapter_id != "0" else None
    )
    case.status = MysteryCase.STATUS_EVALUATED
    db.session.commit()

    flash("評価結果を保存しました。", "success")
    return redirect(url_for("mystery.detail", project_id=project.id, case_id=case.id))
