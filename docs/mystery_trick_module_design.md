# ミステリー・トリック生成モジュール 設計書

## 0. 位置づけ（重要・CLAUDE.md更新が必要）

`CLAUDE.md`「見送った・保留にした機能」には以下の記載がある。

> **トリック提案AI・構造化難事件データベース（RAG）**：利用者の方針により不要と判断済み。
> トリックの着想はAIの一般知識に任せ、問題があれば人間のシナリオライターが編集する運用。

今回、利用者の判断により**この方針を覆し、AI Story Studio本体にトリック生成AIを組み込む**。
ただし以下の条件で、当時懸念されていた「専用データベース（RAG）」の部分は引き続き見送る。

- 過去事件のベクトル検索・類似トリック検索DBは**作らない**
- トリックの着想はAIの一般知識＋厳格なJSON出力＋矛盾検証プロンプトに任せる
- 生成結果は他の生成機能と同じく「生成→プレビュー→選択保存」を経てからDBに確定する

**Claude Codeでの作業時は、この設計書に着手する前に `CLAUDE.md` の該当項目を
「実装済み（RAGなし版）」に更新すること。** 見送り理由（RAGを避けた理由等）は
そのまま残し、上書き履歴として追記する形が望ましい。

### 0.1 本モジュールの立ち位置（重要）

事件（トリック・環境・小道具）はストーリーの一要素であり、**本編シナリオ生成の「ルール」
そのものには一切手を入れない**。具体的には以下は変更しない。

- `build_prompt()` の基本構造、セリフ書式（`【名前】「セリフ」`）、`■背景:`/`■場面:` の挿入規約
- 「起承転結が完結する話数完結型」の章構成方針
- 4プロバイダ（OpenAI/Gemini/Claude/Ollama）の振り分けロジック

このモジュールが行うのは、**事件が絡む章を生成するときだけ、本編生成プロンプトに渡す
「材料」を増やすこと**に限定する（配役済みモブキャラをキャラクター一覧に混ぜる、
`Project.constraints`と同じ要領で`environment_json`の客観的事実を任意注入する、等）。
新しい生成ルールを追加するのではなく、既存ルールへの入力を補強するだけ、という位置づけ。

メインシナリオと事件の顛末との辻褄合わせは、このモジュール単体の責務ではなく、以下の
**二段構え**で担保する。

1. **生成時（予防）**：事件範囲の章にだけ必要な材料（配役済みキャラ・客観的事実）を渡し、
   そもそも矛盾が生まれにくい状態を作る
2. **生成後（検知）**：Phase 5（判定・アナライザーAI）が、実際に書かれた章本文を正解データと
   突き合わせて矛盾を検出する

Phase 5が「検知」を担う以上、予防（1）を完璧にする必要はない。多少の注入漏れがあっても
Phase 5で拾える、という前提で設計してよい（過度にプロンプトを複雑化させない）。

---

## 1. スコープ

利用者原案のPhase構成をベースにするが、**「先にトリックが人数・役割を要求し、後から配役する」**
という順序に修正する（詳細は2章・3章）。トリックや環境・小道具によって必要になる目撃者・
協力者の人数は、トリックが決まるまで見積れないため。

| Phase | 内容 | 実装対象 |
|---|---|---|
| Phase 1 | ユーザー入力（世界観・オチ。登場人物は**任意の固定指定のみ**） | ○ 実装する |
| Phase 2 | トリック提案AI（**抽象配役表を含む**） | ○ 実装する |
| Phase 2.5 | 配役（キャスティング）：抽象役割 → 既存キャラ or 事件専用モブキャラ | ○ 新設 |
| Phase 3 | 環境・小道具生成AI（配役済みの実名で生成） | ○ 実装する |
| Phase 4 | シミュレーター（キャラ会話ログ生成） | × 対象外（原案通り、既存の章生成／尋問シナリオ生成機能で代替） |
| Phase 5 | 判定・アナライザーAI | ○ 実装する（章本文 or 尋問ログを解析対象にする） |

**Phase 1の変更点**：当初案では「登場人物リスト」をユーザーが最初に入力する想定だったが、
それだとトリック側の要求（目撃者は何人必要か等）と噛み合わない場合がある。Phase 1では
「探偵役はこのキャラクターに固定したい」のような**強い希望がある場合のみ**任意入力とし、
それ以外の役割（被害者・犯人・目撃者・協力者等）はPhase 2のAIに委ねる。

Phase 4を実装しない理由：AI Story Studioには既に「章生成」「本文のAIによる部分修正」
「尋問シナリオ生成（独立セッション方式）」があり、これらが実質的にPhase 4の役割を担う。
新しい生成経路を増やさず、既存パターンを再利用する（`CLAUDE.md`の開発方針「新しい仕組みを
増やしすぎない」に従う）。

---

## 2. データモデル

新規テーブル `MysteryCase` を `Project` に1対多で追加する。1作品に複数の事件（章単位の
ミステリー、例：第1章の殺人事件、第2章の窃盗事件）を持てるようにする。

