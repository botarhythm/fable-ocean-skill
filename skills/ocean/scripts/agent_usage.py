#!/usr/bin/env python3
"""トークン実測+金額換算ツール(orch型 v0.4.2 + Ocean側パッチ)。

3モード:
  (1) 委譲先実測:   agent_usage.py <transcript.jsonl> [<transcript2.jsonl> ...]
      AgentツールのJSONLトランスクリプトからusageを集計し、API単価換算のUSDも出す。
  (2) 指令塔実測:   agent_usage.py --main <session.jsonl>
      メインセッションのJSONLをモデル別に集計し、USD金額も出す。
      セッションJSONLの場所: ~/.claude/projects/<cwdの/を-に置換>/ の最新 *.jsonl
      (例: ls -t ~/.claude/projects/<cwdの/を-に置換>/*.jsonl | head -1)
      集計範囲は指定したファイルだけ。サブエージェント・別セッション・外部の器の利用台帳は含まない。
  (3) 事前見積り:   agent_usage.py --estimate <リクエスト数> [--model claude-opus-5-5] [--ctx 145] [--cold 1] [--out 1.4]
      着手前に指令塔コストのUSDレンジを出す(GO/NO-GO提示用)。--ctx/--outはk tok単位。

集計の注意(2026-07-05 実測で確定):
- 同一message.idの行が複数ある(ストリーミング分割)ため、id単位で各usageフィールドのmaxを取る。
  出力トークンは累積更新されるので、初出値や単純合算では大幅に狂う(実測: 87.6k→7.8kに過小)。
- 単価はモデルID単位(PRICES)。照合は正規IDとの完全一致で、末尾の "[1m]" 等の注記と日付サフィックスだけ取り除く。
  単価表にないモデルはトークンだけ集計し、金額は「未算定」(合計は不完全)と表示する。
- キャッシュ読取はモデルごとの公表値。公表値がないモデルは入力×0.1 で推定し「読取推定」と表示する。
- キャッシュ書込は入力単価×1.25(5分TTL)、×2(1時間TTL)。usage.cache_creation に TTL 別の内訳があればそれを使い、
  無い行だけ --ttl(既定5m)を仮定する。仮定した行数を表示する。
- Haiku 5.5 は1リクエストの入力計(非キャッシュ+書込+読取)が10万トークンを超えると高い単価になる。リクエスト単位で判定する。
- 金額はすべて API 単価での換算。「従量」は --metered で指定したモデルを従量課金と仮定した換算額で、請求実額ではない。
- 破損行・構造不正行・ID欠損行・TTL内訳の不一致の件数を表示する。集計対象が0件なら「集計対象なし」と表示する。
  --strict を付けると、これらや未算定があれば終了コード2で終わる。
- DeepSeek はキャッシュミス入力を入力単価そのままで計算する(Claude の書込倍率は掛けない)。--estimate は対応しない(実費は DeepSeek のダッシュボードで見る)。
"""
import argparse
import json
import math
import re
import sys

# Ocean側パッチ: Windowsコンソール(cp932)は ≈ 等を出力できず落ちるため stdout をUTF-8化。
# upstream(Linux前提)との差分はこのブロックのみ — 突合時は本ブロックを除いて比較する。
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Ocean側パッチ3(2026-10-19): 単価表をモデルID単位にし、リクエスト単位で金額を計算する。
# USD per MTok。read が None のモデルは入力×0.1 で推定する。
# long = (入力計のしきい値, 超過時の入力単価, 超過時の出力単価)。
# 出典: Anthropic 公式のモデル表(2026-10-06時点)、DeepSeek公式 pricing(2026-10-19確認)。
# deepseek-flash はオフピーク単価(平日 JST 10-13・15-19時は2倍)。DeepSeek の実費の正は DeepSeek のダッシュボード。
PRICES = {
    "claude-fable-5-1": {"in": 10.0, "out": 50.0, "read": 0.25},
    "claude-fable-5": {"in": 10.0, "out": 50.0, "read": None},
    "claude-opus-5-5": {"in": 4.0, "out": 20.0, "read": 0.20},
    "claude-opus-5": {"in": 5.0, "out": 25.0, "read": None},
    "claude-opus-4-8": {"in": 5.0, "out": 25.0, "read": None},
    "claude-opus-4-7": {"in": 5.0, "out": 25.0, "read": None},
    "claude-opus-4-6": {"in": 5.0, "out": 25.0, "read": None},
    "claude-sonnet-5-5": {"in": 2.0, "out": 10.0, "read": 0.20},
    "claude-sonnet-5": {"in": 2.0, "out": 10.0, "read": None},
    "claude-sonnet-4-6": {"in": 3.0, "out": 15.0, "read": None},
    "claude-haiku-5-5": {"in": 0.10, "out": 0.50, "read": None, "long": (100_000, 0.50, 2.50)},
    "claude-haiku-4-5": {"in": 1.0, "out": 5.0, "read": None},
    "deepseek-flash": {"in": 0.15, "out": 0.60, "read": 0.003, "write": "miss"},  # 書込倍率なし
}
METERED_PREFIXES = ["claude-fable", "deepseek"]  # 従量と仮定するモデル。main() で --metered から上書き
CACHE_WRITE_MULT = {"5m": 1.25, "1h": 2.0}
TTL = "5m"  # cache_creation の内訳が無い行に仮定する TTL。main() で --ttl から上書き
FIELDS = ("input", "cache_write", "cache_read", "output", "write_5m", "write_1h")
_SUFFIX = re.compile(r"(\[[^\]]*\])+$|[-@]\d{8}$")


