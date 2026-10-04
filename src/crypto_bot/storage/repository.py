import hashlib
import json
import sqlite3
import uuid
from dataclasses import asdict, is_dataclass
from decimal import Decimal as D
from enum import Enum
from typing import Any

from crypto_bot.domain.enums import Mode, OrderSide, OrderState, PositionPhase, PositionSide
from crypto_bot.domain.models import (
    ApprovedSize,
    ControlCommand,
    ExecutionEvent,
    FillEvent,
    IncomeEvent,
    IndicatorState,
    OrderIntent,
    OrderObservation,
    OrderUpdate,
    Position,
    Signal,
    Trial,
)
from crypto_bot.storage.database import Database


def plain(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return plain(asdict(value))
    if isinstance(value, (D, Enum)):
        return str(value)
    if isinstance(value, dict):
        return {
            str(k): plain(v)
            for k, v in value.items()
            if not any(
                secret in str(k).lower()
                for secret in ("password", "secret", "signature", "token", "api_key", "listenkey")
            )
        }
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value


def encode(value: Any) -> str:
    return json.dumps(plain(value), sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(encode(value).encode()).hexdigest()


def make_intent(
    run_id: str,
    signal_id: str,
    role: str,
    generation: int,
    symbol: str,
    side: OrderSide,
    quantity: D | None = None,
    limit_price: D | None = None,
    trigger_price: D | None = None,
) -> OrderIntent:
    logical = f"{run_id}:{signal_id}:{role}:{generation}"
    client = "cb-" + hashlib.sha256(logical.encode()).hexdigest()[:32]
    request_hash = digest([symbol, side, quantity, limit_price, trigger_price, role])
    return OrderIntent(
        logical,
        client,
        role,
        generation,
        symbol,
        side,
        quantity,
        limit_price,
        trigger_price,
        request_hash,
        run_id,
        signal_id,
    )


def decode_intent(payload: str) -> OrderIntent:
    data = json.loads(payload)
    data["side"] = OrderSide(data["side"])
    for key in ("quantity", "limit_price", "trigger_price"):
        data[key] = D(data[key]) if data[key] is not None else None
    return OrderIntent(**data)


def decode_order(payload: str) -> OrderObservation:
    data = json.loads(payload)
    data["state"] = OrderState(data["state"])
    for key in ("cumulative_quantity", "average_price"):
        data[key] = D(data[key])
    return OrderObservation(**data)


class Repository:
    def __init__(self, database: Database) -> None:
        self.db = database

    def current_trial(self) -> Trial | None:
        row = self.db.connection.execute(
            "SELECT r.*,s.halt_reason FROM runs r JOIN run_state s USING(run_id) WHERE active=1"
        ).fetchone()
        if row is None:
            return None
        return Trial(
            row["run_id"],
            Mode(row["mode"]),
            D(row["baseline"]),
            D(row["floor"]),
            D(row["php_per_usdt"]),
            json.loads(row["hashes"]),
            row["halt_reason"],
            row["start_ms"],
            row["qualification_status"],
        )

    def create_run(
        self, mode: Mode, baseline: D, hashes: dict[str, str], at_ms: int, run_id: str | None = None
    ) -> Trial:
        if not baseline.is_finite() or baseline <= 0:
            raise ValueError("Invalid baseline")
        identity = run_id or uuid.uuid4().hex
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO runs(run_id,mode,baseline,floor,php_per_usdt,hashes,start_ms) VALUES(?,?,?,?,?,?,?)",
                (
                    identity,
                    mode.value,
                    str(baseline),
                    str(baseline * D("0.90")),
                    str(D("1000") / baseline),
                    encode(hashes),
                    at_ms,
                ),
            )
            conn.execute("INSERT INTO run_state(run_id,state) VALUES(?,'PAUSED')", (identity,))
        trial = self.current_trial()
        assert trial is not None
        return trial

    def state(self) -> dict[str, Any]:
        trial = self.current_trial()
        if trial is None:
            return {}
        return dict(
            self.db.connection.execute(
                "SELECT * FROM run_state WHERE run_id=?", (trial.run_id,)
            ).fetchone()
        )

    def latch_halt(self, run_id: str, reason: str) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE run_state SET state='HALTED',halt_reason=CASE WHEN halt_reason='TRIAL_LOSS' THEN halt_reason ELSE ? END,reconciled=0 WHERE run_id=?",
                (reason, run_id),
            )
            conn.execute(
                "INSERT INTO audit_events(run_id,at_ms,kind,payload) VALUES(?,0,'HALTED',?)",
                (run_id, encode({"reason": reason})),
            )

    def pause(self) -> None:
        trial = self.current_trial()
        if trial:
            self.db.connection.execute(
                "UPDATE run_state SET state='PAUSED' WHERE run_id=? AND halt_reason IS NULL",
                (trial.run_id,),
            )

    def resume(self, run_id: str) -> bool:
        with self.db.transaction() as conn:
            changed = conn.execute(
                "UPDATE run_state SET state='RUNNING',halt_reason=NULL WHERE run_id=? AND reconciled=1 AND (halt_reason IS NULL OR halt_reason!='TRIAL_LOSS')",
                (run_id,),
            ).rowcount
        return bool(changed)

    def mark_reconciled(self, at_ms: int, healthy: bool) -> None:
        trial = self.current_trial()
        if trial:
            self.db.connection.execute(
                "UPDATE run_state SET reconciled=?,last_reconciliation=? WHERE run_id=?",
                (int(healthy), at_ms, trial.run_id),
            )

    def reserve_entry(self, signal: Signal, size: ApprovedSize) -> OrderIntent | None:
        trial = self.current_trial()
        if trial is None or trial.halt_reason:
            return None
        side = OrderSide.BUY if signal.side is PositionSide.LONG else OrderSide.SELL
        price = size.evidence.get("limit_price", size.evidence.get("entry"))
        intent = make_intent(
            trial.run_id,
            signal.identity,
            "ENTRY",
            0,
            signal.symbol,
            side,
            size.quantity,
            D(price) if price is not None else None,
        )
        try:
            with self.db.transaction() as conn:
                if conn.execute("SELECT 1 FROM active_slot").fetchone():
                    return None
                conn.execute(
                    "INSERT INTO signals VALUES(?,?,?,?,?,?,?,?)",
                    (
                        signal.identity,
                        trial.run_id,
                        trial.mode.value,
                        signal.strategy_hash,
                        signal.symbol,
                        signal.close_ms,
                        encode(signal),
                        "ATTEMPTED",
                    ),
                )
                conn.execute(
                    "INSERT INTO order_intents VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        intent.logical_id,
                        intent.client_id,
                        trial.run_id,
                        signal.identity,
                        "ENTRY",
                        0,
                        encode(intent),
                        encode(size),
                        "PREPARED",
                    ),
                )
                conn.execute("INSERT INTO active_slot VALUES(1,?)", (intent.logical_id,))
        except sqlite3.IntegrityError:
            return None
        return intent

    def add_intent(self, intent: OrderIntent, evidence: Any = None) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO order_intents VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    intent.logical_id,
                    intent.client_id,
                    intent.run_id,
                    intent.signal_id,
                    intent.role,
                    intent.generation,
                    encode(intent),
                    encode(evidence or {}),
                    "PREPARED",
                ),
            )

    def intents(self) -> tuple[OrderIntent, ...]:
        return tuple(
            decode_intent(r[0])
            for r in self.db.connection.execute("SELECT payload FROM order_intents ORDER BY rowid")
        )

    def intent_state(self, identity: str) -> OrderState:
        return OrderState(
            self.db.connection.execute(
                "SELECT state FROM order_intents WHERE logical_id=?", (identity,)
            ).fetchone()[0]
        )

    def set_intent_state(self, identity: str, state: OrderState) -> None:
        self.db.connection.execute(
            "UPDATE order_intents SET state=? WHERE logical_id=?", (state.value, identity)
        )

    def has_active_slot(self) -> bool:
        return bool(self.db.connection.execute("SELECT 1 FROM active_slot").fetchone())

    def has_unresolved_intent(self, signal_id: str) -> bool:
        return bool(
            self.db.connection.execute(
                "SELECT 1 FROM order_intents WHERE signal_id=? AND state IN ('PREPARED','SUBMITTED','UNKNOWN','ACKNOWLEDGED','PARTIALLY_FILLED')",
                (signal_id,),
            ).fetchone()
        )

    def release_slot(self) -> None:
        self.db.connection.execute("DELETE FROM active_slot")

    def record_execution(self, event: ExecutionEvent) -> bool:
        if isinstance(event, OrderUpdate):
            self.record_order(event.observation)
            return True
        trial = self.current_trial()
        if trial is None:
            raise RuntimeError("No active run")
        with self.db.transaction() as conn:
            if isinstance(event, FillEvent):
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO fills VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        event.environment,
                        event.account,
                        event.symbol,
                        event.trade_id,
                        event.order_id,
                        trial.run_id,
                        str(event.price),
                        str(event.quantity),
                        str(event.commission),
                        event.commission_asset,
                        event.at_ms,
                        encode(event),
                    ),
                )
            else:
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO income_events VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        event.environment,
                        event.account,
                        event.symbol,
                        event.transaction_id,
                        event.income_type,
                        trial.run_id,
                        str(event.amount),
                        event.asset,
                        event.at_ms,
                        encode(event),
                    ),
                )
            return cursor.rowcount == 1

    def count_income_events(self) -> int:
        return int(self.db.connection.execute("SELECT count(*) FROM income_events").fetchone()[0])

    def income_events(self) -> tuple[IncomeEvent, ...]:
        events = []
        for row in self.db.connection.execute("SELECT payload FROM income_events ORDER BY at_ms"):
            value = json.loads(row[0])
            value["amount"] = D(value["amount"])
            events.append(IncomeEvent(**value))
        return tuple(events)

    def fills(self) -> tuple[FillEvent, ...]:
        events = []
        for row in self.db.connection.execute("SELECT payload FROM fills ORDER BY at_ms"):
            value = json.loads(row[0])
            for key in ("price", "quantity", "commission"):
                value[key] = D(value[key])
            value["side"] = OrderSide(value["side"])
            events.append(FillEvent(**value))
        return tuple(events)

    def record_order(self, observation: OrderObservation) -> None:
        old = self.order(observation.client_id)
        if old and (
            observation.cumulative_quantity < old.cumulative_quantity
            or (old.state.terminal and not observation.state.terminal)
        ):
            return
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO orders VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    observation.client_id,
                    observation.namespace,
                    observation.venue_id,
                    observation.state.value,
                    str(observation.cumulative_quantity),
                    str(observation.average_price),
                    observation.first_fill_ms,
                    observation.observed_ms,
                    encode(observation),
                ),
            )
            conn.execute(
                "UPDATE order_intents SET state=? WHERE client_id=?",
                (observation.state.value, observation.client_id),
            )

    def order(self, client_id: str) -> OrderObservation | None:
        row = self.db.connection.execute(
            "SELECT payload FROM orders WHERE client_id=?", (client_id,)
        ).fetchone()
        return decode_order(row[0]) if row else None

    def save_position(self, position: Position | None) -> None:
        trial = self.current_trial()
        assert trial is not None
        if position is None:
            self.db.connection.execute("DELETE FROM positions WHERE run_id=?", (trial.run_id,))
        else:
            self.db.connection.execute(
                "INSERT OR REPLACE INTO positions VALUES(?,?)", (trial.run_id, encode(position))
            )

    def position(self) -> Position | None:
        trial = self.current_trial()
        if trial is None:
            return None
        row = self.db.connection.execute(
            "SELECT payload FROM positions WHERE run_id=?", (trial.run_id,)
        ).fetchone()
        if row is None:
            return None
        data = json.loads(row[0])
        for key in ("quantity", "average_entry", "atr", "stop", "target", "liquidation_price"):
            data[key] = D(data[key]) if data[key] is not None else None
        data["side"] = PositionSide(data["side"])
        data["phase"] = PositionPhase(data["phase"])
        return Position(**data)

    def enqueue_command(self, command: ControlCommand) -> str:
        trial = self.current_trial()
        if trial is None:
            raise RuntimeError("No active run")
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO control_commands VALUES(?,?,?,?,?,'PENDING',NULL)",
                (command.request_id, trial.run_id, command.action, command.operator, command.at_ms),
            )
        return command.request_id

    def pending_commands(self) -> tuple[ControlCommand, ...]:
        return tuple(
            ControlCommand(r["request_id"], r["action"], r["operator"], r["at_ms"])
            for r in self.db.connection.execute(
                "SELECT * FROM control_commands WHERE state='PENDING' ORDER BY at_ms,rowid"
            )
        )

    def complete_command(self, identity: str, accepted: bool, evidence: Any) -> None:
        self.db.connection.execute(
            "UPDATE control_commands SET state=?,evidence=? WHERE request_id=?",
            ("CONFIRMED" if accepted else "REJECTED", encode(evidence), identity),
        )

    def checkpoint_indicator(self, state: IndicatorState, source_hash: str = "") -> None:
        self.db.connection.execute(
            "INSERT OR REPLACE INTO indicator_checkpoints VALUES(?,?,?,?,?)",
            (state.symbol, state.seed_epoch, state.last_close_ms, encode(state), source_hash),
        )

    def indicator(self, symbol: str) -> IndicatorState | None:
        row = self.db.connection.execute(
            "SELECT payload FROM indicator_checkpoints WHERE symbol=?", (symbol,)
        ).fetchone()
        if row is None:
            return None
        value = json.loads(row[0])
        for key in ("close_sum", "tr_sum", "previous_close", "ema", "atr"):
            value[key] = D(value[key]) if value[key] is not None else None
        return IndicatorState(**value)
