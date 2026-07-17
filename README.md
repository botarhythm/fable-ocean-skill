# Fable Ocean — 開発手法の型（`/ocean` skill）

Fable（設計・レビュー）と Opus / Sonnet（実装）を**文書ハンドオフ**で連携させ、
高い最上位モデルのトークンを「判断が成果物になる仕事」だけに使うための、プロジェクト非依存の型です。
Claude Code の `/ocean` スキルとして、どのプロジェクトからでも同じ手順で起動できます。

## 名前の由来

> **すべてを受け止め、育む海のような Fable。そこから生まれる、永劫的で有機的な営み。**

Fable は、雑多な文脈も未整理の断片もいったんすべて受け止め、そこから設計・判断という秩序を生む「海」。
その海から生まれる営みは一度きりで終わらない——現場でつまずいた事実が型に還り、版を重ね、次のセッションへ受け継がれていく。
この**有機的に育ち続ける循環**（後述の「学びのループ」）こそ、この型の背骨です。

## 哲学（3つの柱）

1. **最上位モデルは"3点"だけ** — AC（受け入れ条件）の設計 / 差し戻しの診断 / 抜き取り監査。判断が成果物になる短い仕事にだけ使う。
2. **量のある仕事は割安モデルへ** — 実装・調査・定型作業は、受け入れ条件つきの1ファイル（ハンドオフ文書）にして下位モデルへ渡す。別セッション・別AIにも手渡せる。
3. **状態はファイルに外部化** — `CLAUDE.md` / `KNOWLEDGE.md` / `docs/adr/` / `tasks/` に。誰も常駐しなくても新規セッションが動き出せる。

### 学びのループ（この型の核）

「ここはこう直して」という一言を、その場の修正で終わらせない。**困った事実として受け止め、まず型・ACそのものに反映し、改訂履歴に理由ごと刻む。** 次からは誰が回しても最初からそのルールが効く。理屈だけの改訂はしない——根拠は必ず「現場で困った事実」。修正が消えずに資産として積み上がる。

```
現場で困る / あなたが直す  →  型・ACに反映  →  改訂履歴に理由を刻む  →  全セッションに恒久適用
```

## 構成

| パス | 内容 |
|---|---|
| `skills/ocean/SKILL.md` | 単一エントリ（`/ocean`）。発注→3軸選択→承認ゲート→ハンドオフ→検証→記録 |
| `skills/ocean/scripts/` | トークン実測の計器（`scope_size.py` / `agent_usage.py`） |
| `skills/ocean-review/SKILL.md` | 実装前レビューの型（`/ocean-review`）。異種AI（Codex CLI / GPT-5.6系）に2巡の敵対的レビューをさせ、採択を設計へ反映して `docs/reviews/` に記録 |
| `templates/01_model-selection.md` | モデル選択の3軸（曖昧さ×検出コスト×影響半径） |
| `templates/02_handoff-task.md` | ハンドオフ文書（1ファイルで往復が完結） |
| `templates/03_verification-loop.md` | 検証ループ（2敗差し戻し・抜き取り監査） |
| `templates/04_state-persistence.md` | 状態永続化（KNOWLEDGE / ADR / tasks） |
| `templates/05_order-review.md` | 既存プロジェクトのレビュー発注 |
| `templates/06_order-fullstack.md` | 新規開発の発注（確認ゲートは2回だけ） |

## 導入

```bash
# 1. clone（テンプレの正本・読み取り専用）
git clone https://github.com/botarhythm/fable-ocean-skill ~/fable-ocean-skill

# 2. スキルを共通置き場へコピー（全プロジェクトで /ocean・/ocean-review 有効）
mkdir -p ~/.claude/skills
cp -r ~/fable-ocean-skill/skills/ocean ~/.claude/skills/
cp -r ~/fable-ocean-skill/skills/ocean-review ~/.claude/skills/   # 実装前レビュー（Codex 2巡）。要 Codex CLI

# 3. SKILL.md 冒頭の「ハブのパス」を clone 先に合わせる（既定と違う場合）
```

各プロジェクトで `/ocean` を起動すると、そのプロジェクトに `KNOWLEDGE.md` / `tasks/` が用意され、型どおりに回り始めます。プロジェクト固有の前提は、そのプロジェクトの `CLAUDE.md` に数行置けば型より優先されます。

## 姉妹プロジェクトと謝辞

同じ問い——「高い最上位モデルのトークンを、何に使うべきか」——から独立に生まれた盟友の型
[**fable_orchestra**](https://github.com/akiratsukakoshi/fable_orchestra)（akiratsukakoshi）に敬意を。
両者は最上位モデルを"3点"に絞る配置へ独立に収束しており、これはこの哲学の正しさの独立検証です。
本リポジトリのトークン実測スクリプトとフェーズ規律の発想は fable_orchestra 由来。それぞれの独自性を、優劣ではなく個性として大切にしています。

2026年7月、両者の輸入は双方向に一巡しました——Ocean の規格7点が Orchestra 型 v0.4.5 に、Orchestra の計器と運転規則（`agent_usage.py` v0.4.2・壁打ちステージ・セッション境界の経済学）が本リポジトリの SKILL §8/§9 に。
往復書簡: upstream の [letter-to-ocean.md](https://github.com/akiratsukakoshi/fable_orchestra/blob/main/docs/letter-to-ocean.md) と、当方の [letter-to-orchestra.md](docs/letter-to-orchestra.md)（輸入の決定記録は [adr/0005](docs/adr/0005-import-orchestra-return-pack.md)）。

## もっと知る（図解）

非エンジニア向けの図解と技術解説はこちら:
**https://botarhythm.github.io/ai-workshop-charter/**

## ライセンス

MIT（`LICENSE` 参照）。
