use anyhow::{Context, Result, bail};
use csv::{ReaderBuilder, WriterBuilder};
use rand::{Rng, SeedableRng};
use rand_chacha::ChaCha8Rng;
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::env;
use std::fs;
use std::io::{BufRead, BufReader, BufWriter, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};

const DAYS: [i32; 3] = [-2, -1, 0];
const PRODUCTS: [&str; 2] = ["ASH_COATED_OSMIUM", "INTARIAN_PEPPER_ROOT"];
const DEFAULT_TICKS_PER_DAY: usize = 10_000;
const TIMESTAMP_STEP: i32 = 100;
const POSITION_LIMIT: i32 = 80;
const STRATEGY_RUN_TIMEOUT_MS: u64 = 900;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum FvMode {
    Replay,
    Simulate,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum TradeMode {
    ReplayTimes,
    Simulate,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum TomatoSupport {
    Continuous,
    Half,
    Quarter,
}

#[derive(Clone, Debug)]
struct Config {
    output_dir: PathBuf,
    actual_dir: PathBuf,
    calibration_path: PathBuf,
    fv_mode: FvMode,
    trade_mode: TradeMode,
    tomato_support: TomatoSupport,
    seed: u64,
    strategy_path: Option<PathBuf>,
    python_bin: String,
    sessions: usize,
    write_session_limit: usize,
    ticks_per_day: usize,
}

#[derive(Clone, Debug)]
struct ReplayData {
    series_by_key: HashMap<(i32, String), ProductDaySeries>,
    pooled_trade_events: HashMap<String, Vec<ObservedTradeEvent>>,
    trade_count_samples: HashMap<String, Vec<usize>>,
}

#[derive(Clone, Debug)]
struct ProductDaySeries {
    ticks: Vec<ObservedTick>,
    trade_events: Vec<Vec<ObservedTradeEvent>>,
}

#[allow(dead_code)]
#[derive(Clone, Debug, Deserialize)]
struct ProductCalibration {
    stationary_anchor: f64,
    drift_per_tick: f64,
    fair_step_vol: f64,
    active_timestamp_rate: f64,
    trades_per_active_step_mean: f64,
    spread_mode: i32,
    touch_rate: f64,
    sweep_rate_visible: f64,
    queue_ahead_factor: f64,
    block_len_min: usize,
    block_len_max: usize,
    candidate_search_count: usize,
    continuity_weight: f64,
    trend_weight: f64,
    trend_horizon: usize,
    outage_fill_prob_50: Option<f64>,
    outage_fill_prob_100: Option<f64>,
    outage_fill_size_min: Option<i32>,
    outage_fill_size_max: Option<i32>,
}

#[derive(Clone, Debug)]
struct ObservedTick {
    book: Book,
    fair: f64,
    mid: f64,
}

#[derive(Clone, Copy, Debug)]
struct ObservedTradeEvent {
    market_buy: bool,
    quantity: i32,
}

#[derive(Clone, Debug)]
struct SampledTick {
    observed: ObservedTick,
    source_day: i32,
    source_index: usize,
}

#[derive(Clone, Debug)]
struct DayOutput {
    day: i32,
    price_rows: Vec<PriceRow>,
    trade_rows: Vec<TradeRow>,
    trace_rows: Vec<TraceRow>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum LevelOwner {
    Bot,
    Strategy,
}

#[derive(Clone, Debug)]
struct Level {
    price: i32,
    quantity: i32,
    owner: LevelOwner,
    queue_ahead: i32,
}

#[derive(Clone, Debug)]
struct SimBook {
    bids: Vec<Level>,
    asks: Vec<Level>,
}

#[derive(Clone, Debug)]
struct Fill {
    symbol: String,
    price: i32,
    quantity: i32,
    buyer: Option<String>,
    seller: Option<String>,
    timestamp: i32,
}

#[derive(Clone, Debug, Default)]
struct ProductLedger {
    position: i32,
    cash: f64,
}

#[derive(Clone, Copy, Debug, Default)]
struct RunningLinearFit {
    n: f64,
    sum_x: f64,
    sum_y: f64,
    sum_xx: f64,
    sum_yy: f64,
    sum_xy: f64,
}

impl RunningLinearFit {
    fn update(&mut self, x: f64, y: f64) {
        self.n += 1.0;
        self.sum_x += x;
        self.sum_y += y;
        self.sum_xx += x * x;
        self.sum_yy += y * y;
        self.sum_xy += x * y;
    }

    fn slope_per_step(&self) -> f64 {
        let denom = self.n * self.sum_xx - self.sum_x * self.sum_x;
        if denom.abs() < 1e-12 {
            0.0
        } else {
            (self.n * self.sum_xy - self.sum_x * self.sum_y) / denom
        }
    }

    fn r_squared(&self) -> f64 {
        let x_var = self.n * self.sum_xx - self.sum_x * self.sum_x;
        let y_var = self.n * self.sum_yy - self.sum_y * self.sum_y;
        if x_var.abs() < 1e-12 || y_var.abs() < 1e-12 {
            0.0
        } else {
            let cov = self.n * self.sum_xy - self.sum_x * self.sum_y;
            (cov * cov) / (x_var * y_var)
        }
    }
}

#[derive(Clone, Debug, Serialize)]
struct SessionSummary {
    session_id: usize,
    total_pnl: f64,
    ash_coated_osmium_pnl: f64,
    intarian_pepper_root_pnl: f64,
    ash_coated_osmium_position: i32,
    intarian_pepper_root_position: i32,
    ash_coated_osmium_cash: f64,
    intarian_pepper_root_cash: f64,
    total_slope_per_step: f64,
    total_r2: f64,
    ash_coated_osmium_slope_per_step: f64,
    ash_coated_osmium_r2: f64,
    intarian_pepper_root_slope_per_step: f64,
    intarian_pepper_root_r2: f64,
}

#[derive(Clone, Debug, Serialize)]
struct RunSummary {
    session_id: usize,
    day: i32,
    total_pnl: f64,
    ash_coated_osmium_pnl: f64,
    intarian_pepper_root_pnl: f64,
    total_slope_per_step: f64,
    total_r2: f64,
    ash_coated_osmium_slope_per_step: f64,
    ash_coated_osmium_r2: f64,
    intarian_pepper_root_slope_per_step: f64,
    intarian_pepper_root_r2: f64,
}

#[derive(Clone, Debug)]
struct SessionOutput {
    session_id: usize,
    summary: SessionSummary,
    run_summaries: Vec<RunSummary>,
    day_outputs: Vec<DayOutput>,
}

#[derive(Clone, Debug)]
struct Book {
    bids: Vec<(i32, i32)>,
    asks: Vec<(i32, i32)>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
struct InputPriceRow {
    day: i32,
    timestamp: i32,
    product: String,
    bid_price_1: Option<i32>,
    bid_volume_1: Option<i32>,
    bid_price_2: Option<i32>,
    bid_volume_2: Option<i32>,
    bid_price_3: Option<i32>,
    bid_volume_3: Option<i32>,
    ask_price_1: Option<i32>,
    ask_volume_1: Option<i32>,
    ask_price_2: Option<i32>,
    ask_volume_2: Option<i32>,
    ask_price_3: Option<i32>,
    ask_volume_3: Option<i32>,
    mid_price: f64,
    profit_and_loss: f64,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
struct InputTradeRow {
    timestamp: i32,
    buyer: Option<String>,
    seller: Option<String>,
    symbol: String,
    currency: String,
    price: f64,
    quantity: i32,
}

#[derive(Clone, Debug, Serialize)]
struct PriceRow {
    day: i32,
    timestamp: i32,
    product: String,
    bid_price_1: Option<i32>,
    bid_volume_1: Option<i32>,
    bid_price_2: Option<i32>,
    bid_volume_2: Option<i32>,
    bid_price_3: Option<i32>,
    bid_volume_3: Option<i32>,
    ask_price_1: Option<i32>,
    ask_volume_1: Option<i32>,
    ask_price_2: Option<i32>,
    ask_volume_2: Option<i32>,
    ask_price_3: Option<i32>,
    ask_volume_3: Option<i32>,
    mid_price: f64,
    profit_and_loss: f64,
}

#[derive(Clone, Debug, Serialize)]
struct TradeRow {
    timestamp: i32,
    buyer: Option<String>,
    seller: Option<String>,
    symbol: String,
    currency: String,
    price: f64,
    quantity: i32,
}

#[derive(Clone, Debug, Serialize)]
struct TraceRow {
    day: i32,
    timestamp: i32,
    product: String,
    fair_value: f64,
    position: i32,
    cash: f64,
    mtm_pnl: f64,
}

#[derive(Debug, Serialize)]
struct WorkerTrade {
    symbol: String,
    price: i32,
    quantity: i32,
    buyer: Option<String>,
    seller: Option<String>,
    timestamp: i32,
}

#[derive(Debug, Serialize)]
struct WorkerOrderDepth {
    buy_orders: HashMap<String, i32>,
    sell_orders: HashMap<String, i32>,
}

#[derive(Debug, Serialize)]
struct WorkerRequest {
    #[serde(rename = "type")]
    request_type: String,
    timestamp: i32,
    timeout_ms: u64,
    trader_data: String,
    order_depths: HashMap<String, WorkerOrderDepth>,
    own_trades: HashMap<String, Vec<WorkerTrade>>,
    market_trades: HashMap<String, Vec<WorkerTrade>>,
    position: HashMap<String, i32>,
}

#[allow(dead_code)]
#[derive(Clone, Debug, Deserialize)]
struct WorkerOrder {
    symbol: String,
    price: i32,
    quantity: i32,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
struct WorkerResponse {
    orders: Option<HashMap<String, Vec<WorkerOrder>>>,
    conversions: Option<i32>,
    trader_data: Option<String>,
    stdout: Option<String>,
    error: Option<String>,
}

impl Config {
    fn from_args() -> Result<Self> {
        let mut config = Config {
            output_dir: PathBuf::from("../tmp/rust_simulator_output"),
            actual_dir: PathBuf::from("../data/round1"),
            calibration_path: PathBuf::from("config/round1_product_calibration.json"),
            fv_mode: FvMode::Replay,
            trade_mode: TradeMode::ReplayTimes,
            tomato_support: TomatoSupport::Continuous,
            seed: 20_260_401,
            strategy_path: None,
            python_bin: "python3".to_string(),
            sessions: 1,
            write_session_limit: 0,
            ticks_per_day: DEFAULT_TICKS_PER_DAY,
        };

        let mut args = env::args().skip(1);
        while let Some(arg) = args.next() {
            match arg.as_str() {
                "--output" => {
                    config.output_dir =
                        PathBuf::from(args.next().context("missing value for --output")?);
                }
                "--actual-dir" => {
                    config.actual_dir =
                        PathBuf::from(args.next().context("missing value for --actual-dir")?);
                }
                "--calibration" => {
                    config.calibration_path =
                        PathBuf::from(args.next().context("missing value for --calibration")?);
                }
                "--fv-mode" => {
                    let value = args.next().context("missing value for --fv-mode")?;
                    config.fv_mode = match value.as_str() {
                        "replay" => FvMode::Replay,
                        "simulate" => FvMode::Simulate,
                        other => bail!("unsupported --fv-mode {}", other),
                    };
                }
                "--trade-mode" => {
                    let value = args.next().context("missing value for --trade-mode")?;
                    config.trade_mode = match value.as_str() {
                        "replay-times" => TradeMode::ReplayTimes,
                        "simulate" => TradeMode::Simulate,
                        other => bail!("unsupported --trade-mode {}", other),
                    };
                }
                "--tomato-support" => {
                    let value = args.next().context("missing value for --tomato-support")?;
                    config.tomato_support = match value.as_str() {
                        "continuous" => TomatoSupport::Continuous,
                        "0.5" | "half" => TomatoSupport::Half,
                        "0.25" | "quarter" => TomatoSupport::Quarter,
                        other => bail!("unsupported --tomato-support {}", other),
                    };
                }
                "--seed" => {
                    config.seed = args
                        .next()
                        .context("missing value for --seed")?
                        .parse()
                        .context("invalid --seed")?;
                }
                "--strategy" => {
                    config.strategy_path = Some(PathBuf::from(
                        args.next().context("missing value for --strategy")?,
                    ));
                }
                "--python-bin" => {
                    config.python_bin = args.next().context("missing value for --python-bin")?;
                }
                "--sessions" => {
                    config.sessions = args
                        .next()
                        .context("missing value for --sessions")?
                        .parse()
                        .context("invalid --sessions")?;
                }
                "--write-session-limit" => {
                    config.write_session_limit = args
                        .next()
                        .context("missing value for --write-session-limit")?
                        .parse()
                        .context("invalid --write-session-limit")?;
                }
                "--ticks-per-day" => {
                    config.ticks_per_day = args
                        .next()
                        .context("missing value for --ticks-per-day")?
                        .parse()
                        .context("invalid --ticks-per-day")?;
                }
                other => bail!("unknown argument {}", other),
            }
        }

        Ok(config)
    }
}

fn main() -> Result<()> {
    let config = Config::from_args()?;
    let calibrations = load_product_calibrations(&config.calibration_path)?;
    let replay_data = ReplayData::load(&config)?;

    if config.strategy_path.is_some() {
        let outputs = run_backtests(&config, &replay_data, &calibrations)?;
        write_backtest_outputs(&config, &outputs)?;
        write_run_log(&config)?;
        return Ok(());
    }

    let outputs = DAYS
        .par_iter()
        .map(|day| generate_day(*day, &config, &replay_data, &calibrations))
        .collect::<Result<Vec<_>>>()?;

    write_outputs(&config, &outputs)?;
    write_run_log(&config)?;
    Ok(())
}

fn load_product_calibrations(path: &Path) -> Result<HashMap<String, ProductCalibration>> {
    let raw = fs::read_to_string(path)
        .with_context(|| format!("failed to read calibration file {}", path.display()))?;
    let calibrations: HashMap<String, ProductCalibration> = serde_json::from_str(&raw)
        .with_context(|| format!("failed to parse calibration file {}", path.display()))?;
    for product in PRODUCTS {
        if !calibrations.contains_key(product) {
            bail!("missing calibration for {}", product);
        }
    }
    Ok(calibrations)
}

fn product_calibration<'a>(
    product: &str,
    calibrations: &'a HashMap<String, ProductCalibration>,
) -> Result<&'a ProductCalibration> {
    calibrations
        .get(product)
        .with_context(|| format!("missing product calibration for {}", product))
}

impl ReplayData {
    fn load(config: &Config) -> Result<Self> {
        let mut series_by_key = HashMap::new();
        let mut pooled_trade_events = HashMap::<String, Vec<ObservedTradeEvent>>::new();
        let mut trade_count_samples = HashMap::<String, Vec<usize>>::new();

        for day in DAYS {
            let prices = load_price_rows(&config.actual_dir, day)?;
            let trades = load_trade_rows(&config.actual_dir, day)?;
            let mut trades_by_key = HashMap::<(String, i32), Vec<InputTradeRow>>::new();
            for trade in trades {
                trades_by_key
                    .entry((trade.symbol.clone(), trade.timestamp))
                    .or_default()
                    .push(trade);
            }

            for product in PRODUCTS {
                let mut rows = prices
                    .iter()
                    .filter(|row| row.product == product)
                    .collect::<Vec<_>>();
                rows.sort_by_key(|row| row.timestamp);

                let ticks = rows.iter().map(|row| observed_tick_from_row(row)).collect::<Vec<_>>();
                let mut trade_events = Vec::with_capacity(rows.len());
                for (index, row) in rows.iter().enumerate() {
                    let events = infer_trade_events(
                        trades_by_key
                            .get(&(product.to_string(), row.timestamp))
                            .map(Vec::as_slice)
                            .unwrap_or(&[]),
                        &ticks[index],
                    );
                    pooled_trade_events
                        .entry(product.to_string())
                        .or_default()
                        .extend(events.iter().copied());
                    trade_count_samples
                        .entry(product.to_string())
                        .or_default()
                        .push(events.len());
                    trade_events.push(events);
                }

                series_by_key.insert(
                    (day, product.to_string()),
                    ProductDaySeries {
                        ticks,
                        trade_events,
                    },
                );
            }
        }

        Ok(Self { series_by_key, pooled_trade_events, trade_count_samples })
    }
}

fn generate_day(
    day: i32,
    config: &Config,
    replay: &ReplayData,
    calibrations: &HashMap<String, ProductCalibration>,
) -> Result<DayOutput> {
    let mut rng = ChaCha8Rng::seed_from_u64(seed_for_day(config.seed, day));
    let ash_sequence =
        sampled_tick_sequence("ASH_COATED_OSMIUM", day, config, replay, calibrations, &mut rng)?;
    let pepper_sequence = sampled_tick_sequence(
        "INTARIAN_PEPPER_ROOT",
        day,
        config,
        replay,
        calibrations,
        &mut rng,
    )?;

    let mut price_rows = Vec::with_capacity(config.ticks_per_day * PRODUCTS.len());
    let mut trade_rows = Vec::new();

    for tick in 0..config.ticks_per_day {
        let timestamp = (tick as i32) * TIMESTAMP_STEP;
        let ash_tick = &ash_sequence[tick];
        let pepper_tick = &pepper_sequence[tick];
        let ash_book = ash_tick.observed.book.clone();
        let pepper_book = pepper_tick.observed.book.clone();

        price_rows.push(book_to_price_row(day, timestamp, "ASH_COATED_OSMIUM", &ash_book));
        price_rows.push(book_to_price_row(day, timestamp, "INTARIAN_PEPPER_ROOT", &pepper_book));

        let mut ash_live = book_to_sim_book(&ash_book);
        for event in trade_events_for_tick(
            "ASH_COATED_OSMIUM",
            day,
            tick,
            config,
            replay,
            calibrations,
            Some(ash_tick),
            &mut rng,
        )? {
            let fills = execute_taker_trade_quantity(
                "ASH_COATED_OSMIUM",
                timestamp,
                &mut ash_live,
                &mut ProductLedger::default(),
                event.market_buy,
                event.quantity,
            );
            trade_rows.extend(fills.iter().map(fill_to_trade_row));
        }
        let mut pepper_live = book_to_sim_book(&pepper_book);
        for event in trade_events_for_tick(
            "INTARIAN_PEPPER_ROOT",
            day,
            tick,
            config,
            replay,
            calibrations,
            Some(pepper_tick),
            &mut rng,
        )? {
            let fills = execute_taker_trade_quantity(
                "INTARIAN_PEPPER_ROOT",
                timestamp,
                &mut pepper_live,
                &mut ProductLedger::default(),
                event.market_buy,
                event.quantity,
            );
            trade_rows.extend(fills.iter().map(fill_to_trade_row));
        }
    }

    price_rows.sort_by(|a, b| {
        a.timestamp
            .cmp(&b.timestamp)
            .then(a.product.cmp(&b.product))
    });
    trade_rows.sort_by(|a, b| a.timestamp.cmp(&b.timestamp).then(a.symbol.cmp(&b.symbol)));

    Ok(DayOutput {
        day,
        price_rows,
        trade_rows,
        trace_rows: Vec::new(),
    })
}

fn write_outputs(config: &Config, outputs: &[DayOutput]) -> Result<()> {
    let round_dir = config.output_dir.join("round1");
    fs::create_dir_all(&round_dir)
        .with_context(|| format!("failed to create {}", round_dir.display()))?;

    for output in outputs {
        let price_path = round_dir.join(format!("prices_round_1_day_{}.csv", output.day));
        let trade_path = round_dir.join(format!("trades_round_1_day_{}.csv", output.day));

        let mut price_writer = WriterBuilder::new()
            .delimiter(b';')
            .from_path(&price_path)
            .with_context(|| format!("failed to open {}", price_path.display()))?;
        for row in &output.price_rows {
            price_writer.serialize(row)?;
        }
        price_writer.flush()?;

        let mut trade_writer = WriterBuilder::new()
            .delimiter(b';')
            .from_path(&trade_path)
            .with_context(|| format!("failed to open {}", trade_path.display()))?;
        for row in &output.trade_rows {
            trade_writer.serialize(row)?;
        }
        trade_writer.flush()?;
    }

    Ok(())
}

struct StrategyWorker {
    child: Child,
    stdin: BufWriter<ChildStdin>,
    stdout: BufReader<ChildStdout>,
}

impl StrategyWorker {
    fn spawn(config: &Config) -> Result<Self> {
        let strategy_path = config
            .strategy_path
            .as_ref()
            .context("missing strategy path")?
            .canonicalize()
            .with_context(|| "failed to canonicalize strategy path")?;
        let project_root = env::var("PROSPERITY4MCBT_ROOT")
            .map(PathBuf::from)
            .or_else(|_| {
                env::current_dir().map(|cwd| {
                    cwd.parent()
                        .map(Path::to_path_buf)
                        .unwrap_or(cwd)
                })
            })
            .context("failed to resolve project root for python strategy worker")?;
        let worker_path = project_root.join("scripts/python_strategy_worker.py");
        if !worker_path.is_file() {
            bail!(
                "python strategy worker not found at {}",
                worker_path.display()
            );
        }

        let mut child = Command::new(&config.python_bin)
            .arg(worker_path)
            .arg(strategy_path)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .spawn()
            .context("failed to spawn python strategy worker")?;

        let stdin = BufWriter::new(child.stdin.take().context("missing worker stdin")?);
        let stdout = BufReader::new(child.stdout.take().context("missing worker stdout")?);

        Ok(Self {
            child,
            stdin,
            stdout,
        })
    }

    fn reset(&mut self) -> Result<()> {
        let payload = serde_json::json!({ "type": "reset" });
        self.send(&payload)?;
        let response = self.read_response()?;
        if let Some(error) = response.error {
            bail!("python worker reset failed: {}", error);
        }
        Ok(())
    }

    fn run(&mut self, request: &WorkerRequest) -> Result<WorkerResponse> {
        self.send(request)?;
        let response = self.read_response()?;
        if let Some(error) = &response.error {
            bail!("python worker failed: {}", error);
        }
        Ok(response)
    }

    fn send<T: Serialize>(&mut self, payload: &T) -> Result<()> {
        serde_json::to_writer(&mut self.stdin, payload)?;
        self.stdin.write_all(b"\n")?;
        self.stdin.flush()?;
        Ok(())
    }

    fn read_response(&mut self) -> Result<WorkerResponse> {
        let mut line = String::new();
        let bytes = self.stdout.read_line(&mut line)?;
        if bytes == 0 {
            bail!("python worker exited unexpectedly");
        }
        let response = serde_json::from_str::<WorkerResponse>(line.trim())
            .context("failed to decode python worker response")?;
        Ok(response)
    }
}

impl Drop for StrategyWorker {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

fn run_backtests(
    config: &Config,
    replay: &ReplayData,
    calibrations: &HashMap<String, ProductCalibration>,
) -> Result<Vec<SessionOutput>> {
    let mut outputs = (0..config.sessions)
        .into_par_iter()
        .map(|session_id| {
            run_backtest_session(
                session_id,
                session_id < config.write_session_limit,
                config,
                replay,
                calibrations,
            )
        })
        .collect::<Result<Vec<_>>>()?;
    outputs.sort_by_key(|output| output.session_id);
    Ok(outputs)
}

fn monte_carlo_session_day(session_id: usize) -> i32 {
    DAYS[session_id % DAYS.len()]
}

fn run_backtest_session(
    session_id: usize,
    capture_outputs: bool,
    config: &Config,
    replay: &ReplayData,
    calibrations: &HashMap<String, ProductCalibration>,
) -> Result<SessionOutput> {
    let mut worker = StrategyWorker::spawn(config)?;
    let mut day_outputs = Vec::with_capacity(1);
    let mut ash_total = 0.0;
    let mut pepper_total = 0.0;
    let mut ash_cash_total = 0.0;
    let mut pepper_cash_total = 0.0;
    let mut ash_final_position = 0;
    let mut pepper_final_position = 0;
    let mut total_fit = RunningLinearFit::default();
    let mut ash_fit = RunningLinearFit::default();
    let mut pepper_fit = RunningLinearFit::default();
    let mut global_step = 0usize;
    let mut run_summaries = Vec::with_capacity(1);
    let session_day = monte_carlo_session_day(session_id);

    for day in [session_day] {
        worker.reset()?;
        let mut rng = ChaCha8Rng::seed_from_u64(seed_for_session_day(config.seed, session_id, day));
        let ash_sequence =
            sampled_tick_sequence("ASH_COATED_OSMIUM", day, config, replay, calibrations, &mut rng)?;
        let pepper_sequence = sampled_tick_sequence(
            "INTARIAN_PEPPER_ROOT",
            day,
            config,
            replay,
            calibrations,
            &mut rng,
        )?;

        let mut ledgers = HashMap::from([
            ("ASH_COATED_OSMIUM".to_string(), ProductLedger::default()),
            ("INTARIAN_PEPPER_ROOT".to_string(), ProductLedger::default()),
        ]);
        let mut trader_data = String::new();
        let mut prev_own_trades = empty_trade_map();
        let mut prev_market_trades = empty_trade_map();
        let mut day_total_fit = RunningLinearFit::default();
        let mut day_ash_fit = RunningLinearFit::default();
        let mut day_pepper_fit = RunningLinearFit::default();
        let mut day_step = 0usize;
        let mut price_rows = if capture_outputs {
            Vec::with_capacity(config.ticks_per_day * PRODUCTS.len())
        } else {
            Vec::new()
        };
        let mut trade_rows = Vec::new();
        let mut trace_rows = Vec::new();

        for tick in 0..config.ticks_per_day {
            let timestamp = (tick as i32) * TIMESTAMP_STEP;
            let ash_tick = &ash_sequence[tick];
            let pepper_tick = &pepper_sequence[tick];
            let ash_book = ash_tick.observed.book.clone();
            let pepper_book = pepper_tick.observed.book.clone();

            if capture_outputs {
                price_rows.push(book_to_price_row(day, timestamp, "ASH_COATED_OSMIUM", &ash_book));
                price_rows.push(book_to_price_row(day, timestamp, "INTARIAN_PEPPER_ROOT", &pepper_book));
            }

            let order_depths = HashMap::from([
                ("ASH_COATED_OSMIUM".to_string(), book_to_worker_depth(&ash_book)),
                ("INTARIAN_PEPPER_ROOT".to_string(), book_to_worker_depth(&pepper_book)),
            ]);
            let position = ledgers
                .iter()
                .map(|(product, ledger)| (product.clone(), ledger.position))
                .collect::<HashMap<_, _>>();
            let request = WorkerRequest {
                request_type: "run".to_string(),
                timestamp,
                timeout_ms: STRATEGY_RUN_TIMEOUT_MS,
                trader_data: trader_data.clone(),
                order_depths,
                own_trades: fills_to_worker_trade_map(&prev_own_trades),
                market_trades: fills_to_worker_trade_map(&prev_market_trades),
                position,
            };
            let response = worker.run(&request)?;
            trader_data = response.trader_data.unwrap_or_default();

            let mut live_books = HashMap::from([
                ("ASH_COATED_OSMIUM".to_string(), book_to_sim_book(&ash_book)),
                ("INTARIAN_PEPPER_ROOT".to_string(), book_to_sim_book(&pepper_book)),
            ]);
            let strategy_orders = normalize_strategy_orders(response.orders.unwrap_or_default());
            let filtered_orders = enforce_strategy_limits(&strategy_orders, &ledgers);

            let mut own_trades_this_tick = empty_trade_map();
            let mut market_trades_this_tick = empty_trade_map();

            for product in PRODUCTS {
                let product_key = product.to_string();
                let orders = filtered_orders
                    .get(product)
                    .cloned()
                    .unwrap_or_default();
                let book = live_books
                    .get_mut(&product_key)
                    .context("missing live book")?;
                let ledger = ledgers.get_mut(&product_key).context("missing ledger")?;
                let fills =
                    execute_strategy_orders(product, timestamp, book, ledger, &orders, calibrations)?;
                if capture_outputs {
                    trade_rows.extend(fills.iter().map(fill_to_trade_row));
                }
                own_trades_this_tick.insert(product_key.clone(), fills);
            }

            {
                let product_key = "INTARIAN_PEPPER_ROOT".to_string();
                let book = live_books
                    .get_mut(&product_key)
                    .context("missing pepper live book for outage response")?;
                let ledger = ledgers
                    .get_mut(&product_key)
                    .context("missing pepper ledger for outage response")?;
                let had_bids = !pepper_book.bids.is_empty();
                let had_asks = !pepper_book.asks.is_empty();
                let outage_fills = execute_pepper_outage_response(
                    timestamp,
                    book,
                    ledger,
                    pepper_tick.observed.fair,
                    had_bids,
                    had_asks,
                    calibrations,
                    &mut rng,
                )?;
                if capture_outputs {
                    trade_rows.extend(outage_fills.iter().map(fill_to_trade_row));
                }
                own_trades_this_tick
                    .entry(product_key.clone())
                    .or_default()
                    .extend(outage_fills);
            }

            for product in PRODUCTS {
                let product_key = product.to_string();
                let book = live_books
                    .get_mut(&product_key)
                    .context("missing live book for taker execution")?;
                let ledger = ledgers.get_mut(&product_key).context("missing ledger for taker execution")?;
                let sampled_tick = if product == "ASH_COATED_OSMIUM" {
                    Some(ash_tick)
                } else {
                    Some(pepper_tick)
                };
                for event in trade_events_for_tick(
                    product,
                    day,
                    tick,
                    config,
                    replay,
                    calibrations,
                    sampled_tick,
                    &mut rng,
                )? {
                    let fills = execute_taker_trade_quantity(
                        product,
                        timestamp,
                        book,
                        ledger,
                        event.market_buy,
                        event.quantity,
                    );
                    for fill in fills {
                        let row = fill_to_trade_row(&fill);
                        if fill_involves_strategy(&fill) {
                            own_trades_this_tick.entry(product_key.clone()).or_default().push(fill);
                        } else {
                            market_trades_this_tick.entry(product_key.clone()).or_default().push(fill);
                        }
                        if capture_outputs {
                            trade_rows.push(row);
                        }
                    }
                }
            }

            if capture_outputs {
                for product in PRODUCTS {
                    let product_key = product.to_string();
                    let ledger = ledgers.get(&product_key).context("missing ledger for trace")?;
                    let fair = if product == "ASH_COATED_OSMIUM" {
                        ash_tick.observed.fair
                    } else {
                        pepper_tick.observed.fair
                    };
                    trace_rows.push(TraceRow {
                        day,
                        timestamp,
                        product: product_key,
                        fair_value: fair,
                        position: ledger.position,
                        cash: ledger.cash,
                        mtm_pnl: ledger.cash + ledger.position as f64 * fair,
                    });
                }
            }

            let ash_ledger = ledgers
                .get("ASH_COATED_OSMIUM")
                .context("missing ash ledger for fit")?;
            let pepper_ledger = ledgers
                .get("INTARIAN_PEPPER_ROOT")
                .context("missing pepper ledger for fit")?;
            let ash_mtm = ash_ledger.cash + ash_ledger.position as f64 * ash_tick.observed.fair;
            let pepper_mtm = pepper_ledger.cash + pepper_ledger.position as f64 * pepper_tick.observed.fair;
            let session_x = global_step as f64;
            let day_x = day_step as f64;
            ash_fit.update(session_x, ash_mtm);
            pepper_fit.update(session_x, pepper_mtm);
            total_fit.update(session_x, ash_mtm + pepper_mtm);
            day_ash_fit.update(day_x, ash_mtm);
            day_pepper_fit.update(day_x, pepper_mtm);
            day_total_fit.update(day_x, ash_mtm + pepper_mtm);
            global_step += 1;
            day_step += 1;

            prev_own_trades = own_trades_this_tick;
            prev_market_trades = market_trades_this_tick;
        }

        let ash_fair = ash_sequence.last().map(|state| state.observed.fair).unwrap_or(10_000.0);
        let pepper_fair = pepper_sequence
            .last()
            .map(|state| state.observed.fair)
            .unwrap_or(10_000.0);
        let ash_ledger = ledgers
            .get("ASH_COATED_OSMIUM")
            .context("missing ash ledger")?;
        let pepper_ledger = ledgers
            .get("INTARIAN_PEPPER_ROOT")
            .context("missing pepper ledger")?;
        let ash_pnl = ash_ledger.cash + ash_ledger.position as f64 * ash_fair;
        let pepper_pnl = pepper_ledger.cash + pepper_ledger.position as f64 * pepper_fair;

        ash_total += ash_pnl;
        pepper_total += pepper_pnl;
        ash_cash_total += ash_ledger.cash;
        pepper_cash_total += pepper_ledger.cash;
        ash_final_position = ash_ledger.position;
        pepper_final_position = pepper_ledger.position;

        run_summaries.push(RunSummary {
            session_id,
            day,
            total_pnl: ash_pnl + pepper_pnl,
            ash_coated_osmium_pnl: ash_pnl,
            intarian_pepper_root_pnl: pepper_pnl,
            total_slope_per_step: day_total_fit.slope_per_step(),
            total_r2: day_total_fit.r_squared(),
            ash_coated_osmium_slope_per_step: day_ash_fit.slope_per_step(),
            ash_coated_osmium_r2: day_ash_fit.r_squared(),
            intarian_pepper_root_slope_per_step: day_pepper_fit.slope_per_step(),
            intarian_pepper_root_r2: day_pepper_fit.r_squared(),
        });

        day_outputs.push(DayOutput {
            day,
            price_rows,
            trade_rows,
            trace_rows,
        });
    }

    let summary = SessionSummary {
        session_id,
        total_pnl: ash_total + pepper_total,
        ash_coated_osmium_pnl: ash_total,
        intarian_pepper_root_pnl: pepper_total,
        ash_coated_osmium_position: ash_final_position,
        intarian_pepper_root_position: pepper_final_position,
        ash_coated_osmium_cash: ash_cash_total,
        intarian_pepper_root_cash: pepper_cash_total,
        total_slope_per_step: total_fit.slope_per_step(),
        total_r2: total_fit.r_squared(),
        ash_coated_osmium_slope_per_step: ash_fit.slope_per_step(),
        ash_coated_osmium_r2: ash_fit.r_squared(),
        intarian_pepper_root_slope_per_step: pepper_fit.slope_per_step(),
        intarian_pepper_root_r2: pepper_fit.r_squared(),
    };

    Ok(SessionOutput {
        session_id,
        summary,
        run_summaries,
        day_outputs,
    })
}

fn write_backtest_outputs(config: &Config, outputs: &[SessionOutput]) -> Result<()> {
    fs::create_dir_all(&config.output_dir)?;
    let summary_path = config.output_dir.join("session_summary.csv");
    let mut writer = WriterBuilder::new()
        .delimiter(b',')
        .from_path(&summary_path)
        .with_context(|| format!("failed to open {}", summary_path.display()))?;
    for output in outputs {
        writer.serialize(&output.summary)?;
    }
    writer.flush()?;

    let run_summary_path = config.output_dir.join("run_summary.csv");
    let mut run_writer = WriterBuilder::new()
        .delimiter(b',')
        .from_path(&run_summary_path)
        .with_context(|| format!("failed to open {}", run_summary_path.display()))?;
    for output in outputs {
        for run_summary in &output.run_summaries {
            run_writer.serialize(run_summary)?;
        }
    }
    run_writer.flush()?;

    for output in outputs.iter().take(config.write_session_limit) {
        let round_dir = config
            .output_dir
            .join("sessions")
            .join(format!("session_{:05}", output.session_id))
            .join("round1");
        fs::create_dir_all(&round_dir)?;
        for day_output in &output.day_outputs {
            let price_path = round_dir.join(format!("prices_round_1_day_{}.csv", day_output.day));
            let trade_path = round_dir.join(format!("trades_round_1_day_{}.csv", day_output.day));
            let trace_path = round_dir.join(format!("trace_round_1_day_{}.csv", day_output.day));
            let mut price_writer = WriterBuilder::new().delimiter(b';').from_path(&price_path)?;
            for row in &day_output.price_rows {
                price_writer.serialize(row)?;
            }
            price_writer.flush()?;

            let mut trade_writer = WriterBuilder::new().delimiter(b';').from_path(&trade_path)?;
            for row in &day_output.trade_rows {
                trade_writer.serialize(row)?;
            }
            trade_writer.flush()?;

            let mut trace_writer = WriterBuilder::new().delimiter(b';').from_path(&trace_path)?;
            for row in &day_output.trace_rows {
                trace_writer.serialize(row)?;
            }
            trace_writer.flush()?;
        }
    }

    Ok(())
}

fn write_run_log(config: &Config) -> Result<()> {
    let log_path = config.output_dir.join("run.log");
    let contents = format!(
        "seed={}\nfv_mode={:?}\ntrade_mode={:?}\ntomato_support={:?}\nactual_dir={}\nstrategy={}\nsessions={}\nwrite_session_limit={}\n",
        config.seed,
        config.fv_mode,
        config.trade_mode,
        config.tomato_support,
        config.actual_dir.display()
        ,
        config
            .strategy_path
            .as_ref()
            .map(|path| path.display().to_string())
            .unwrap_or_else(|| "".to_string()),
        config.sessions,
        config.write_session_limit,
    );
    fs::create_dir_all(&config.output_dir)?;
    fs::write(&log_path, contents)
        .with_context(|| format!("failed to write {}", log_path.display()))?;
    Ok(())
}

fn seed_for_day(seed: u64, day: i32) -> u64 {
    let mut value = seed ^ (day as i64 as u64).wrapping_mul(0x9E37_79B9_7F4A_7C15);
    value ^= value >> 33;
    value = value.wrapping_mul(0xFF51_AFD7_ED55_8CCD);
    value ^= value >> 33;
    value
}

fn seed_for_session_day(seed: u64, session_id: usize, day: i32) -> u64 {
    seed_for_day(seed ^ ((session_id as u64).wrapping_mul(0xA24B_AED4_963E_E407)), day)
}

fn empty_trade_map() -> HashMap<String, Vec<Fill>> {
    HashMap::from([
        ("ASH_COATED_OSMIUM".to_string(), Vec::new()),
        ("INTARIAN_PEPPER_ROOT".to_string(), Vec::new()),
    ])
}

fn fills_to_worker_trade_map(source: &HashMap<String, Vec<Fill>>) -> HashMap<String, Vec<WorkerTrade>> {
    PRODUCTS
        .iter()
        .map(|product| {
            let trades = source
                .get(*product)
                .cloned()
                .unwrap_or_default()
                .into_iter()
                .map(|fill| WorkerTrade {
                    symbol: fill.symbol,
                    price: fill.price,
                    quantity: fill.quantity,
                    buyer: fill.buyer,
                    seller: fill.seller,
                    timestamp: fill.timestamp,
                })
                .collect::<Vec<_>>();
            ((*product).to_string(), trades)
        })
        .collect()
}

fn book_to_worker_depth(book: &Book) -> WorkerOrderDepth {
    let buy_orders = book
        .bids
        .iter()
        .map(|(price, qty)| (price.to_string(), *qty))
        .collect::<HashMap<_, _>>();
    let sell_orders = book
        .asks
        .iter()
        .map(|(price, qty)| (price.to_string(), -*qty))
        .collect::<HashMap<_, _>>();
    WorkerOrderDepth {
        buy_orders,
        sell_orders,
    }
}

fn book_to_sim_book(book: &Book) -> SimBook {
    SimBook {
        bids: book
            .bids
            .iter()
            .map(|(price, quantity)| Level {
                price: *price,
                quantity: *quantity,
                owner: LevelOwner::Bot,
                queue_ahead: 0,
            })
            .collect(),
        asks: book
            .asks
            .iter()
            .map(|(price, quantity)| Level {
                price: *price,
                quantity: *quantity,
                owner: LevelOwner::Bot,
                queue_ahead: 0,
            })
            .collect(),
    }
}

fn normalize_strategy_orders(
    raw: HashMap<String, Vec<WorkerOrder>>,
) -> HashMap<String, Vec<WorkerOrder>> {
    PRODUCTS
        .iter()
        .map(|product| {
            (
                (*product).to_string(),
                raw.get(*product).cloned().unwrap_or_default(),
            )
        })
        .collect()
}

fn enforce_strategy_limits(
    orders: &HashMap<String, Vec<WorkerOrder>>,
    ledgers: &HashMap<String, ProductLedger>,
) -> HashMap<String, Vec<WorkerOrder>> {
    orders
        .iter()
        .map(|(product, product_orders)| {
            let current_position = ledgers.get(product).map(|ledger| ledger.position).unwrap_or(0);
            let total_buy: i32 = product_orders
                .iter()
                .filter(|order| order.quantity > 0)
                .map(|order| order.quantity)
                .sum();
            let total_sell: i32 = product_orders
                .iter()
                .filter(|order| order.quantity < 0)
                .map(|order| -order.quantity)
                .sum();

            let limit = position_limit(product);
            let accepted = if current_position + total_buy > limit
                || current_position - total_sell < -limit
            {
                Vec::new()
            } else {
                product_orders.clone()
            };

            (product.clone(), accepted)
        })
        .collect()
}

fn execute_strategy_orders(
    product: &str,
    timestamp: i32,
    book: &mut SimBook,
    ledger: &mut ProductLedger,
    orders: &[WorkerOrder],
    calibrations: &HashMap<String, ProductCalibration>,
) -> Result<Vec<Fill>> {
    let mut fills = Vec::new();
    let mut passive_bids: HashMap<i32, i32> = HashMap::new();
    let mut passive_asks: HashMap<i32, i32> = HashMap::new();

    for order in orders {
        if order.quantity > 0 {
            let mut remaining = order.quantity;
            while remaining > 0 {
                let Some(best_ask) = book.asks.first_mut() else {
                    break;
                };
                if best_ask.owner != LevelOwner::Bot || best_ask.price > order.price {
                    break;
                }
                let fill_qty = remaining.min(best_ask.quantity);
                fills.push(Fill {
                    symbol: product.to_string(),
                    price: best_ask.price,
                    quantity: fill_qty,
                    buyer: Some("SUBMISSION".to_string()),
                    seller: Some("BOT".to_string()),
                    timestamp,
                });
                ledger.position += fill_qty;
                ledger.cash -= best_ask.price as f64 * fill_qty as f64;
                remaining -= fill_qty;
                best_ask.quantity -= fill_qty;
                if best_ask.quantity == 0 {
                    book.asks.remove(0);
                }
            }
            if remaining > 0 {
                *passive_bids.entry(order.price).or_insert(0) += remaining;
            }
        } else if order.quantity < 0 {
            let mut remaining = -order.quantity;
            while remaining > 0 {
                let Some(best_bid) = book.bids.first_mut() else {
                    break;
                };
                if best_bid.owner != LevelOwner::Bot || best_bid.price < order.price {
                    break;
                }
                let fill_qty = remaining.min(best_bid.quantity);
                fills.push(Fill {
                    symbol: product.to_string(),
                    price: best_bid.price,
                    quantity: fill_qty,
                    buyer: Some("BOT".to_string()),
                    seller: Some("SUBMISSION".to_string()),
                    timestamp,
                });
                ledger.position -= fill_qty;
                ledger.cash += best_bid.price as f64 * fill_qty as f64;
                remaining -= fill_qty;
                best_bid.quantity -= fill_qty;
                if best_bid.quantity == 0 {
                    book.bids.remove(0);
                }
            }
            if remaining > 0 {
                *passive_asks.entry(order.price).or_insert(0) += remaining;
            }
        }
    }

    for (price, quantity) in passive_bids {
        let queue_ahead = queue_ahead_quantity(product, price, &book.bids, true, calibrations)?;
        insert_level(
            &mut book.bids,
            Level {
                price,
                quantity,
                owner: LevelOwner::Strategy,
                queue_ahead,
            },
            true,
        );
    }
    for (price, quantity) in passive_asks {
        let queue_ahead = queue_ahead_quantity(product, price, &book.asks, false, calibrations)?;
        insert_level(
            &mut book.asks,
            Level {
                price,
                quantity,
                owner: LevelOwner::Strategy,
                queue_ahead,
            },
            false,
        );
    }

    Ok(fills)
}

fn execute_taker_trade_quantity(
    product: &str,
    timestamp: i32,
    book: &mut SimBook,
    ledger: &mut ProductLedger,
    market_buy: bool,
    quantity: i32,
) -> Vec<Fill> {
    let mut fills = Vec::new();
    let available_volume = if market_buy {
        book.asks.iter().map(|level| level.quantity + level.queue_ahead).sum()
    } else {
        book.bids.iter().map(|level| level.quantity + level.queue_ahead).sum()
    };
    if available_volume <= 0 {
        return fills;
    }

    let mut remaining = quantity.min(available_volume);

    while remaining > 0 {
        let (price, owner, fill_qty) = if market_buy {
            let Some(best_ask) = book.asks.first_mut() else {
                break;
            };
            if best_ask.owner == LevelOwner::Strategy && best_ask.queue_ahead > 0 {
                let consume_ahead = remaining.min(best_ask.queue_ahead);
                best_ask.queue_ahead -= consume_ahead;
                remaining -= consume_ahead;
                if best_ask.queue_ahead == 0 && best_ask.quantity == 0 {
                    book.asks.remove(0);
                }
                continue;
            }
            let fill_qty = remaining.min(best_ask.quantity);
            let price = best_ask.price;
            let owner = best_ask.owner;
            best_ask.quantity -= fill_qty;
            if best_ask.quantity == 0 {
                book.asks.remove(0);
            }
            (price, owner, fill_qty)
        } else {
            let Some(best_bid) = book.bids.first_mut() else {
                break;
            };
            if best_bid.owner == LevelOwner::Strategy && best_bid.queue_ahead > 0 {
                let consume_ahead = remaining.min(best_bid.queue_ahead);
                best_bid.queue_ahead -= consume_ahead;
                remaining -= consume_ahead;
                if best_bid.queue_ahead == 0 && best_bid.quantity == 0 {
                    book.bids.remove(0);
                }
                continue;
            }
            let fill_qty = remaining.min(best_bid.quantity);
            let price = best_bid.price;
            let owner = best_bid.owner;
            best_bid.quantity -= fill_qty;
            if best_bid.quantity == 0 {
                book.bids.remove(0);
            }
            (price, owner, fill_qty)
        };

        if fill_qty <= 0 {
            break;
        }

        let fill = match (market_buy, owner) {
            (true, LevelOwner::Bot) => Fill {
                symbol: product.to_string(),
                price,
                quantity: fill_qty,
                buyer: Some("BOT_TAKER".to_string()),
                seller: Some("BOT_MAKER".to_string()),
                timestamp,
            },
            (true, LevelOwner::Strategy) => {
                ledger.position -= fill_qty;
                ledger.cash += price as f64 * fill_qty as f64;
                Fill {
                    symbol: product.to_string(),
                    price,
                    quantity: fill_qty,
                    buyer: Some("BOT_TAKER".to_string()),
                    seller: Some("SUBMISSION".to_string()),
                    timestamp,
                }
            }
            (false, LevelOwner::Bot) => Fill {
                symbol: product.to_string(),
                price,
                quantity: fill_qty,
                buyer: Some("BOT_MAKER".to_string()),
                seller: Some("BOT_TAKER".to_string()),
                timestamp,
            },
            (false, LevelOwner::Strategy) => {
                ledger.position += fill_qty;
                ledger.cash -= price as f64 * fill_qty as f64;
                Fill {
                    symbol: product.to_string(),
                    price,
                    quantity: fill_qty,
                    buyer: Some("SUBMISSION".to_string()),
                    seller: Some("BOT_TAKER".to_string()),
                    timestamp,
                }
            }
        };
        fills.push(fill);
        remaining -= fill_qty;
    }

    fills
}

fn execute_pepper_outage_response(
    timestamp: i32,
    book: &mut SimBook,
    ledger: &mut ProductLedger,
    fair: f64,
    had_bids: bool,
    had_asks: bool,
    calibrations: &HashMap<String, ProductCalibration>,
    rng: &mut ChaCha8Rng,
) -> Result<Vec<Fill>> {
    if had_bids == had_asks {
        return Ok(Vec::new());
    }

    let calibration = product_calibration("INTARIAN_PEPPER_ROOT", calibrations)?;
    let prob_50 = calibration.outage_fill_prob_50.unwrap_or(0.02).clamp(0.0, 1.0);
    let prob_100 = calibration.outage_fill_prob_100.unwrap_or(0.045).clamp(0.0, 1.0);
    let size_min = calibration.outage_fill_size_min.unwrap_or(4).max(1);
    let size_max = calibration.outage_fill_size_max.unwrap_or(6).max(size_min);

    if !had_asks {
        let Some(best_ask) = book.asks.first_mut() else {
            return Ok(Vec::new());
        };
        if best_ask.owner != LevelOwner::Strategy {
            return Ok(Vec::new());
        }
        let edge = best_ask.price as f64 - fair;
        let fill_prob = if edge <= 50.0 {
            prob_50
        } else if edge <= 100.0 {
            prob_100
        } else {
            0.0
        };
        if fill_prob <= 0.0 || !rng.gen_bool(fill_prob) {
            return Ok(Vec::new());
        }
        let wanted = rng.gen_range(size_min..=size_max);
        let fill_qty = wanted.min(best_ask.quantity);
        if fill_qty <= 0 {
            return Ok(Vec::new());
        }
        let price = best_ask.price;
        best_ask.quantity -= fill_qty;
        if best_ask.quantity == 0 {
            book.asks.remove(0);
        }
        ledger.position -= fill_qty;
        ledger.cash += price as f64 * fill_qty as f64;
        return Ok(vec![Fill {
            symbol: "INTARIAN_PEPPER_ROOT".to_string(),
            price,
            quantity: fill_qty,
            buyer: Some("BOT_OUTAGE_ASK".to_string()),
            seller: Some("SUBMISSION".to_string()),
            timestamp,
        }]);
    }

    let Some(best_bid) = book.bids.first_mut() else {
        return Ok(Vec::new());
    };
    if best_bid.owner != LevelOwner::Strategy {
        return Ok(Vec::new());
    }
    let edge = fair - best_bid.price as f64;
    let fill_prob = if edge <= 50.0 {
        prob_50
    } else if edge <= 100.0 {
        prob_100
    } else {
        0.0
    };
    if fill_prob <= 0.0 || !rng.gen_bool(fill_prob) {
        return Ok(Vec::new());
    }
    let wanted = rng.gen_range(size_min..=size_max);
    let fill_qty = wanted.min(best_bid.quantity);
    if fill_qty <= 0 {
        return Ok(Vec::new());
    }
    let price = best_bid.price;
    best_bid.quantity -= fill_qty;
    if best_bid.quantity == 0 {
        book.bids.remove(0);
    }
    ledger.position += fill_qty;
    ledger.cash -= price as f64 * fill_qty as f64;
    Ok(vec![Fill {
        symbol: "INTARIAN_PEPPER_ROOT".to_string(),
        price,
        quantity: fill_qty,
        buyer: Some("SUBMISSION".to_string()),
        seller: Some("BOT_OUTAGE_BID".to_string()),
        timestamp,
    }])
}

fn insert_level(levels: &mut Vec<Level>, level: Level, descending: bool) {
    if let Some(existing) = levels
        .iter_mut()
        .find(|existing| existing.price == level.price && existing.owner == level.owner)
    {
        existing.quantity += level.quantity;
    } else {
        levels.push(level);
    }
    if descending {
        levels.sort_by(|a, b| b.price.cmp(&a.price).then(owner_priority(a.owner).cmp(&owner_priority(b.owner))));
    } else {
        levels.sort_by(|a, b| a.price.cmp(&b.price).then(owner_priority(a.owner).cmp(&owner_priority(b.owner))));
    }
}

fn owner_priority(owner: LevelOwner) -> i32 {
    match owner {
        LevelOwner::Bot => 0,
        LevelOwner::Strategy => 1,
    }
}

fn position_limit(_product: &str) -> i32 {
    POSITION_LIMIT
}

fn queue_ahead_factor(
    product: &str,
    calibrations: &HashMap<String, ProductCalibration>,
) -> Result<f64> {
    Ok(product_calibration(product, calibrations)?.queue_ahead_factor)
}

fn queue_ahead_quantity(
    product: &str,
    price: i32,
    levels: &[Level],
    is_bid: bool,
    calibrations: &HashMap<String, ProductCalibration>,
) -> Result<i32> {
    let better_price_exists = if is_bid {
        levels.iter().any(|level| level.price > price)
    } else {
        levels.iter().any(|level| level.price < price)
    };
    if better_price_exists {
        return Ok(0);
    }

    let visible_ahead = levels
        .iter()
        .filter(|level| level.owner == LevelOwner::Bot && level.price == price)
        .map(|level| level.quantity)
        .sum::<i32>();
    Ok(((visible_ahead as f64) * queue_ahead_factor(product, calibrations)?).round() as i32)
}

fn fill_involves_strategy(fill: &Fill) -> bool {
    fill.buyer.as_deref() == Some("SUBMISSION") || fill.seller.as_deref() == Some("SUBMISSION")
}

fn fill_to_trade_row(fill: &Fill) -> TradeRow {
    TradeRow {
        timestamp: fill.timestamp,
        buyer: fill.buyer.clone(),
        seller: fill.seller.clone(),
        symbol: fill.symbol.clone(),
        currency: "XIRECS".to_string(),
        price: fill.price as f64,
        quantity: fill.quantity,
    }
}

fn load_price_rows(actual_dir: &Path, day: i32) -> Result<Vec<InputPriceRow>> {
    let path = actual_dir.join(format!("prices_round_1_day_{}.csv", day));
    let mut reader = ReaderBuilder::new()
        .delimiter(b';')
        .from_path(&path)
        .with_context(|| format!("failed to read {}", path.display()))?;
    let mut rows = Vec::new();
    for record in reader.deserialize() {
        let row: InputPriceRow = record?;
        rows.push(row);
    }
    Ok(rows)
}

fn load_trade_rows(actual_dir: &Path, day: i32) -> Result<Vec<InputTradeRow>> {
    let path = actual_dir.join(format!("trades_round_1_day_{}.csv", day));
    let mut reader = ReaderBuilder::new()
        .delimiter(b';')
        .from_path(&path)
        .with_context(|| format!("failed to read {}", path.display()))?;
    let mut rows = Vec::new();
    for record in reader.deserialize() {
        let row: InputTradeRow = record?;
        rows.push(row);
    }
    Ok(rows)
}

fn observed_tick_from_row(row: &InputPriceRow) -> ObservedTick {
    let book = book_from_row(row);
    let fair = infer_fair_from_row(row);
    let mid = if let (Some((bid, _)), Some((ask, _))) = (book.bids.first(), book.asks.first()) {
        (*bid as f64 + *ask as f64) / 2.0
    } else {
        row.mid_price
    };
    ObservedTick { book, fair, mid }
}

fn book_from_row(row: &InputPriceRow) -> Book {
    let mut bids = Vec::new();
    let mut asks = Vec::new();

    for (price, volume) in [
        (row.bid_price_1, row.bid_volume_1),
        (row.bid_price_2, row.bid_volume_2),
        (row.bid_price_3, row.bid_volume_3),
    ] {
        if let (Some(price), Some(volume)) = (price, volume) {
            if volume > 0 {
                bids.push((price, volume));
            }
        }
    }

    for (price, volume) in [
        (row.ask_price_1, row.ask_volume_1),
        (row.ask_price_2, row.ask_volume_2),
        (row.ask_price_3, row.ask_volume_3),
    ] {
        if let (Some(price), Some(volume)) = (price, volume) {
            if volume > 0 {
                asks.push((price, volume));
            }
        }
    }

    Book { bids, asks }
}

fn infer_fair_from_row(row: &InputPriceRow) -> f64 {
    match (row.bid_price_1, row.ask_price_1, row.bid_volume_1, row.ask_volume_1) {
        (Some(bid), Some(ask), Some(bid_volume), Some(ask_volume)) if bid_volume + ask_volume > 0 => {
            (ask as f64 * bid_volume as f64 + bid as f64 * ask_volume as f64)
                / (bid_volume + ask_volume) as f64
        }
        (Some(bid), Some(ask), _, _) => (bid as f64 + ask as f64) / 2.0,
        (Some(bid), None, _, _) => bid as f64,
        (None, Some(ask), _, _) => ask as f64,
        _ => row.mid_price,
    }
}

fn infer_trade_events(rows: &[InputTradeRow], tick: &ObservedTick) -> Vec<ObservedTradeEvent> {
    rows.iter()
        .map(|trade| ObservedTradeEvent {
            market_buy: infer_trade_side(trade.price, tick),
            quantity: trade.quantity.max(1),
        })
        .collect()
}

fn infer_trade_side(price: f64, tick: &ObservedTick) -> bool {
    let best_bid = tick.book.bids.first().map(|(level_price, _)| *level_price as f64);
    let best_ask = tick.book.asks.first().map(|(level_price, _)| *level_price as f64);
    match (best_bid, best_ask) {
        (Some(_), Some(ask)) if price >= ask => true,
        (Some(bid), Some(_)) if price <= bid => false,
        (Some(bid), Some(ask)) => {
            let dist_to_ask = (ask - price).abs();
            let dist_to_bid = (price - bid).abs();
            if dist_to_ask < dist_to_bid {
                true
            } else if dist_to_bid < dist_to_ask {
                false
            } else {
                price >= tick.mid
            }
        }
        (None, Some(_)) => true,
        (Some(_), None) => false,
        (None, None) => true,
    }
}

fn shift_observed_tick(source: &ObservedTick, fair_shift: f64) -> ObservedTick {
    let shift_price = |price: i32| -> i32 { (price as f64 + fair_shift).round() as i32 };
    let mut book = Book {
        bids: source
            .book
            .bids
            .iter()
            .map(|(price, qty)| (shift_price(*price), *qty))
            .collect(),
        asks: source
            .book
            .asks
            .iter()
            .map(|(price, qty)| (shift_price(*price), *qty))
            .collect(),
    };
    book.bids.sort_by(|a, b| b.0.cmp(&a.0));
    book.asks.sort_by(|a, b| a.0.cmp(&b.0));

    let fair = source.fair + fair_shift;
    let mid = if let (Some((bid, _)), Some((ask, _))) = (book.bids.first(), book.asks.first()) {
        (*bid as f64 + *ask as f64) / 2.0
    } else {
        source.mid + fair_shift
    };
    ObservedTick { book, fair, mid }
}

fn series_for_product_day<'a>(
    replay: &'a ReplayData,
    product: &str,
    day: i32,
) -> Result<&'a ProductDaySeries> {
    replay
        .series_by_key
        .get(&(day, product.to_string()))
        .with_context(|| format!("missing series for {} day {}", product, day))
}

fn pooled_fair_increments(product: &str, replay: &ReplayData) -> Result<Vec<f64>> {
    let mut increments = Vec::new();
    for day in DAYS {
        let series = series_for_product_day(replay, product, day)?;
        for window in series.ticks.windows(2) {
            increments.push(window[1].fair - window[0].fair);
        }
    }
    if increments.is_empty() {
        bail!("no fair increments for {}", product);
    }
    Ok(increments)
}

fn sampled_open_fair(product: &str, replay: &ReplayData, rng: &mut ChaCha8Rng) -> Result<f64> {
    let day = DAYS[rng.gen_range(0..DAYS.len())];
    let series = series_for_product_day(replay, product, day)?;
    Ok(series
        .ticks
        .first()
        .map(|tick| tick.fair)
        .unwrap_or(10_000.0))
}

fn day_trend_and_residuals(series: &ProductDaySeries) -> (f64, f64, Vec<f64>) {
    let len = series.ticks.len().max(1);
    let start = series.ticks.first().map(|tick| tick.fair).unwrap_or(0.0);
    let end = series.ticks.last().map(|tick| tick.fair).unwrap_or(start);
    let slope = if len > 1 {
        (end - start) / (len - 1) as f64
    } else {
        0.0
    };
    let residuals = series
        .ticks
        .iter()
        .enumerate()
        .map(|(idx, tick)| tick.fair - (start + slope * idx as f64))
        .collect::<Vec<_>>();
    (start, slope, residuals)
}

fn generate_ash_fair_path(
    config: &Config,
    replay: &ReplayData,
    calibration: &ProductCalibration,
    rng: &mut ChaCha8Rng,
) -> Result<Vec<f64>> {
    let increments = pooled_fair_increments("ASH_COATED_OSMIUM", replay)?;
    let mean_increment = increments.iter().sum::<f64>() / increments.len() as f64;
    let mut fair = sampled_open_fair("ASH_COATED_OSMIUM", replay, rng)?;
    let mut path = Vec::with_capacity(config.ticks_per_day);
    path.push(fair);
    for _ in 1..config.ticks_per_day {
        let sampled = increments[rng.gen_range(0..increments.len())] - mean_increment;
        fair += sampled + calibration.drift_per_tick;
        path.push(fair);
    }
    Ok(path)
}

fn generate_pepper_fair_path(
    config: &Config,
    replay: &ReplayData,
    calibration: &ProductCalibration,
    rng: &mut ChaCha8Rng,
) -> Result<Vec<f64>> {
    let slope_day = DAYS[rng.gen_range(0..DAYS.len())];
    let residual_day = DAYS[rng.gen_range(0..DAYS.len())];
    let slope_series = series_for_product_day(replay, "INTARIAN_PEPPER_ROOT", slope_day)?;
    let residual_series = series_for_product_day(replay, "INTARIAN_PEPPER_ROOT", residual_day)?;
    let (start, slope, _) = day_trend_and_residuals(slope_series);
    let (_, _, residuals) = day_trend_and_residuals(residual_series);
    let base_residual = residuals.first().copied().unwrap_or(0.0);
    let mut path = Vec::with_capacity(config.ticks_per_day);
    for tick in 0..config.ticks_per_day {
        let idx = tick.min(residuals.len().saturating_sub(1));
        let residual = residuals.get(idx).copied().unwrap_or(0.0) - base_residual;
        path.push(start + slope * tick as f64 + residual);
    }
    if let Some(first) = path.first_mut() {
        *first = start.max(calibration.stationary_anchor * 0.7);
    }
    Ok(path)
}

fn select_observed_template_tick(
    product: &str,
    target_fair: f64,
    target_tick: usize,
    replay: &ReplayData,
    calibration: &ProductCalibration,
    rng: &mut ChaCha8Rng,
) -> Result<(i32, usize)> {
    let tries = calibration.candidate_search_count.max(12);
    let mut best_choice = None::<(i32, usize, f64)>;
    for _ in 0..tries {
        let day = DAYS[rng.gen_range(0..DAYS.len())];
        let index = random_tick_index(product, day, replay, rng)?;
        let series = series_for_product_day(replay, product, day)?;
        let candidate_fair = series.ticks[index].fair;
        let fair_score =
            ((candidate_fair - target_fair).abs() / calibration.fair_step_vol.max(1.0))
                * calibration.continuity_weight;
        let time_score = ((index as i64 - target_tick as i64).abs() as f64
            / config_safe_len(series.ticks.len()) as f64)
            * calibration.trend_weight;
        let score = fair_score + time_score;
        match best_choice {
            Some((_, _, best_score)) if score >= best_score => {}
            _ => best_choice = Some((day, index, score)),
        }
    }
    best_choice
        .map(|(day, index, _)| (day, index))
        .context("failed to select observed template tick")
}

fn config_safe_len(len: usize) -> usize {
    len.max(1)
}

fn sampled_tick_sequence(
    product: &str,
    day: i32,
    config: &Config,
    replay: &ReplayData,
    calibrations: &HashMap<String, ProductCalibration>,
    rng: &mut ChaCha8Rng,
) -> Result<Vec<SampledTick>> {
    let mut sequence = Vec::with_capacity(config.ticks_per_day);

    match config.fv_mode {
        FvMode::Replay => {
            let series = replay
                .series_by_key
                .get(&(day, product.to_string()))
                .with_context(|| format!("missing replay series for {} day {}", product, day))?;
            for index in 0..config.ticks_per_day.min(series.ticks.len()) {
                sequence.push(SampledTick {
                    observed: series.ticks[index].clone(),
                    source_day: day,
                    source_index: index,
                });
            }
            while sequence.len() < config.ticks_per_day {
                let last = sequence
                    .last()
                    .cloned()
                    .context("empty replay sequence for sampled_tick_sequence")?;
                sequence.push(last);
            }
        }
        FvMode::Simulate => {
            let calibration = product_calibration(product, calibrations)?;
            let fair_path = if product == "ASH_COATED_OSMIUM" {
                generate_ash_fair_path(config, replay, calibration, rng)?
            } else {
                generate_pepper_fair_path(config, replay, calibration, rng)?
            };
            for (tick_index, target_fair) in fair_path.iter().copied().enumerate() {
                let (source_day, source_index) = select_observed_template_tick(
                    product,
                    target_fair,
                    tick_index,
                    replay,
                    calibration,
                    rng,
                )?;
                let series = series_for_product_day(replay, product, source_day)?;
                let source_tick = &series.ticks[source_index];
                let shifted = shift_observed_tick(source_tick, target_fair - source_tick.fair);
                sequence.push(SampledTick {
                    observed: shifted,
                    source_day,
                    source_index,
                });
            }
        }
    }

    Ok(sequence)
}

fn random_tick_index(
    product: &str,
    day: i32,
    replay: &ReplayData,
    rng: &mut ChaCha8Rng,
) -> Result<usize> {
    let series = replay
        .series_by_key
        .get(&(day, product.to_string()))
        .with_context(|| format!("missing series for {} day {}", product, day))?;
    if series.ticks.is_empty() {
        bail!("empty series for {} day {}", product, day);
    }
    Ok(rng.gen_range(0..series.ticks.len()))
}

fn trade_events_for_tick(
    product: &str,
    day: i32,
    tick: usize,
    config: &Config,
    replay: &ReplayData,
    calibrations: &HashMap<String, ProductCalibration>,
    sampled_tick: Option<&SampledTick>,
    rng: &mut ChaCha8Rng,
) -> Result<Vec<ObservedTradeEvent>> {
    match config.trade_mode {
        TradeMode::ReplayTimes => replay
            .series_by_key
            .get(&(day, product.to_string()))
            .and_then(|series| series.trade_events.get(tick))
            .cloned()
            .with_context(|| format!("missing replay trade events for {} day {} tick {}", product, day, tick)),
        TradeMode::Simulate => {
            if let Some(sampled_tick) = sampled_tick {
                if let Some(events) = replay
                    .series_by_key
                    .get(&(sampled_tick.source_day, product.to_string()))
                    .and_then(|series| series.trade_events.get(sampled_tick.source_index))
                {
                    return Ok(events.clone());
                }
            }
            let pooled = replay
                .pooled_trade_events
                .get(product)
                .with_context(|| format!("missing pooled trade events for {}", product))?;
            if pooled.is_empty() {
                return Ok(Vec::new());
            }
            let calibration = product_calibration(product, calibrations)?;
            let draw_count = replay
                .trade_count_samples
                .get(product)
                .and_then(|counts| counts.get(rng.gen_range(0..counts.len())).copied())
                .unwrap_or_else(|| {
                    if rng.gen_bool(calibration.active_timestamp_rate.clamp(0.0, 1.0)) {
                        calibration.trades_per_active_step_mean.round().max(1.0) as usize
                    } else {
                        0
                    }
                });
            let mut events = Vec::with_capacity(draw_count);
            for _ in 0..draw_count {
                events.push(pooled[rng.gen_range(0..pooled.len())]);
            }
            Ok(events)
        }
    }
}

fn book_to_price_row(day: i32, timestamp: i32, product: &str, book: &Book) -> PriceRow {
    let bid1 = book.bids.first().copied();
    let bid2 = book.bids.get(1).copied();
    let bid3 = book.bids.get(2).copied();
    let ask1 = book.asks.first().copied();
    let ask2 = book.asks.get(1).copied();
    let ask3 = book.asks.get(2).copied();
    let mid_price = match (bid1, ask1) {
        (Some((bid, _)), Some((ask, _))) => (bid as f64 + ask as f64) / 2.0,
        (Some((bid, _)), None) => bid as f64,
        (None, Some((ask, _))) => ask as f64,
        (None, None) => 0.0,
    };

    PriceRow {
        day,
        timestamp,
        product: product.to_string(),
        bid_price_1: bid1.map(|x| x.0),
        bid_volume_1: bid1.map(|x| x.1),
        bid_price_2: bid2.map(|x| x.0),
        bid_volume_2: bid2.map(|x| x.1),
        bid_price_3: bid3.map(|x| x.0),
        bid_volume_3: bid3.map(|x| x.1),
        ask_price_1: ask1.map(|x| x.0),
        ask_volume_1: ask1.map(|x| x.1),
        ask_price_2: ask2.map(|x| x.0),
        ask_volume_2: ask2.map(|x| x.1),
        ask_price_3: ask3.map(|x| x.0),
        ask_volume_3: ask3.map(|x| x.1),
        mid_price,
        profit_and_loss: 0.0,
    }
}