```python
# app/models.py に追記

class MysteryCase(db.Model):
    __tablename__ = "mystery_cases"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)

    title = db.Column(db.String(200), nullable=False)  # 事件名（例：離島館連続殺人事件）

    # --- Phase 1: ユーザー入力（構想） ---
    case_world_setting = db.Column(db.Text)      # 事件固有の舞台設定（作品全体のWorldSettingとは別に、事件用の補足）
    climax_twist = db.Column(db.Text)             # 絶対条件・オチ
    fixed_role_hints_json = db.Column(db.Text)
    # 任意。「探偵役はこのキャラクターに固定したい」等、強い希望がある役割のみユーザーが指定。
    # [{"character_id": 12, "role": "detective"}] のように既存Characterへの参照のみを持つ。
    # ここで指定しなかった役割は、すべてPhase2のAIが必要数を判断する。

    # --- Phase 2: トリック（真相）+ 抽象配役表 ---
    trick_json = db.Column(db.Text)   # TrickOutput相当のJSON文字列（真相・矛盾セットを含む秘匿情報）
    required_cast_json = db.Column(db.Text)
    # トリックが要求する役割の一覧（配役前・抽象状態）。
    # [
    #   {"role_key": "witness_1", "role_type": "witness", "public_trait": "船着き場の管理人。20時頃の船の出入りを見ている",
    #    "fixed_character_id": null, "assigned_character_id": null},
    #   {"role_key": "accomplice_1", "role_type": "accomplice", "public_trait": "犯人に協力した館の使用人", ...}
    # ]
    # fixed_character_id は Phase1の fixed_role_hints_json から引き継いだ確定分。
    # assigned_character_id はPhase2.5（配役）で確定するまではnull。

    # --- Phase 2.5: 配役（キャスティング）確定結果 ---
    cast_status = db.Column(db.String(20), default="pending")
    # pending(未配役あり) / complete(全役割に配役済み)
    # 配役自体は required_cast_json の各要素の assigned_character_id を埋めていく形で記録するため、
    # 別テーブルは持たず required_cast_json を正とする（他の生成物と同じくJSON1本化で管理を簡単にする）。

    # --- Phase 3: 環境・小道具 ---
    environment_json = db.Column(db.Text)  # timeline / location / weather / items（配役確定後、実名で生成）
    # Phase3生成中に「この場面にはもう1人、船頭が必要」等、追加の抽象役割が判明した場合は
    # required_cast_json に role_type="witness"等で追記し、cast_status を再度 pending に戻す
    # （配役ステップへ差し戻し、環境生成をやり直す）。

    # --- Phase 5: 判定結果（任意・複数回実施しうるため履歴は別テーブルでも可） ---
    latest_evaluation_json = db.Column(db.Text)
    latest_evaluation_source = db.Column(db.String(20))  # "chapter" | "interrogation"
    latest_evaluation_target_chapter_id = db.Column(db.Integer, db.ForeignKey("chapters.id"))

    # 章との紐付け（この事件がどの章で発生するか。任意・未設定可）
    # ※ Chapter が major_number/sub_number構成になった場合も、ここは常にサブ章単位の
    #   Chapter.id を指す（詳細は5.1節）。
    trigger_chapter_id = db.Column(db.Integer, db.ForeignKey("chapters.id"))
    resolution_chapter_id = db.Column(db.Integer, db.ForeignKey("chapters.id"))

    status = db.Column(db.String(20), default="draft")
    # draft(構想中) / trick_ready(トリック確定) / environment_ready(環境確定) / evaluated(評価済み)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    project = db.relationship("Project", backref="mystery_cases")
```

### 2.1 モブキャラクターのスコープ設計（メインのキャラクター管理を汚さない）

配役（Phase 2.5）で「既存キャラクターに割り当てる」以外に「事件専用の新規モブキャラを
その場で生成する」選択肢を用意する。これらのモブキャラは、既存の「キャラクター管理」一覧
（`characters.index`）を煩雑にしないよう、**事件という箱の中だけに存在するスコープ**を持たせる。

**新規テーブルは作らず、既存の `Character` モデルにスコープ用カラムを追加する方式を推奨する**
（サムネイル・名前重複チェック等の既存機能をそのまま使い回せるため。CLAUDE.mdの「新しい仕組みを
増やしすぎない」方針にも合致する）。

```python
# app/models.py の Character に追記

class Character(db.Model):
    ...
    mystery_case_id = db.Column(db.Integer, db.ForeignKey("mystery_cases.id"), nullable=True)
    # NULL = 通常のメインキャスト（従来通りキャラクター管理一覧に表示）
    # 値あり = その事件専用のモブキャラ（メインのキャラクター管理一覧には出さない）
```

- `characters.index`（キャラクター管理一覧）のクエリに `Character.mystery_case_id.is_(None)`
  の絞り込みを追加するだけで、モブキャラは自動的に一覧から除外される。
