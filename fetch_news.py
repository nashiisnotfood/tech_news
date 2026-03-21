#!/usr/bin/env python3
"""
AI & Tech News Fetcher
RSS フィードから AI・技術ニュースを収集し、HTML レポートを生成します。

使い方:
  python fetch_news.py                        # デフォルト設定で実行
  python fetch_news.py -o output.html         # 出力ファイルを指定
  python fetch_news.py --days 7               # 過去7日分を取得
  python fetch_news.py --ai-summary           # OpenAI で要約（OPENAI_API_KEY 環境変数が必要）
"""

import argparse
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from typing import Optional

import feedparser
import requests
from dateutil import parser as dateutil_parser


# ─────────────────────────────────────────────────────────────────────────────
# ユーティリティ
# ─────────────────────────────────────────────────────────────────────────────

def clean_html(text: str) -> str:
    """HTML タグを除去してプレーンテキストにする。"""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def truncate(text: str, max_len: int) -> str:
    """単語境界でテキストを切り詰める。"""
    if len(text) <= max_len:
        return text
    truncated = text[:max_len].rsplit(" ", 1)[0]
    return truncated + "…"


def parse_date(entry) -> Optional[datetime]:
    """feedparser エントリから datetime を取得する。"""
    for attr in ("published", "updated"):
        raw = getattr(entry, attr, None)
        if raw:
            try:
                return dateutil_parser.parse(raw).astimezone(timezone.utc)
            except Exception:
                pass
    for attr in ("published_parsed", "updated_parsed"):
        tup = getattr(entry, attr, None)
        if tup:
            try:
                return datetime(*tup[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# ニュース取得
# ─────────────────────────────────────────────────────────────────────────────

def fetch_feed(feed_cfg: dict, max_items: int, cutoff: Optional[datetime]) -> list[dict]:
    """RSS フィードを取得して記事リストを返す。"""
    articles = []
    url = feed_cfg["url"]
    name = feed_cfg["name"]
    category = feed_cfg["category"]

    print(f"  取得中: {name} ...", end=" ", flush=True)
    try:
        feed = feedparser.parse(url)
        if feed.bozo and not feed.entries:
            print(f"スキップ (取得エラー)")
            return articles

        count = 0
        for entry in feed.entries:
            if count >= max_items:
                break

            pub_date = parse_date(entry)

            # 日付フィルタ
            if cutoff and pub_date and pub_date < cutoff:
                continue

            title = clean_html(getattr(entry, "title", "（タイトルなし）"))
            link = getattr(entry, "link", "")

            raw_summary = (
                getattr(entry, "summary", "")
                or getattr(entry, "description", "")
            )
            summary = clean_html(raw_summary)

            articles.append(
                {
                    "title": title,
                    "url": link,
                    "summary": summary,
                    "source": name,
                    "category": category,
                    "lang": feed_cfg.get("lang", "en"),
                    "pub_date": pub_date,
                    "pub_date_str": (
                        pub_date.strftime("%Y-%m-%d %H:%M UTC")
                        if pub_date
                        else "日付不明"
                    ),
                    "ai_summary": None,
                    "ja_title": None,
                    "ja_summary": None,
                }
            )
            count += 1

        print(f"{count} 件")
    except Exception as exc:
        print(f"エラー: {exc}")

    return articles


def fetch_all_feeds(feeds: list, max_items: int, days: int) -> list:
    """全フィードからニュースを収集する。"""
    cutoff = None
    if days > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    all_articles = []
    for feed_cfg in feeds:
        articles = fetch_feed(feed_cfg, max_items, cutoff)
        all_articles.extend(articles)
        time.sleep(0.3)  # サーバー負荷軽減

    # 日付降順ソート（日付なしは末尾）
    all_articles.sort(
        key=lambda a: a["pub_date"] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return all_articles


# ─────────────────────────────────────────────────────────────────────────────
# OpenAI 要約（オプション）
# ─────────────────────────────────────────────────────────────────────────────

def summarize_with_openai(articles: list, api_key: str, max_summary_len: int) -> None:
    """OpenAI API を使って記事を日本語要約する（ai_summary フィールドを更新）。"""
    try:
        from openai import OpenAI
    except ImportError:
        print("警告: openai パッケージが見つかりません。`pip install openai` でインストールしてください。")
        return

    client = OpenAI(api_key=api_key)
    total = len(articles)

    for i, article in enumerate(articles, 1):
        source_text = article["summary"] or article["title"]
        if not source_text:
            continue

        print(f"  AI要約 ({i}/{total}): {article['title'][:60]}...")
        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "あなたは IT・AI ニュースの専門家です。"
                            "与えられた記事のタイトルと内容を読み、"
                            "日本語で 2〜3 文の簡潔な要約を作成してください。"
                            "重要なポイントと技術的な意義を含めてください。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"タイトル: {article['title']}\n\n"
                            f"内容: {source_text[:1500]}"
                        ),
                    },
                ],
                max_tokens=300,
                temperature=0.3,
            )
            article["ai_summary"] = response.choices[0].message.content.strip()
        except Exception as exc:
            print(f"    要約エラー: {exc}")
            article["ai_summary"] = None

        time.sleep(0.8)  # レート制限対策


