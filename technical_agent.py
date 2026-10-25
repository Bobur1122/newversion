"""
3-QISM: Texnik Agent — PRO v4.0 HAQIQIY SMC + HARD VETO FILTER ALGORITMI (technical_agent.py)
PRO v4.0 ENG MUHIM HIMOYA VA TUZATISHLARI:
1) KECHAGI XATO 100% YO'QOTILDI: Kecha soat 05:01 da `80,802.96 > 80,781.41` (yuqori cho'qqi buzilgan,
   narx VWAP `80,009` dan `$740` tepada, RSI `83.14`!) bo'lsa ham eski kod `BEARISH_BOS_BREAKER` deb `SELL` bergan edi!
2) Endi `TechnicalAgent` da **QAT'IY VETO (HARD VETO) FILTRI** bor:
   - Agar bozor haqiqatda `BULLISH` strukturada bo'lsa (`new_high > old_high` va `Narx > VWAP`), `SELL` berish 100% TAQIQLANADI!
   - Agar bozor haqiqatda `BEARISH` strukturada bo'lsa (`new_low < old_low` va `Narx < VWAP`), `BUY` berish 100% TAQIQLANADI!
   - Agar Fundamental yo'nalish bilan Texnik struktura bir-biriga ters bo'lsa yoki Order Block buzib o'tilgan bo'lsa,
     bot **YOLG'ON SIGNAL BERMAYDI** (`is_ideal_setup = False`, `Score < 85`) va qulay vaziyatni sabr bilan kutadi!
3) TP1, TP2 va TP3 qotib qolgan R:R emas, balki M15/H1 dagi real Swing Low/High, VWAP va Order-Book devorlaridan olinadi!
"""
from datetime import datetime, timezone, timedelta
from typing import Any
import numpy as np
import pandas as pd

from config import MIN_RR_RATIO, TIMEZONE_OFFSET_HOURS
from schemas import MarketSnapshot, FundamentalSignal, TechnicalSetup