- 一方で `character_id` としては通常のCharacterと同一の実体なので、既存の章生成プロンプト組み立て
  （`build_prompt()` のキャラクター一覧取得部分）やサムネイル表示、名前重複チェックのロジックを
  そのまま再利用できる。
- 名前重複チェックは**プロジェクト全体で統一**のままにする（モブキャラだけ別スコープの緩い
  ルールにすると実装が複雑になるため。同名になりそうな場合は既存の警告フローに任せる）。
- 事件（`MysteryCase`）を削除した場合は、紐づくモブキャラ（`mystery_case_id`が一致するもの）も
  まとめて削除する（キャラクターサムネイル削除と同様、孤立データを残さない）。

**代替案（不採用）**：モブキャラ専用の軽量テーブルを別途新設する案も検討したが、サムネイル・
名前重複チェック・章生成プロンプトへの取り込みロジックを二重実装することになり、
「新しい仕組みを増やしすぎない」方針に反するため見送る。

### 2.2 章との紐付けスコープ

モブキャラは「その事件が関わる章」の生成時のみプロンプトに含める。`MysteryCase` の
`trigger_chapter_id`〜`resolution_chapter_id` の範囲（章番号ベース）に該当する章を生成する
ときだけ、`build_prompt()` が該当事件のモブキャラ一覧を追加で取得してキャラクター情報に含める。
範囲外の章では取得しない（無関係な事件のモブが全章のプロンプトに常時混ざるのを防ぐ）。

**設計上のポイント**

- `trick_json`（真相・矛盾セット）は**秘匿情報**。既存の尋問シナリオ機能における
  「探偵役プロンプトには`Project.constraints`を渡さない」設計思想と同じく、
  この項目を**通常の章生成プロンプトへは絶対に自動注入しない**（後述4章）。
  誤って全文をAIに見せると「知るはずのない情報」を書いてしまう事故につながる。
- `involved_characters_json` は既存の `Character` テーブルへの参照（`character_id`）を
  優先し、未登録の人物（被害者など、キャラクター管理に登録するまでもない端役）は
  `character_id: null` のまま名前のみ保持できるようにする（伏線生成の「該当する章がない
  場合は未設定のまま登録」と同じ緩い紐付け方針）。
- `migrate_db.py` の `COLUMNS_TO_ENSURE` に本テーブルの全カラムを追記すること
  （`CLAUDE.md` 4.のルール）。

---

## 3. 画面・ルート設計（既存パターンの踏襲）

既存の「AIでたたき台を生成 → プレビュー → 選択保存」と同じ二段階を、Phase単位で繰り返す。

```
app/routes/mystery.py

GET/POST /projects/<id>/mystery-cases/new
    → Phase 1入力フォーム（世界観補足・オチ・固定配役の希望〈任意〉）→ MysteryCase(draft)をDB作成

GET  /projects/<id>/mystery-cases/<case_id>
    → 事件詳細（Phase1〜5の状態を一覧表示。真相(trick_json)は「ネタバレ表示」トグルで
      デフォルト非表示にする。制作者がうっかりスクショ等で漏らすのを防ぐUI配慮）

POST /mystery-cases/<case_id>/generate-trick        → Phase2 AI呼び出し（プレビューのみ、DB未保存）
POST /mystery-cases/<case_id>/generate-trick/confirm → 選択確定 → trick_json + required_cast_json保存、
                                                         status=trick_ready, cast_status=pending

--- Phase 2.5: 配役（キャスティング）---
GET  /mystery-cases/<case_id>/cast
    → required_cast_json の未配役ロール一覧を表示。各ロールごとに
      (a) 既存キャラクターから選ぶ プルダウン か
      (b) 「AIでモブキャラ案を生成」ボタン（既存のAIキャラクター生成と同じプレビュー→選択保存UIを
          再利用。生成されたCharacterには mystery_case_id をセットして保存）
      のどちらかを選べる。

POST /mystery-cases/<case_id>/cast/assign
    → 選んだ character_id を required_cast_json の該当ロールの assigned_character_id に書き込む。
      全ロールが埋まったら cast_status=complete。

POST /mystery-cases/<case_id>/generate-environment        → Phase3 AI呼び出し（trick_json + 配役済み
                                                              の実名一覧が必須。cast_status=complete
                                                              でないと実行不可）
POST /mystery-cases/<case_id>/generate-environment/confirm → environment_json保存、status=environment_ready
      ※このAI応答内で新たな役割（例：船頭）が必要と判明した場合は、required_cast_jsonに追記して
        cast_status を pending に戻し、配役ステップへ差し戻す（プレビュー画面に警告表示）。

POST /mystery-cases/<case_id>/evaluate
    → Phase5。対象を選択：
        - 既存の章本文（Chapter.content_path のMarkdown）
        - 既存の尋問シナリオログ（Interrogation機能の出力）
      trick_json + environment_json（正解データ）と対象テキストを比較し、
      評価結果をプレビュー表示 → 保存ボタンで latest_evaluation_json に確定保存
```

