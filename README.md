# AI Story Studio

小説・ノベルゲーム・アドベンチャーゲームのシナリオ制作を支援するWebアプリケーション（Flask版・雛形）。

仕様書「AI Story Studio 仕様概要書」に基づき、以下の情報管理機能を実装しています。

- プロジェクト（作品）管理
- キャラクター管理
- 世界設定管理
- 章管理（本文はMarkdownファイルとして保存）
- 伏線管理（pending / processing / completed / failed）
- **AIシナリオ生成（マルチプロバイダ対応）**：ChatGPT（OpenAI）/ Gemini（Google）/ Claude（Anthropic）/ Ollama（ローカル実行）から生成時に選択可能。作品情報・キャラクター・世界設定・過去章要約・未回収の伏線をプロンプトに含めて次章を自動生成
- **AIキャラクター生成**：作品情報（タイトル・ジャンル・あらすじ）と既存のキャラクター・世界設定をもとに、AIが新規キャラクター案をJSON形式で複数提案。プレビュー画面で内容を確認し、登録したいものだけ選んでDBに保存できる（未保存の下書き段階を挟むため、気に入らない案はそのまま破棄できる）
- **AI世界設定生成**：キャラクター生成と同じ設計で、世界観の案を複数提案・プレビュー・選択登録できる
- **AI伏線生成**：既存の章一覧を踏まえて配置章・回収章の候補も自動で対応付け。該当する章がない場合は未設定のまま登録され、後で手動編集できる

## セットアップ

```bash
python -m venv venv
source venv/bin/activate   # Windowsは venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# .env を編集し、SECRET_KEY と使用したいAIプロバイダのAPIキーを設定する

python run.py
```

起動後、ブラウザで `http://127.0.0.1:5000/` にアクセスしてください。

## ディレクトリ構成

```
ai_story_studio/
├── app/
│   ├── __init__.py       # アプリケーションファクトリ
│   ├── models.py         # DBモデル（Project / Character / WorldSetting / Chapter / Foreshadowing）
│   ├── forms.py          # Flask-WTFフォーム（バリデーション）
│   ├── extensions.py     # SQLAlchemy / CSRF初期化
│   ├── ai_service.py     # AIシナリオ生成（OpenAI/Gemini/Claude/Ollama）
│   ├── routes/           # Blueprint（機能ごとにCRUDルートを分割）
│   ├── templates/        # Jinja2 + Bootstrap5テンプレート
│   └── static/css/
├── data/chapters/        # 章本文のMarkdownファイル保存先
├── config.py
├── run.py
└── requirements.txt
```

## 実装済みのエラーハンドリング

- 存在しないデータへのアクセス → 404エラー画面
- 章本文のMarkdownファイルが存在しない場合 → 空文字として編集画面を表示
- 必須項目未入力（タイトル・章番号・キャラクター名・プロジェクト名）→ 保存せず入力画面へ戻す
- 本文ファイル保存失敗時 → DB更新を中止しエラーメッセージを表示
- AI生成失敗時（認証エラー・通信エラー・レート制限など）→ 章の生成ステータスを`failed`に更新し、エラーメッセージを表示し、再生成可能な状態にする

## AIシナリオ生成機能の使い方

1. `.env` に、使用したいAIプロバイダのAPIキーを設定する
   - ChatGPT: `OPENAI_API_KEY`（https://platform.openai.com/api-keys ）
   - Gemini: `GEMINI_API_KEY`（https://aistudio.google.com/apikey ）
   - Claude: `ANTHROPIC_API_KEY`（https://console.anthropic.com/settings/keys ）
   - Ollama: APIキー不要。ローカルで `ollama serve` を起動し、`OLLAMA_BASE_URL`（デフォルト `http://localhost:11434`）と
     `OLLAMA_MODEL`（例: `llama3.1`）を指定する
2. `DEFAULT_AI_PROVIDER` で生成画面を開いたときに最初に選択されるプロバイダを指定できる（省略時は`gemini`）
3. 作品詳細画面、または章一覧画面の「AIで次章生成」ボタンから生成画面へ
4. 章番号（自動的に次の番号が入力される）・仮タイトル・**利用するAI（プルダウン）**・追加指示（任意）を入力して生成
5. 生成された本文は自動的にMarkdownファイルとして保存され、本文編集画面へ遷移する
6. 生成に失敗した場合は章一覧に「再生成」ボタンが表示され、別のAIプロバイダに切り替えて再試行することもできる

内部的には `app/ai_service.py` の `build_prompt()` が、作品情報・キャラクター・世界設定・
これまでの章の要約・未回収の伏線（`Foreshadowing.status == "pending"`）を
共通のプロンプトとして組み立て、選択されたプロバイダに応じて
`_generate_with_openai()` / `_generate_with_gemini()` / `_generate_with_claude()` / `_generate_with_ollama()`
のいずれかに振り分けている。どの章がどのAIで生成されたかは `Chapter.generation_provider` に記録される。

### 章構成の方針（常に適用される）

`build_prompt()` は、追加指示の有無にかかわらず常に次の方針をAIへ指示する。

- 各章は、それ単体で起承転結が完結する小さな事件・エピソードとして書く（「逆転裁判」のような話数完結型のイメージ）
- 同時に、その章の中に最終章で明かされる大きな謎につながる手がかりを、読者に今は気づかせない程度にさりげなく仕込む

この方針を章ごとに変えたい場合は、生成画面の「追加指示」欄で上書き・補足する。

## AIキャラクター/世界設定/伏線生成機能の使い方

1. 各管理画面（キャラクター管理・世界設定管理・伏線管理）の「AIでたたき台を生成」ボタンから生成画面へ
2. 生成する件数（1〜10件）・利用するAI・追加指示（任意）を入力して生成
3. プレビュー画面で候補が表示される（この時点ではまだDBに保存されていない）
4. 登録したいものだけチェックを入れて「登録する」を押すと保存される
5. 登録後は通常の編集画面から個別に内容を調整できる

章生成とは異なり、生成結果を確認してから選んで保存する一段階を挟んでいる点が特徴。
不要な候補はチェックを外すか、そのままプレビュー画面を離れれば破棄される（DBには一切残らない）。

伏線生成のみ、AIが提案した配置章・回収章の「章番号」を既存の章と照合し、
一致する章があれば自動的に紐付ける。該当する章がない場合（まだ章を登録していない、
AIが存在しない章番号を答えた等）は未設定のまま登録されるので、必要に応じて後から編集する。

## 今後の拡張予定

- AI生成中アニメーション
- Markdownプレビュー
- ダークモード / レスポンシブ対応
