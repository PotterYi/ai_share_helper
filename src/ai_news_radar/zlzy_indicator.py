
"""
主力作妖 多指标信号系统
通达信 主力捉妖 公式转 Python 实现
"""
import numpy as np

class ZhuLiZuoYao:
    """主力作妖 多指标信号系统"""

    @staticmethod
    def _ema(arr, n):
        result = np.full_like(arr, np.nan)
        start = np.where(~np.isnan(arr))[0]
        if len(start) == 0 or len(arr) < 1:
            return result
        first = start[0]
        result[first] = arr[first]
        n = max(n, 1)
        k = 2.0 / (n + 1)
        for i in range(first + 1, len(arr)):
            if np.isnan(arr[i]):
                continue
            result[i] = arr[i] * k + result[i - 1] * (1 - k)
        return result

    @staticmethod
    def _ma(arr, n):
        if len(arr) < n:
            return np.full_like(arr, np.nan)
        result = np.full_like(arr, np.nan)
        result[n - 1:] = np.convolve(arr, np.ones(n) / n, mode='valid')
        return result

    @staticmethod
    def _ref(arr, n):
        result = np.full_like(arr, np.nan)
        result[n:] = arr[:-n]
        return result

    @staticmethod
    def _hhv(arr, n):
        if len(arr) < n:
            return np.full_like(arr, np.nan)
        result = np.full_like(arr, np.nan)
        result[n - 1:] = np.array([np.max(arr[i - n + 1:i + 1]) for i in range(n - 1, len(arr))])
        return result

    @staticmethod
    def _llv(arr, n):
        if len(arr) < n:
            return np.full_like(arr, np.nan)
        result = np.full_like(arr, np.nan)
        result[n - 1:] = np.array([np.min(arr[i - n + 1:i + 1]) for i in range(n - 1, len(arr))])
        return result

    @staticmethod
    def _count(cond_arr, n):
        result = np.zeros(len(cond_arr), dtype=float)
        for i in range(n - 1, len(cond_arr)):
            result[i] = np.sum(cond_arr[i - n + 1:i + 1])
        return result

    @staticmethod
    def _every(cond_arr, n):
        result = np.zeros(len(cond_arr), dtype=bool)
        for i in range(n - 1, len(cond_arr)):
            result[i] = np.all(cond_arr[i - n + 1:i + 1])
        return result

    @staticmethod
    def _sum(arr, n):
        result = np.full(len(arr), np.nan)
        for i in range(n - 1, len(arr)):
            result[i] = np.nansum(arr[i - n + 1:i + 1])
        return result

    @staticmethod
    def _between(val, low, high):
        return (val >= low) & (val <= high)

    @staticmethod
    def _filter(cond_arr, n):
        result = np.zeros(len(cond_arr), dtype=bool)
        suppress_until = -1
        for i in range(len(cond_arr)):
            if i < suppress_until:
                continue
            if cond_arr[i]:
                result[i] = True
                suppress_until = i + n
        return result

    @staticmethod
    def _forcast(arr, n):
        result = np.full(len(arr), np.nan)
        if len(arr) < n:
            return result
        for i in range(n - 1, len(arr)):
            y = arr[i - n + 1:i + 1]
            if np.isnan(y).any():
                continue
            x = np.arange(n)
            coeffs = np.polyfit(x, y, 1)
            result[i] = np.polyval(coeffs, n - 1)
        return result

    @staticmethod
    def _estimate_ppart(close, open_, high, low, volume, avg_volume):
        daily_range = np.maximum(high - low, 0.001)
        position = (close - low) / daily_range
        direction = np.where(close > open_, 1, np.where(close < open_, -1, 0))
        vol_ratio = np.where(avg_volume > 0, volume / np.maximum(avg_volume, 0.001), 1.0)
        vol_factor = np.clip(vol_ratio / 2, 0.5, 2.0)
        ppart = 50 + direction * position * 40 * vol_factor
        return np.clip(ppart, 0, 100)

    def calculate(self, close, high, low, open_, volume, capital_shares):
        import numpy as np
        c = np.array(close, dtype=float)
        h = np.array(high, dtype=float)
        l = np.array(low, dtype=float)
        o = np.array(open_, dtype=float)
        v = np.array(volume, dtype=float)

        if len(c) < 200:
            return {'error': f'数据不足200天, 当前{len(c)}天'}

        capital_shou = capital_shares / 100.0
        if capital_shou <= 0:
            return {'error': '无效的流通股本'}

        v_ma135 = self._ma(v, 135)

        # VAR1
        var1 = self._count(self._every(v >= v_ma135, 1), 35) >= 15

        # VAR2
        var2_part1 = (self._count(self._every(v >= v_ma135, 10), 50) >= 1) & (self._count(self._every(v >= v_ma135, 1), 15) >= 5)
        var2_part2 = self._count(self._every(var1, 1), 15) >= 1
        var2 = var2_part1 | var2_part2

        # N1
        v_ref1 = self._ref(v, 1)
        n1 = (v >= v_ref1 * 1.45) & np.array(var2, dtype=bool)

        # 成交量
        vol_cond = self._count(self._every(n1, 1), 25) >= 1

        # 短期爆量
        short_vol = self._between(self._sum(v / capital_shou * 100, 35), 65, 500)

        # PPART
        avg_v60 = self._ma(v, 60)
        ppart_val = self._estimate_ppart(c, o, h, l, v, avg_v60)
        ppart_ref1 = self._ref(ppart_val, 1)

        # 庄家吸筹
        ch35 = self._ref(self._hhv(c, 35), 2)
        cl165 = self._ref(self._llv(c, 165), 2)
        acc = (ppart_ref1 >= 18) & ((ch35 - cl165) / np.maximum(cl165, 0.001) * 100 < 58) & (self._count(self._every(n1, 1), 85) >= 1) & (self._count(self._every(v > v_ma135, 1), 15) >= 2)

        # 拉涨
        la_zhang = ppart_val >= 20

        # 条件 (原始通达信公式阈值，根据流通市值: <65亿直通, 65~125亿需拉升, <=18.5亿需强主力)
        mcap_2d = self._ref(c, 2) * capital_shares / 100000000
        cond_ = (mcap_2d > 65) & (mcap_2d < 125) & self._ref(la_zhang, 1)

        # VAR3
        mcap_1d = self._ref(c, 1) * capital_shares / 100000000
        var3 = (mcap_1d <= 18.5) & (mcap_1d < 85) & (ppart_val >= 30)

        # 量能基础
        vol_base = ((mcap_2d < 65) | cond_ | var3) & (vol_cond | short_vol | acc)

        # MACD (BDIF0/BDEA0/BMACD0)
        bdif0 = self._ema(c, 12) - self._ema(c, 26)
        bdea0 = self._ema(bdif0, 9)
        bmacd0 = (bdif0 - bdea0) * 2

        # 强势区域1
        bm_nz = bmacd0 >= 0
        bm_rise = (bmacd0 >= self._ref(bmacd0, 1)) & (bmacd0 < 0)
        strong1 = ((self._count(self._every(bm_nz, 1), 7) >= 3) | (self._count(self._every(bm_rise, 1), 9) >= 2)) & (bdif0 >= 0) & (bdea0 >= 0) & (self._count(self._every((bdif0 >= 0) & (bdea0 >= 0), 1), 7) >= 2)

        # MACD
        dif = self._ema(c, 12) - self._ema(c, 26)
        dea = self._ema(dif, 9)
        macd_ = (dif - dea) * 2
        strong2 = macd_ > 0

        # ABC1
        c_ref1 = self._ref(c, 1)
        fc4 = self._forcast(v, 4)
        fc12 = self._forcast(v, 12)
        abc1 = (c / np.maximum(c_ref1, 0.001) > 1.028) & (np.abs(c - h) <= 0.001) & self._between(fc4, 0.2 * fc12, 2.1 * fc12)

        # 拉升/拉升2
        la1 = self._filter(abc1, 28) & (l > self._ref(c, 1) * 0.93) & strong1 & vol_base
        la2 = self._filter(abc1, 28) & (l > self._ref(c, 1) * 0.93) & strong2 & vol_base

        # 最终信号
        signal = la1 | la2

        latest = bool(signal[-1]) if len(signal) > 0 else False
        prev = bool(signal[-2]) if len(signal) > 1 else False

        return {'signal': latest, 'prev_signal': prev, 'is_new': latest and not prev}
