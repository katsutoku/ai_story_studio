# 事件生成モジュール 追加変更：オチの任意化 ＋ 全部お任せモード

## 0. 位置づけ

`docs/mystery_trick_module_design.md` で実装済みの「ミステリー・トリック生成モジュール」に対する
追加変更。新しいテーブルは作らず、既存の `MysteryCase` / `Character` モデルと、既存のPhase構成
（Phase1〜Phase3）をそのまま使い回す。既存の「AIで生成 → プレビュー → 選択保存」という3段階
パターンも崩さない。

本ドキュメントに着手する前に、`CLAUDE.md` の該当箇所（事件生成モジュールの説明）に以下を
追記すること。

> オチ（`climax_twist`）は任意入力に変更（空欄の場合はオチ自体もAIが考案し、
> `case_conclusion_source` に生成元を記録する）。加えて、Phase2〜Phase3
> （トリック生成・配役・環境生成）を1回の操作でまとめて実行する「お任せモード」を追加。

---

## 1. 変更点の全体像

| 変更 | 内容 |
|---|---|
| A | Phase1の「オチ」を任意入力にする |
| B | オチが空の場合、Phase2のAIにオチ自体も考案させ、DBへ書き戻す |
| C | Phase2（トリック生成）〜Phase2.5（配役）〜Phase3（環境生成）を一括実行する「お任せモード」を追加 |
| D | お任せモードでは、配役の各役割にAI生成モブキャラを自動採用する（既存キャラは固定配役希望のみ反映） |

いずれも新規テーブルは不要。`MysteryCase.climax_twist` は既存スキーマで既に `nullable=True` の
ため、DBマイグレーションは不要（`app/forms.py` のバリデータ変更のみで対応できる）。

---

## 2. A・B：オチの任意化

### 2.1 フォーム変更（`app/forms.py`）

`MysteryCaseForm.climax_twist` の `DataRequired` を `Optional` に変更する。

```python
climax_twist = TextAreaField(
    "オチ・絶対条件（任意）",
    validators=[Optional()],
    description=(
        "例：実は被害者は双子で、事件当夜に入れ替わっていた。"
        "空欄の場合、オチ自体もAIが考案します（トリック生成時に自動で決定されます）。"
    ),
)
```

### 2.2 プロンプト分岐（`app/ai_service.py` の `build_trick_prompt()`）

現状、`build_trick_prompt()` は無条件で以下を埋め込んでいる。

```python
lines.append(f"\n# オチ（絶対条件。この結末に矛盾なく到達するトリックを設計すること）\n{case.climax_twist}")
```

これを、`case.climax_twist` が空かどうかで分岐させる。

```python
if case.climax_twist and case.climax_twist.strip():
    lines.append(
        "\n# オチ（絶対条件。この結末に矛盾なく到達するトリックを設計すること）\n"
        f"{case.climax_twist}"
    )
else:
    lines.append(
        "\n# オチについて\n"
        "オチ（事件の結末・意外性のある真相の要点）は指定されていません。"
        "あなたがこの事件にふさわしいオチを自由に考案し、出力JSONの "
        "generated_conclusion フィールドに、後から読み返しても分かるよう簡潔な文章"
        "（1〜3文程度）で明記してください。"
    )
```

出力JSONスキーマにも `generated_conclusion` を追加する（オチ指定済みの場合は空文字でよい旨を
明記）。

```python
lines.append(
    "\n# 出力JSONスキーマ\n"
    "{\n"
    '  "trick_type": "string",\n'
    '  "true_mechanism": "string（真相の仕組み。秘匿情報）",\n'
    '  "misdirection": "string",\n'
    '  "generated_conclusion": "string（オチが未指定だった場合のみ、あなたが考案したオチを記載。'
    'オチが指定済みだった場合は空文字でよい）",\n'
    '  "contradiction_set": { ... },\n'
    '  "required_cast": [ ... ]\n'
    "}\n"
)
```

`generate_trick()` の戻り値パース処理にも、他のキー（`contradiction_set` / `required_cast`）と
同様のデフォルト処理を追加する。

```python
if not isinstance(data.get("generated_conclusion"), str):
    data["generated_conclusion"] = ""
```

### 2.3 生成結果の書き戻し（`app/routes/mystery.py` の `generate_trick_confirm()`）

Phase2確定時、`case.climax_twist` が空だった場合に限り、AIが考案した
`generated_conclusion` を `climax_twist` へ書き戻す。