CSRF・「すべて削除」・生成中インジケーターは既存の他機能と同じ実装を横展開する。

---

## 4. プロンプト設計（`app/ai_service.py` への追加）

既存の `_dispatch(prompt, provider)` をそのまま再利用し、4プロバイダ共通のJSON生成関数として
`_generate_structured(system_prompt, user_prompt, provider)` を用意する（Gemini限定の
`response_schema` 強制機能には頼らず、**プロンプトでJSON専用出力を指示 → パース失敗時は
既存のfailedステータス＋再生成可能パターンで対応**する。理由：OpenAI/Claude/Ollamaも含めた
共通口を維持するため、Gemini固有機能に依存させない）。

### 4.1 build_trick_prompt()（Phase 2）

```python
def build_trick_prompt(case: MysteryCase, project: Project) -> str:
    return f"""
あなたは本格ミステリーのトリック設計専門AIです。
以下の制約を守り、JSON形式のみでトリック構造を出力してください（前置き・Markdown装飾は禁止）。

【制約】
1. 超自然現象・SF的ガジェット・偶然任せのトリックは禁止（ノックスの十戒に準拠）。
2. プレイヤーが証言と証拠品を突きつけて破綻させられる、明確な矛盾(contradiction_set)を
   必ず1つ以上作ること。
3. プレイヤーを別人物へ誘導するミスディレクションを最低1つ含めること。
4. 【禁止事項・既知の事実】に反する設定を作らないこと。

【世界観】
{project.world_setting}
{case.case_world_setting or ""}

【禁止事項・既知の事実】
{project.constraints or "（特になし）"}

【オチ】
{case.climax_twist}

【固定配役の希望（指定があれば必ずその役割で使うこと。指定がない役割は自由に設計してよい）】
{case.fixed_role_hints_json or "（特になし）"}

【出力JSONスキーマ】
{{
  "trick_type": "string",
  "true_mechanism": "string（真相の仕組み。秘匿情報）",
  "misdirection": "string",
  "contradiction_set": {{
    "witness_statement": "string",
    "evidence_fact": "string",
    "key_evidence": "string"
  }},
  "required_cast": [
    {{
      "role_key": "string（例: witness_1）",
      "role_type": "detective|victim|culprit|suspect|witness|accomplice|other",
      "public_trait": "string（この役割に求められる表面的な特徴。配役の判断材料）",
      "necessity_reason": "string（トリック上、なぜこの役割が何人必要なのかの理由）"
    }}
  ]
}}

固定配役の希望で指定された役割は required_cast にも含め、role_key を対応させること。
それ以外の役割は、トリックの成立に本当に必要な人数だけを過不足なく設計すること
（矛盾セットの成立に不要な役割を水増ししない）。
"""
```

`Project.constraints` は既存方針どおりここでも常時注入する（他の全生成機能と統一）。
`required_cast` はこの時点では**抽象状態**（名前を持たない役割の集合）であり、
Phase 2.5（配役）で初めて実際のキャラクター（既存 or 新規モブ）に結びつく。

### 4.2 build_environment_prompt()（Phase 3）

Phase 2で確定した `trick_json`（真相を含む）に加え、**配役済み（Phase 2.5完了後）の
`required_cast_json`**（role_key → 実際のキャラクター名・特徴）を渡し、矛盾のない現場データを
実名で作らせる（「赤城のナイフ」のように抽象役割ではなく実名で描写できるようにするため）。
出力スキーマは原案の `timeline / location / weather / items` をそのまま採用する。
生成AIが応答の中で「この現場にはもう1人、船着き場を見張っていた人物が必要」等、Phase 2で
想定していなかった追加役割を示唆してきた場合は、`additional_required_cast`（Phase 2と同形式の
配列）として別枠で出力させ、ルート側で `required_cast_json` に追記 → `cast_status=pending` に
戻す処理につなげる。

### 4.3 build_evaluation_prompt()（Phase 5）

```python
def build_evaluation_prompt(case: MysteryCase, target_text: str, source_label: str) -> str:
    return f"""
あなたはゲームシナリオの監修者・クオリティアナライザーAIです。
【正解の設定データ】と【実際の本文/ログ】を比較し、JSON形式で評価してください。

【正解の設定データ（外部に漏らしてはいけない真相を含む）】
trick: {case.trick_json}
environment: {case.environment_json}

【検証対象（{source_label}）】
{target_text}

【検証項目】
1. logical_flaws: キャラが知るはずのない情報を話していないか。タイムライン・天候との矛盾。
2. contradiction_playability: 探偵役が提示証拠で嘘を論破できる構成になっているか。
3. narrative_quality: ドラマ性（自然な言い逃れ、駆け引き）。

【出力JSONスキーマ】
{{
  "overall_grade": "S|A|B|C",
  "logical_flaws": [{{"description": "string", "location_hint": "string", "suggestion": "string"}}],
  "contradiction_playability": "string",
  "narrative_quality": "string",
  "notable_excerpts": ["string"]
}}
"""
```

