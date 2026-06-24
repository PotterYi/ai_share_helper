"""
十全十美 多指标共振系统 (sqsm2版本)
通达信公式转 Python 实现
N1=3, N2=5, N3=9, N4=13, N5=21, N6=34
10个指标求和，9分及以上为共振
"""
import numpy as np


class ShiQuanShiMei:
    """十全十美 多指标共振系统 (sqsm2版)"""

    @staticmethod
    def _ema(arr, n):
        """通达信EMA算法: 首值=X[0], 之后 EMA=2/(N+1)*X + (N-1)/(N+1)*REF(EMA,1)"""
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
            prev = result[i - 1]
            result[i] = arr[i] * k + prev * (1 - k)
        return result

    @staticmethod
    def _sma(arr, n, m=1):
        """通达信SMA算法: 首值=X[0], 之后 SMA=(M*X + (N-M)*REF(SMA,1))/N"""
        result = np.full_like(arr, np.nan)
        start = np.where(~np.isnan(arr))[0]
        if len(start) == 0 or len(arr) < 1:
            return result
        first = start[0]
        result[first] = arr[first]
        n = max(n, 1)
        m = max(m, 1)
        for i in range(first + 1, len(arr)):
            if np.isnan(arr[i]):
                continue
            prev = result[i - 1]
            result[i] = (arr[i] * m + prev * (n - m)) / n
        return result

    @staticmethod
    def _hhv(arr, n):
        if len(arr) < n:
            return np.full_like(arr, np.nan)
        result = np.copy(arr)
        for i in range(n, len(arr)):
            result[i] = np.max(arr[i - n + 1:i + 1])
        return result

    @staticmethod
    def _llv(arr, n):
        if len(arr) < n:
            return np.full_like(arr, np.nan)
        result = np.copy(arr)
        for i in range(n, len(arr)):
            result[i] = np.min(arr[i - n + 1:i + 1])
        return result

    @staticmethod
    def _ref(arr, n):
        result = np.full_like(arr, np.nan)
        result[n:] = arr[:-n]
        return result

    def _ma(self, arr, n):
        if len(arr) < n:
            return np.full_like(arr, np.nan)
        result = np.full_like(arr, np.nan)
        result[n - 1:] = np.convolve(arr, np.ones(n) / n, mode="valid")
        return result

    def calculate(self, close, high, low, volume, stock_code=""):
        """计算十全十美 (sqsm2版) 10指标共振系统"""
        c, h, l, v = [np.array(x, dtype=float) for x in [close, high, low, volume]]
        if len(c) < 80:
            return {"error": f"数据不足80天, 当前{len(c)}天"}

        N1, N2, N3, N4 = 3, 5, 9, 13

        # ── 1. MACD ──
        diff = self._ema(c, N3) - self._ema(c, N4)
        dea = self._ema(diff, N2)
        a1 = diff > dea

        # ── 2. KDJ ──
        denom = np.maximum(self._hhv(h, N3) - self._llv(l, N3), 0.001)
        rsv1 = (c - self._llv(l, N3)) / denom * 100
        k = self._sma(rsv1, N1, 1)
        d = self._sma(k, N1, 1)
        a2 = k > d

        # ── 3. RSI ──
        lc = self._ref(c, 1)
        ru2 = self._sma(np.maximum(c - lc, 0), N2, 1)
        rd2 = self._sma(np.abs(c - lc), N2, 1)
        r1 = np.where(rd2 != 0, ru2 / rd2 * 100, 50)
        ru4 = self._sma(np.maximum(c - lc, 0), N4, 1)
        rd4 = self._sma(np.abs(c - lc), N4, 1)
        r2 = np.where(rd4 != 0, ru4 / rd4 * 100, 50)
        a3 = r1 > r2

        # ── 4. LWR ──
        h4 = self._hhv(h, N4)
        l4 = self._llv(l, N4)
        rsv = -(h4 - c) / np.maximum(h4 - l4, 0.001) * 100
        lwr1 = self._sma(rsv, N1, 1)
        lwr2 = self._sma(lwr1, N1, 1)
        a4 = lwr1 > lwr2

        # ── 5. BBI ──
        bbi = (self._ma(c, N1) + self._ma(c, N2)
               + self._ma(c, N3) + self._ma(c, N4)) / 4
        a5 = c > bbi

        # ── 6. ZLMM ──
        mtm = c - self._ref(c, 1)
        m_f = self._ema(self._ema(mtm, N2), N1)
        a_f = self._ema(self._ema(np.abs(mtm), N2), N1)
        m_s = self._ema(self._ema(mtm, N4), N3)
        a_s = self._ema(self._ema(np.abs(mtm), N4), N3)
        a6 = np.where(a_f != 0, 100 * m_f / a_f, 0) > np.where(a_s != 0, 100 * m_s / a_s, 0)

        # ── 7. DBCD ──
        mac2 = self._ma(c, N2)
        bias = (c - mac2) / np.maximum(mac2, 0.001)
        dif_b = bias - self._ref(bias, 16)
        dbcd = self._sma(dif_b, 76, 1)
        mm_d = self._ma(dbcd, 5)
        a7 = dbcd > mm_d

        # ── 8. CGZ ──
        denom27 = np.maximum(self._hhv(h, 27) - self._llv(l, 27), 0.001)
        cg = (c - self._llv(l, 27)) / denom27 * 100
        cg5 = self._sma(cg, 5, 1)
        cg53 = self._sma(cg5, 3, 1)
        chigu = 3 * cg5 - 2 * cg53
        xiadie = self._ma(chigu, 12)
        a8 = chigu > xiadie

        # ── 9. ZLGJ ──
        mt = c - self._ref(c, 1)
        zf = self._ema(self._ema(mt, N3), N3)
        za = self._ema(self._ema(np.abs(mt), N3), N3)
        zlgj = np.where(za != 0, 100 * zf / za, 0)
        mazl = self._ma(zlgj, 5)
        a9 = zlgj > mazl

        # ── 10. ZJL ──
        o = self._ref(c, 1)
        o[0] = c[0]
        pjj = self._ema((h + l + c * 2) / 4, 3)  # DMA近似用EMA(3)
        hl2 = (h - l) * 2 - np.abs(c - o)
        qjj = np.where(np.abs(hl2) > 0.001, v / hl2, 0)

        xvl = np.zeros_like(c)
        up = c > o
        dn = c < o
        eq = ~(up | dn)
        xvl[up] = qjj[up] * (h[up] - l[up]) - qjj[up] * (h[up] - c[up] + o[up] - l[up])
        xvl[dn] = qjj[dn] * (h[dn] - o[dn] + c[dn] - l[dn]) - qjj[dn] * (h[dn] - l[dn])
        xvl[eq] = v[eq] / 2 - v[eq] / 2

        hsl = (xvl / 20) / 1.15
        gjl = hsl * 0.55 + self._ref(hsl, 1) * 0.33 + self._ref(hsl, 2) * 0.22
        lljx = self._ema(gjl, 3)
        a10 = lljx > 0

        # ── 汇总 ──
        def sb(arr):
            return bool(arr[-1]) if len(arr) > 0 else False

        names = ["MACD", "KDJ", "RSI", "LWR", "BBI",
                 "ZLMM", "DBCD", "CGZ", "ZLGJ", "ZJL"]
        arrs = [a1, a2, a3, a4, a5, a6, a7, a8, a9, a10]
        latest = {n: sb(a) for n, a in zip(names, arrs)}
        bc = sum(1 for v in latest.values() if v)

        return {
            "latest": latest,
            "bull_ratio": f"{bc}/10",
            "score": bc * 2 - 10,
            "eight_resonance": bc > 8,  # 9分及以上算共振
            "total": bc,
            "components": {
                "macd": {"diff": float(diff[-1]), "dea": float(dea[-1])},
                "kdj": {"k": float(k[-1]), "d": float(d[-1])},
                "rsi": {"rsi5": float(r1[-1]), "rsi13": float(r2[-1])},
                "bbi": {"bbi": float(bbi[-1]), "close": float(c[-1])},
                "zlmm": {"mms": float(m_f[-1]), "mmm": float(m_s[-1])},
                "dbcd": {"dbcd": float(dbcd[-1]), "mm": float(mm_d[-1])},
                "cgz": {"chigu": float(chigu[-1]), "xiadie": float(xiadie[-1])},
                "zlgj": {"zlgj": float(zlgj[-1]), "mazl": float(mazl[-1])},
                "zjl": {"lljx": float(lljx[-1])},
            },
        }