```python
if not case.climax_twist or not case.climax_twist.strip():
    generated_conclusion = trick_data.get("generated_conclusion", "").strip()
    if generated_conclusion:
        case.climax_twist = generated_conclusion
```

`trick_json` 自体には `generated_conclusion` を含めない（`true_mechanism` /
`misdirection` / `contradiction_set` のみを保存する現状の方針を維持する）。理由：オチは
`climax_twist` という既存の単一の置き場所に統一しておいた方が、事件詳細画面や今後の機能から
参照しやすいため。

---

## 3. C・D：全部お任せモード

### 3.1 目的・スコープ

Phase2（トリック生成）→ Phase2.5（配役）→ Phase3（環境・小道具生成）を、ユーザーの操作
1回（起動フォーム送信）でまとめて実行し、最後に一括プレビュー画面で内容を確認してから
確定保存する。

- Phase2.5の「配役」は、`fixed_role_hints_json` で指定された役割はそのまま使い、指定のない
  役割は**すべてAI生成のモブキャラを1件だけ生成して自動採用**する（既存のモブキャラ生成
  （`MysteryMobGenerateForm`、複数候補生成→人間が選ぶ）を、候補数1・自動選択という形で
  内部的に再利用する）。
- 既存キャラクターからの手動選択は行わない（お任せモードの定義上、人間の選択操作を挟まない
  ため）。既存キャラを事件に関わらせたい場合は、従来通りPhase1の「固定配役の希望」で
  指定してもらう。
- DBへの書き込みは、Phase2・配役・Phase3のすべてが成功し、**最後の一括プレビュー画面で
  ユーザーが確定ボタンを押したタイミングでまとめて行う**。生成の途中経過（トリックだけ確定、
  配役だけ確定、のような中途半端な状態）はDBに残さない。

### 3.2 データモデル

新規カラム・新規テーブルは不要。既存の `MysteryCase` の各フィールド（`trick_json` /
`required_cast_json` / `environment_json` / `status` / `cast_status`）をそのまま使う。

### 3.3 サービス層（`app/ai_service.py` への追加）

```python
def auto_assign_cast(
    case: MysteryCase,
    project: Project,
    required_cast: list[dict],
    provider: str = "",
) -> list[dict]:
    """お任せモード専用：fixed_character_idが設定されていない役割すべてに、
    AI生成モブキャラを1件だけ生成して自動的に割り当てる。

    既存のモブキャラ生成関数（候補を複数生成する版）を候補数1で呼び出し、
    生成された1件をそのまま採用する。DBへのCharacter保存はここでは行わず、
    呼び出し側（ルート）でPhase3成功後にまとめてコミットする。
    """
    updated_cast = []
    pending_mob_characters = []  # (role_key, Character未保存インスタンス) のリスト

    for entry in required_cast:
        entry = dict(entry)
        if entry.get("fixed_character_id") or entry.get("assigned_character_id"):
            updated_cast.append(entry)
            continue

        candidates = generate_mob_candidates(
            case, project, entry, count=1, provider=provider
        )
        if not candidates:
            raise AIGenerationError(
                f"役割「{entry.get('role_key')}」のモブキャラ生成に失敗しました。"
            )
        character = Character(
            project_id=project.id,
            mystery_case_id=case.id,
            name=candidates[0]["name"],
            age=candidates[0].get("age"),
            gender=candidates[0].get("gender"),
            personality=candidates[0].get("personality"),
            appearance=candidates[0].get("appearance"),
            background=candidates[0].get("background"),
        )
        pending_mob_characters.append(character)
        # assigned_character_idはこの時点では未確定（未コミットのため）。
        # ルート側でdb.session.add_all → flush後にidを埋める。
        entry["_pending_character_ref"] = character
        updated_cast.append(entry)

    return updated_cast, pending_mob_characters
```

> 既存の `generate_mob_candidates()` の実際の関数名・引数は `app/ai_service.py` の配役まわりの
> 実装（`cast_generate_mob` ルートが呼んでいる関数）に合わせること。上記は設計意図を示す
> 疑似コードであり、Claude Codeで実装する際は既存のシグネチャに揃えて調整してよい。

### 3.4 ルート設計（`app/routes/mystery.py` への追加）

既存パターン（`generate-trick` → `generate-trick/confirm` のような「生成」「確定」の対）を
踏襲しつつ、お任せモードは中間状態をDBに残さないため、確定は最後の1回のみにする。

