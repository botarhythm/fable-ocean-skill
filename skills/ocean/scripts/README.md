# ocean skill scripts

fable_orchestra (akiratsukakoshi/fable_orchestra) 由来の計器を移植。

- `scope_size.py` — 対象スコープの推定トークンと帯(直読圏/ハイブリッド/蒸留必須)を実測
- `agent_usage.py` — トークン実測+金額換算(orch型 v0.4.2)。3モード:
  - 引数にJSONL: 委譲先サブエージェントの入力(非キャッシュ/書込/読取)と出力を分離集計
  - `--main <session.jsonl>`: 指令塔セッション自体をモデル別に集計しUSD換算(従量=Fable分を明示)
  - `--estimate <req数> [--ctx --cold --out]`: 着手前のFableコストUSDレンジ見積り(承認ゲート提示用)

更新時は upstream の同名スクリプトと突合すること。
(v0.4.2 取込: 2026-07-08、upstream docs/letter-to-ocean.md の「お返し」による。message.id単位でmaxを取る集計修正を含む — 旧版の「最後のusage採用」は出力トークンを大幅過小計上していた)
(Ocean側パッチ: agent_usage.py 冒頭の stdout UTF-8 shim のみ — Windows cp932 コンソール対策。突合時はこのブロックを除いて比較)
