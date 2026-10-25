"""
PRO v3.0 — 3-MODUL: Avtomatik Win-Rate % va Foyda ($) Statistika Hisoblagichi (stats_tracker.py)
"""
from datetime import datetime, timezone
import json
import os
from typing import Any

HISTORY_FILE = "trade_history.json"


class PerformanceStatsTracker:
    def __init__(self, history_file: str = HISTORY_FILE):
        self.history_file = history_file
        self.data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"trades": []}

    def _save(self) -> None:
        try:
            with open(self.history_file, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def record_new_signal(
        self,
        symbol: str,
        direction: str,
        order_type: str,
        entry_price: float,
        sl_price: float,
        tp1_price: float,
        tp2_price: float,
        risk_usd: float = 100.0,
    ) -> None:
        trades = self.data.setdefault("trades", [])
        for tr in reversed(trades):
            if tr.get("symbol") == symbol and tr.get("status") in ("PENDING", "TRIGGERED"):
                return

        trades.append({
            "id": len(trades) + 1,
            "symbol": symbol,
            "direction": direction,
            "order_type": order_type,
            "entry_price": round(entry_price, 2),
            "sl_price": round(sl_price, 2),
            "tp1_price": round(tp1_price, 2),
            "tp2_price": round(tp2_price, 2),
            "risk_usd": round(risk_usd, 2),
            "status": "TRIGGERED" if "MARKET" in order_type else "PENDING",
            "pnl_usd": 0.0,
            "opened_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "closed_at": None,
        })
        self._save()

    def record_triggered(self, symbol: str) -> None:
        for tr in reversed(self.data.get("trades", [])):
            if tr.get("symbol") == symbol and tr.get("status") == "PENDING":
                tr["status"] = "TRIGGERED"
                self._save()
                return

    def record_outcome(self, symbol: str, outcome: str) -> float:
        for tr in reversed(self.data.get("trades", [])):
            if tr.get("symbol") == symbol and tr.get("status") in ("PENDING", "TRIGGERED", "TP1_HIT"):
                entry = float(tr["entry_price"])
                sl = float(tr["sl_price"])
                tp1 = float(tr["tp1_price"])
                tp2 = float(tr["tp2_price"])
                risk_usd = float(tr.get("risk_usd", 100.0))
                risk_pts = max(abs(entry - sl), 1e-4)

                rr_tp1 = abs(entry - tp1) / risk_pts
                rr_tp2 = abs(entry - tp2) / risk_pts

                if outcome == "TP1_HIT":
                    pnl = round(risk_usd * rr_tp1 * 0.70, 2)
                elif outcome == "TP2_HIT":
                    pnl = round(risk_usd * (0.70 * rr_tp1 + 0.30 * rr_tp2), 2)
                elif outcome == "BREAKEVEN":
                    pnl = 0.0
                else:
                    pnl = round(-risk_usd, 2)

                tr["status"] = outcome
                tr["pnl_usd"] = pnl
                tr["closed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                self._save()
                return pnl
        return 0.0

    def compute_metrics(self) -> dict[str, Any]:
        trades = self.data.get("trades", [])
        closed = [t for t in trades if t.get("status") in ("TP1_HIT", "TP2_HIT", "BREAKEVEN", "SL_HIT")]
        wins = [t for t in closed if t.get("status") in ("TP1_HIT", "TP2_HIT")]
        losses = [t for t in closed if t.get("status") == "SL_HIT"]
        bes = [t for t in closed if t.get("status") == "BREAKEVEN"]
        active = [t for t in trades if t.get("status") in ("PENDING", "TRIGGERED")]

        decided = len(wins) + len(losses)
        win_rate = round((len(wins) / decided) * 100.0, 1) if decided > 0 else 100.0
        net_pnl = round(sum(float(t.get("pnl_usd", 0.0)) for t in closed), 2)

        return {
            "total_signals": len(trades),
            "closed_count": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "breakevens": len(bes),
            "active_count": len(active),
            "win_rate": win_rate,
            "net_pnl": net_pnl,
            "recent_closed": closed[-5:],
        }

    def get_summary_badge(self) -> str:
        m = self.compute_metrics()
        if m["closed_count"] == 0:
            return "📈 *PRO v3.0 Statistika:* `Yangi sikl (0 yopilgan bitim)` | `/stats`"
        sign = "+" if m["net_pnl"] >= 0 else ""
        return (
            f"📈 *Jonli Statistika:* `{m['closed_count']} ta bitim` | "
            f"✅ *G\'alaba:* `{m['wins']} ta ({m['win_rate']}%)` | "
            f"🛡️ *Bezubitok:* `{m['breakevens']}` | "
            f"❌ *SL:* `{m['losses']}` | "
            f"💰 *Sof Foyda:* `{sign}${m['net_pnl']:.2f}`"
        )

    def format_full_stats_report(self) -> str:
        m = self.compute_metrics()
        sign = "+" if m["net_pnl"] >= 0 else ""
        lines = [
            "🏆 *PRO v3.0 AI SNAYPER — RASMIY STATISTIKA HISOBOTI*",
            f"• *Jami yuborilgan A+ signallar:* `{m['total_signals']} ta`",
            f"• *Yopilgan bitimlar:* `{m['closed_count']} ta` (Aktiv: `{m['active_count']} ta`)",
            f"• *G\'alabalar (TP1 / TP2):* `{m['wins']} ta`",
            f"• *Bezubitok (0% zarar):* `{m['breakevens']} ta`",
            f"• *Stop-Loss (SL):* `{m['losses']} ta`",
            f"• 🎯 *Aniq Win-Rate:* `{m['win_rate']}%`",
            f"• 💰 *Jami Sof Foyda (1% = $100 riskda):* `{sign}${m['net_pnl']:.2f}`\n",
        ]
        if m["recent_closed"]:
            lines.append("📋 *Oxirgi Yopilgan Bitimlar:*")
            for tr in reversed(m["recent_closed"]):
                p = float(tr.get("pnl_usd", 0.0))
                ps = f"+${p:.2f}" if p >= 0 else f"-${abs(p):.2f}"
                lines.append(
                    f"  • `#{tr['id']}` *{tr['symbol']}* ({tr['order_type']} @ `{tr['entry_price']}`) ➡️ *{tr['status']}* (`{ps}`)"
                )
        return "\n".join(lines)