**JSON出力の安定化**：4プロバイダとも構造化出力の強制力が異なるため、以下を共通処理とする
（既存の「AI生成エラー時はfailedへ更新し再生成可能」パターンをそのまま踏襲）。

1. レスポンス文字列からMarkdownコードフェンス（&#96;&#96;&#96;json ... &#96;&#96;&#96;）を除去
2. `json.loads()` を試行、失敗したら `status` を `failed` 相当にしてエラーメッセージ表示＋再生成ボタン
3. 成功時のみプレビュー画面へ渡す（DB保存はまだしない＝既存の3段階パターンを維持）

---

## 5. 本編シナリオ生成との連携（秘匿情報の扱いが最重要）

既存の章本文生成 `build_prompt()` への統合は、**尋問シナリオ生成と同じ「情報を渡す相手を
役割ごとに厳密に分ける」設計思想**を踏襲する。

- 通常の章生成プロンプト（地の文・複数キャラの会話を1回のAI呼び出しで書く形式）には、
  `trick_json.true_mechanism` と `contradiction_set` を**そのままの形では渡さない**。
  渡すのは `environment_json`（timeline / location / weather / items）のうち、
  「作中で自然に描写してよい客観的事実」の部分のみ。
- `true_mechanism`（真相そのもの）は、該当章が「解決編」の章（`resolution_chapter_id`と
  一致する章）を生成する場合に限り、明示的なオプションチェック（「この章で真相を開示する」）
  を入れた上で注入する。デフォルトでは注入しない。
- 尋問シナリオ生成を使う場合は、`suspect_secret` の入力欄に `trick_json` の該当部分を
  制作者が手動で転記する運用（自動連携はしない）。理由：自動連携すると、どの容疑者に
  どこまでの真相を渡すかの判断をAI任せにしてしまい、既存の「容疑者ごとに秘密を厳密に分ける」
  設計の安全性を損なう可能性があるため。
- モブキャラ（2.1節）は、事件範囲内（`trigger_chapter_id`〜`resolution_chapter_id`）の章を
  生成するときのみ、`build_prompt()` のキャラクター一覧取得部分に追加される。範囲外の章では
  存在しないものとして扱う。尋問シナリオ生成で容疑者役にモブキャラを指名する場合も、
  通常のCharacterと同じ扱いで選択できる（`mystery_case_id` の有無はAI呼び出しの入力データとしては
  意識しなくてよく、UI上の一覧表示フィルタの違いだけに留める）。

この節は特に重要なので、実装時にレビューを厳密に行うこと（誤って真相が地の文生成プロンプトに
混入すると、伏線が全て台無しになる）。

### 5.1 章のサブ章構成との関係（前提）

`Chapter`は将来的に `major_number`（第◯章）と `sub_number`（章内の何番目か。例：第1章-2）の
組で管理される想定（既存の「章が一直線に並ぶ」モデルを維持したまま、カラム追加のみで対応する
案。詳細は別途合意済み）。本モジュールの `trigger_chapter_id` / `resolution_chapter_id` は、
**major_number単位ではなく、常に個々の`Chapter.id`（＝サブ章単位）を指す**。

- 1つの事件が「捜査編（第3章-1）で発生し、法廷2日目（第3章-3）で解決する」というように、
  同じ`major_number`内の異なるサブ章にまたがるケースを自然に表現できる
- 章一覧・章生成側の`major_number`/`sub_number`表示ロジックが変わっても、本モジュールの
  FK参照先はサブ章＝`Chapter.id`のままで変更不要（親子テーブル化のような大きな変更が
  将来行われない限り、本モジュール側の追従作業は発生しない）

---

## 6. UI上の配慮

- 事件詳細画面では `trick_json`（真相）をデフォルト非表示にし、「ネタバレを表示する」トグルを
  挟む。

  **補足**：本アプリの想定利用者は基本的に一人（または少人数）の制作担当者であり、
  画面共有や配信を前提とした機能ではない。それでもこのトグルを残すのは、以下のような
  「一人利用でも起こりうる」ケースを想定した最低限の安全策として位置づけているため。

  - 事件詳細画面を開いたまま離席し、後から他の作業（他アプリの画面共有、別の人への
    ちょっとした画面提示など）を行ってしまうケース
  - 制作終盤で外部（テストプレイヤー、共同作業者、レビュー依頼先など）に画面を
    見せる場面が後から発生した場合に、既存の画面のままで対応できるようにしておくケース

  実装コストは「デフォルト非表示のトグル1つ」程度で軽微なため、常時複数人利用を
  想定した本格的なアクセス制御（ユーザー権限管理等）までは行わない。あくまで
  「うっかり見える」を防ぐ簡易的な配慮にとどめる。