def price_key(model: str):
    """正規IDに一致すればそのキー、しなければ None(未算定)。"""
    m = (model or "").lower().strip()
    while True:
        stripped = _SUFFIX.sub("", m)
        if stripped == m:
            break
        m = stripped
    return m if m in PRICES else None


def is_metered(key) -> bool:
    if key is None:
        return False
    # "fable" や "opus-5-5" のように claude- を省いた指定も受け付ける
    return "all" in METERED_PREFIXES or any(
        key.startswith(p) or key.startswith("claude-" + p) for p in METERED_PREFIXES)


def rates(key: str, prompt_total: int):
    """(入力, 出力, 読取, 読取が推定か) をリクエストの入力計に応じて返す。"""
    p = PRICES[key]
    pin, pout = p["in"], p["out"]
    long = p.get("long")
    is_long = bool(long and prompt_total > long[0])
    if is_long:
        pin, pout = long[1], long[2]
    if p["read"] is None or is_long:
        return pin, pout, pin * 0.10, True
    return pin, pout, p["read"], False


def write_cost(key: str, r: dict, pin: float) -> tuple:
    """書込の USD と、(TTL を仮定したか, 内訳が総量と一致しないか)。"""
    if PRICES[key].get("write") == "miss":
        return r["cache_write"] * pin, False, False
    split = r["write_5m"] + r["write_1h"]
    if not split:
        return r["cache_write"] * pin * CACHE_WRITE_MULT[TTL], bool(r["cache_write"]), False
    cost = r["write_5m"] * pin * CACHE_WRITE_MULT["5m"] + r["write_1h"] * pin * CACHE_WRITE_MULT["1h"]
    rest = r["cache_write"] - split
    if rest > 0:  # 内訳が足りない分は TTL を仮定して計上する(落とさない)
        cost += rest * pin * CACHE_WRITE_MULT[TTL]
    return cost, False, rest != 0


def record_usd(key: str, r: dict) -> tuple:
    """1リクエストの USD と、(読取推定か, TTL を仮定したか, TTL 内訳が不一致か)。"""
    prompt_total = r["input"] + r["cache_write"] + r["cache_read"]
    pin, pout, pread, estimated = rates(key, prompt_total)
    write, assumed, mismatch = write_cost(key, r, pin)
    cost = (r["input"] * pin + write + r["cache_read"] * pread + r["output"] * pout) / 1_000_000
    return cost, estimated, assumed, mismatch


def _tokens(value):
    """トークン値を検証する。欠損は0、不正(負数・非整数)は None。"""
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def collect(path: str) -> dict:
    """message.id単位で各usageフィールドのmaxを取り、モデル別に合算して返す。"""
    per_id: dict = {}
    stats = {"corrupt": 0, "invalid": 0, "noid": 0}
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                stats["corrupt"] += 1
                continue
            if not isinstance(rec, dict):
                stats["invalid"] += 1
                continue
            msg = rec.get("message")
            if not isinstance(msg, dict):
                continue  # usage を持たない種類の行(要約・ツール結果など)
            usage = msg.get("usage")
            if not usage or msg.get("role") != "assistant":
                continue
            split = usage.get("cache_creation") if isinstance(usage, dict) else None
            if not isinstance(usage, dict) or not isinstance(split or {}, dict):
                stats["invalid"] += 1
                continue
            split = split or {}
            vals = {
                "input": _tokens(usage.get("input_tokens")),
                "cache_write": _tokens(usage.get("cache_creation_input_tokens")),
                "cache_read": _tokens(usage.get("cache_read_input_tokens")),
                "output": _tokens(usage.get("output_tokens")),
                "write_5m": _tokens(split.get("ephemeral_5m_input_tokens")),
                "write_1h": _tokens(split.get("ephemeral_1h_input_tokens")),
            }
            if any(v is None for v in vals.values()):
                stats["invalid"] += 1
                continue
            mid = msg.get("id") or rec.get("requestId")
            if not mid:
                stats["noid"] += 1
                mid = f"__noid_{lineno}"  # 重複排除できないので1行1リクエストとして数える
            r = per_id.setdefault(mid, {"model": msg.get("model") or "?", **{k: 0 for k in FIELDS}})
            for k in FIELDS:
                r[k] = max(r[k], vals[k])
    by_model: dict = {}
    for r in per_id.values():
        key = price_key(r["model"])
        t = by_model.setdefault(r["model"], {**{k: 0 for k in FIELDS}, "calls": 0, "usd": 0.0,
                                             "key": key, "read_est": False, "ttl_assumed": 0,
                                             "ttl_mismatch": 0})
        for k in FIELDS:
            t[k] += r[k]
        t["calls"] += 1
        if key is None:
            t["usd"] = None
        else:
            cost, est, assumed, mismatch = record_usd(key, r)
            t["usd"] += cost
            t["read_est"] = t["read_est"] or est
            t["ttl_assumed"] += assumed
            t["ttl_mismatch"] += mismatch
    return {"models": by_model, "stats": stats}


