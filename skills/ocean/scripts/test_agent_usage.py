"""agent_usage.py の集計と金額換算のテスト(合成JSONLと手計算の期待値)。

実行: python skills/ocean/scripts/test_agent_usage.py
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent_usage as au  # noqa: E402

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_usage.py")
M = 1_000_000
ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}  # 子プロセスの stderr を UTF-8 に揃える(Windows cp932 対策)


def line(mid, model, inp=0, write=0, read=0, out=0, w5=None, w1=None, rid=None):
    usage = {"input_tokens": inp, "cache_creation_input_tokens": write,
             "cache_read_input_tokens": read, "output_tokens": out}
    if w5 is not None or w1 is not None:
        usage["cache_creation"] = {"ephemeral_5m_input_tokens": w5 or 0, "ephemeral_1h_input_tokens": w1 or 0}
    msg = {"role": "assistant", "model": model, "usage": usage}
    if mid:
        msg["id"] = mid
    rec = {"message": msg}
    if rid:
        rec["requestId"] = rid
    return json.dumps(rec)


class AgentUsageTest(unittest.TestCase):
    def setUp(self):
        au.TTL = "5m"
        au.METERED_PREFIXES = ["claude-fable", "deepseek"]
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def jsonl(self, *lines):
        path = os.path.join(self.tmp.name, f"t{len(os.listdir(self.tmp.name))}.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return path

    def model(self, path, name):
        return au.collect(path)["models"][name]

    # 単価と照合
    def test_price_keys(self):
        self.assertEqual(au.price_key("claude-opus-5-5"), "claude-opus-5-5")
        self.assertEqual(au.price_key("claude-opus-5-5[1m]"), "claude-opus-5-5")
        self.assertEqual(au.price_key("claude-opus-5-5-20261001"), "claude-opus-5-5")
        self.assertEqual(au.price_key("claude-opus-5"), "claude-opus-5")
        self.assertEqual(au.price_key("claude-fable-5-1"), "claude-fable-5-1")
        self.assertEqual(au.price_key("deepseek-flash[1m]"), "deepseek-flash")
        self.assertIsNone(au.price_key("claude-opus-6"))  # 未登録の新世代を旧世代で拾わない
        self.assertIsNone(au.price_key("claude-opus-5-55"))

    def test_each_model_price(self):
        # 入力・読取・出力を各100万。書込なし。
        cases = {
            "claude-opus-5-5": 4 + 0.20 + 20,
            "claude-fable-5-1": 10 + 0.25 + 50,
            "claude-opus-5": 5 + 0.50 + 25,      # 読取は公表値なし → 入力×0.1
            "claude-sonnet-5-5": 2 + 0.20 + 10,
            "deepseek-flash": 0.15 + 0.003 + 0.60,
        }
        lines = [line(f"m{i}", m, inp=M, read=M, out=M) for i, m in enumerate(cases)]
        path = self.jsonl(*lines)
        for m, expected in cases.items():
            self.assertAlmostEqual(self.model(path, m)["usd"], expected, places=6, msg=m)

    # キャッシュ書込と TTL
    def test_write_ttl_split_and_fallback(self):
        path = self.jsonl(
            line("a", "claude-opus-5-5", write=M, w5=0, w1=M),   # 内訳あり 1h → 4×2
            line("b", "claude-opus-5-5", write=M, w5=M, w1=0),   # 内訳あり 5m → 4×1.25
            line("c", "claude-opus-5-5", write=M),               # 内訳なし → --ttl 既定 5m
        )
        t = self.model(path, "claude-opus-5-5")
        self.assertAlmostEqual(t["usd"], 8 + 5 + 5, places=6)
        self.assertEqual(t["ttl_assumed"], 1)
        au.TTL = "1h"
        self.assertAlmostEqual(self.model(path, "claude-opus-5-5")["usd"], 8 + 5 + 8, places=6)

    # ストリーミング分割の重複は max を取る
    def test_streaming_duplicates(self):
        path = self.jsonl(
            line("x", "claude-sonnet-5-5", inp=100, out=10),
            line("x", "claude-sonnet-5-5", inp=100, out=900),
        )
        t = self.model(path, "claude-sonnet-5-5")
        self.assertEqual((t["calls"], t["input"], t["output"]), (1, 100, 900))

    # ID 欠損・破損行・未知モデル
    def test_missing_id_corrupt_unknown(self):
        path = self.jsonl(
            line(None, "claude-opus-5-5", out=100),
            line(None, "claude-opus-5-5", out=100),
            "{broken json",
            line("u", "claude-opus-6", inp=M),
        )
        res = au.collect(path)
        self.assertEqual(res["stats"], {"corrupt": 1, "invalid": 0, "noid": 2})
        self.assertEqual(res["models"]["claude-opus-5-5"]["calls"], 2)  # 1行1リクエスト
        self.assertEqual(res["models"]["claude-opus-5-5"]["output"], 200)
        self.assertIsNone(res["models"]["claude-opus-6"]["usd"])
        r = subprocess.run([sys.executable, SCRIPT, "--main", path, "--strict"],
                           capture_output=True, text=True, encoding="utf-8", env=ENV)
        self.assertEqual(r.returncode, 2)
        self.assertIn("未算定", r.stdout)

    def test_request_id_fallback(self):
        path = self.jsonl(line(None, "claude-opus-5-5", out=5, rid="r1"),
                          line(None, "claude-opus-5-5", out=7, rid="r1"))
        res = au.collect(path)
        self.assertEqual(res["stats"]["noid"], 0)
        self.assertEqual(res["models"]["claude-opus-5-5"]["output"], 7)

    # Haiku 5.5 の10万トークン境界はリクエスト単位
    def test_haiku_long_context_boundary(self):
        path = self.jsonl(
            line("s", "claude-haiku-5-5", inp=100_000),               # 境界ちょうど → 通常 0.10
            line("l", "claude-haiku-5-5", inp=50_001, read=50_000),   # 入力計100,001 → 0.50、読取は0.05
        )
        t = self.model(path, "claude-haiku-5-5")
        expected = (100_000 * 0.10 + 50_001 * 0.50 + 50_000 * 0.05) / M
        self.assertAlmostEqual(t["usd"], expected, places=9)

    # 従量の仮定
    def test_metered(self):
        self.assertTrue(au.is_metered("claude-fable-5-1"))
        self.assertFalse(au.is_metered("claude-opus-5-5"))
        au.METERED_PREFIXES = ["all"]
        self.assertTrue(au.is_metered("claude-opus-5-5"))
        self.assertFalse(au.is_metered(None))

    # 事前見積り: (要求−コールド)×読取 + コールド×書込 + 全要求×出力
    def test_estimate_formula(self):
        r = subprocess.run([sys.executable, SCRIPT, "--estimate", "10", "--cold", "2",
                            "--ctx", "100", "--out", "1", "--ttl", "1h"],
                           capture_output=True, text=True, encoding="utf-8", env=ENV)
        self.assertEqual(r.returncode, 0, r.stderr)
        expected = 8 * 100_000 * 0.20 / M + 2 * 100_000 * 4 * 2 / M + 10 * 1000 * 20 / M
        self.assertIn(f"${expected:.2f}", r.stdout)

    # TTL 内訳が総量と一致しない
    def test_ttl_split_mismatch(self):
        path = self.jsonl(line("p", "claude-opus-5-5", write=1000, w5=400, w1=0))  # 不足600は TTL 仮定
        t = self.model(path, "claude-opus-5-5")
        self.assertAlmostEqual(t["usd"], (400 * 4 * 1.25 + 600 * 4 * 1.25) / M, places=12)
        self.assertEqual(t["ttl_mismatch"], 1)
        over = self.jsonl(line("q", "claude-opus-5-5", write=100, w5=0, w1=300))  # 内訳が総量より多い
        self.assertEqual(self.model(over, "claude-opus-5-5")["ttl_mismatch"], 1)
        r = subprocess.run([sys.executable, SCRIPT, "--main", path, "--strict"],
                           capture_output=True, text=True, encoding="utf-8", env=ENV)
        self.assertEqual(r.returncode, 2)

    # 構造不正・集計対象なし
    def test_invalid_structure_and_empty(self):
        path = self.jsonl("[]", json.dumps({"message": {"role": "assistant", "model": "claude-opus-5-5",
                                                         "id": "n", "usage": {"input_tokens": -5}}}))
        res = au.collect(path)
        self.assertEqual(res["stats"]["invalid"], 2)
        self.assertEqual(res["models"], {})
        for p in (path, self.jsonl("{}")):
            r = subprocess.run([sys.executable, SCRIPT, "--main", p, "--strict"],
                               capture_output=True, text=True, encoding="utf-8", env=ENV)
            self.assertEqual(r.returncode, 2, p)
            self.assertIn("集計対象なし", r.stdout)

    # 委譲先の合算行には課金区分を付けない
    def test_subagent_row_has_no_billing_tag(self):
        path = self.jsonl(line("a", "claude-fable-5-1", out=10), line("b", "claude-sonnet-5-5", out=10))
        r = subprocess.run([sys.executable, SCRIPT, path], capture_output=True, text=True,
                           encoding="utf-8", env=ENV)
        row = [ln for ln in r.stdout.splitlines() if ln.startswith(os.path.basename(path))][0]
        self.assertNotIn("枠内", row)
        self.assertIn("内訳モデル", r.stdout)
        # 複数ファイルなら合算行
        other = self.jsonl(line("c", "claude-sonnet-5-5", out=5))
        r = subprocess.run([sys.executable, SCRIPT, path, other], capture_output=True, text=True,
                           encoding="utf-8", env=ENV)
        self.assertIn("== 合算 ==", r.stdout)

    def test_metered_without_claude_prefix(self):
        au.METERED_PREFIXES = ["opus-5-5"]
        self.assertTrue(au.is_metered("claude-opus-5-5"))
        self.assertFalse(au.is_metered("claude-opus-5"))

    def test_estimate_read_estimated_note(self):
        r = subprocess.run([sys.executable, SCRIPT, "--estimate", "5", "--model", "claude-fable-5"],
                           capture_output=True, text=True, encoding="utf-8", env=ENV)
        self.assertIn("推定", r.stdout)

    # DeepSeek は書込倍率を掛けない・見積りは拒否
    def test_deepseek_write_and_estimate(self):
        path = self.jsonl(line("d", "deepseek-flash", write=M))
        self.assertAlmostEqual(self.model(path, "deepseek-flash")["usd"], 0.15, places=9)
        r = subprocess.run([sys.executable, SCRIPT, "--estimate", "5", "--model", "deepseek-flash"],
                           capture_output=True, text=True, encoding="utf-8", env=ENV)
        self.assertNotEqual(r.returncode, 0)

    def test_estimate_validation(self):
        for args in (["--cold", "11"], ["--cold", "-1"], ["--ctx", "-5"], ["--model", "claude-opus-6"],
                     ["--ctx", "nan"], ["--out", "inf"]):
            r = subprocess.run([sys.executable, SCRIPT, "--estimate", "10", *args],
                               capture_output=True, text=True, encoding="utf-8", env=ENV)
            self.assertNotEqual(r.returncode, 0, args)


if __name__ == "__main__":
    unittest.main()