# ─────────────────────────────────────────────────────────────────────────────
# 日本語翻訳
# ─────────────────────────────────────────────────────────────────────────────

def translate_articles(articles: list, max_summary_len: int) -> None:
    """英語記事のタイトルと要約を日本語に機械翻訳する。"""
    try:
        from deep_translator import GoogleTranslator
    except ImportError:
        print("警告: deep-translator が見つかりません。`pip3 install deep-translator` でインストールしてください。")
        return

    translator = GoogleTranslator(source="en", target="ja")
    targets = [a for a in articles if a.get("lang", "en") != "ja"]
    total = len(targets)

    for i, article in enumerate(targets, 1):
        print(f"  翻訳 ({i}/{total}): {article['title'][:55]}...", flush=True)
        try:
            title_text = article["title"][:4500]
            article["ja_title"] = translator.translate(title_text) if title_text else None

            summary_text = article["summary"][:max_summary_len] if article["summary"] else None
            article["ja_summary"] = translator.translate(summary_text) if summary_text else None
        except Exception as exc:
            print(f"    翻訳エラー: {exc}")
            article["ja_title"] = None
            article["ja_summary"] = None
        time.sleep(0.15)  # 過度なリクエスト防止


# ─────────────────────────────────────────────────────────────────────────────
# HTML 生成
# ─────────────────────────────────────────────────────────────────────────────

CATEGORY_COLORS = {
    "AI": "#6366f1",
    "AI / Tech": "#8b5cf6",
    "Tech": "#0ea5e9",
    "日本語": "#10b981",
}

HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>AI・テクノロジーニュース — {generated_at}</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

    :root {{
      --bg:        #0f172a;
      --surface:   #1e293b;
      --surface2:  #273449;
      --border:    #334155;
      --text:      #e2e8f0;
      --muted:     #94a3b8;
      --accent:    #38bdf8;
      --ai-color:  #818cf8;
      --tech-color:#38bdf8;
    }}

    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.6;
      padding: 0 1rem 4rem;
    }}

    /* ── Header ── */
    header {{
      max-width: 1100px;
      margin: 0 auto;
      padding: 2.5rem 0 1.5rem;
      border-bottom: 1px solid var(--border);
    }}
    header h1 {{
      font-size: 2rem;
      font-weight: 700;
      background: linear-gradient(135deg, var(--ai-color), var(--accent));
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
    }}
    header p.meta {{
      color: var(--muted);
      font-size: 0.85rem;
      margin-top: 0.4rem;
    }}

    /* ── Stats bar ── */
    .stats-bar {{
      max-width: 1100px;
      margin: 1.5rem auto;
      display: flex;
      gap: 1rem;
      flex-wrap: wrap;
    }}
    .stat-pill {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 0.3rem 1rem;
      font-size: 0.8rem;
      color: var(--muted);
    }}
    .stat-pill strong {{ color: var(--text); }}

    /* ── Filter tabs ── */
    .filter-tabs {{
      max-width: 1100px;
      margin: 0 auto 1.5rem;
      display: flex;
      gap: 0.5rem;
      flex-wrap: wrap;
    }}
    .filter-tab {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 0.35rem 0.9rem;
      font-size: 0.8rem;
      cursor: pointer;
      color: var(--muted);
      transition: all 0.15s;
      user-select: none;
    }}
    .filter-tab:hover, .filter-tab.active {{
      background: var(--accent);
      border-color: var(--accent);
      color: #0f172a;
      font-weight: 600;
    }}

    /* ── Sections ── */
    .news-section {{
      max-width: 1100px;
      margin: 0 auto 2.5rem;
    }}
    .section-header {{
      font-size: 1.1rem;
      font-weight: 700;
      color: var(--text);
      margin-bottom: 1rem;
      padding-bottom: 0.5rem;
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }}
    .section-count {{
      font-size: 0.75rem;
      color: var(--muted);
      font-weight: 400;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 0.1rem 0.6rem;
    }}

    /* ── Grid ── */
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
      gap: 1.25rem;
    }}

    /* ── Card ── */
    .card {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 1.25rem 1.4rem;
      display: flex;
      flex-direction: column;
      gap: 0.6rem;
      transition: transform 0.15s, box-shadow 0.15s;
    }}
    .card:hover {{
      transform: translateY(-3px);
      box-shadow: 0 8px 30px rgba(0,0,0,0.4);
    }}
    .card-header {{
      display: flex;
      align-items: center;
      gap: 0.5rem;
      flex-wrap: wrap;
    }}
    .badge {{
      display: inline-block;
      padding: 0.15rem 0.55rem;
      border-radius: 999px;
      font-size: 0.7rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}
    .badge-source {{
      background: var(--surface2);
      color: var(--muted);
      border: 1px solid var(--border);
    }}
    .badge-category {{
      color: #fff;
    }}
    .card-date {{
      font-size: 0.72rem;
      color: var(--muted);
      margin-left: auto;
    }}
    .card-title {{
      font-size: 0.98rem;
      font-weight: 600;
      color: var(--text);
      line-height: 1.4;
    }}
    .card-title a {{
      color: inherit;
      text-decoration: none;
    }}
    .card-title a:hover {{
      color: var(--accent);
      text-decoration: underline;
    }}
    .card-summary {{
      font-size: 0.84rem;
      color: var(--muted);
      line-height: 1.55;
      flex: 1;
    }}
    .ai-summary-box {{
      background: var(--surface2);
      border-left: 3px solid var(--ai-color);
      border-radius: 0 8px 8px 0;
      padding: 0.6rem 0.9rem;
      font-size: 0.82rem;
      color: #c7d2fe;
      line-height: 1.55;
    }}
    .ai-summary-label {{
      font-size: 0.68rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--ai-color);
      margin-bottom: 0.3rem;
    }}
    .card-footer {{
      display: flex;
      justify-content: flex-end;
    }}
    .read-more {{
      font-size: 0.78rem;
      color: var(--accent);
      text-decoration: none;
    }}
    .read-more:hover {{ text-decoration: underline; }}

    /* ── Japanese translation block ── */
    .ja-block {{
      background: rgba(52, 211, 153, 0.07);
      border-left: 3px solid #34d399;
      border-radius: 0 8px 8px 0;
      padding: 0.6rem 0.9rem;
      line-height: 1.55;
    }}
    .ja-block-label {{
      font-size: 0.68rem;
      font-weight: 700;
      letter-spacing: 0.06em;
      color: #34d399;
      margin-bottom: 0.3rem;
    }}
    .ja-title {{
      font-size: 0.9rem;
      font-weight: 600;
      color: #d1fae5;
      margin-bottom: 0.25rem;
    }}
    .ja-summary {{
      font-size: 0.82rem;
      color: #a7f3d0;
    }}

    /* ── No results ── */
    .no-results {{
      max-width: 1100px;
      margin: 3rem auto;
      text-align: center;
      color: var(--muted);
    }}

    /* ── Footer ── */
    footer {{
      max-width: 1100px;
      margin: 3rem auto 0;
      padding-top: 1.5rem;
      border-top: 1px solid var(--border);
      font-size: 0.78rem;
      color: var(--muted);
      text-align: center;
    }}

    /* ── Responsive ── */
    @media (max-width: 600px) {{
      .grid {{ grid-template-columns: 1fr; }}
      header h1 {{ font-size: 1.5rem; }}
    }}
  </style>
