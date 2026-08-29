use std::collections::{HashMap, HashSet};
use std::env;
use std::future::Future;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use anyhow::{bail, Context, Result};
use futures_util::{Stream, StreamExt};
use p2panda::streams::{Source, StreamEvent, StreamPublisher, StreamSubscription};
use p2panda::{Node, Topic};
use serde::{Deserialize, Serialize};
use tokio::task::JoinSet;
use tokio::time::{interval, sleep, timeout, MissedTickBehavior};

#[repr(C)]
#[derive(Clone, Copy, Debug, Default)]
struct FfiAction {
    selected: u16,
    next_cursor: u16,
    reset_sent: u8,
    _padding: [u8; 3],
}

#[repr(C)]
#[derive(Clone, Copy, Debug, Default)]
struct FfiSimulation {
    success: u8,
    _padding: [u8; 7],
    rounds: u32,
    _padding2: u32,
    communication_units: u64,
    useful_deliveries: u64,
    duplicate_deliveries: u64,
    policy_calls: u64,
    violations: u64,
}

unsafe extern "C" {
    fn starlings_stage7c_abi_version() -> u32;
    fn starlings_stage7c_init_state(
        population_size: u16,
        fact_count: u16,
        topology: u8,
        redundancy: u16,
        bandwidth: u16,
        seed: u64,
        operator_index: u16,
        out_knowledge: *mut u64,
        out_words: usize,
    ) -> i32;
    fn starlings_stage7c_decide(
        population_size: u16,
        fact_count: u16,
        topology: u8,
        redundancy: u16,
        bandwidth: u16,
        seed: u64,
        operator_index: u16,
        round: u32,
        cursor: u16,
        novelty_permille: u16,
        exploration_permille: u16,
        retry_permille: u16,
        bandwidth_utilization_permille: u16,
        knowledge_words: *const u64,
        sent_words: *const u64,
        input_words: usize,
        out_fact_words: *mut u64,
        out_words: usize,
        out_action: *mut FfiAction,
    ) -> i32;
    fn starlings_stage7c_simulate(
        population_size: u16,
        fact_count: u16,
        topology: u8,
        redundancy: u16,
        bandwidth: u16,
        seed: u64,
        max_rounds: u32,
        novelty_permille: u16,
        exploration_permille: u16,
        retry_permille: u16,
        bandwidth_utilization_permille: u16,
        out_simulation: *mut FfiSimulation,
    ) -> i32;
}

const ABI_VERSION: u32 = 1;
const STARTUP_TIMEOUT: Duration = Duration::from_secs(30);
const OPERATION_TIMEOUT: Duration = Duration::from_secs(10);
const SHUTDOWN_TIMEOUT: Duration = Duration::from_secs(5);

fn transient_node_builder() -> p2panda::NodeBuilder {
    p2panda::builder()
}

async fn bounded<T>(
    phase: &str,
    limit: Duration,
    future: impl Future<Output = Result<T>>,
) -> Result<T> {
    timeout(limit, future)
        .await
        .with_context(|| format!("{phase}: timed out after {limit:?}"))?
        .with_context(|| phase.to_owned())
}

async fn receive_while<F, S, H, T>(
    operation: F,
    rx: &mut S,
    stop: &AtomicBool,
    mut on_event: H,
) -> Result<Option<T>>
where
    F: Future<Output = Result<T>>,
    S: Stream + Unpin,
    H: FnMut(S::Item) -> Result<()>,
{
    tokio::pin!(operation);
    let mut cancellation = interval(Duration::from_millis(10));
    loop {
        if stop.load(Ordering::Acquire) {
            return Ok(None);
        }
        tokio::select! {
            biased;
            _ = cancellation.tick() => {}
            result = &mut operation => return result.map(Some),
            event = rx.next() => {
                let event = event.context("topic stream closed while publishing")?;
                on_event(event)?;
            }
        }
    }
}

#[derive(Clone, Copy, Debug)]
struct Theta {
    novelty: u16,
    exploration: u16,
    retry: u16,
    utilization: u16,
}

#[derive(Clone, Copy, Debug)]
enum Topology {
    Ring,
    Complete,
    Grid,
}

impl Topology {
    fn code(self) -> u8 {
        match self {
            Self::Ring => 0,
            Self::Complete => 1,
            Self::Grid => 2,
        }
    }