- 事件一覧はプロジェクト詳細画面に「事件」セクションとして追加し、既存の
  キャラクター/世界設定/章/伏線と横並びのカードUIにする（既存のBootstrap5デザイン方針を踏襲）。

---

## 7. 実装しないと決めた理由の記録（今後のため）

- **RAG／過去事件データベース**：今回も見送り。トリックの着想はAIの一般知識に任せる方針は
  維持しつつ、「AIによるトリック提案」自体は本体機能として復活させる、という**部分的な方針転換**
  であることを `CLAUDE.md` に明記すること。
- **Phase 4（シミュレーター）専用の新機構**：既存の章生成／尋問シナリオ生成で代替するため作らない。
- **矛盾の自動検知の常時実行**：Phase 5はボタン起動の都度実行とし、章生成のたびに自動実行はしない
  （相関図のキャッシュ更新方針と同じ「コスト意識」を踏襲。AI呼び出しは利用者が押したときのみ）。

---

## 8. Claude Codeへの引き継ぎ用チェックリスト

- [ ] （前提・本モジュールとは別作業）`Chapter`に`major_number`/`sub_number`を追加し、
      既存`chapter_number`からの移行を行う（5.1節）。本モジュールの章参照はこれを前提とするが、
      未対応のままでも`Chapter.id`参照自体は成立するため、本モジュール単体で先行実装しても問題はない
- [ ] `CLAUDE.md`「見送った・保留にした機能」の該当項目を更新（RAGなし版として復活した旨を追記）
- [ ] `app/models.py` に `MysteryCase` 追加（`required_cast_json` / `cast_status` 含む）
- [ ] `app/models.py` の `Character` に `mystery_case_id`（nullable FK）を追加
- [ ] `characters.index` のクエリに `mystery_case_id IS NULL` 絞り込みを追加（モブを一覧から除外）
- [ ] `migrate_db.py` の `COLUMNS_TO_ENSURE` に上記すべてのカラムを追記
- [ ] `app/ai_service.py` に `build_trick_prompt`（required_cast出力込み） /
      `build_environment_prompt`（配役済み実名を利用） / `build_evaluation_prompt` と
      `_generate_structured` を追加（既存の `_dispatch` を再利用）
- [ ] `app/routes/mystery.py` を新規作成（Phase1〜3+配役+評価。配役は既存のAIキャラクター生成
      プレビュー→選択保存フローを再利用し、保存時に `mystery_case_id` をセットする）
- [ ] `app/templates/mystery/` にフォーム・プレビュー・配役画面・詳細（ネタバレトグル付き）画面を追加
- [ ] `build_prompt()`（章生成）に、事件範囲内の章のときだけモブキャラを取り込む分岐を追加
- [ ] 章生成プロンプトへの `environment_json`（客観的事実のみ）の任意注入オプションを追加
      （デフォルトOFF、真相注入は別途明示チェックが必要）
- [ ] 事件削除時にモブキャラも連動削除する処理を追加
- [ ] Flaskテストクライアントで一連のフロー（トリック生成→配役→環境生成→章生成連携→評価）を検証

---

## 9. 既知の不具合と対応（実装後に判明したもの）

### 9.1 お任せ生成時の「本文中の配役名」と「配役結果」の不一致

**症状**：事件をお任せ生成（固定配役の指定なし）した場合、`trick_json`の本文
（`true_mechanism`・`misdirection`・`contradiction_set`内のテキスト）に書かれている
人物名と、配役（Phase 2.5）で実際に割り当てたキャラクター名が食い違うことがある。
固定配役として指定した役割は正しい。

**原因**：`build_trick_prompt()`は固定配役の役割にのみ実名を渡している。それ以外の
（お任せの）役割については名前を一切指定していないため、AIが本文中でその場限りの
名前を創作してしまう。後の配役ステップで別の実在キャラクター（または新規モブ）を
割り当てても、本文中の創作名は書き換わらず、そのまま残ってしまう。

**対応**：AIに名前を創作させず、未配役の役割には`【CAST:role_key】`形式の一意な
トークンで言及させ、配役完了のタイミングで機械的に実名へ置換する。

1. `build_trick_prompt()`の出力JSONスキーマ説明に以下を追記する。

   ```
   固定配役として実名を渡した役割は、その実名をそのまま本文中で使うこと。
   それ以外の役割（まだ配役されていない役割）については、本文中（true_mechanism /
   misdirection / contradiction_set内の全テキスト）で絶対に名前を創作しないこと。
   代わりに必ず「【CAST:role_key】」の形式のトークンで言及すること
   （例：探偵役が witness_1 を問い詰める場面なら「【CAST:witness_1】は…」と書く）。
   ```

