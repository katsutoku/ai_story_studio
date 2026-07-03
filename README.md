# AI Story Studio

小説・ノベルゲーム・アドベンチャーゲームのシナリオ制作を支援するWebアプリケーション（Flask版・雛形）。

仕様書「AI Story Studio 仕様概要書」に基づき、以下の情報管理機能を実装しています。

- プロジェクト（作品）管理
- キャラクター管理
- 世界設定管理
- 章管理（本文はMarkdownファイルとして保存）
- 伏線管理（pending / processing / completed / failed）

AIシナリオ生成機能（OpenAI API連携）は `app/ai_service.py` に雛形のみ用意しており、
今後の実装予定です。

## セットアップ

```bash
python -m venv venv
source venv/bin/activate   # Windowsは venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# .env を編集し、SECRET_KEY や OPENAI_API_KEY を設定する

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
│   ├── ai_service.py     # AIシナリオ生成の雛形（未接続）
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

## 今後の拡張予定

- OpenAI APIによるAI章生成（`app/ai_service.py` を接続）
- AI生成中アニメーション
- Markdownプレビュー
- ダークモード / レスポンシブ対応