    fn name(self) -> &'static str {
        match self {
            Self::Ring => "ring",
            Self::Complete => "complete",
            Self::Grid => "grid",
        }
    }

    fn parse(value: &str) -> Result<Self> {
        match value {
            "ring" => Ok(Self::Ring),
            "complete" => Ok(Self::Complete),
            "grid" => Ok(Self::Grid),
            _ => bail!("unknown topology {value:?}; use ring|complete|grid"),
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum FaultKind {
    None,
    Partition,
    CrashPersist,
    CrashReset,
}

impl FaultKind {
    fn name(self) -> &'static str {
        match self {
            Self::None => "no_fault",
            Self::Partition => "partition",
            Self::CrashPersist => "crash_restart_persist",
            Self::CrashReset => "crash_restart_reset",
        }
    }

    fn parse(value: &str) -> Result<Self> {
        match value {
            "no_fault" => Ok(Self::None),
            "partition" => Ok(Self::Partition),
            "crash_restart_persist" => Ok(Self::CrashPersist),
            "crash_restart_reset" => Ok(Self::CrashReset),
            _ => bail!(
                "unknown fault {value:?}; use no_fault|partition|crash_restart_persist|crash_restart_reset"
            ),
        }
    }
}

#[derive(Clone, Debug)]
struct Config {
    profile: String,
    theta: Theta,
    nodes: u16,
    facts: u16,
    topology: Topology,
    redundancy: u16,
    bandwidth: u16,
    seed: u64,
    tick_ms: u64,
    startup_ms: u64,
    drain_ms: u64,
    max_ticks: u32,
    sim_max_rounds: u32,
    fault: FaultKind,
    partition_start: u32,
    partition_end: u32,
    partition_cut: u16,
    crash_node: u16,
    crash_start: u32,
    crash_end: u32,
    no_header: bool,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            profile: "theta51".into(),
            theta: profile_theta("theta51").unwrap(),
            nodes: 8,
            facts: 32,
            topology: Topology::Ring,
            redundancy: 2,
            bandwidth: 2,
            seed: 0,
            tick_ms: 20,
            startup_ms: 1000,
            drain_ms: 500,
            max_ticks: 1024,
            sim_max_rounds: 4096,
            fault: FaultKind::None,
            partition_start: 8,
            partition_end: 48,
            partition_cut: 4,
            crash_node: 0,
            crash_start: 8,
            crash_end: 40,
            no_header: false,
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
struct Envelope {
    run_nonce: u64,
    sender: u16,
    sequence: u32,
    logical_round: u32,
    recipients: Vec<u16>,
    facts: Vec<u16>,
}

type AttemptKey = (u16, u32, u16);

#[derive(Debug, Default)]
struct Ledger {
    attempted: HashMap<AttemptKey, Vec<u16>>,
    delivered: HashSet<AttemptKey>,
    partitioned: HashSet<AttemptKey>,
    crashed: HashSet<AttemptKey>,
    duplicate_terminals: u64,
    collector_attempted: Vec<u64>,
    collector_delivered: Vec<u64>,
    collector_partitioned: Vec<u64>,
    collector_crashed: Vec<u64>,
}

impl Ledger {
    fn new(facts: usize) -> Self {
        Self {
            collector_attempted: vec![0; facts],
            collector_delivered: vec![0; facts],
            collector_partitioned: vec![0; facts],
            collector_crashed: vec![0; facts],
            ..Self::default()
        }
    }

    fn record_attempt(&mut self, envelope: &Envelope, recipient: u16) {
        let key = (envelope.sender, envelope.sequence, recipient);
        if self.attempted.contains_key(&key) {
            return;
        }
        self.attempted.insert(key, envelope.facts.clone());
        if recipient == 0 {
            for fact in &envelope.facts {
                self.collector_attempted[*fact as usize] += 1;
            }
        }
    }

    fn close(&mut self, key: AttemptKey, facts: &[u16], terminal: Terminal) {
        let inserted = match terminal {
            Terminal::Delivered => self.delivered.insert(key),
            Terminal::Partitioned => self.partitioned.insert(key),
            Terminal::Crashed => self.crashed.insert(key),
        };
        if !inserted {
            self.duplicate_terminals += 1;
            return;
        }

        if key.2 == 0 {
            let target = match terminal {
                Terminal::Delivered => &mut self.collector_delivered,
                Terminal::Partitioned => &mut self.collector_partitioned,
                Terminal::Crashed => &mut self.collector_crashed,
            };
            for fact in facts {
                target[*fact as usize] += 1;
            }
        }
    }

    fn is_closed(&self, key: AttemptKey) -> bool {
        self.delivered.contains(&key)
            || self.partitioned.contains(&key)
            || self.crashed.contains(&key)
    }

    fn pending_count(&self) -> usize {
        self.attempted
            .keys()
            .filter(|key| !self.is_closed(**key))
            .count()
    }

    fn counts(&self) -> LedgerCounts {
        LedgerCounts {
            attempts: self.attempted.len() as u64,
            delivered: self.delivered.len() as u64,
            partitioned: self.partitioned.len() as u64,
            crashed: self.crashed.len() as u64,
            pending: self.pending_count() as u64,
        }
    }

    fn classify_missing(&self, collector_knowledge: &[u64], fact_count: u16) -> MissingCounts {
        let mut result = MissingCounts::default();
        let pending_facts = self.pending_collector_facts();

        for fact in 0..fact_count {
            if has_fact(collector_knowledge, fact) {
                continue;
            }
            let i = fact as usize;
            if pending_facts.contains(&fact) {
                result.pending_at_censor += 1;
            } else if self.collector_crashed[i] > 0 {
                result.crashed_before_merge += 1;
            } else if self.collector_partitioned[i] > 0 {
                result.delivery_faulted += 1;
            } else if self.collector_attempted[i] == 0 {
                result.never_transmitted += 1;
            } else if self.collector_delivered[i] > 0 {
                // A delivered fact absent at final state can only be explained by
                // crash-reset erasure at the collector.
                result.crashed_before_merge += 1;
            } else {
                result.unattributed += 1;
            }
        }
        result
    }

    fn pending_collector_facts(&self) -> HashSet<u16> {
        let mut facts = HashSet::new();
        for (key, envelope_facts) in &self.attempted {
            if key.2 != 0 || self.is_closed(*key) {
                continue;
            }
            facts.extend(envelope_facts.iter().copied());
        }
        facts
    }
}

#[derive(Clone, Copy)]
enum Terminal {
    Delivered,
    Partitioned,
    Crashed,
}

#[derive(Clone, Copy, Debug, Default)]
struct LedgerCounts {
    attempts: u64,
    delivered: u64,
    partitioned: u64,
    crashed: u64,
    pending: u64,
}

impl LedgerCounts {
    fn accounted(self) -> bool {
        self.attempts == self.delivered + self.partitioned + self.crashed + self.pending
    }
}

#[derive(Clone, Copy, Debug, Default)]
struct MissingCounts {
    never_transmitted: usize,
    delivery_faulted: usize,
    crashed_before_merge: usize,
    pending_at_censor: usize,
    unattributed: usize,
}

impl MissingCounts {
    fn total(self) -> usize {
        self.never_transmitted
            + self.delivery_faulted
            + self.crashed_before_merge
            + self.pending_at_censor
            + self.unattributed
    }
}

#[derive(Debug)]
struct LocalState {
    knowledge: Vec<u64>,
    sent: Vec<u64>,
    cursor: u16,
    round: u32,
    sequence: u32,
    restart_applied: bool,
}

#[derive(Clone, Debug, Default)]
struct NodeStats {
    node: u16,
    initial_facts: usize,
    final_facts: usize,
    rounds: u32,
    actions: u64,
    logical_messages: u64,
    communication_units: u64,
    useful_deliveries: u64,
    duplicate_deliveries: u64,
    duplicate_envelopes: u64,
    p2panda_local_operations: u64,
    p2panda_remote_operations: u64,
    sync_sessions: u64,
    sync_errors: u64,
    policy_errors: u64,
    collector_complete: bool,
    collector_knowledge: Vec<u64>,
}

#[derive(Debug, Default)]
struct AggregateStats {
    actions: u64,
    logical_messages: u64,
    communication_units: u64,
    useful_deliveries: u64,
    duplicate_deliveries: u64,
    duplicate_envelopes: u64,
    p2panda_local_operations: u64,
    p2panda_remote_operations: u64,
    sync_sessions: u64,
    sync_errors: u64,
    policy_errors: u64,
    max_local_round: u32,
}

#[tokio::main(flavor = "multi_thread")]
async fn main() -> Result<()> {
    let config = parse_args()?;
    validate_config(&config)?;

    let abi = unsafe { starlings_stage7c_abi_version() };
    if abi != ABI_VERSION {
        bail!("F1b ABI mismatch: Rust expects {ABI_VERSION}, Zig reports {abi}");
    }

    let simulation = simulate(&config)?;
    if simulation.violations != 0 {
        bail!("synchronous policy baseline reported {} violations", simulation.violations);
    }

    let topic = Topic::random();
    let run_nonce = run_nonce(&config);
    let ledger = Arc::new(Mutex::new(Ledger::new(config.facts as usize)));

    let mut node_guards: Vec<Node> = Vec::with_capacity(config.nodes as usize);
    let mut streams = Vec::with_capacity(config.nodes as usize);

    for node_index in 0..config.nodes {
        let node = bounded(
            &format!("spawn p2panda node {node_index}"),
            STARTUP_TIMEOUT,
            async { Ok(transient_node_builder().spawn().await?) },
        )
        .await?;
        let pair = bounded(
            &format!("create topic stream for node {node_index}"),
            STARTUP_TIMEOUT,
            async { Ok(node.stream::<Envelope>(topic.clone()).await?) },
        )
        .await?;
        node_guards.push(node);
        streams.push(pair);
    }

    sleep(Duration::from_millis(config.startup_ms)).await;

    let stop = Arc::new(AtomicBool::new(false));
    let collector_complete = Arc::new(AtomicBool::new(false));
    let mut handles = JoinSet::new();

    for (node_index, (tx, rx)) in streams.into_iter().enumerate() {
        let node_config = config.clone();
        let node_stop = stop.clone();
        let node_collector_complete = collector_complete.clone();
        let node_ledger = ledger.clone();
        handles.spawn(async move {
            run_node(
                node_index as u16,
                node_config,
                run_nonce,
                tx,
                rx,
                node_stop,
                node_collector_complete,
                node_ledger,
            )
            .await
        });
    }

    let max_runtime = Duration::from_millis(
        config
            .tick_ms
            .saturating_mul(config.max_ticks as u64)
            .saturating_add(config.startup_ms)
            .saturating_add(config.drain_ms)
            .saturating_add(5000),
    );

    let mut node_stats = collect_nodes(
        handles,
        &stop,
        &collector_complete,
        max_runtime,
        Duration::from_millis(config.drain_ms),
        SHUTDOWN_TIMEOUT,
    )
    .await?;
    node_stats.sort_by_key(|stats| stats.node);
    drop(node_guards);

    let collector = node_stats
        .iter()
        .find(|stats| stats.node == 0)
        .context("collector stats missing")?;
    let aggregate = aggregate(&node_stats);

    let ledger = ledger.lock().expect("F1b ledger poisoned");
    let counts = ledger.counts();
    let missing = ledger.classify_missing(&collector.collector_knowledge, config.facts);
    let missing_total = config.facts as usize - collector.final_facts;
    let missing_accounted = missing.total() == missing_total;
    let fully_accounted =
        counts.accounted() && missing_accounted && missing.unattributed == 0;
    let signature = result_signature(
        &config,
        collector,
        &aggregate,
        counts,
        missing,
        fully_accounted,
    );

    if !config.no_header {
        println!(
            "profile\ttopology\tseed\tfault\tsim_success\tdist_success\tcollector_initial\tcollector_final\tmax_local_round\tactions\tlogical_messages\ttransport_attempts\tdelivered\tpartitioned\tcrashed\tpending\tcommunication_units\tuseful\tduplicate\tduplicate_envelopes\tp2panda_local_ops\tp2panda_remote_ops\tsync_sessions\tsync_errors\tpolicy_errors\tnever_transmitted\tdelivery_faulted\tcrashed_before_merge\tpending_at_censor\tunattributed\tenvelope_accounted\tmissing_accounted\tfully_accounted\tresult_signature"
        );
    }

    println!(
        "{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{:016x}",
        config.profile,
        config.topology.name(),
        config.seed,
        config.fault.name(),
        yes_no(simulation.success != 0),
        yes_no(collector.collector_complete),
        collector.initial_facts,
        collector.final_facts,
        aggregate.max_local_round,
        aggregate.actions,
        aggregate.logical_messages,
        counts.attempts,
        counts.delivered,
        counts.partitioned,
        counts.crashed,
        counts.pending,
        aggregate.communication_units,
        aggregate.useful_deliveries,
        aggregate.duplicate_deliveries,
        aggregate.duplicate_envelopes,
        aggregate.p2panda_local_operations,
        aggregate.p2panda_remote_operations,
        aggregate.sync_sessions,
        aggregate.sync_errors,
        aggregate.policy_errors,
        missing.never_transmitted,
        missing.delivery_faulted,
        missing.crashed_before_merge,
        missing.pending_at_censor,
        missing.unattributed,
        yes_no(counts.accounted()),
        yes_no(missing_accounted),
        yes_no(fully_accounted),
        signature,
    );

    Ok(())
}

async fn collect_nodes(
    mut tasks: JoinSet<Result<NodeStats>>,
    stop: &AtomicBool,
    collector_complete: &AtomicBool,
    max_runtime: Duration,
    drain: Duration,
    shutdown: Duration,
) -> Result<Vec<NodeStats>> {
    let deadline = sleep(max_runtime);
    tokio::pin!(deadline);
    let mut poll = interval(Duration::from_millis(10));
    let mut stats = Vec::with_capacity(tasks.len());

    while !tasks.is_empty() {
        tokio::select! {
            biased;
            _ = &mut deadline => break,
            result = tasks.join_next() => {
                stats.push(result.context("missing node task")?
                    .context("join F1b node task")??);
            }
            _ = poll.tick() => {
                if stop.load(Ordering::Acquire) {
                    break;
                }
                if collector_complete.load(Ordering::Acquire) {
                    sleep(drain).await;
                    break;
                }
            }
        }
    }

    stop.store(true, Ordering::Release);
    bounded("node shutdown", shutdown, async {
        while let Some(result) = tasks.join_next().await {
            stats.push(result.context("join F1b node task")??);
        }
        Ok(())
    })
    .await?;
    Ok(stats)
}

async fn run_node(
    node_index: u16,
    config: Config,
    run_nonce: u64,
    tx: StreamPublisher<Envelope>,
    mut rx: StreamSubscription<Envelope>,
    stop: Arc<AtomicBool>,
    collector_complete: Arc<AtomicBool>,
    ledger: Arc<Mutex<Ledger>>,
) -> Result<NodeStats> {
    let words = active_words(config.facts);
    let mut knowledge = vec![0_u64; words];
    init_state(&config, node_index, &mut knowledge)?;

    let mut state = LocalState {
        knowledge,
        sent: vec![0_u64; words],
        cursor: 0,
        round: 0,
        sequence: 0,
        restart_applied: false,
    };
    let mut stats = NodeStats {
        node: node_index,
        initial_facts: count_facts(&state.knowledge, config.facts),
        collector_knowledge: vec![0; words],
        ..NodeStats::default()
    };

    if node_index == 0 && contains_all(&state.knowledge, config.facts) {
        stats.collector_complete = true;
        collector_complete.store(true, Ordering::Release);
    }

    let mut seen: HashSet<(u64, u16, u32)> = HashSet::new();
    let mut ticker = interval(Duration::from_millis(config.tick_ms));
    ticker.set_missed_tick_behavior(MissedTickBehavior::Delay);
    let mut emission_done = false;
    let mut cancellation = interval(Duration::from_millis(10));

    while !stop.load(Ordering::Acquire) {
        tokio::select! {
            _ = cancellation.tick() => {}
            _ = ticker.tick() => {
                if emission_done {
                    continue;
                }
                if state.round >= config.max_ticks {
                    emission_done = true;
                    continue;
                }

                state.round += 1;
                stats.rounds = state.round;
                apply_restart_if_needed(node_index, &config, &mut state)?;

                if is_crashed(&config, node_index, state.round) {
                    continue;
                }

                let mut output_words = vec![0_u64; words];
                let mut action = FfiAction::default();
                let status = unsafe {
                    starlings_stage7c_decide(
                        config.nodes,
                        config.facts,
                        config.topology.code(),
                        config.redundancy,
                        config.bandwidth,
                        config.seed,
                        node_index,
                        state.round,
                        state.cursor,
                        config.theta.novelty,
                        config.theta.exploration,
                        config.theta.retry,
                        config.theta.utilization,
                        state.knowledge.as_ptr(),
                        state.sent.as_ptr(),
                        words,
                        output_words.as_mut_ptr(),
                        words,
                        &mut action,
                    )
                };

                if status < 0 {
                    stats.policy_errors += 1;
                    stop.store(true, Ordering::Release);
                    continue;
                }
                if status == 0 || action.selected == 0 {
                    continue;
                }

                let facts = words_to_facts(&output_words, config.facts);
                if facts.len() != action.selected as usize {
                    stats.policy_errors += 1;
                    stop.store(true, Ordering::Release);
                    continue;
                }

                let recipients = recipients(config.topology, node_index, config.nodes);
                state.sequence = state.sequence.wrapping_add(1);
                let envelope = Envelope {
                    run_nonce,
                    sender: node_index,
                    sequence: state.sequence,
                    logical_round: state.round,
                    recipients: recipients.clone(),
                    facts: facts.clone(),
                };

                {
                    let mut audit = ledger.lock().expect("F1b ledger poisoned");
                    for recipient in &recipients {
                        audit.record_attempt(&envelope, *recipient);
                    }
                }

                let operation = async {
                    let processing = bounded(
                        &format!("node {node_index}: publish"),
                        OPERATION_TIMEOUT,
                        async { Ok(tx.publish(envelope).await?) },
                    ).await?;
                    let processed = bounded(
                        &format!("node {node_index}: local processing"),
                        OPERATION_TIMEOUT,
                        async { Ok(processing.await?) },
                    ).await?;
                    if processed.is_failed() {
                        bail!(
                            "node {node_index}: local processing failed: {:?}",
                            processed.failure_reason()
                        );
                    }
                    Ok(())
                };

                let publication = receive_while(operation, &mut rx, &stop, |event| {
                    apply_event(
                        event,
                        node_index,
                        &config,
                        run_nonce,
                        &mut state,
                        &mut stats,
                        &mut seen,
                        &collector_complete,
                        &ledger,
                    )
                }).await?;

                if publication.is_none() {
                    break;
                }

                if action.reset_sent != 0 {
                    state.sent.fill(0);
                }
                for fact in &facts {
                    set_fact(&mut state.sent, *fact);
                }
                state.cursor = action.next_cursor;

                stats.actions += 1;
                stats.logical_messages += recipients.len() as u64;
                stats.communication_units +=
                    facts.len() as u64 * recipients.len() as u64;
            }
            event = rx.next() => {
                let event = event.context("topic stream closed before run completed")?;
                apply_event(
                    event,
                    node_index,
                    &config,
                    run_nonce,
                    &mut state,
                    &mut stats,
                    &mut seen,
                    &collector_complete,
                    &ledger,
                )?;
            }
        }
    }

    stats.final_facts = count_facts(&state.knowledge, config.facts);
    if node_index == 0 {
        stats.collector_complete = contains_all(&state.knowledge, config.facts);
        stats.collector_knowledge.clone_from(&state.knowledge);
    }
    Ok(stats)
}

fn apply_event(
    event: StreamEvent<Envelope>,
    node_index: u16,
    config: &Config,
    run_nonce: u64,
    state: &mut LocalState,
    stats: &mut NodeStats,
    seen: &mut HashSet<(u64, u16, u32)>,
    collector_complete: &AtomicBool,
    ledger: &Arc<Mutex<Ledger>>,
) -> Result<()> {
    match event {
        StreamEvent::Processed { operation, source } => {
            match source {
                Source::LocalStore => stats.p2panda_local_operations += 1,
                Source::SyncSession { .. } => stats.p2panda_remote_operations += 1,
                Source::ExternalStream { .. } => {}
            }

            let envelope = operation.message();
            if envelope.run_nonce != run_nonce {
                return Ok(());
            }

            apply_envelope(envelope, node_index, config, state, stats, seen, ledger)?;

            if node_index == 0 && contains_all(&state.knowledge, config.facts) {
                stats.collector_complete = true;
                collector_complete.store(true, Ordering::Release);
            }
        }
        StreamEvent::SyncEnded {
            sent_bytes: _,
            received_bytes: _,
            error,
            ..
        } => {
            stats.sync_sessions += 1;
            if error.is_some() {
                stats.sync_errors += 1;
            }
        }
        StreamEvent::ProcessingFailed { .. }
        | StreamEvent::ReplayFailed { .. }
        | StreamEvent::DecodeFailed { .. }
        | StreamEvent::AckFailed { .. } => {
            stats.sync_errors += 1;
        }
        _ => {}
    }
    Ok(())
}

fn apply_envelope(
    envelope: &Envelope,
    node_index: u16,
    config: &Config,
    state: &mut LocalState,
    stats: &mut NodeStats,
    seen: &mut HashSet<(u64, u16, u32)>,
    ledger: &Arc<Mutex<Ledger>>,
) -> Result<()> {
    if !envelope.recipients.contains(&node_index) {
        return Ok(());
    }

    let event_key = (envelope.run_nonce, envelope.sender, envelope.sequence);
    if !seen.insert(event_key) {
        stats.duplicate_envelopes += 1;
        return Ok(());
    }

    let attempt_key = (envelope.sender, envelope.sequence, node_index);

    if is_partitioned(config, envelope.sender, node_index, envelope.logical_round) {
        ledger
            .lock()
            .expect("F1b ledger poisoned")
            .close(attempt_key, &envelope.facts, Terminal::Partitioned);
        return Ok(());
    }

    if is_crashed(config, node_index, state.round) {
        ledger
            .lock()
            .expect("F1b ledger poisoned")
            .close(attempt_key, &envelope.facts, Terminal::Crashed);
        return Ok(());
    }

    ledger
        .lock()
        .expect("F1b ledger poisoned")
        .close(attempt_key, &envelope.facts, Terminal::Delivered);

    for fact in &envelope.facts {
        if has_fact(&state.knowledge, *fact) {
            stats.duplicate_deliveries += 1;
        } else {
            stats.useful_deliveries += 1;
            set_fact(&mut state.knowledge, *fact);
        }
    }

    Ok(())
}

fn apply_restart_if_needed(
    node_index: u16,
    config: &Config,
    state: &mut LocalState,
) -> Result<()> {
    if state.restart_applied
        || node_index != config.crash_node
        || config.fault == FaultKind::None
        || config.fault == FaultKind::Partition
        || state.round < config.crash_end
    {
        return Ok(());
    }

    if config.fault == FaultKind::CrashReset {
        init_state(config, node_index, &mut state.knowledge)?;
    }
    state.sent.fill(0);
    state.cursor = 0;
    state.restart_applied = true;
    Ok(())
}

fn init_state(config: &Config, node_index: u16, knowledge: &mut [u64]) -> Result<()> {
    let status = unsafe {
        starlings_stage7c_init_state(
            config.nodes,
            config.facts,
            config.topology.code(),
            config.redundancy,
            config.bandwidth,
            config.seed,
            node_index,
            knowledge.as_mut_ptr(),
            knowledge.len(),
        )
    };
    if status != 0 {
        bail!("node {node_index}: Zig state initialization failed with {status}");
    }
    Ok(())
}

fn simulate(config: &Config) -> Result<FfiSimulation> {
    let mut simulation = FfiSimulation::default();
    let status = unsafe {
        starlings_stage7c_simulate(
            config.nodes,
            config.facts,
            config.topology.code(),
            config.redundancy,
            config.bandwidth,
            config.seed,
            config.sim_max_rounds,
            config.theta.novelty,
            config.theta.exploration,
            config.theta.retry,
            config.theta.utilization,
            &mut simulation,
        )
    };
    if status != 0 {
        bail!("synchronous policy simulation failed with status {status}");
    }
    Ok(simulation)
}

fn aggregate(nodes: &[NodeStats]) -> AggregateStats {
    let mut total = AggregateStats::default();
    for stats in nodes {
        total.actions += stats.actions;
        total.logical_messages += stats.logical_messages;
        total.communication_units += stats.communication_units;
        total.useful_deliveries += stats.useful_deliveries;
        total.duplicate_deliveries += stats.duplicate_deliveries;
        total.duplicate_envelopes += stats.duplicate_envelopes;
        total.p2panda_local_operations += stats.p2panda_local_operations;
        total.p2panda_remote_operations += stats.p2panda_remote_operations;
        total.sync_sessions += stats.sync_sessions;
        total.sync_errors += stats.sync_errors;
        total.policy_errors += stats.policy_errors;
        total.max_local_round = total.max_local_round.max(stats.rounds);
    }
    total
}

fn is_partitioned(config: &Config, sender: u16, recipient: u16, round: u32) -> bool {
    config.fault == FaultKind::Partition
        && round >= config.partition_start
        && round < config.partition_end
        && (sender < config.partition_cut) != (recipient < config.partition_cut)
}

fn is_crashed(config: &Config, node: u16, round: u32) -> bool {
    matches!(config.fault, FaultKind::CrashPersist | FaultKind::CrashReset)
        && node == config.crash_node
        && round >= config.crash_start
        && round < config.crash_end
}

fn recipients(topology: Topology, sender: u16, population: u16) -> Vec<u16> {
    match topology {
        Topology::Ring => {
            let left = (sender + population - 1) % population;
            let right = (sender + 1) % population;
            if left == right { vec![left] } else { vec![left, right] }
        }
        Topology::Complete => (0..population).filter(|candidate| *candidate != sender).collect(),
        Topology::Grid => {
            let width = grid_width(population as usize);
            let sender = sender as usize;
            let row = sender / width;
            let col = sender % width;
            let mut result = Vec::with_capacity(4);
            if col > 0 {
                result.push((sender - 1) as u16);
            }
            if col + 1 < width && sender + 1 < population as usize {
                let recipient = sender + 1;
                if recipient / width == row {
                    result.push(recipient as u16);
                }
            }
            if sender >= width {
                result.push((sender - width) as u16);
            }
            if sender + width < population as usize {
                result.push((sender + width) as u16);
            }
            result
        }
    }
}

fn grid_width(population: usize) -> usize {
    let mut width = 1;
    while width * width < population {
        width += 1;
    }
    width
}

fn active_words(facts: u16) -> usize {
    (facts as usize + 63) / 64
}

fn has_fact(words: &[u64], fact: u16) -> bool {
    let index = fact as usize;
    (words[index / 64] & (1_u64 << (index % 64))) != 0
}

fn set_fact(words: &mut [u64], fact: u16) {
    let index = fact as usize;
    words[index / 64] |= 1_u64 << (index % 64);
}

fn words_to_facts(words: &[u64], fact_count: u16) -> Vec<u16> {
    (0..fact_count).filter(|fact| has_fact(words, *fact)).collect()
}

fn count_facts(words: &[u64], fact_count: u16) -> usize {
    (0..fact_count).filter(|fact| has_fact(words, *fact)).count()
}

fn contains_all(words: &[u64], fact_count: u16) -> bool {
    count_facts(words, fact_count) == fact_count as usize
}

fn profile_theta(name: &str) -> Option<Theta> {
    match name {
        "theta37" => Some(Theta { novelty: 244, exploration: 94, retry: 15, utilization: 958 }),
        "theta51" => Some(Theta { novelty: 354, exploration: 141, retry: 0, utilization: 994 }),
        "theta93" => Some(Theta { novelty: 685, exploration: 283, retry: 960, utilization: 344 }),
        "round_robin" => Some(Theta { novelty: 0, exploration: 0, retry: 1000, utilization: 1000 }),
        "seeded" => Some(Theta { novelty: 0, exploration: 1000, retry: 1000, utilization: 1000 }),
        "novel_first" => Some(Theta { novelty: 1000, exploration: 0, retry: 0, utilization: 1000 }),
        _ => None,
    }
}

fn parse_args() -> Result<Config> {
    let mut config = Config::default();
    let mut args = env::args().skip(1);

    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--profile" => {
                let value = next_value(&mut args, "--profile")?;
                config.theta = profile_theta(&value).with_context(|| {
                    format!("unknown profile {value:?}")
                })?;
                config.profile = value;
            }
            "--nodes" => config.nodes = next_value(&mut args, "--nodes")?.parse()?,
            "--facts" => config.facts = next_value(&mut args, "--facts")?.parse()?,
            "--topology" => config.topology = Topology::parse(&next_value(&mut args, "--topology")?)?,
            "--redundancy" => config.redundancy = next_value(&mut args, "--redundancy")?.parse()?,
            "--bandwidth" => config.bandwidth = next_value(&mut args, "--bandwidth")?.parse()?,
            "--seed" => config.seed = next_value(&mut args, "--seed")?.parse()?,
            "--tick-ms" => config.tick_ms = next_value(&mut args, "--tick-ms")?.parse()?,
            "--startup-ms" => config.startup_ms = next_value(&mut args, "--startup-ms")?.parse()?,
            "--drain-ms" => config.drain_ms = next_value(&mut args, "--drain-ms")?.parse()?,
            "--max-ticks" => config.max_ticks = next_value(&mut args, "--max-ticks")?.parse()?,
            "--sim-max-rounds" => config.sim_max_rounds = next_value(&mut args, "--sim-max-rounds")?.parse()?,
            "--fault" => config.fault = FaultKind::parse(&next_value(&mut args, "--fault")?)?,
            "--partition-start" => config.partition_start = next_value(&mut args, "--partition-start")?.parse()?,
            "--partition-end" => config.partition_end = next_value(&mut args, "--partition-end")?.parse()?,
            "--partition-cut" => config.partition_cut = next_value(&mut args, "--partition-cut")?.parse()?,
            "--crash-node" => config.crash_node = next_value(&mut args, "--crash-node")?.parse()?,
            "--crash-start" => config.crash_start = next_value(&mut args, "--crash-start")?.parse()?,
            "--crash-end" => config.crash_end = next_value(&mut args, "--crash-end")?.parse()?,
            "--no-header" => config.no_header = true,
            "--help" | "-h" => {
                println!(
                    "usage: starlings-stage7c-p2panda [--profile NAME] [--topology ring|grid] [--seed N] [--fault no_fault|partition|crash_restart_persist|crash_restart_reset]"
                );
                std::process::exit(0);
            }
            _ => bail!("unknown argument {arg:?}"),
        }
    }
    Ok(config)
}

