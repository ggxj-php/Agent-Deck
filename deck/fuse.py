"""本轮工具「熔断」：模型在同一件事上无进展地反复试错时，强制它停下来。

只在一轮提问内生效，跨轮自动重置。触发条件（任一）：
  1) 连续 N 轮都是「write_file → run_code」这种试错组合；
  2) 同一个工具连续 N 次返回几乎一样的结果；
  3) 本轮工具调用总数超过预算。
触发后不再执行工具，只回「熔断」提示，逼模型收尾/向用户说明卡点。
"""
import re

_WS = re.compile(r"\s+")


def _signature(result):
    """结果指纹：去掉空白、取前 400 字（够区分「换了个写法但其实一样」）。"""
    text = _WS.sub(" ", str(result or "")).strip()
    return text[:400]


class TurnFuse:
    def __init__(self, tool_budget=40, trial_rounds=6, same_result_limit=5):
        self.tool_budget = max(1, int(tool_budget or 40))
        self.trial_rounds = max(1, int(trial_rounds or 6))
        self.same_result_limit = max(2, int(same_result_limit or 5))
        self.calls = 0
        self.trial_streak = 0        # 连续「写脚本→跑脚本」轮数
        self.results = {}            # 工具名 -> [(指纹, 次数)]
        self.active = False
        self.reason = ""

    # ---------- 观察 ----------
    def observe_round(self, tool_names):
        """一轮工具调用结束后调用：统计试错组合的连续轮数。"""
        names = {str(n) for n in (tool_names or [])}
        if "write_file" in names and "run_code" in names:
            self.trial_streak += 1
        else:
            self.trial_streak = 0
        if not self.active and self.trial_streak >= self.trial_rounds:
            self.trip(f"连续 {self.trial_streak} 轮都在「写脚本→跑脚本」试错，没有进展")

    def observe_result(self, name, result):
        """记录一次工具结果；重复太多就直接熔断，否则返回一句提醒（或 None）。"""
        if not name:
            return None
        sig = _signature(result)
        bucket = self.results.setdefault(str(name), [])
        for item in bucket:
            if item[0] == sig:
                item[1] += 1
                count = item[1]
                if count >= self.same_result_limit:
                    self.trip(f"{name} 连续 {count} 次返回几乎一样的结果")
                    return None
                if count >= 2:
                    return (f"（提醒：这个工具这是第 {count} 次返回几乎一样的结果，"
                            "说明这个思路没往前走。换思路，或者直接告诉用户卡在哪。）")
                return None
        bucket.append([sig, 1])
        if len(bucket) > 20:
            del bucket[0]
        return None

    def observe_call(self):
        self.calls += 1
        if not self.active and self.calls > self.tool_budget:
            self.trip(f"本轮工具调用已经超过 {self.tool_budget} 次")

    # ---------- 熔断 ----------
    def trip(self, reason):
        if not self.active:
            self.active = True
            self.reason = str(reason or "重复试错没有进展")

    def blocked_text(self):
        return (f"【已熔断】{self.reason}。别再调用工具了：现在立刻停下，"
                "用一段话向用户说明：你卡在哪一步、试过什么、需要用户补充什么"
                "（比如把局部截图放大、换更清晰的原图、或直接告诉答案格式）。")

    def notice(self):
        return f"工具熔断：{self.reason}，已让它停下来汇报卡点"