2. `app/ai_service.py`に置換処理を追加する。

   ```python
   def _resolve_trick_json_placeholders(case, project) -> None:
       """配役完了時に、trick_json内の【CAST:role_key】トークンを実際のキャラクター名に
       置換する。冪等（トークンが残っていなければ何もしない）なので、何度呼んでも安全。
       """
       if not case.trick_json:
           return
       try:
           trick = json.loads(case.trick_json)
       except (json.JSONDecodeError, TypeError):
           return

       character_lookup = {c.id: c for c in project.characters}
       character_lookup.update({c.id: c for c in case.mob_characters})
       required_cast = _load_json_list(case.required_cast_json)

       replacements = {}
       for entry in required_cast:
           role_key = entry.get("role_key")
           character = character_lookup.get(entry.get("assigned_character_id"))
           if role_key and character:
               replacements[f"【CAST:{role_key}】"] = character.name

       def _replace_in(text):
           if not isinstance(text, str):
               return text
           for token, name in replacements.items():
               text = text.replace(token, name)
           return text

       trick["true_mechanism"] = _replace_in(trick.get("true_mechanism"))
       trick["misdirection"] = _replace_in(trick.get("misdirection"))
       cs = trick.get("contradiction_set")
       if isinstance(cs, dict):
           for key in ("witness_statement", "evidence_fact", "key_evidence"):
               cs[key] = _replace_in(cs.get(key))

       case.trick_json = json.dumps(trick, ensure_ascii=False)
   ```

3. `app/routes/mystery.py`の`_recompute_cast_status()`に`project`引数を追加し、
   `cast_status`が`COMPLETE`になったタイミングで上記関数を呼ぶ。

   ```python
   def _recompute_cast_status(case, project) -> list:
       required_cast = _load_json_list(case.required_cast_json)
       if required_cast and all(entry.get("assigned_character_id") for entry in required_cast):
           case.cast_status = MysteryCase.CAST_STATUS_COMPLETE
           _resolve_trick_json_placeholders(case, project)
       else:
           case.cast_status = MysteryCase.CAST_STATUS_PENDING
       return required_cast
   ```

   呼び出し元（`cast_assign`・モブキャラ確定ルート）は`_recompute_cast_status(case)`を
   `_recompute_cast_status(case, project)`に変更する。

4. `build_environment_prompt()`は配役完了後の（＝トークン置換済みの）`trick_json`を
   参照する前提のままでよく、変更不要。

**適用範囲の注意**：この修正は今後の新規生成分にのみ有効。既にお任せ生成済みで
名前が食い違っている事件は、トークンではなく創作された名前が直接埋め込まれているため
機械的な置換では直せない。該当の事件はトリックを再生成するか、`trick_json`を手動編集する。

### 9.2 固定配役の役割入力（英単語）の負担軽減

**症状**：Phase1フォームの「固定配役の希望」欄は`detective`/`victim`/`culprit`/
`suspect`/`witness`/`accomplice`/`other`という英単語での入力を要求しており、
毎回意味を調べたり正確に入力したりする手間がある。

**対応**：`_parse_fixed_role_hints()`が役割の単語を正規化する際に、英単語に加えて
日本語ラベルも受け付けるようにする（内部的に使う`role_type`の値は英語のまま変更しない）。

```python
ROLE_LABEL_TO_KEY = {
    "探偵": "detective",
    "被害者": "victim",
    "犯人": "culprit",
    "容疑者": "suspect",
    "目撃者": "witness",
    "共犯者": "accomplice",
    "その他": "other",
}

# _parse_fixed_role_hints() 内、role を判定する箇所の直前に追記
role = ROLE_LABEL_TO_KEY.get(role, role)
if role not in ROLE_TYPE_CHOICES or character is None:
    skipped_lines.append(line)
    continue
```

`app/forms.py`の`MysteryCaseForm.fixed_role_hints_text`の`description`も、英単語例
（`detective: 名探偵コナン`）から日本語例（`探偵: 名探偵コナン`）に変更し、英単語も
引き続き使えることを併記する。

ドロップダウン選択式のUIへの作り替えも可能だが、テキスト欄1つで完結する現状の
シンプルさを崩すコストに見合わないため見送る。将来的に入力頻度が高くなり負担が
無視できなくなった場合に再検討する。

---

## 10. 生成済み事件内容のAIによる部分修正

既存の「本文のAIによる部分修正」（`app/routes/chapters.py`の`revise`/`revise_confirm`、
`build_revision_prompt`）と同じ3段構成（修正指示入力 → プレビュー → 確認保存）を、
確定済みのトリック（`trick_json`）・環境データ（`environment_json`）にも適用する。

### 10.1 対象範囲の制限（重要）

- 修正対象は**確定済みのデータのみ**（トリックは`status`が`trick_ready`以降、環境は
  `environment_ready`以降）。まだ配役前（本文に`【CAST:role_key】`トークンが残っている
  状態）は対象外とし、その場合はPhase2のトリック再生成をやり直す運用とする。