fn next_value(args: &mut impl Iterator<Item = String>, option: &str) -> Result<String> {
    args.next().with_context(|| format!("{option} requires a value"))
}

fn validate_config(config: &Config) -> Result<()> {
    if config.nodes < 2 || config.nodes > 128 {
        bail!("nodes must be in 2..=128");
    }
    if config.facts == 0 || config.facts > 512 {
        bail!("facts must be in 1..=512");
    }
    if config.redundancy == 0 || config.redundancy > config.nodes {
        bail!("redundancy must be in 1..=nodes");
    }
    if config.bandwidth == 0 {
        bail!("bandwidth must be positive");
    }
    if config.tick_ms == 0 || config.max_ticks == 0 {
        bail!("tick-ms and max-ticks must be positive");
    }
    if config.fault == FaultKind::Partition {
        if config.partition_start >= config.partition_end
            || config.partition_end > config.max_ticks
            || config.partition_cut == 0
            || config.partition_cut >= config.nodes
        {
            bail!("invalid partition window/cut");
        }
    }
    if matches!(config.fault, FaultKind::CrashPersist | FaultKind::CrashReset) {
        if config.crash_start >= config.crash_end
            || config.crash_end > config.max_ticks
            || config.crash_node >= config.nodes
        {
            bail!("invalid crash window/node");
        }
    }
    Ok(())
}

