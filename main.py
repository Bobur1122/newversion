"""
5-QISM: Asosiy Asinxron Orkestrator — PRO v4.0 (Haqiqiy Snayper Rejimi + Anti-Revenge SL Cooldown + BUY/SELL Kuzatuvchisi) (main.py)
FOYDALANUVCHI TALABIGA BINOAN PRO v4.0 O'ZGARISHLARI:
1) `/signal` buyrug'i va `.png` rasm yuborish butunlay olib tashlandi! Bot faqatgina bozorda HAQIQIY A+ QULAY VAZIYAT
   (`>=88/100 ball`, Makro va Jonli Order-Flow bir-birini tasdiqlaganda) o'zi mustaqil signal beradi!
2) **SL Cooldown (Anti-Revenge Himoyasi):** Agar bitim Stop-Loss (`SL`) ga tegsa, bot o'sha aktivda **45 daqiqa (3 ta M15 shamcha)**
   davomida sovish rejimiga (`cooldown_until`) o'tadi — kechagidek SL bo'lishi bilan 2 daqiqadan keyin yana o'sha
   yo'nalishga qayta kirib ketmaydi!
3) Ham `SELL`, ham `BUY` bitimlarni `PENDING -> TRIGGERED -> BREAKEVEN -> TP1 -> TP2 -> TP3` bo'yicha to'liq kuzatadi!
"""
import asyncio
import argparse
from datetime import datetime, timezone, timedelta
import json
import logging
import os
import time
from typing import Any