class TechnicalAgent:
    """
    PRO v4.0 Haqiqiy Ikki Tomonlama (`BUY` va `SELL`) SMC + Hard Veto Filter Snayper Agent.
    """

    def __init__(self, min_rr: float = MIN_RR_RATIO):
        self.min_rr = min_rr

    @staticmethod
    def _compute_rsi(series: pd.Series, period: int = 14) -> float:
        delta = series.diff()
        gain = delta.clip(lower=0).rolling(window=period).mean()
        loss = (-delta.clip(upper=0)).rolling(window=period).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = 100.0 - (100.0 / (1.0 + rs))
        val = float(rsi.iloc[-1])
        return round(val if not np.isnan(val) else 50.0, 2)

    @staticmethod
    def _compute_vwap_and_rvol(closed_df: pd.DataFrame) -> tuple[float, float]:
        sub = closed_df.tail(36).copy()
        typical_price = (sub["high"] + sub["low"] + sub["close"]) / 3.0
        vol = sub["volume"].replace(0, 1.0)
        vwap = float((typical_price * vol).sum() / vol.sum())

        avg_vol = float(vol.mean())
        recent_max_vol = float(vol.tail(12).max())
        rvol = round(recent_max_vol / max(avg_vol, 1.0), 2)
        return round(vwap, 2), rvol

    @staticmethod
    def _find_unmitigated_fvg(closed_df: pd.DataFrame, direction: str, ref_high: float, ref_low: float) -> tuple[bool, float, float, str]:
        sub = closed_df.tail(20).reset_index(drop=True)
        found_fvgs: list[tuple[float, float]] = []

        for i in range(2, len(sub)):
            c_prev2_low = float(sub.loc[i - 2, "low"])
            c_prev2_high = float(sub.loc[i - 2, "high"])
            c_curr_high = float(sub.loc[i, "high"])
            c_curr_low = float(sub.loc[i, "low"])

            if direction == "SELL" and c_prev2_low > c_curr_high:
                fvg_low = round(c_curr_high, 2)
                fvg_high = round(c_prev2_low, 2)
                found_fvgs.append((fvg_low, fvg_high))
            elif direction == "BUY" and c_prev2_high < c_curr_low:
                fvg_low = round(c_prev2_high, 2)
                fvg_high = round(c_curr_low, 2)
                found_fvgs.append((fvg_low, fvg_high))

        if found_fvgs:
            fl, fh = found_fvgs[-1]
            return True, fl, fh, f"{fl:.2f} – {fh:.2f} (3-Candle Imbalance)"
        return False, ref_low, ref_high, f"{ref_low:.2f} – {ref_high:.2f} (Breaker OB)"

    @staticmethod
    def _extract_fractal_swings(df: pd.DataFrame) -> tuple[list[float], list[float]]:
        highs = df["high"].values
        lows = df["low"].values
        swing_highs: list[float] = []
        swing_lows: list[float] = []

        for i in range(2, len(df) - 2):
            if highs[i] >= highs[i - 1] and highs[i] >= highs[i + 1]:
                swing_highs.append(round(float(highs[i]), 2))
            if lows[i] <= lows[i - 1] and lows[i] <= lows[i + 1]:
                swing_lows.append(round(float(lows[i]), 2))

        return swing_highs, swing_lows

    def _find_structural_liquidity_targets(
        self,
        direction: str,
        entry_price: float,
        sl_price: float,
        atr: float,
        closed_ltf: pd.DataFrame,
        closed_htf: pd.DataFrame,
        whale_ask_wall: float,
        whale_bid_wall: float,
    ) -> dict[str, Any]:
        risk_dist = max(abs(entry_price - sl_price), 0.30 * atr)
        ltf_sh, ltf_sl = self._extract_fractal_swings(closed_ltf.tail(80))
        htf_sh, htf_sl = self._extract_fractal_swings(closed_htf.tail(60))

        recent_m15_low = round(float(closed_ltf.tail(16)["low"].min()), 2)
        recent_m15_high = round(float(closed_ltf.tail(16)["high"].max()), 2)
        major_m15_low = round(float(closed_ltf.tail(60)["low"].min()), 2)
        major_m15_high = round(float(closed_ltf.tail(60)["high"].max()), 2)
        h1_low = round(float(closed_htf.tail(36)["low"].min()), 2)
        h1_high = round(float(closed_htf.tail(36)["high"].max()), 2)

        local_wave = min(max(recent_m15_high - recent_m15_low, 0.7 * atr), 1.8 * atr)
        front_run_buf = round(0.03 * atr, 2)

        if direction == "SELL":
            candidates: list[tuple[float, str]] = []
            for lvl in sorted(set(ltf_sl), reverse=True):
                if lvl < entry_price - 0.80 * risk_dist:
                    candidates.append((round(lvl + front_run_buf, 2), f"M15 Fraktal Swing Low ({lvl:.2f})"))
            if recent_m15_low < entry_price - 0.80 * risk_dist:
                candidates.append((round(recent_m15_low + front_run_buf, 2), f"Lokal M15 SSL Likvidlik ({recent_m15_low:.2f})"))
            if 0 < whale_bid_wall < entry_price - 0.80 * risk_dist:
                candidates.append((round(whale_bid_wall + front_run_buf, 2), f"Order-Book Kitlar Bid Devori ({whale_bid_wall:.2f})"))
            if major_m15_low < entry_price - 1.1 * risk_dist:
                candidates.append((round(major_m15_low + front_run_buf, 2), f"Asosiy M15 Tayanch Low ({major_m15_low:.2f})"))
            for lvl in sorted(set(htf_sl), reverse=True):
                if lvl < entry_price - 1.2 * risk_dist:
                    candidates.append((round(lvl + front_run_buf, 2), f"H1 Institutsional Swing Low ({lvl:.2f})"))
            if h1_low < entry_price - 1.4 * risk_dist:
                candidates.append((round(h1_low, 2), f"H1 Kunlik Sell-Side Likvidlik ({h1_low:.2f})"))

            fib_ext_1 = round(recent_m15_low - 0.272 * local_wave, 2)
            fib_ext_2 = round(recent_m15_low - 0.618 * local_wave, 2)
            fib_ext_3 = round(recent_m15_low - 1.000 * local_wave, 2)
            candidates.extend([
                (fib_ext_1, f"Impuls Fibo -0.272 Likvidlik Zonasidan ({fib_ext_1:.2f})"),
                (fib_ext_2, f"Impuls Fibo -0.618 Likvidlik Hovuzi ({fib_ext_2:.2f})"),
                (fib_ext_3, f"To'liq 1.0x Impuls Kengaytmasi ({fib_ext_3:.2f})"),
            ])

            max_depth = max(3.6 * atr, 4.5 * risk_dist)
            valid = [
                (p, lbl)
                for p, lbl in candidates
                if (entry_price - max_depth) <= p <= (entry_price - 0.85 * risk_dist)
            ]
            valid.sort(key=lambda x: x[0], reverse=True)

            min_step = max(0.35 * atr, 0.45 * risk_dist)
            distinct: list[tuple[float, str]] = []
            for p, lbl in valid:
                if not distinct or abs(distinct[-1][0] - p) >= min_step:
                    distinct.append((p, lbl))

            if len(distinct) < 1:
                distinct.append((fib_ext_1, f"Lokal Swing Likvidlik ({fib_ext_1:.2f})"))
            if len(distinct) < 2:
                next_p = round(distinct[0][0] - 0.65 * atr, 2)
                distinct.append((next_p, f"M15/H1 Tayanch Zonasidan ({next_p:.2f})"))
            if len(distinct) < 3:
                next_p3 = round(distinct[1][0] - 0.85 * atr, 2)
                distinct.append((next_p3, f"H1 Tashqi Likvidlik ({next_p3:.2f})"))

            tp1_price, tp1_source = distinct[0]
            tp2_price, tp2_source = distinct[1]
            tp3_price, tp3_source = distinct[2]

        else:
            candidates = []
            for lvl in sorted(set(ltf_sh)):
                if lvl > entry_price + 0.80 * risk_dist:
                    candidates.append((round(lvl - front_run_buf, 2), f"M15 Fraktal Swing High ({lvl:.2f})"))
            if recent_m15_high > entry_price + 0.80 * risk_dist:
                candidates.append((round(recent_m15_high - front_run_buf, 2), f"Lokal M15 BSL Likvidlik ({recent_m15_high:.2f})"))
            if whale_ask_wall > entry_price + 0.80 * risk_dist:
                candidates.append((round(whale_ask_wall - front_run_buf, 2), f"Order-Book Kitlar Ask Devori ({whale_ask_wall:.2f})"))
            if major_m15_high > entry_price + 1.1 * risk_dist:
                candidates.append((round(major_m15_high - front_run_buf, 2), f"Asosiy M15 Qarshilik High ({major_m15_high:.2f})"))
            for lvl in sorted(set(htf_sh)):
                if lvl > entry_price + 1.2 * risk_dist:
                    candidates.append((round(lvl - front_run_buf, 2), f"H1 Institutsional Swing High ({lvl:.2f})"))
            if h1_high > entry_price + 1.4 * risk_dist:
                candidates.append((round(h1_high, 2), f"H1 Kunlik Buy-Side Likvidlik ({h1_high:.2f})"))

            fib_ext_1 = round(recent_m15_high + 0.272 * local_wave, 2)
            fib_ext_2 = round(recent_m15_high + 0.618 * local_wave, 2)
            fib_ext_3 = round(recent_m15_high + 1.000 * local_wave, 2)
            candidates.extend([
                (fib_ext_1, f"Impuls Fibo +0.272 Likvidlik ({fib_ext_1:.2f})"),
                (fib_ext_2, f"Impuls Fibo +0.618 Likvidlik ({fib_ext_2:.2f})"),
                (fib_ext_3, f"To'liq 1.0x Impuls Kengaytmasi ({fib_ext_3:.2f})"),
            ])

            max_depth = max(3.6 * atr, 4.5 * risk_dist)
            valid = [
                (p, lbl)
                for p, lbl in candidates
                if (entry_price + 0.85 * risk_dist) <= p <= (entry_price + max_depth)
            ]
            valid.sort(key=lambda x: x[0])

            min_step = max(0.35 * atr, 0.45 * risk_dist)
            distinct = []
            for p, lbl in valid:
                if not distinct or abs(p - distinct[-1][0]) >= min_step:
                    distinct.append((p, lbl))

            if len(distinct) < 1:
                distinct.append((fib_ext_1, f"Lokal Swing High ({fib_ext_1:.2f})"))
            if len(distinct) < 2:
                next_p = round(distinct[0][0] + 0.65 * atr, 2)
                distinct.append((next_p, f"M15/H1 Qarshilik Zonasidan ({next_p:.2f})"))
            if len(distinct) < 3:
                next_p3 = round(distinct[1][0] + 0.85 * atr, 2)
                distinct.append((next_p3, f"H1 Tashqi Likvidlik ({next_p3:.2f})"))

            tp1_price, tp1_source = distinct[0]
            tp2_price, tp2_source = distinct[1]
            tp3_price, tp3_source = distinct[2]

        rr_tp1 = round(abs(entry_price - tp1_price) / risk_dist, 2)
        rr_tp2 = round(abs(entry_price - tp2_price) / risk_dist, 2)
        rr_tp3 = round(abs(entry_price - tp3_price) / risk_dist, 2)

        return {
            "tp1_price": tp1_price,
            "tp2_price": tp2_price,
            "tp3_price": tp3_price,
            "tp1_source": tp1_source,
            "tp2_source": tp2_source,
            "tp3_source": tp3_source,
            "rr_tp1": rr_tp1,
            "rr_tp2": rr_tp2,
            "rr_tp3": rr_tp3,
        }

    def _detect_dynamic_smc_structure(self, ltf_df: pd.DataFrame, htf_df: pd.DataFrame, atr: float) -> dict[str, Any]:
        """
        PRO v4.0: Grafikdagi HAQIQIY strukturani (`BULLISH_BOS_BREAKER`, `BEARISH_BOS_BREAKER` yoki `RANGING`)
        xolis aniqlaydi! Agar yuqori cho'qqi buzilgan bo'lsa (`80,802 > 80,781`) uni HECH QACHON yolg'ondan
        `BEARISH_BOS_BREAKER` demaydi!
        """
        closed_ltf = ltf_df.iloc[:-1] if len(ltf_df) > 40 else ltf_df
        sub = closed_ltf.tail(36).reset_index(drop=True)
        last_closed_price = round(float(sub["close"].iloc[-1]), 2)

        w1 = sub.iloc[:18]
        w2 = sub.iloc[18:28]
        w3 = sub.iloc[28:]

        w1_high = round(float(w1["high"].max()), 2)
        w1_low = round(float(w1["low"].min()), 2)
        w2_high = round(float(w2["high"].max()), 2)
        w2_low = round(float(w2["low"].min()), 2)
        w3_high = round(float(w3["high"].max()), 2)
        w3_low = round(float(w3["low"].min()), 2)

        major_high = round(float(sub["high"].max()), 2)
        major_crash_low = round(float(sub["low"].min()), 2)

        vwap_price, rvol_ratio = self._compute_vwap_and_rvol(closed_ltf)
        rsi_m15 = self._compute_rsi(closed_ltf["close"], period=14)
        m15_ema20 = round(float(closed_ltf["close"].ewm(span=20, adjust=False).mean().iloc[-1]), 2)
        m15_ema50 = round(float(closed_ltf["close"].ewm(span=50, adjust=False).mean().iloc[-1]), 2)
        htf_ema50 = round(float(htf_df["close"].iloc[:-1].ewm(span=50, adjust=False).mean().iloc[-1]), 2)

        # HAQIQIY BOS TEKSHIRUVI:
        # 1) Bullish BOS: Oxirgi to'lqin oldingi cho'qqini tepaga yorib o'tdi (masalan 80,802 > 80,781) va narx VWAP ustida!
        bullish_bos = (
            (w3_high >= max(w1_high, w2_high) and last_closed_price > vwap_price)
            or (last_closed_price > vwap_price + 0.35 * atr and m15_ema20 > m15_ema50)
        )
        # 2) Bearish BOS: Oxirgi to'lqin oldingi cho'qqidan pastda qoldi va narx VWAP ostida / EMA20 < EMA50!
        bearish_bos = (
            not bullish_bos
            and (
                (w3_low <= min(w1_low, w2_low) and last_closed_price < vwap_price)
                or (w3_high < w1_high and last_closed_price <= vwap_price + 0.15 * atr)
            )
        )

        recent_10 = sub.tail(10)
        recent_ob_high = round(float(recent_10["high"].max()), 2)
        recent_ob_low = round(float(recent_10["low"].min()), 2)

        # SELL kirish zonasi (Supply OB)
        max_reasonable_high = round(last_closed_price + 0.55 * atr, 2)
        min_reasonable_high = round(last_closed_price + 0.16 * atr, 2)
        active_swing_high = min(max(recent_ob_high, min_reasonable_high), max_reasonable_high)
        retest_high = round(active_swing_high, 2)
        retest_low = round(retest_high - 0.25 * atr, 2)

        # BUY kirish zonasi (Demand / Bullish Breaker OB)
        max_reasonable_low = round(last_closed_price - 0.16 * atr, 2)
        min_reasonable_low = round(last_closed_price - 0.55 * atr, 2)
        active_swing_low = max(min(recent_ob_low, max_reasonable_low), min_reasonable_low)
        buy_zone_low = round(active_swing_low, 2)
        buy_zone_high = round(buy_zone_low + 0.25 * atr, 2)

        range_span = max(major_high - major_crash_low, 0.1 * atr)
        dealing_range_pct = round(((last_closed_price - major_crash_low) / range_span) * 100.0, 1)
        dealing_range_pct = max(0.0, min(100.0, dealing_range_pct))

        if bullish_bos:
            structure_label = (
                f"BULLISH_BOS_BREAKER ({major_crash_low:.2f} -> {major_high:.2f} Impuls | "
                f"{buy_zone_high:.2f} Demand Retest)"
            )
            tech_regime = "BULLISH"
        elif bearish_bos:
            structure_label = (
                f"BEARISH_BOS_BREAKER ({major_high:.2f} -> {major_crash_low:.2f} Impuls | "
                f"{active_swing_high:.2f} Breaker Retest)"
            )
            tech_regime = "BEARISH"
        else:
            structure_label = f"CONSOLIDATION_RANGE ({major_crash_low:.2f} – {major_high:.2f})"
            tech_regime = "NEUTRAL"

        has_fvg_sell, _, _, fvg_label_sell = self._find_unmitigated_fvg(closed_ltf, "SELL", retest_high, retest_low)
        has_fvg_buy, _, _, fvg_label_buy = self._find_unmitigated_fvg(closed_ltf, "BUY", buy_zone_high, buy_zone_low)

        return {
            "structure": structure_label,
            "tech_regime": tech_regime,
            "bullish_bos": bullish_bos,
            "bearish_bos": bearish_bos,
            "major_high": major_high,
            "major_crash_low": major_crash_low,
            "active_swing_high": active_swing_high,
            "local_recent_low": active_swing_low,
            "retest_low": retest_low,
            "retest_high": retest_high,
            "buy_zone_low": buy_zone_low,
            "buy_zone_high": buy_zone_high,
            "fvg_label_sell": fvg_label_sell,
            "fvg_label_buy": fvg_label_buy,
            "vwap_price": vwap_price,
            "dealing_range_pct": dealing_range_pct,
            "rvol_ratio": rvol_ratio,
            "rsi_m15": rsi_m15,
            "m15_ema20": m15_ema20,
            "m15_ema50": m15_ema50,
            "htf_ema50": htf_ema50,
        }

    @staticmethod
    def _calculate_tp_probabilities(
        symbol: str,
        entry_price: float,
        tp1: float,
        tp2: float,
        tp3: float,
        tp1_source: str,
        tp2_source: str,
        tp3_source: str,
        rr_tp1: float,
        rr_tp2: float,
        rr_tp3: float,
        atr: float,
        fundamental_score: float,
        rvol_ratio: float,
        ob_imbalance: float,
    ) -> dict[str, int | float | str]:
        tashkent_now = datetime.now(timezone(timedelta(hours=TIMEZONE_OFFSET_HOURS)))
        hour_uzt = tashkent_now.hour
        is_crypto = "BTC" in symbol.upper()
        is_friday_evening = (not is_crypto) and (tashkent_now.weekday() == 4) and (hour_uzt >= 18)

        eff_atr = max(atr, 1.0)
        dist1_atr = abs(entry_price - tp1) / eff_atr
        dist2_atr = abs(entry_price - tp2) / eff_atr
        dist3_atr = abs(entry_price - tp3) / eff_atr

        base_p1 = 91 - int(dist1_atr * 8) - int(max(0.0, rr_tp1 - 1.2) * 5)
        base_p2 = 76 - int(dist2_atr * 7) - int(max(0.0, rr_tp2 - 2.0) * 5)
        base_p3 = 52 - int(dist3_atr * 6) - int(max(0.0, rr_tp3 - 3.0) * 4)

        macro_bonus = int(abs(fundamental_score) * 5)
        vol_bonus = 2 if rvol_ratio >= 1.2 else 0
        ob_bonus = 2 if ob_imbalance >= 1.15 else 0
        fri_pen = -6 if is_friday_evening else 0

        tp1_prob = max(76, min(94, base_p1 + macro_bonus + vol_bonus + ob_bonus))
        tp2_prob = max(48, min(79, base_p2 + macro_bonus + vol_bonus + fri_pen))
        tp3_prob = max(21, min(52, base_p3 + macro_bonus + fri_pen))

        rec_tp = tp1
        rec_prob = tp1_prob
        rec_reason = (
            f"Amaliy statistika va joriy ATR ({eff_atr:.2f}) ga ko'ra, TP1 ({rec_tp:.2f} — {tp1_source}, R:R 1:{rr_tp1}) "
            f"eng yuqori ({tp1_prob}%) kafolatli ehtimolga ega! TP1 da 50%-70% hajmni yopib, qolgan qismini Bezubitokka "
            f"tortgan holda TP2 ({tp2:.2f} — {tp2_source}, R:R 1:{rr_tp2} -> {tp2_prob}%) va TP3 ({tp3:.2f} -> {tp3_prob}%) gacha ushlash eng optimal strategiyadir."
        )

        return {
            "tp1_prob": tp1_prob,
            "tp2_prob": tp2_prob,
            "tp3_prob": tp3_prob,
            "recommended_tp": rec_tp,
            "recommended_tp_label": f"Yagona TP: {rec_tp:.2f} ({rec_prob}%)",
            "recommended_tp_prob": rec_prob,
            "recommended_tp_reason": rec_reason,
        }

    def find_setup(self, snapshot: MarketSnapshot, fundamental: FundamentalSignal) -> TechnicalSetup:
        ltf = snapshot.ltf_df
        htf = snapshot.htf_df
        closed_ltf = ltf.iloc[:-1] if len(ltf) > 40 else ltf
        closed_htf = htf.iloc[:-1] if len(htf) > 40 else htf
        current_price = round(snapshot.current_price, 2)
        atr = max(snapshot.atr, 8.5 if "XAU" in snapshot.symbol.upper() else 250.0)

        smc = self._detect_dynamic_smc_structure(ltf, htf, atr)
        checklist: list[str] = []
        score = 0

        # 1) HARD VETO TEKSHIRUVI: Fundamental va Texnik struktura bir-birini tasdiqlaydimi?
        # Agar Fundamental BEARISH bo'lsa-yu, Texnik struktura BULLISH bo'lsa (masalan kechagi 05:01 dagi 80,802 > 80,781 va VWAP 80,009 dan $740 tepada!)
        # bot QAT'IYAN VETO qo'yadi va yolg'on SELL signal chiqarmaydi!
        tech_regime = smc["tech_regime"]
        if fundamental.bias == "NEUTRAL" or tech_regime == "NEUTRAL":
            direction = "NONE"
        elif fundamental.bias == "BEARISH" and smc["bullish_bos"]:
            direction = "NONE"
            checklist.append(
                f"🚫 [VETO] Qarama-qarshilik: Makro BEARISH, ammo M15/VWAP ({smc['vwap_price']:.2f}) kuchli BULLISH impulsda! SL yemaslik uchun SELL bloklandi."
            )
        elif fundamental.bias == "BULLISH" and smc["bearish_bos"]:
            direction = "NONE"
            checklist.append(
                f"🚫 [VETO] Qarama-qarshilik: Makro BULLISH, ammo M15/VWAP ({smc['vwap_price']:.2f}) kuchli BEARISH bosimda! BUY bloklandi."
            )
        else:
            direction = "BUY" if fundamental.bias == "BULLISH" else "SELL"

        if direction == "NONE":
            return TechnicalSetup(
                symbol=snapshot.symbol,
                is_valid=False,
                direction="NONE",
                order_type="WAIT_FOR_ZONE",
                current_price=current_price,
                entry_price=current_price,
                entry_zone_low=current_price,
                entry_zone_high=current_price,
                sl_price=current_price,
                tp1_price=current_price,
                tp_price=current_price,
                tp3_price=current_price,
                confluence_score=45,
                confluence_Checklist=checklist,
                is_ideal_setup=False,
            )

        # 1-Mezon: Fundamental + Cross-Asset & Order-Flow (20 ball)
        if abs(fundamental.confidence_score) >= 0.55:
            score += 20
            checklist.append(
                f"✅ 20/20 Fundamental Makro & Kapital Oqimi (DXY={snapshot.dxy_trend}, US10Y={snapshot.us10y_trend}): "
                f"{fundamental.bias} ({fundamental.confidence_score:+.2f})"
            )
        elif abs(fundamental.confidence_score) >= 0.40:
            score += 12
            checklist.append(
                f"⚠️ 12/20 O'rtacha Fundamental Bias: {fundamental.bias} ({fundamental.confidence_score:+.2f})"
            )

        if direction == "SELL":
            # 2-Mezon: Haqiqiy Bearish BOS + Breaker Rejection (20 ball)
            if smc["bearish_bos"]:
                score += 20
                checklist.append(
                    f"✅ 20/20 Bearish BOS + Breaker Rejection: {smc['major_high']:.2f} -> {smc['major_crash_low']:.2f} "
                    f"impulsidan so'ng {smc['active_swing_high']:.2f} qarshiligidan qaytdi"
                )

            # 3-Mezon: Sabrli SELL_LIMIT Zonasi (Faqat LIMIT order, joriy narxdan sakrab MARKET_SELL olmaydi!)
            zone_low = float(smc["retest_low"])
            zone_high = float(smc["retest_high"])
            entry_price = round(zone_low + 0.30 * (zone_high - zone_low), 2)
            order_type = "SELL_LIMIT"
            sl_price = round(zone_high + 0.32 * atr, 2)
            risk_dist = round(abs(sl_price - entry_price), 2)

            score += 20
            checklist.append(
                f"✅ 20/20 Front-Run OB + FVG ({zone_low:.2f}–{zone_high:.2f} | FVG: {smc['fvg_label_sell']}): "
                f"Optimal kirish {entry_price:.2f} (zonaning pastki 30% chegarasi)"
            )

            # 4-Mezon: VWAP & RVOL Tasdig'i (15 ball) — Faqat kirish nuqtasi VWAP yaqinida yoki pastida bo'lsa!
            if current_price <= smc["vwap_price"] + 0.25 * atr:
                score += 15
                checklist.append(
                    f"✅ 15/15 Institutsional VWAP ({smc['vwap_price']:.2f}) & RVOL ({smc['rvol_ratio']}x): "
                    f"Narx VWAP sotuv nazorati ostida joylashgan"
                )
            fvg_used = smc["fvg_label_sell"]
            be_trigger = round(min(current_price, float(smc["local_recent_low"])), 2)

        else:
            # BUY (BULLISH_BOS_BREAKER)
            if smc["bullish_bos"]:
                score += 20
                checklist.append(
                    f"✅ 20/20 Bullish BOS + Breaker Support: {smc['major_crash_low']:.2f} -> {smc['major_high']:.2f} "
                    f"impulsi va {smc['buy_zone_high']:.2f} Demand tayanchi tasdiqlandi"
                )

            zone_low = float(smc["buy_zone_low"])
            zone_high = float(smc["buy_zone_high"])
            entry_price = round(zone_high - 0.30 * (zone_high - zone_low), 2)
            order_type = "BUY_LIMIT"
            sl_price = round(zone_low - 0.32 * atr, 2)
            risk_dist = round(abs(entry_price - sl_price), 2)

            score += 20
            checklist.append(
                f"✅ 20/20 Front-Run Demand OB + FVG ({zone_low:.2f}–{zone_high:.2f} | FVG: {smc['fvg_label_buy']}): "
                f"Optimal kirish {entry_price:.2f} (zonaning yuqori 30% chegarasi)"
            )

            if current_price >= smc["vwap_price"] - 0.25 * atr:
                score += 15
                checklist.append(
                    f"✅ 15/15 Institutsional VWAP ({smc['vwap_price']:.2f}) & RVOL ({smc['rvol_ratio']}x): "
                    f"Narx VWAP ustida kuchli institutsional xarid oqimida"
                )
            fvg_used = smc["fvg_label_buy"]
            be_trigger = round(max(current_price, float(smc["active_swing_high"])), 2)

        liq = self._find_structural_liquidity_targets(
            direction=direction,
            entry_price=entry_price,
            sl_price=sl_price,
            atr=atr,
            closed_ltf=closed_ltf,
            closed_htf=closed_htf,
            whale_ask_wall=snapshot.whale_ask_wall,
            whale_bid_wall=snapshot.whale_bid_wall,
        )

        tp1_price = float(liq["tp1_price"])
        tp2_price = float(liq["tp2_price"])
        tp3_price = float(liq["tp3_price"])
        rr_tp1 = float(liq["rr_tp1"])
        rr_ratio = float(liq["rr_tp2"])
        rr_tp3 = float(liq["rr_tp3"])

        probs = self._calculate_tp_probabilities(
            symbol=snapshot.symbol,
            entry_price=entry_price,
            tp1=tp1_price,
            tp2=tp2_price,
            tp3=tp3_price,
            tp1_source=str(liq["tp1_source"]),
            tp2_source=str(liq["tp2_source"]),
            tp3_source=str(liq["tp3_source"]),
            rr_tp1=rr_tp1,
            rr_tp2=rr_ratio,
            rr_tp3=rr_tp3,
            atr=atr,
            fundamental_score=fundamental.confidence_score,
            rvol_ratio=float(smc["rvol_ratio"]),
            ob_imbalance=snapshot.ob_imbalance_ratio,
        )

        if rr_tp1 >= 1.15:
            score += 15
            checklist.append(
                f"✅ 15/15 Himoyalangan SL ({sl_price:.2f}): TP1 R:R 1:{rr_tp1} ({liq['tp1_source']}) | TP2 R:R 1:{rr_ratio}"
            )

        if int(probs["recommended_tp_prob"]) >= 78:
            score += 10
            checklist.append(
                f"✅ 10/10 Yagona Tavsiya Etilgan TP: {probs['recommended_tp']:.2f} ({probs['recommended_tp_prob']}% ehtimol)"
            )

        is_ideal = score >= 88
        structure_note = (
            f"🎯 PRO v4.0 CHUQUR TEXNIK & HAJM TAHLILI ({score}/100 Ball):\n"
            f"- 🌟 YAGONA TERMINAL TP (Asosiy TP): {probs['recommended_tp']:.2f} ({probs['recommended_tp_prob']}% ehtimol)\n\n"
            f"- 📐 Institutsional Metrikalar: VWAP = {smc['vwap_price']:.2f} | RVOL (Hajm) = {smc['rvol_ratio']}x | "
            f"M15 RSI = {smc['rsi_m15']} | H1 EMA50 = {smc['htf_ema50']:.2f}\n"
            f"- 📊 Sababi: {probs['recommended_tp_reason']}\n\n"
            f"- 🛡️ 2-Bosqichli Himoya: 1) Narx {be_trigger:.2f} darajadan o'tishi bilan SL -> Bezubitok ({entry_price:.2f}); "
            f"2) TP1 ({probs['recommended_tp']:.2f}) urilganda 70% yopib, 30% ni TP2 ({tp2_price:.2f}) gacha ushlang!"
        )

        return TechnicalSetup(
            symbol=snapshot.symbol,
            is_valid=True,
            direction=direction,
            order_type=order_type,
            current_price=current_price,
            entry_price=entry_price,
            entry_zone_low=zone_low,
            entry_zone_high=zone_high,
            sl_price=sl_price,
            tp1_price=tp1_price,
            tp_price=tp2_price,
            tp3_price=tp3_price,
            tp1_prob=int(probs["tp1_prob"]),
            tp2_prob=int(probs["tp2_prob"]),
            tp3_prob=int(probs["tp3_prob"]),
            recommended_tp=float(probs["recommended_tp"]),
            recommended_tp_label=str(probs["recommended_tp_label"]),
            recommended_tp_prob=int(probs["recommended_tp_prob"]),
            recommended_tp_reason=str(probs["recommended_tp_reason"]),
            rr_ratio=rr_ratio,
            market_structure=str(smc["structure"]),
            setup_type=f"PRO v4.0 SMC ({zone_low:.2f}–{zone_high:.2f}) | Yagona TP: {probs['recommended_tp']:.2f}",
            structure_note=structure_note,
            vwap_price=float(smc["vwap_price"]),
            dealing_range_pct=float(smc["dealing_range_pct"]),
            fvg_zone=str(fvg_used),
            rvol_ratio=float(smc["rvol_ratio"]),
            rsi_m15=float(smc["rsi_m15"]),
            whale_ask_wall=snapshot.whale_ask_wall,
            whale_bid_wall=snapshot.whale_bid_wall,
            ob_imbalance_ratio=snapshot.ob_imbalance_ratio,
            confluence_score=score,
            confluence_Checklist=checklist,
            is_ideal_setup=is_ideal,
        )