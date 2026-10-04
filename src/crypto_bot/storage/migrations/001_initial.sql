CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY);
CREATE TABLE runs(run_id TEXT PRIMARY KEY,mode TEXT NOT NULL,baseline TEXT NOT NULL,
 floor TEXT NOT NULL,php_per_usdt TEXT NOT NULL,hashes TEXT NOT NULL,start_ms INTEGER NOT NULL,
 end_ms INTEGER,qualification_status TEXT NOT NULL DEFAULT 'OBSERVING',active INTEGER NOT NULL DEFAULT 1);
CREATE UNIQUE INDEX current_run ON runs(active) WHERE active=1;
CREATE TABLE run_state(run_id TEXT PRIMARY KEY REFERENCES runs,state TEXT NOT NULL,
 halt_reason TEXT,last_heartbeat INTEGER,last_reconciliation INTEGER,reconciled INTEGER NOT NULL DEFAULT 0);
CREATE TABLE signals(identity TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES runs,
 mode TEXT NOT NULL,strategy_hash TEXT NOT NULL,symbol TEXT NOT NULL,close_ms INTEGER NOT NULL,
 payload TEXT NOT NULL,result TEXT NOT NULL, UNIQUE(mode,strategy_hash,symbol,close_ms,run_id));
CREATE TABLE order_intents(logical_id TEXT PRIMARY KEY,client_id TEXT NOT NULL UNIQUE,
 run_id TEXT NOT NULL REFERENCES runs,signal_id TEXT NOT NULL,role TEXT NOT NULL,
 generation INTEGER NOT NULL,payload TEXT NOT NULL,evidence TEXT NOT NULL,state TEXT NOT NULL);
CREATE TABLE active_slot(slot INTEGER PRIMARY KEY CHECK(slot=1),intent_id TEXT NOT NULL UNIQUE
 REFERENCES order_intents(logical_id));
CREATE TABLE orders(client_id TEXT PRIMARY KEY REFERENCES order_intents(client_id),namespace TEXT NOT NULL,
 venue_id TEXT NOT NULL,status TEXT NOT NULL,cumulative_quantity TEXT NOT NULL,
 average_price TEXT NOT NULL,first_fill_ms INTEGER,observed_ms INTEGER NOT NULL,payload TEXT NOT NULL);
CREATE TABLE fills(environment TEXT NOT NULL,account TEXT NOT NULL,symbol TEXT NOT NULL,
 trade_id TEXT NOT NULL,order_id TEXT NOT NULL,run_id TEXT NOT NULL REFERENCES runs,
 price TEXT NOT NULL,quantity TEXT NOT NULL,commission TEXT NOT NULL,commission_asset TEXT NOT NULL,
 at_ms INTEGER NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(environment,account,symbol,trade_id));
CREATE TABLE positions(run_id TEXT PRIMARY KEY REFERENCES runs,payload TEXT NOT NULL);
CREATE TABLE income_events(environment TEXT NOT NULL,account TEXT NOT NULL,symbol TEXT NOT NULL,
 transaction_id TEXT NOT NULL,income_type TEXT NOT NULL,run_id TEXT NOT NULL REFERENCES runs,
 amount TEXT NOT NULL,asset TEXT NOT NULL,at_ms INTEGER NOT NULL,payload TEXT NOT NULL,
 PRIMARY KEY(environment,account,symbol,transaction_id,income_type));
CREATE TABLE equity_snapshots(id INTEGER PRIMARY KEY,run_id TEXT NOT NULL REFERENCES runs,
 at_ms INTEGER NOT NULL,mark_equity TEXT NOT NULL,closing_equity TEXT NOT NULL,
 floor TEXT NOT NULL,freshness TEXT NOT NULL);
CREATE TABLE control_commands(request_id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES runs,
 action TEXT NOT NULL,operator TEXT NOT NULL,at_ms INTEGER NOT NULL,state TEXT NOT NULL,evidence TEXT);
CREATE TABLE audit_events(id INTEGER PRIMARY KEY,run_id TEXT NOT NULL REFERENCES runs,
 at_ms INTEGER NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL);
CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit_events BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit_events BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TABLE indicator_checkpoints(symbol TEXT PRIMARY KEY,seed_epoch INTEGER NOT NULL,
 last_close_ms INTEGER NOT NULL,payload TEXT NOT NULL,source_hash TEXT NOT NULL);
CREATE TABLE web_sessions(token_hash TEXT PRIMARY KEY,csrf_hash TEXT NOT NULL,
 created_ms INTEGER NOT NULL,last_seen_ms INTEGER NOT NULL,revoked INTEGER NOT NULL DEFAULT 0);
INSERT INTO schema_migrations VALUES(1);