from config import SYMBOLS, NEWS_CHECK_INTERVAL_SECONDS, TIMEZONE_OFFSET_HOURS
from data_feed import MarketDataCollector
from news_scraper import MacroNewsScraper
from fundamental_agent import FundamentalAgent
from technical_agent import TechnicalAgent
from risk_manager import RiskManager
from telegram_bot import (
    TelegramCopilotNotifier,
    PerformanceStatsTracker,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("AI_Gold_Sniper")

STATE_FILE = "active_setups_state.json"
SL_COOLDOWN_SECONDS = 45 * 60  # SL dan keyin 45 daqiqa (3 ta M15 shamcha) yangi struktura shakllanishini kutish!


class MultiAgentTradingOrchestrator:
    """
    PRO v4.0 24/7 fon rejimida ishlovchi, faqat bozorda haqiqiy A+ qulay vaziyat bo'lganda signal beruvchi
    va SL dan keyin 45 daqiqa Cooldown saqlovchi orkestrator.
    """

    def __init__(
        self,
        use_mt5: bool = False,
        enforce_killzone: bool = False,
        sniper_only: bool = True,
        gold_only: bool = False,
    ):
        self.collector = MarketDataCollector(use_mt5=use_mt5)
        self.scraper = MacroNewsScraper()
        self.fundamental_agent = FundamentalAgent()
        self.technical_agent = TechnicalAgent()
        self.risk_manager = RiskManager(enforce_killzone=enforce_killzone)
        self.notifier = TelegramCopilotNotifier()
        self.stats_tracker = PerformanceStatsTracker()
        self.sniper_only = sniper_only
        self.gold_only = gold_only
        self.active_setups: dict[str, dict[str, Any]] = self._load_state()
        self.sl_cooldown_until: dict[str, float] = {}
        self.last_check_time: str = "Hali tekshirilmadi"
        self.last_prices: dict[str, float] = {}
        self.total_cycles: int = 0

    @staticmethod
    def _load_state() -> dict[str, dict[str, Any]]:
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_state(self) -> None:
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(self.active_setups, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.debug(f"Holatni saqlashda xato: {e}")

    @staticmethod
    def is_forex_market_open() -> bool:
        tashkent_now = datetime.now(timezone(timedelta(hours=TIMEZONE_OFFSET_HOURS)))
        wd = tashkent_now.weekday()
        hr = tashkent_now.hour
        if wd == 5 and hr >= 2:
            return False
        if wd == 6:
            return False
        if wd == 0 and hr < 3:
            return False
        return True

    async def handle_telegram_commands(self) -> None:
        cmds = await asyncio.to_thread(self.notifier.poll_commands)
        for cmd in cmds:
            if "/start" in cmd:
                welcome = (
                    "🚀 *PRO v4.0 AI Gold & BTC Snayper Boti Ishlamoqda!*\n\n"
                    "Bot faqatgina bozorda **haqiqiy A+ qulay vaziyat** (Makro + Jonli Kapital Oqimi + SMC Order Block) "
                    "shakllanganda avtomatik signal yuboradi.\n\n"
                    "📌 *Mavjud Buyruqlar:*\n"
                    "• `/stats` — Nechta signal TP1, TP2, TP3, Sof B/U yoki SL bo'lganini ko'rish\n"
                    "• `/status` — Botning jonli holati, oxirgi narxlar va aktiv qulflangan bitimlar\n"
                    "• `/clear` — Eski tarixni va qulflarni `0` ga tozalash"
                )
                await asyncio.to_thread(self.notifier.send_text_update, welcome)
            elif "/clear" in cmd or "/reset" in cmd:
                self.stats_tracker.clear_history()
                self.active_setups.clear()
                self.sl_cooldown_until.clear()
                self._save_state()
                msg = (
                    "🧹 *Statistika, Cooldown va Aktiv Qulflar 100% tozalandi (`0 ta signal`)!*\n"
                    "Endi bozorda keyingi haqiqiy A+ vaziyat chiqishi bilan `🆔 SIGNAL #001` dan hisoblanadi."
                )
                await asyncio.to_thread(self.notifier.send_text_update, msg)
            elif "/stats" in cmd:
                report = self.stats_tracker.format_full_stats_report()
                await asyncio.to_thread(self.notifier.send_text_update, report)
            elif "/status" in cmd:
                m = self.stats_tracker.compute_metrics()
                locked = (
                    ", ".join(
                        f"#{v.get('signal_id', 0):03d} {k} ({v.get('direction')} {v.get('status')} @ {v.get('entry_price')})"
                        for k, v in self.active_setups.items()
                    )
                    or "Yo'q (Bozorda haqiqiy A+ vaziyat kutilmoqda)"
                )
                status_msg = (
                    f"🤖 *PRO v4.0 AI Snayper Jonli Holati:*\n"
                    f"• *Bozor tekshirildi (1-daqiqalik skanerlash):* `{self.total_cycles} marta`\n"
                    f"• *Jami yuborilgan signallar:* `{m['total_signals']} ta` (Aktiv: `{m['active_count']}` | Yopilgan: `{m['closed_count']}`)\n"
                    f"• *Oxirgi tekshiruv:* `{self.last_check_time}`\n"
                    f"• *Jonli narxlar:* `{json.dumps(self.last_prices)}`\n"
                    f"• *Aktiv qulflangan bitimlar:* `{locked}`\n"
                    f"{self.stats_tracker.get_summary_badge()}"
                )
                await asyncio.to_thread(self.notifier.send_text_update, status_msg)

    async def _monitor_existing_setup(self, symbol: str, current_price: float) -> bool:
        """
        PRO v4.0 Ikki Tomonlama (`SELL` va `BUY`) Bitim va Aniq B/U Kuzatuvchisi:
        PENDING -> TRIGGERED -> BREAKEVEN -> TP1_HIT -> TP2_HIT -> TP3_FULL!
        """
        setup = self.active_setups.get(symbol)
        if not setup:
            return False

        now_ts = time.time()
        age_minutes = (now_ts - float(setup.get("created_ts", now_ts))) / 60.0
        sig_id = int(setup.get("signal_id", 1))
        direction = setup["direction"]
        entry = float(setup["entry_price"])
        zone_low = float(setup["zone_low"])
        zone_high = float(setup["zone_high"])
        sl = float(setup["sl_price"])
        tp1 = float(setup["recommended_tp"])
        tp2 = float(setup.get("tp2_price", tp1))
        tp3 = float(setup.get("tp3_price", tp2))
        status = setup.get("status", "PENDING")

        if status == "PENDING" and age_minutes >= 240:
            logger.info(f"[SIGNAL #{sig_id:03d} {symbol}] Pending setup 4 soatda ishga tushmadi — bekor qilindi.")
            self.stats_tracker.record_outcome(symbol, "CANCELLED")
            del self.active_setups[symbol]
            self._save_state()
            return False

        is_sell = direction == "SELL"
        triggered_cond = (current_price >= zone_low) if is_sell else (current_price <= zone_high)
        missed_cond = (current_price <= tp1) if is_sell else (current_price >= tp1)
        sl_hit_cond = (current_price >= sl) if is_sell else (current_price <= sl)
        tp3_hit_cond = (current_price <= tp3) if is_sell else (current_price >= tp3)
        tp2_hit_cond = (current_price <= tp2) if is_sell else (current_price >= tp2)
        tp1_hit_cond = (current_price <= tp1) if is_sell else (current_price >= tp1)
        halfway = (entry - 0.5 * (entry - tp1)) if is_sell else (entry + 0.5 * (tp1 - entry))
        bu_hit_cond = (current_price <= halfway) if is_sell else (current_price >= halfway)

        # 1) PENDING -> TRIGGERED
        if status == "PENDING" and triggered_cond:
            setup["status"] = "TRIGGERED"
            self._save_state()
            self.stats_tracker.record_triggered(symbol)
            msg = (
                f"⚡ *SIGNAL #{sig_id:03d} ({symbol}) — {direction} LIMIT ISHGA TUSHDI!*\n"
                f"📍 *Joriy Narx:* `{current_price:.2f}` (Entry: `{entry:.2f}` | Zona: `{zone_low:.2f}–{zone_high:.2f}`)\n"
                f"🛑 *Stop-Loss:* `{sl:.2f}`\n"
                f"🎯 *Maqsadlar:* TP1 `{tp1:.2f}` | TP2 `{tp2:.2f}` | TP3 `{tp3:.2f}`"
            )
            await asyncio.to_thread(self.notifier.send_text_update, msg)
            return True

        # Agar Limitga tegmasdan to'g'ri TP1 ga ketib qolsa
        if status == "PENDING" and missed_cond:
            self.stats_tracker.record_outcome(symbol, "CANCELLED")
            msg = (
                f"⚪ *SIGNAL #{sig_id:03d} ({symbol}) — LIMITGA KELMADI:*\n"
                f"Narx (`{current_price:.2f}`) kirish nuqtasiga (`{entry:.2f}`) qaytmasdan to'g'ri TP1 (`{tp1:.2f}`) ga yetib bordi. "
                f"Eski Limit order bekor qilindi, yangi struktura kutilmoqda."
            )
            await asyncio.to_thread(self.notifier.send_text_update, msg)
            del self.active_setups[symbol]
            self._save_state()
            return False

        # 2) AKTIV BITIMNI KUZATISH
        if status in ("TRIGGERED", "BREAKEVEN", "TP1_HIT", "TP2_HIT"):
            if sl_hit_cond:
                if status == "TP2_HIT":
                    _, pnl = self.stats_tracker.record_outcome(symbol, "TP2_REACHED")
                    msg = (
                        f"🚀 *SIGNAL #{sig_id:03d} ({symbol}) YAKUNLANDI — TP1 VA TP2 OLINDI (+${pnl:.2f}), QOLGAN 20% B/U DA YOPILDI!*\n"
                        f"• Ushbu signal statistikada *🚀 TP2 gacha bordi* deb qayd etildi.\n"
                        f"{self.stats_tracker.get_summary_badge()}"
                    )
                elif status == "TP1_HIT":
                    _, pnl = self.stats_tracker.record_outcome(symbol, "TP1_ONLY")
                    msg = (
                        f"✅ *SIGNAL #{sig_id:03d} ({symbol}) YAKUNLANDI — FAQAT TP1 OLINDI (+${pnl:.2f}), QOLGAN 50% B/U DA YOPILDI!*\n"
                        f"• Ushbu signal statistikada *✅ Faqat TP1 gacha bordi* deb qayd etildi.\n"
                        f"{self.stats_tracker.get_summary_badge()}"
                    )
                elif status == "BREAKEVEN":
                    _, pnl = self.stats_tracker.record_outcome(symbol, "PURE_BU")
                    msg = (
                        f"🛡️ *SIGNAL #{sig_id:03d} ({symbol}) — SOF BEZUBITOKDA (B/U = $0.00) YOPILDI!*\n"
                        f"• Narx TP1 ga yetmasdan 50% yo'ldan orqaga qaytib, kirish nuqtangizda (`{entry:.2f}`) 0% zarar bilan yopildi.\n"
                        f"{self.stats_tracker.get_summary_badge()}"
                    )
                else:
                    _, pnl = self.stats_tracker.record_outcome(symbol, "SL_HIT")
                    # SL bo'lganda 45 daqiqa Cooldown yoqamiz (2 daqiqadan keyin yana SL yemaslik uchun!)
                    self.sl_cooldown_until[symbol] = time.time() + SL_COOLDOWN_SECONDS
                    msg = (
                        f"❌ *SIGNAL #{sig_id:03d} ({symbol}) — STOP-LOSS ({sl:.2f}) URILDI (${pnl:+.2f}).*\n"
                        f"⏸️ *Anti-Revenge Himoyasi:* Bozor strukturasi qayta shakllanishi uchun `{symbol}` da 45 daqiqa (3 ta M15 shamcha) tanaffus e'lon qilindi!\n"
                        f"{self.stats_tracker.get_summary_badge()}"
                    )
                await asyncio.to_thread(self.notifier.send_text_update, msg)
                del self.active_setups[symbol]
                self._save_state()
                return False

            if tp3_hit_cond:
                _, pnl = self.stats_tracker.record_outcome(symbol, "TP3_FULL")
                msg = (
                    f"👑 *SIGNAL #{sig_id:03d} ({symbol}) — BARCHA 3 TA TP (TP1 + TP2 + TP3 @ {tp3:.2f}) TO'LIQ URILDI! (+${pnl:.2f})* 🔥💰\n"
                    f"• Ushbu signal statistikada *👑 TP3 FULL (3/3)* deb muhrlandi!\n"
                    f"{self.stats_tracker.get_summary_badge()}"
                )
                await asyncio.to_thread(self.notifier.send_text_update, msg)
                del self.active_setups[symbol]
                self._save_state()
                return False

            if status != "TP2_HIT" and tp2_hit_cond:
                setup["status"] = "TP2_HIT"
                setup["sl_price"] = entry
                self._save_state()
                _, locked_pnl = self.stats_tracker.record_stage_progress(symbol, "TP2_HIT", max_tp_stage=2)
                msg = (
                    f"🚀 *SIGNAL #{sig_id:03d} ({symbol}) — TP2 ({tp2:.2f}) URILDI! (+${locked_pnl:.2f} naqd qilindi)* 💰\n"
                    f"👉 Pozitsiyaning yana *30%* qismini yoping (jami 80% yopildi) va qolgan *20%* qismini B/U (`{entry:.2f}`) da TP3 (`{tp3:.2f}`) gacha ushlang!"
                )
                await asyncio.to_thread(self.notifier.send_text_update, msg)
                return True

            if status not in ("TP1_HIT", "TP2_HIT") and tp1_hit_cond:
                setup["status"] = "TP1_HIT"
                setup["sl_price"] = entry
                self._save_state()
                _, locked_pnl = self.stats_tracker.record_stage_progress(symbol, "TP1_HIT", max_tp_stage=1)
                msg = (
                    f"✅ *SIGNAL #{sig_id:03d} ({symbol}) — GARANT TP1 ({tp1:.2f}) URILDI! (+${locked_pnl:.2f} naqd qilindi)* 💰\n"
                    f"👉 Pozitsiyaning *50%* qismini hozir foydada yoping, SL ni `{entry:.2f}` (B/U) ga qo'yib, qolganini TP2 (`{tp2:.2f}`) va TP3 (`{tp3:.2f}`) gacha ushlang!"
                )
                await asyncio.to_thread(self.notifier.send_text_update, msg)
                return True

            if status == "TRIGGERED" and bu_hit_cond:
                setup["status"] = "BREAKEVEN"
                setup["sl_price"] = entry
                self._save_state()
                self.stats_tracker.record_stage_progress(symbol, "BREAKEVEN", max_tp_stage=0)
                msg = (
                    f"🛡️ *SIGNAL #{sig_id:03d} ({symbol}) — BEZUBITOK (B/U) VAQTI!*\n"
                    f"Narx TP1 yo'lining yarmini (`{current_price:.2f}`) bosib o'tdi. Stop-Loss'ni `{sl:.2f}` dan "
                    f"kirish nuqtangizga (`{entry:.2f}`) ko'chiring (100% risksiz rejim)!"
                )
                await asyncio.to_thread(self.notifier.send_text_update, msg)
                return True

        logger.info(
            f"[SIGNAL #{sig_id:03d} {symbol}] 🔒 Aktiv ({direction} {status}): Entry={entry:.2f} | SL={sl:.2f} | TP1={tp1:.2f} | TP2={tp2:.2f} | TP3={tp3:.2f} | Joriy={current_price:.2f}"
        )
        return True

    async def analyze_symbol(
        self,
        symbol: str,
        imminent_news: bool,
    ) -> None:
        if "XAU" in symbol.upper() and not self.is_forex_market_open():
            logger.info("[XAUUSD] ⏸️ Dam olish kuni — Oltin (Forex) bozori yopiq. Dushanba 03:00 UZT da avtomatik davom etadi.")
            return

        snapshot = await asyncio.to_thread(self.collector.get_snapshot, symbol)
        cp = round(snapshot.current_price, 2)
        self.last_prices[symbol] = cp

        is_locked = await self._monitor_existing_setup(symbol, cp)
        if is_locked:
            return

        # SL Cooldown tekshiruvi (SL dan keyin 45 daqiqa davomida shoshilib qayta kirmaslik!)
        cooldown_exp = self.sl_cooldown_until.get(symbol, 0.0)
        if time.time() < cooldown_exp:
            rem_min = int((cooldown_exp - time.time()) // 60) + 1
            logger.info(
                f"[{symbol}] ⏸️ SL Cooldown rejimida ({rem_min} daqiqa qoldi). Bozor strukturasi barqarorlashishi kutilmoqda..."
            )
            return

        headlines = await asyncio.to_thread(self.scraper.fetch_headlines, 8, symbol)
        fundamental_sig = await asyncio.to_thread(
            self.fundamental_agent.analyze, snapshot, headlines, imminent_news
        )
        tech_setup = await asyncio.to_thread(self.technical_agent.find_setup, snapshot, fundamental_sig)
        final_signal = self.risk_manager.evaluate_trade(fundamental_sig, tech_setup)

        if self.sniper_only and (not tech_setup.is_ideal_setup or not final_signal.is_approved):
            reason_str = final_signal.rejection_reason or "Ideal A+ Setup kutilmoqda"
            logger.info(
                f"[{symbol}] Kuzatuvda: Narx={cp:.2f} | Bias={fundamental_sig.bias} ({fundamental_sig.confidence_score:+.2f}) | "
                f"Score={tech_setup.confluence_score}/100. ({reason_str})"
            )
            return

        sig_id = self.stats_tracker.next_signal_id()
        final_signal.signal_id = sig_id
        stats_badge = self.stats_tracker.get_summary_badge()

        print("\n" + self.notifier.format_signal_message(final_signal, stats_badge=stats_badge) + "\n")
        if final_signal.is_approved:
            rec_id = self.stats_tracker.record_new_signal(
                symbol=symbol,
                direction=tech_setup.direction,
                order_type=tech_setup.order_type,
                entry_price=tech_setup.entry_price,
                sl_price=tech_setup.sl_price,
                tp1_price=tech_setup.recommended_tp,
                tp2_price=tech_setup.tp_price,
                tp3_price=tech_setup.tp3_price,
                risk_usd=final_signal.risk_amount_usd,
            )
            final_signal.signal_id = rec_id
            await asyncio.to_thread(
                self.notifier.send_signal, final_signal, stats_badge
            )
            initial_status = "TRIGGERED" if "MARKET" in tech_setup.order_type else "PENDING"
            self.active_setups[symbol] = {
                "signal_id": rec_id,
                "symbol": symbol,
                "direction": tech_setup.direction,
                "order_type": tech_setup.order_type,
                "entry_price": tech_setup.entry_price,
                "zone_low": tech_setup.entry_zone_low,
                "zone_high": tech_setup.entry_zone_high,
                "sl_price": tech_setup.sl_price,
                "recommended_tp": tech_setup.recommended_tp,
                "tp2_price": tech_setup.tp_price,
                "tp3_price": tech_setup.tp3_price,
                "status": initial_status,
                "created_ts": time.time(),
            }
            self._save_state()

    async def _handle_http_ping(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await asyncio.wait_for(reader.read(1024), timeout=3.0)
            status_payload = {
                "status": "alive",
                "version": "PRO v4.0 Pure Institutional Text Sniper (Hard Veto + OrderFlow + SL Cooldown)",
                "forex_market_open": self.is_forex_market_open(),
                "market_scan_cycles": self.total_cycles,
                "last_check_utc": self.last_check_time,
                "last_prices": self.last_prices,
                "stats_summary": self.stats_tracker.get_summary_badge(),
                "active_locked_setups": self.active_setups,
            }
            body = json.dumps(status_payload, ensure_ascii=False).encode("utf-8")
            headers = (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: application/json; charset=utf-8\r\n"
                f"Content-Length: {len(body)}\r\n"
                "Connection: close\r\n\r\n"
            ).encode("utf-8")
            writer.write(headers + body)
            await writer.drain()
        except Exception:
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def start_keepalive_server(self) -> asyncio.AbstractServer:
        port = int(os.getenv("PORT", "8080"))
        server = await asyncio.start_server(self._handle_http_ping, host="0.0.0.0", port=port)
        logger.info(
            f"🌐 UptimeRobot / Render Keep-Alive Web Server ishga tushdi: http://0.0.0.0:{port}/health"
        )
        return server

    def _get_target_symbols(self) -> list[str]:
        if self.gold_only:
            return [SYMBOLS["GOLD"]]
        return [SYMBOLS["GOLD"], SYMBOLS["BTC"]]

    async def run_once(self) -> None:
        imminent_news, _ = await asyncio.to_thread(self.scraper.check_imminent_high_impact_news, 30)
        self.total_cycles += 1
        self.last_check_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        await self.handle_telegram_commands()
        for sym in self._get_target_symbols():
            await self.analyze_symbol(sym, imminent_news)

    async def run_forever(self) -> None:
        server = await self.start_keepalive_server()
        await asyncio.to_thread(self.notifier.init_offset)
        logger.info(
            "🎯 PRO v4.0 Institutional Snayper Boti (Rasmsiz Toza Matn + Hard Veto + OrderFlow + SL Cooldown) ishga tushdi!"
        )
        async with server:
            while True:
                try:
                    imminent_news, _ = await asyncio.to_thread(self.scraper.check_imminent_high_impact_news, 30)
                    self.total_cycles += 1
                    self.last_check_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                    for sym in self._get_target_symbols():
                        await self.analyze_symbol(sym, imminent_news)
                except Exception as e:
                    logger.error(f"Sikl ichida xatolik (bot to'xtamaydi): {e}")

                steps = max(1, int(NEWS_CHECK_INTERVAL_SECONDS // 2))
                for _ in range(steps):
                    try:
                        await self.handle_telegram_commands()
                    except Exception:
                        pass
                    await asyncio.sleep(2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PRO v4.0 Institutional AI Trading Bot")
    parser.add_argument("--once", action="store_true", help="Tahlilni bir marta ishga tushirish")
    parser.add_argument("--mt5", action="store_true", help="MetaTrader 5 terminaliga ulanish (ixtiyoriy)")
    parser.add_argument("--killzone-only", action="store_true", help="Faqat London va NY Killzone vaqtida ruxsat berish")
    parser.add_argument("--all-setups", action="store_true", help="88 balldan past standart setuplarni ham ko'rsatish")
    parser.add_argument("--gold-only", action="store_true", help="Faqat XAUUSD (Oltin) uchun signal yuborish")
    parser.add_argument("--reset-lock", action="store_true", help="Saqlangan aktiv setup qulfini tozalash")
    parser.add_argument("--clear-stats", action="store_true", help="Barcha eski test signallari tarixini 0 ga tozalash")
    parser.add_argument("--stats", action="store_true", help="To'liq TP1/TP2/TP3/B-U/SL va Signal #ID statistikasini ko'rsatish")
    args = parser.parse_args()

    if args.reset_lock and os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)

    orchestrator = MultiAgentTradingOrchestrator(
        use_mt5=args.mt5,
        enforce_killzone=args.killzone_only,
        sniper_only=not args.all_setups,
        gold_only=args.gold_only,
    )
    if args.clear_stats:
        orchestrator.stats_tracker.clear_history()
        print("🧹 Statistika tarixi 0 ga tozalandi!")
    if args.stats:
        print(orchestrator.stats_tracker.format_full_stats_report())
    elif args.once:
        asyncio.run(orchestrator.run_once())
    else:
        asyncio.run(orchestrator.run_forever())