```
GET  /mystery-cases/<case_id>/auto-generate
    → プロバイダ選択のみのフォーム（MysteryAutoGenerateForm）

POST /mystery-cases/<case_id>/auto-generate
    → 以下を順に実行し、すべて成功したら一括プレビュー画面へ（DB書き込みはまだしない）：
        1. generate_trick()（オチが空なら2.2節の分岐プロンプトが自動的に効く）
        2. auto_assign_cast()（fixed_role_hintsの反映込み）
        3. generate_environment()（配役済みの実名一覧を渡す。3.1節の通り既存キャラ使用は
           固定配役希望のみ）
      いずれかのステップで AIGenerationError が発生した場合：
        - どのフェーズで失敗したかをflashメッセージで明示する
          （例：「配役（AIモブキャラ生成）に失敗しました。個別に配役をやり直す場合はこちら」）
        - Phase2の結果だけは既にプレビュー可能な場合、従来の個別フロー
          （generate-trick/confirm → cast → generate-environment）への導線を残し、
          お任せモードが失敗しても手動でリカバリーできるようにする

POST /mystery-cases/<case_id>/auto-generate/confirm
    → 一括プレビューで確認された内容をまとめて保存する：
        1. pending_mob_charactersをdb.session.add_all() → flush()でid確定
        2. required_cast_jsonの各要素の _pending_character_ref を、確定したidで
           assigned_character_idに置き換えてシリアライズ
        3. trick_json / required_cast_json / environment_json / climax_twist（2.3節）を保存
        4. status = environment_ready, cast_status = complete
        5. db.session.commit()
```

### 3.5 テンプレート

- `app/templates/mystery/auto_generate.html`：起動フォーム（プロバイダ選択のみ）。既存の
  `generate_trick.html` 等と同じデザイン方針。
- `app/templates/mystery/auto_generate_preview.html`：トリック・配役結果（自動生成された
  モブキャラの一覧）・環境データを1画面にまとめて表示するプレビュー。既存の
  `trick_preview.html` / `cast.html` / `generate_environment.html` の表示要素を1画面に集約する
  イメージ。「この内容で確定する」ボタンと「キャンセルして個別フローに戻る」リンクを置く。
  `trick_json` の表示は既存方針通りデフォルト非表示＋「ネタバレを表示する」トグルを踏襲する。

### 3.6 事件詳細画面への導線

`app/templates/mystery/detail.html`（事件詳細画面）に、`status == "draft"` の場合のみ
「AIにすべてお任せして生成する」ボタンを追加する（`auto-generate` へのリンク）。既に
`trick_ready` 以降まで進んでいる事件には表示しない（部分的にお任せモードを適用する複雑な
差分マージは今回のスコープ外とする）。

---

## 4. 実装しないこと（今回のスコープ外）

- お任せモード中の「既存キャラクターを役割にマッチングさせる」機能（例：性別や年齢が近い
  既存キャラを自動候補にする、等）。今回は「固定配役の希望で指定した役割以外はすべて
  新規モブキャラ」というシンプルな仕様にとどめる。
- お任せモードの部分適用（Phase2だけお任せ、配役は手動、のような混在フロー）。
  必要になった場合は別途設計する。
- Phase5（矛盾検知）の自動実行への組み込み。お任せモードはPhase3までとし、Phase5は
  従来通りボタン起動のまま変更しない（`docs/mystery_trick_module_design.md` 7章の
  「矛盾の自動検知の常時実行はしない」という既存方針を維持）。

---

## 5. 実装後の確認事項

- `MysteryCaseForm.climax_twist` を空で送信した場合に、Phase2生成後
  `case.climax_twist` が正しく埋まることを確認する。
- お任せモードの確定保存後、`required_cast_json` の全要素に `assigned_character_id` が
  埋まっており、`cast_status` が `complete` になっていることを確認する。
- お任せモードで生成されたモブキャラが、`mystery_case_id` 経由で正しくスコープされ、
  メインのキャラクター管理一覧（`characters.index`）に表示されないことを確認する
  （既存のモブキャラ設計方針を踏襲できているかの回帰確認）。
- お任せモード実行中にAI呼び出しが1回でも失敗した場合、DBに中途半端な状態が残らないこと
  （`db.session.add` 等をconfirmルートまで遅延させている設計が機能しているか）を確認する。
