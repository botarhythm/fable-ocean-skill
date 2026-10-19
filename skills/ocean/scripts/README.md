# ocean skill scripts

fable_orchestra (akiratsukakoshi/fable_orchestra) 由来の計器を移植。

- `scope_size.py` — 対象スコープの推定トークンと帯(直読圏/ハイブリッド/蒸留必須)を実測
- `agent_usage.py` — トークン実測+金額換算(orch型 v0.4.2 + Ocean側パッチ)。3モード:
  - 引数にJSONL: 委譲先サブエージェントの入力(非キャッシュ/書込/読取)と出力を分離集計し、API単価換算のUSDも出す
  - `--main <session.jsonl>`: 指令塔セッション自体をモデル別に集計しUSD換算(`--metered` で従量と仮定するモデルを指定。既定は Fable 系と DeepSeek、`all` で全モデル)
  - `--estimate <req数> [--model --ctx --cold --out]`: 着手前の指令塔コストUSDレンジ見積り(承認ゲート提示用。既定モデルは claude-opus-5-5)
  - `--strict`: 集計が不完全(未算定のモデル・破損行・構造不正・ID欠損・TTL内訳の不一致・集計対象なし)なら終了コード2
- `test_agent_usage.py` — 合成JSONLと手計算の期待値による `agent_usage.py` のテスト。`python skills/ocean/scripts/test_agent_usage.py` で実行する。単価表を変えたらここも直す

更新時は upstream の同名スクリプトと突合すること。
(v0.4.2 取込: 2026-07-08、upstream docs/letter-to-ocean.md の「お返し」による。message.id単位でmaxを取る集計修正を含む — 旧版の「最後のusage採用」は出力トークンを大幅過小計上していた)
(Ocean側パッチ: agent_usage.py 冒頭の stdout UTF-8 shim のみ — Windows cp932 コンソール対策。突合時はこのブロックを除いて比較)
(Ocean側パッチ2 2026-09-18: `--ttl 5m|1h` を追加。キャッシュ書込倍率を 5m=×1.25 / 1h=×2 で切替。upstream v0.4.2 には無い引数 — 突合時は CACHE_WRITE_MULT/TTL 周辺を除いて比較)
(Ocean側パッチ3 2026-10-19: 単価表をモデルID単位(入力・出力・キャッシュ読取)にし、リクエスト単位で金額を計算。正規IDの完全一致で照合し、未登録モデルは未算定。Haiku 5.5 の10万トークン超の料金帯、TTL内訳(`cache_creation.ephemeral_5m/1h_input_tokens`)の優先使用、DeepSeek の書込倍率なし、見積り式の二重計上の修正、`--model`・`--metered`・`--strict`、破損・構造不正・ID欠損の検出を追加。upstream との差分が大きくなったため、突合は「集計(message.id 単位の max)」の部分に限って行う)
(単価表は公式の公表単価だけを載せる。改定されたら `templates/01_model-selection.md` §2 とテストの期待値もあわせて直す)

## TTL前提の確認

既定は保守的に 5m。セッション JSONL の usage に TTL 別の内訳 `cache_creation.ephemeral_1h_input_tokens` / `ephemeral_5m_input_tokens` が記録されていれば（2026-10-19 確認）、`agent_usage.py` はその内訳を優先して使い、`--ttl` は内訳の無い行にだけ効く。内訳の無い古いログを Claude Code の指令塔セッションとして集計・見積りするときは `--ttl 1h` を付ける（Claude Code のセッションは1時間TTLで動いている旨が環境情報に表示される。2026-09-18 確認、usage overage 時は5分TTLへ落ちる）。自分の請求ダッシュボードで書込単価を照合して既定を決めること。