- **配役（誰がどの役割を演じるか）の変更はこの機能では扱わない**。既存の配役画面
  （Phase 2.5）に案内する。理由：自由記述のテキスト修正で名前を書き換えさせると、
  `required_cast_json`の`assigned_character_id`と本文の記述が食い違う、9.1で修正した
  不一致バグと同種の問題を再発させるおそれがあるため。プロンプト側で「登場人物の名前・
  人数・役割構成は変更しない」ことを厳守事項として明示する。

### 10.2 ルート設計

```
GET/POST /mystery-cases/<case_id>/trick/revise
    → 修正指示＋利用AIを入力 → build_trick_revision_prompt()でプレビュー生成（未保存）
POST     /mystery-cases/<case_id>/trick/revise/confirm
    → プレビュー確認後、trick_jsonを上書き保存

GET/POST /mystery-cases/<case_id>/environment/revise
POST     /mystery-cases/<case_id>/environment/revise/confirm
    → 同構成。修正後に新たな役割が必要と判明した場合はrequired_cast_jsonに追記し、
      cast_statusをpendingへ戻して配役画面へ差し戻す（Phase3の generate-environment と同じ扱い）
```

### 10.3 プロンプト設計（`app/ai_service.py`）

```python
def build_trick_revision_prompt(case: MysteryCase, project: Project, revision_instructions: str) -> str:
    lines = [
        "以下は、あるミステリーゲームの事件について、既に確定しているトリック（真相）データです。",
        "この内容に対して、下記の「修正指示」で指定された変更点だけを反映するように書き換えてください。",
        "",
        "【厳守事項】",
        "1. 登場人物の名前・人数・役割構成（誰が探偵役／目撃者役か）は絶対に変更しないこと。"
        "配役自体を変えたいという意図の指示であっても、名前は変えずそのまま維持すること"
        "（配役の変更は別の画面で行うため、この修正では扱わない）。",
        "2. contradiction_set（証言・客観的事実・キー証拠）の整合性が崩れないよう、"
        "変更箇所に連動する他の項目も必要な範囲で合わせて修正すること。",
        "3. JSON構造（キー名）は変更しないこと。",
        "",
        f"## 修正対象の事件データ\n```json\n{case.trick_json}\n```",
        f"\n## 修正指示\n{revision_instructions}",
        "\n修正後のJSON全体のみを出力してください（前置き・説明・Markdown装飾は禁止）。",
    ]
    return "\n".join(lines)


def revise_trick(case: MysteryCase, project: Project, revision_instructions: str, provider: str = "") -> dict:
    """確定済みのtrick_jsonを、指示された変更点だけ反映する形でAIに書き換えさせる（未保存）。"""
    prompt = build_trick_revision_prompt(case, project, revision_instructions)
    raw_text = _dispatch(prompt, provider)
    return _parse_json_object(raw_text)
```

`build_environment_revision_prompt`/`revise_environment`も同様の構成とし、環境データ
（timeline/location/weather/items）に対して「配役済みの実名一覧は変更しない」ことを
厳守事項に含める。

### 10.4 プレビュー画面での軽い整合性チェック（自動ブロックはしない）

保存前に、修正後テキストに配役済みキャラクター名が1つも含まれていない場合、
「意図せず新しい人物名を作ってしまっていないか確認してください」という警告を
プレビュー画面に表示する。あくまで確認を促す警告に留め、章の部分修正と同じく
最終判断は利用者の確認に委ねる（自動ブロックはしない）。

```python
def _warn_unexpected_names(revised_trick: dict, case: MysteryCase, project: Project) -> list[str]:
    known_names = {c.name for c in project.characters} | {c.name for c in case.mob_characters}
    text = " ".join([
        revised_trick.get("true_mechanism", ""),
        revised_trick.get("misdirection", ""),
        *revised_trick.get("contradiction_set", {}).values(),
    ])
    if known_names and not any(name in text for name in known_names):
        return [
            "修正後の文章に、配役済みのキャラクター名が見当たりません。"
            "意図せず新しい人物名を作ってしまっていないか確認してください。"
        ]
    return []
```

### 10.5 チェックリスト追記

- [ ] `app/forms.py` に `MysteryTrickReviseForm` / `MysteryEnvironmentReviseForm`
      （`revision_instructions` + `provider`。`ChapterReviseForm`と同型）を追加
- [ ] `app/ai_service.py` に `build_trick_revision_prompt` / `revise_trick` /
      `build_environment_revision_prompt` / `revise_environment` / `_warn_unexpected_names` を追加
- [ ] `app/routes/mystery.py` に `trick/revise`・`trick/revise/confirm`・
      `environment/revise`・`environment/revise/confirm` を追加
- [ ] `app/templates/mystery/` に `trick_revise.html` / `trick_revise_preview.html` /
      `environment_revise.html` / `environment_revise_preview.html` を追加
      （`chapters/revise.html`・`chapters/revise_preview.html`を踏襲）
- [ ] 事件詳細画面に「AIで部分修正」ボタンを追加（トリック・環境それぞれ、確定済みの場合のみ表示）