fn run_nonce(config: &Config) -> u64 {
    mix64(
        config.seed
            ^ ((config.nodes as u64) << 48)
            ^ ((config.facts as u64) << 32)
            ^ ((config.redundancy as u64) << 16)
            ^ config.bandwidth as u64
            ^ match config.topology {
                Topology::Ring => 0x52494e47,
                Topology::Complete => 0x434f4d50,
                Topology::Grid => 0x47524944,
            }
            ^ match config.fault {
                FaultKind::None => 0,
                FaultKind::Partition => 0x50415254,
                FaultKind::CrashPersist => 0x43525053,
                FaultKind::CrashReset => 0x43525253,
            },
    )
}

fn result_signature(
    config: &Config,
    collector: &NodeStats,
    aggregate: &AggregateStats,
    counts: LedgerCounts,
    missing: MissingCounts,
    fully_accounted: bool,
) -> u64 {
    let mut hash = 0xcbf29ce484222325_u64;
    let values = [
        run_nonce(config),
        config.seed,
        config.nodes as u64,
        config.facts as u64,
        collector.collector_complete as u64,
        collector.final_facts as u64,
        aggregate.max_local_round as u64,
        aggregate.actions,
        aggregate.logical_messages,
        counts.attempts,
        counts.delivered,
        counts.partitioned,
        counts.crashed,
        counts.pending,
        aggregate.communication_units,
        aggregate.useful_deliveries,
        aggregate.duplicate_deliveries,
        aggregate.duplicate_envelopes,
        aggregate.p2panda_local_operations,
        aggregate.p2panda_remote_operations,
        aggregate.sync_sessions,
        aggregate.sync_errors,
        aggregate.policy_errors,
        missing.never_transmitted as u64,
        missing.delivery_faulted as u64,
        missing.crashed_before_merge as u64,
        missing.pending_at_censor as u64,
        missing.unattributed as u64,
        fully_accounted as u64,
    ];
    for value in values {
        for byte in value.to_le_bytes() {
            hash ^= byte as u64;
            hash = hash.wrapping_mul(0x100000001b3);
        }
    }
    hash
}

fn yes_no(value: bool) -> &'static str {
    if value { "yes" } else { "no" }
}

fn mix64(value: u64) -> u64 {
    let mut x = value;
    x ^= x >> 30;
    x = x.wrapping_mul(0xbf58476d1ce4e5b9);
    x ^= x >> 27;
    x = x.wrapping_mul(0x94d049bb133111eb);
    x ^= x >> 31;
    x
}