def fmt(n) -> str:
    return f"{n/1000:.1f}k" if n >= 1000 else str(int(n))


def header():
    print(f"{'対象':<30} {'calls':>5} {'入力計':>8} {'非ｷｬｯｼｭ':>7} {'書込':>7} {'読取':>8} {'出力':>7} {'USD':>8}")


def print_row(name, t, tag_billing=True):
    total = t["input"] + t["cache_write"] + t["cache_read"]
    if t["usd"] is None:
        cost = "     未算定"
    else:
        tag = "" if (not tag_billing or is_metered(t["key"])) else "(枠内)"
        if t["read_est"]:
            tag += "(読取推定)"
        cost = f" ${t['usd']:>7.2f}{tag}"
    print(f"{name:<30} {t['calls']:>5} {fmt(total):>9} {fmt(t['input']):>9} "
          f"{fmt(t['cache_write']):>8} {fmt(t['cache_read']):>9} {fmt(t['output']):>8}{cost}")


def report_footer(results) -> bool:
    """注記を出し、不完全なら True を返す。"""
    corrupt = sum(r["stats"]["corrupt"] for r in results)
    invalid = sum(r["stats"]["invalid"] for r in results)
    noid = sum(r["stats"]["noid"] for r in results)
    models = [t for r in results for t in r["models"].values()]
    unpriced = sorted({n for r in results for n, t in r["models"].items() if t["usd"] is None})
    assumed = sum(t.get("ttl_assumed", 0) for t in models)
    mismatch = sum(t.get("ttl_mismatch", 0) for t in models)
    empty = not models
    if empty:
        print("集計対象なし(assistant の usage を持つ行が0件)。ファイルの種類を確認する → 不完全")
    if unpriced:
        print(f"未算定のモデル(単価表に無い): {', '.join(unpriced)} → 金額の合計は不完全")
    if corrupt or invalid or noid:
        print(f"破損行 {corrupt} / 構造不正行 {invalid} / ID欠損行 {noid}"
              f"(ID欠損は1行1リクエストとして計上) → 集計は不完全の可能性")
    if mismatch:
        print(f"TTL内訳が書込の総量と一致しないリクエスト {mismatch} 件(不足分は TTL {TTL} を仮定) → 不完全")
    if assumed:
        print(f"TTL内訳の無い書込 {assumed} リクエストは TTL {TTL}(×{CACHE_WRITE_MULT[TTL]})を仮定(--ttl で切替)")
    metered_hits = [t for t in models if is_metered(t["key"])]
    if "all" not in METERED_PREFIXES and not metered_hits and models:
        print(f"注: --metered({','.join(METERED_PREFIXES)})に一致するモデルは0件")
    print("※金額は API 単価での換算。請求実額はダッシュボードで照合する。")
    return bool(empty or unpriced or corrupt or invalid or noid or mismatch)


def mode_subagents(paths) -> bool:
    header()
    results = []
    grand = {**{k: 0 for k in FIELDS}, "calls": 0, "usd": 0.0, "key": None, "read_est": False}
    for path in paths:
        res = collect(path)
        results.append(res)
        t = {**{k: 0 for k in FIELDS}, "calls": 0, "usd": 0.0, "key": None, "read_est": False}
        names = sorted(res["models"])
        for m in res["models"].values():
            for k in FIELDS + ("calls",):
                t[k] += m[k]
            t["usd"] = None if (t["usd"] is None or m["usd"] is None) else t["usd"] + m["usd"]
            t["read_est"] = t["read_est"] or m["read_est"]
        label = path.replace("\\", "/").rsplit("/", 1)[-1][:28]
        print_row(label, t, tag_billing=False)  # 合算行は課金区分を付けない(モデル混在のため)
        if len(names) > 1:
            print(f"  内訳モデル: {', '.join(names)}")
        for k in FIELDS + ("calls",):
            grand[k] += t[k]
        grand["usd"] = None if (grand["usd"] is None or t["usd"] is None) else grand["usd"] + t["usd"]
        grand["read_est"] = grand["read_est"] or t["read_est"]
    if len(paths) > 1:
        print_row("== 合算 ==", grand, tag_billing=False)
    return report_footer(results)


