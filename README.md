# AI・テクノロジーニュース自動収集ツール

RSS フィードから AI・技術ニュースを毎日収集し、GitHub Pages でスマホから読めるニュースレポートを自動生成します。

## セットアップ手順（初回のみ）

### 1. GitHub リポジトリを作成・プッシュ

```bash
cd /Users/yusukeo/ai_tech_news
git init
git add .
git commit -m "initial commit"
# GitHub で新しいリポジトリを作成後:
git remote add origin https://github.com/あなたのユーザー名/ai_tech_news.git
git branch -M main
git push -u origin main
```

> **注意**: `news_report_*.html` をコミットしないよう `.gitignore` が設定済みです。

### 2. GitHub Pages を有効化

1. リポジトリの **Settings** → **Pages** を開く
2. **Source** を `Deploy from a branch` に設定
3. **Branch** を `main` / `docs` に設定して **Save**
4. 数分後に `https://あなたのユーザー名.github.io/ai_tech_news/` でアクセス可能になります

### 3. 自動実行の確認

- **Actions** タブ → `Daily News Report` ワークフローが表示されていれば OK
- 毎日 **JST 07:00** に自動実行されます
- `Run workflow` ボタンで手動実行も可能

### （任意）OpenAI AI要約を使う場合

1. **Settings** → **Secrets and variables** → **Actions** → **New repository secret**
2. Name: `OPENAI_API_KEY`, Value: `sk-...` を入力して保存
3. ワークフローを `--ai-summary` オプション付きに変更

---

## ローカルでの実行

```bash
# 依存パッケージをインストール（初回のみ）
pip3 install -r requirements.txt

# 実行（過去3日分、docs/index.html に出力）
python3 fetch_news.py -o docs/index.html

# オプション例
python3 fetch_news.py --days 7        # 過去7日分
python3 fetch_news.py --no-translate  # 翻訳スキップ（高速化）
python3 fetch_news.py --ai-summary    # OpenAI で要約（OPENAI_API_KEY 必要）
```

## ファイル構成

```
ai_tech_news/
├── fetch_news.py          # メインスクリプト
├── config.json            # RSSフィード設定
├── requirements.txt       # 依存パッケージ
├── docs/
│   └── index.html         # GitHub Pages で公開されるHTML（自動生成）
└── .github/
    └── workflows/
        └── daily_news.yml # GitHub Actions スケジュール設定
```

## RSSフィード追加方法

`config.json` の `feeds` 配列に追加します:

```json
{
  "name": "フィード名",
  "url": "https://example.com/feed.xml",
  "category": "AI",
  "lang": "en"
}
```

- `lang`: `"en"`（英語、自動翻訳あり）または `"ja"`（日本語）
- `category`: `"AI"` / `"AI / Tech"` / `"Tech"` / `"日本語"`