</head>
<body>

<header>
  <h1>⚡ AI・テクノロジーニュース</h1>
  <p class="meta">生成日時: {generated_at} &nbsp;|&nbsp; 取得期間: 過去 {days} 日間</p>
</header>

<div class="stats-bar">
  <div class="stat-pill">合計 <strong>{total}</strong> 件</div>
  {source_stats}
</div>

<div class="filter-tabs" id="filterTabs">
  <span class="filter-tab active" onclick="filterCards('all', this)">すべて</span>
  {category_tabs}
  {source_tabs}
</div>

{sections}

<footer>
  このレポートは RSS フィードから自動生成されました。{ai_note}
</footer>

<script>
  function filterCards(value, el) {{
    document.querySelectorAll('.filter-tab').forEach(t => t.classList.remove('active'));
    el.classList.add('active');
    document.querySelectorAll('.card').forEach(card => {{
      if (value === 'all') {{
        card.style.display = '';
      }} else {{
        const match = card.dataset.category === value || card.dataset.source === value;
        card.style.display = match ? '' : 'none';
      }}
    }});
    document.querySelectorAll('.news-section').forEach(section => {{
      const hasVisible = [...section.querySelectorAll('.card')].some(c => c.style.display !== 'none');
      section.style.display = hasVisible ? '' : 'none';
    }});
  }}
</script>
</body>
</html>
"""

CARD_TEMPLATE = """\
  <article class="card" data-category="{category_key}" data-source="{source_key}">
    <div class="card-header">
      <span class="badge badge-source">{source}</span>
      <span class="badge badge-category" style="background:{cat_color}">{category}</span>
      <span class="card-date">{pub_date}</span>
    </div>
    <div class="card-title"><a href="{url}" target="_blank" rel="noopener">{title}</a></div>
{summary_block}
{ja_block}    <div class="card-footer">
      <a class="read-more" href="{url}" target="_blank" rel="noopener">元記事を読む →</a>
    </div>
  </article>"""

AI_SUMMARY_BLOCK = """\
    <div class="ai-summary-box">
      <div class="ai-summary-label">✦ AI 要約</div>
      {ai_summary}
    </div>"""

RAW_SUMMARY_BLOCK = """\
    <div class="card-summary">{summary}</div>"""

JA_BLOCK_TEMPLATE = """\
    <div class="ja-block">
      <div class="ja-block-label">🇯🇵 日本語訳</div>
{ja_content}
    </div>