def mode_main(path) -> bool:
    res = collect(path)
    header()
    metered_total = 0.0
    for model in sorted(res["models"]):
        t = res["models"][model]
        print_row(model[:28], t)
        if is_metered(t["key"]) and t["usd"] is not None:
            metered_total += t["usd"]
    print(f"\n従量と仮定した換算額({','.join(METERED_PREFIXES)}): ${metered_total:.2f}(請求実額ではない)")
    print(f"集計範囲: {path} のみ")
    return report_footer([res])


def mode_estimate(requests, ctx_k, cold, out_k, model):
    if (requests < 1 or not math.isfinite(ctx_k) or not math.isfinite(out_k)
            or ctx_k < 0 or out_k < 0 or not 0 <= cold <= requests):
        sys.exit("エラー: リクエスト数は1以上、--ctx/--out は0以上、--cold は 0〜リクエスト数 で指定する")
    key = price_key(model)
    if key is None:
        sys.exit(f"エラー: 単価表にないモデル {model!r}。PRICES に追加してから見積もる")
    if PRICES[key].get("write") == "miss":
        sys.exit("エラー: DeepSeek の見積りには対応しない(キャッシュ課金の方式が違う)。実費は DeepSeek のダッシュボードで見る")
    ctx = ctx_k * 1000
    pin, pout, pread, read_est = rates(key, int(ctx))
    read_cost = ctx * pread / 1e6
    cold_cost = ctx * pin * CACHE_WRITE_MULT[TTL] / 1e6
    out_cost = out_k * 1000 * pout / 1e6
    point = (requests - cold) * read_cost + cold * cold_cost + requests * out_cost
    lo, hi = point * 0.7, point * 1.4
    note = "(読取単価は入力×0.1で推定)" if read_est else ""
    print(f"指令塔({key}) 事前見積り(較正前 確度±30〜50%){note}")
    print(f"  前提: リクエスト{requests}回(うちキャッシュ切れ{cold}回)× 文脈{ctx_k:.0f}k + 出力{out_k:.1f}k (TTL {TTL})")
    print(f"  キャッシュが効いた1回 ≈ ${read_cost + out_cost:.3f} / キャッシュ切れ1回 ≈ ${cold_cost + out_cost:.3f}")
    print(f"  → 概算 ${point:.2f}(レンジ ${lo:.2f}〜${hi:.2f})")
    print(f"  リクエスト数の目安: 計画・委譲指示5〜8 + WPあたり検収3〜5 + 差し戻し1回3〜5")


def main():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--main", dest="main_path")
    ap.add_argument("--estimate", type=int)
    ap.add_argument("--ctx", type=float, default=145.0, help="平均文脈 k tok(既定145=実測較正値)")
    ap.add_argument("--cold", type=int, default=1, help="キャッシュ切れ想定回数")
    ap.add_argument("--out", type=float, default=1.4, help="1リクエスト平均出力 k tok")
    ap.add_argument("--ttl", choices=("5m", "1h"), default="5m", help="TTL内訳の無い書込に仮定するTTL")
    ap.add_argument("--model", default="claude-opus-5-5", help="--estimate の指令塔モデルID")
    ap.add_argument("--metered", help="従量と仮定するモデル(モデルIDの前方一致、カンマ区切り。all で全モデル)")
    ap.add_argument("--strict", action="store_true", help="集計が不完全なら終了コード2")
    args = ap.parse_args()
    global TTL, METERED_PREFIXES
    TTL = args.ttl
    if args.metered:
        METERED_PREFIXES = [p.strip().lower() for p in args.metered.split(",") if p.strip()]
    modes = sum(bool(x) for x in (args.estimate is not None, args.main_path, args.paths))
    if modes != 1:
        print(__doc__)
        sys.exit(1)

    incomplete = False
    if args.estimate is not None:
        mode_estimate(args.estimate, args.ctx, args.cold, args.out, args.model)
    elif args.main_path:
        incomplete = mode_main(args.main_path)
    else:
        incomplete = mode_subagents(args.paths)
    if args.strict and incomplete:
        sys.exit(2)


if __name__ == "__main__":
    main()