"""


def build_html(articles: list, days: int, max_summary_len: int, has_ai: bool) -> str:
    """記事リストから HTML 文字列を構築する。"""
    generated_at = datetime.now(timezone.utc).strftime("%Y年%m月%d日 %H:%M UTC")

    # カテゴリ / ソースの集計
    categories: dict[str, int] = {}
    sources: dict[str, int] = {}
    for a in articles:
        categories[a["category"]] = categories.get(a["category"], 0) + 1
        sources[a["source"]] = sources.get(a["source"], 0) + 1

    # ソース統計ピル
    source_stats = " ".join(
        f'<div class="stat-pill">{src}: <strong>{cnt}</strong></div>'
        for src, cnt in sorted(sources.items())
    )

    # カテゴリタブ
    category_tabs = " ".join(
        f'<span class="filter-tab" onclick="filterCards(\'{html.escape(cat)}\', this)">'
        f'{html.escape(cat)} ({cnt})</span>'
        for cat, cnt in sorted(categories.items())
    )

    # ソースタブ
    source_tabs = " ".join(
        f'<span class="filter-tab" onclick="filterCards(\'{html.escape(src)}\', this)">'
        f'{html.escape(src)} ({cnt})</span>'
        for src, cnt in sorted(sources.items())
    )

    # セクション分類
    def section_key(a):
        if a.get("lang") == "ja":
            return 0
        if a.get("category") in ("AI", "AI / Tech"):
            return 1
        return 2

    # セクション順 → 同セクション内は日付降順
    sorted_articles = sorted(
        articles,
        key=lambda a: (
            section_key(a),
            -(a["pub_date"].timestamp() if a["pub_date"] else 0)
        )
    )

    SECTION_DEFS = [
        (0, "📰 日本語記事"),
        (1, "🤖 AI・機械学習"),
        (2, "💻 テクノロジー"),
    ]

    sections_html_parts = []
    for sec_id, sec_label in SECTION_DEFS:
        sec_articles = [a for a in sorted_articles if section_key(a) == sec_id]
        if not sec_articles:
            continue

        card_parts = []
        for a in sec_articles:
            cat_color = CATEGORY_COLORS.get(a["category"], "#64748b")
            is_ja = a.get("lang", "en") == "ja"

            # オリジナル要約ブロック
            if is_ja and a.get("ai_summary"):
                summary_block = AI_SUMMARY_BLOCK.format(
                    ai_summary=html.escape(a["ai_summary"])
                )
            elif a["summary"]:
                truncated = truncate(a["summary"], max_summary_len)
                summary_block = RAW_SUMMARY_BLOCK.format(summary=html.escape(truncated))
            else:
                summary_block = ""

            # 日本語訳ブロック（英語記事のみ）
            ja_block = ""
            if not is_ja:
                ja_content_parts = []
                if a.get("ja_title"):
                    ja_content_parts.append(
                        f'      <div class="ja-title">{html.escape(a["ja_title"])}</div>'
                    )
                ja_text = a.get("ai_summary") or a.get("ja_summary")
                if ja_text:
                    ja_content_parts.append(
                        f'      <div class="ja-summary">{html.escape(truncate(ja_text, max_summary_len))}</div>'
                    )
                if ja_content_parts:
                    ja_block = JA_BLOCK_TEMPLATE.format(
                        ja_content="\n".join(ja_content_parts)
                    )

            card = CARD_TEMPLATE.format(
                category_key=html.escape(a["category"]),
                source_key=html.escape(a["source"]),
                source=html.escape(a["source"]),
                category=html.escape(a["category"]),
                cat_color=cat_color,
                pub_date=html.escape(a["pub_date_str"]),
                url=html.escape(a["url"]),
                title=html.escape(a["title"]),
                summary_block=summary_block,
                ja_block=ja_block,
            )
            card_parts.append(card)

        grid_html = "\n".join(card_parts)
        sec_html = (
            f'<section class="news-section">\n'
            f'  <h2 class="section-header">{sec_label}'
            f' <span class="section-count">{len(sec_articles)}</span></h2>\n'
            f'  <div class="grid">\n{grid_html}\n  </div>\n'
            f'</section>'
        )
        sections_html_parts.append(sec_html)

    sections_html = "\n\n".join(sections_html_parts) if sections_html_parts else (
        '<p class="no-results">記事が見つかりませんでした。</p>'
    )

    ai_note = (
        "AI 要約は OpenAI GPT-4o-mini によって生成されました。"
        if has_ai
        else "AI 要約なし（--ai-summary フラグと OPENAI_API_KEY で有効化できます）。"
    )

    return HTML_TEMPLATE.format(
        generated_at=generated_at,
        days=days,
        total=len(articles),
        source_stats=source_stats,
        category_tabs=category_tabs,
        source_tabs=source_tabs,
        sections=sections_html,
        ai_note=ai_note,
    )


# ─────────────────────────────────────────────────────────────────────────────
# エントリポイント
# ─────────────────────────────────────────────────────────────────────────────

def load_config(config_path: Path) -> dict:
    with open(config_path, encoding="utf-8") as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(
        description="AI・技術ニュースを RSS から収集して HTML レポートを生成するツール"
    )
    parser.add_argument(
        "-o", "--output",
        default=None,
        help="出力 HTML ファイルパス（デフォルト: config.json の settings.output_file）",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="取得する過去の日数（0 = 制限なし、デフォルト: config.json の settings.days_filter）",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=None,
        help="フィードあたりの最大記事数（デフォルト: config.json の settings.max_items_per_feed）",
    )
    parser.add_argument(
        "--ai-summary",
        action="store_true",
        help="OpenAI で日本語要約を生成する（環境変数 OPENAI_API_KEY が必要）",
    )
    parser.add_argument(
        "--config",
        default=Path(__file__).parent / "config.json",
        type=Path,
        help="設定ファイルのパス（デフォルト: config.json）",
    )
    parser.add_argument(
        "--no-translate",
        action="store_true",
        dest="no_translate",
        help="英語記事の日本語翻訳を無効化する",
    )
    args = parser.parse_args()

    # 設定読み込み
    if not args.config.exists():
        print(f"エラー: 設定ファイルが見つかりません: {args.config}")
        sys.exit(1)

    cfg = load_config(args.config)
    settings = cfg.get("settings", {})
    feeds = cfg.get("feeds", [])

    max_items = args.max_items or settings.get("max_items_per_feed", 5)
    days = args.days if args.days is not None else settings.get("days_filter", 3)
    max_summary_len = settings.get("max_summary_length", 400)

    # 出力ファイル名（-o 未指定時は日付を自動付与）
    if args.output:
        output_file = Path(args.output)
    else:
        base = Path(settings.get("output_file", "news_report.html"))
        date_str = datetime.now().strftime("%Y%m%d")
        output_file = base.with_name(f"{base.stem}_{date_str}{base.suffix}")

    if not output_file.is_absolute():
        output_file = Path(__file__).parent / output_file

    print("=" * 55)
    print("  AI・テクノロジーニュース フェッチャー")
    print("=" * 55)
    print(f"  フィード数   : {len(feeds)}")
    print(f"  フィードあたり: 最大 {max_items} 件")
    print(f"  取得期間     : 過去 {days} 日間" if days > 0 else "  取得期間     : 制限なし")
    print(f"  AI 要約      : {'有効' if args.ai_summary else '無効'}")
    print(f"  出力先       : {output_file}")
    print("-" * 55)

    # ニュース取得
    print("\n[1/4] RSS フィードからニュースを取得中...")
    articles = fetch_all_feeds(feeds, max_items, days)
    print(f"\n  合計 {len(articles)} 件の記事を取得しました。")

    # AI 要約（オプション）
    has_ai = False
    if args.ai_summary:
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            print("\n警告: OPENAI_API_KEY 環境変数が設定されていません。AI 要約をスキップします。")
        else:
            print(f"\n[2/4] OpenAI で {len(articles)} 件の記事を要約中...")
            summarize_with_openai(articles, api_key, max_summary_len)
            has_ai = True
    else:
        print("\n[2/4] AI 要約: スキップ（--ai-summary フラグで有効化）")

    # 日本語翻訳
    if not args.no_translate:
        en_count = sum(1 for a in articles if a.get("lang", "en") != "ja")
        if en_count > 0:
            print(f"\n[3/4] 英語記事 {en_count} 件を日本語に翻訳中...")
            translate_articles(articles, max_summary_len)
        else:
            print("\n[3/4] 翻訳: 全記事が日本語のためスキップ")
    else:
        print("\n[3/4] 翻訳: スキップ（--no-translate フラグ）")

    # HTML 生成
    print("\n[4/4] HTML レポートを生成中...")
    html_content = build_html(articles, days, max_summary_len, has_ai)
    output_file.write_text(html_content, encoding="utf-8")

    print(f"\n✅ 完了! レポートを保存しました: {output_file}")
    print(f"   記事数: {len(articles)} 件")


if __name__ == "__main__":
    main()
