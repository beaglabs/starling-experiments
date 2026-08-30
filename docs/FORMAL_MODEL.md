# Formal Starlings Model

**Version:** 0.1  
**Status:** working formal specification grounded in the validated Starlings implementation and experiments.

This document defines the current mathematical object studied by Starlings.

It is intentionally stricter than a conceptual architecture description. Every
core symbol is tied to an executable boundary in \`beaglabs/starlings\` or to a
validated experiment in this repository.

The goal is to support three things:

1. precise statements about safety, progress, convergence, cost, and emergence;
2. proofs where the implementation already gives enough structure;
3. falsifiable conjectures where the empirical evidence is not yet sufficient.

The formal model does **not** assume that operators are language models. An
operator may be a deterministic rule, learned policy, solver, sensor, physics
tool, language model, human, or any other system that can consume a local
observation and emit a typed proposal.

---

## 1. Canonical population tuple

The canonical Stage-4 Starlings population is

\[
\mathcal{P}
=
(A, G, X, M, F, \Pi, C, \Phi, J)
\]

with:

| Symbol | Meaning | Executable boundary |
| --- | --- | --- |
| \(A\) | finite population of operators | \`Population\`, \`OperatorId\` |
| \(G\) | communication / neighborhood graph | \`Topology\` |
| \(X\) | local operator state space | domain \`State\` |
| \(M\) | typed coordination actions / messages | \`Action\`, core message kinds |
| \(F\) | deterministic state-transition semantics | domain \`Spec.apply\`, runtime transitions |
| \(\Pi\) | family of local coordination policies | \`Policy\` |
| \(C\) | admissibility / control constraints | validation in \`Spec.apply\` and runtime gates |
| \(\Phi\) | global observable / terminal evaluation | \`Spec.evaluate\` |
| \(J\) | cost / measurement vector | \`Cost\`, experiment accounting |

This tuple remains the canonical population abstraction.

The operational semantics below make three auxiliary mappings explicit because
they became important in later experiments:

\[
\Omega,\qquad
\alpha,\qquad
H
\]

where:

- \(\Omega\) is the local observation projection;
- \(\alpha\) is arbitration / scheduling among simultaneously eligible proposals;
- \(H\) is the canonical content-identity / provenance function.

These are not a replacement for the canonical tuple. They make explicit
mechanisms that were previously represented inside \(\Pi\), \(F\), or the
runtime.

---

## 2. Population

Let

\[
A = \{a_1, a_2, \ldots, a_N\}
\]

be a finite set of operators.

Each operator has:

\[
a_i = (\mathrm{id}_i, X_i, \pi_i, M_i, \mathrm{active}_i)
\]

where:

- \(\mathrm{id}_i\) is a unique operator identifier;
- \(X_i\) is its local state space;
- \(\pi_i \in \Pi\) is its local policy;
- \(M_i \subseteq M\) is the subset of actions it is permitted to emit;
- \(\mathrm{active}_i \in \{0,1\}\) determines whether it participates.

The population state is the product

\[
X_A = \prod_{i=1}^{N} X_i.
\]

Starlings does not require homogeneous operators:

\[
X_i \neq X_j,
\qquad
\pi_i \neq \pi_j,
\qquad
M_i \neq M_j
\]

are all allowed.

This is the formal basis for heterogeneous specialist populations.

---

## 3. Topology

The communication topology is

\[
G_t = (A_t, E_t)
\]

where \(A_t \subseteq A\) is the active population and

\[
E_t \subseteq A_t \times A_t
\]

is the communication or neighborhood relation.

For static experiments:

\[
G_t = G
\quad
\forall t.
\]

For partition, crash, reconnect, mobility, or adversarial experiments,
\(G_t\) may vary with time.

The current core \`Topology\` uses a symmetric relation:

\[
(a_i,a_j)\in E
\iff
(a_j,a_i)\in E.
\]

The more general formal model does not require symmetry; directed topologies can
be represented by replacing \(E_t\) with a directed edge set.

Define the neighborhood of operator \(i\) at time \(t\):

\[
\mathcal{N}_i(t)
=
\{a_j \mid (a_i,a_j)\in E_t\}.
\]

---

## 4. Typed messages and actions

The core coordination vocabulary contains the typed message kinds:

\[
M_{\mathrm{core}}
=
\{
\texttt{OBSERVE},
\texttt{QUERY},
\texttt{CLAIM},
\texttt{EVIDENCE},
\texttt{PROPOSE},
\texttt{ACCEPT},
\texttt{REJECT},
\texttt{CHALLENGE},
\texttt{RETRACT},
\texttt{DELEGATE}
\}.
\]

Application experiments may define a domain action algebra \(M_D\), for example:

\[
M_{\mathrm{Evo}}
=
\{
\texttt{estimate\_depth},
\texttt{build\_geometry},
\texttt{propose\_view},
\texttt{render\_view},
\ldots
\}.
\]

The complete action space is treated as a tagged union:

\[
M
=
M_{\mathrm{core}}
\sqcup
M_D.
\]

A proposal is a typed object

\[
p =
(i, m, I, v)
\]

where:

- \(i\) is the proposing operator;
- \(m\in M_i\) is the action kind;
- \(I\) is a finite ordered tuple of causal input references;
- \(v\) is an optional bounded payload.

The exact binary representation is implementation-specific, but the runtime
must use a canonical representation wherever identity or replay depends on it.

---

## 5. Local observation

An operator does not receive the complete global state by default.

For each operator define an observation map

\[
\Omega_i :
\Sigma_t
\rightarrow
O_i
\]

where \(O_i\) is the policy-visible observation space.

The local observation may contain:

- the operator's own state;
- locally visible artifacts or facts;
- local topology information;
- local logical time;
- immutable experiment context;
- remaining budget visible to that role;
- messages received through the protocol.

It may explicitly exclude:

- peer-private state;
- collector/global completion state;
- future fault realizations;
- hidden evaluation labels;
- global novelty information;
- a centrally supplied "next action."

A policy is **locally admissible** with respect to an observation contract
\(\Omega_i\) if

\[
\pi_i
=
\pi_i(\Omega_i(\Sigma_t))
\]

and it does not read state outside that observation.

Stage 7A gives a concrete example: policy observations contain local knowledge,
sent history, cursor, operator index, round, topology degree, and immutable
environment metadata, while excluding peer knowledge and global completion.

---

## 6. Local policy

Each active operator has a policy

\[
\pi_i :
O_i
\rightarrow
M_i \cup \{\bot\}
\]

where \(\bot\) means "emit no proposal."

More generally a parameterized policy is

\[
\pi_{\theta_i}(o_{i,t}).
\]

A stochastic or model-backed policy can be represented as deterministic with an
explicit entropy/input stream \(\xi_i\):

\[
\pi_i(o_{i,t}, \xi_i).
\]

For canonical deterministic experiments, \(\xi_i\) is either absent or derived
from a frozen seed and canonical coordinates.

The policy implementation is intentionally opaque to the runtime.

---

## 7. Admissibility

Define the admissibility predicate

\[
C :
\Sigma_t \times M
\rightarrow
\{0,1\}\times R
\]

where \(R\) is a finite rejection-reason set.

Examples of constraints represented by \(C\):

- operator permission;
- action schema;
- input type;
- causal parent existence;
- role ownership;
- resource ceiling;
- duplicate action;
- prerequisite satisfaction;
- STOP eligibility;
- epistemic closure.

An action is **accepted** iff

\[
C(\Sigma_t,p)=(1,\mathrm{none}).
\]

Otherwise the proposal is rejected and the rejection is itself part of the
auditable trace.

The runtime, not the policy, is the authority for \(C\).

---

## 8. Provenance

Starlings uses content-addressed causal provenance.

Let an event be

\[
e =
(k, v, P)
\]

with:

- event kind \(k\);
- payload \(v\);
- ordered parent set \(P\).

Canonical provenance encoding v1 is:

\[
\mathrm{enc}(e)
=
\mathrm{version}
\Vert
\mathrm{kind}
\Vert
\mathrm{payload}
\Vert
|P|
\Vert
P.
\]

The content identity is

\[
H(e)
=
\mathrm{BLAKE3}(\mathrm{enc}(e)).
\]

The provenance structure is a Merkle DAG

\[
D_t = (V_t,E^D_t)
\]

such that a node may be inserted only when every declared parent already exists.

Therefore:

\[
e\in V_t
\implies
P(e)\subseteq V_t.
\]

Repeated insertion of an event with the same canonical encoding deduplicates to
the same content identity.

---

## 9. Arbitration

Later Starlings experiments make the scheduler/arbitration function explicit.

At logical step \(t\), each active operator may produce a proposal:

\[
p_{i,t}
=
\pi_i(\Omega_i(\Sigma_t)).
\]

The candidate set is

\[
\mathcal{Q}_t
=
\{p_{i,t}\neq\bot\}.
\]

An arbitration function chooses one action or a batch:

\[
\alpha :
(\mathcal{Q}_t,\Sigma_t,\xi_t)
\rightarrow
2^{\mathcal{Q}_t}.
\]

Different experiment classes instantiate \(\alpha\) differently.

### Synchronous population semantics

Stage 4 uses snapshot-batch semantics:

\[
\alpha_{\mathrm{sync}}(\mathcal{Q}_t)=\mathcal{Q}_t.
\]

Every active policy observes the same pre-round snapshot, then all actions are
applied by deterministic domain semantics.

### Queue semantics

The core message runtime uses queue-head arbitration:

\[
\alpha_{\mathrm{queue}}(Q_t)=\mathrm{head}(Q_t).
\]

### D3 semantic-blind arbitration

The EvoScene D3 trial uses:

\[
\alpha_{\mathrm{D3}}
=
\arg\min_{p\in\mathcal{Q}_t}
\mathrm{BLAKE3}
(
d
\Vert
\xi
\Vert
t
\Vert
\mathrm{enc}(p)
).
\]

This arbiter is called **semantic-blind** because it ranks canonical proposal
bytes and does not branch on domain meaning, global quality, or a hand-authored
"which action type should happen next" rule.

Semantic blindness does not mean action bytes are absent from the hash. It means
the arbiter has no domain-level policy for preferring one semantic action class
over another.

---

## 10. Global execution state

The complete operational state at logical step \(t\) is

\[
\Sigma_t
=
(
x_t,
G_t,
Q_t,
D_t,
B_t,
Z_t,
T_t,
E
)
\]

where:

- \(x_t\in X_A\) is the product of local states;
- \(G_t\) is the current topology;
- \(Q_t\) is the message/envelope queue or pending-work set;
- \(D_t\) is the provenance DAG;
- \(B_t\) is the resource/accounting state;
- \(Z_t\) is the active/fault status of operators and links;
- \(T_t\) is the append-only execution trace;
- \(E\) is immutable experiment/environment context.

Not every experiment materializes every component separately. This state is the
least common superstructure required to describe the validated systems.

---

## 11. Transition semantics

One logical transition is:

### 11.1 Observe

\[
o_{i,t}
=
\Omega_i(\Sigma_t)
\]

for every active operator selected for policy evaluation.

### 11.2 Propose

\[
p_{i,t}
=
\pi_i(o_{i,t}).
\]

### 11.3 Arbitrate

\[
S_t
=
\alpha(\mathcal{Q}_t,\Sigma_t,\xi_t).
\]

### 11.4 Validate

For each selected proposal \(p\in S_t\):

\[
(c,r)=C(\Sigma_t,p).
\]

### 11.5 Apply

If \(c=1\):

\[
\Sigma_{t+1}
=
F(\Sigma_t,p).
\]

If \(c=0\), semantic application does not occur, but accounting and the trace
advance deterministically:

\[
\Sigma_{t+1}
=
F_{\mathrm{reject}}(\Sigma_t,p,r).
\]

### 11.6 Evaluate

\[
y_{t+1}
=
\Phi(\Sigma_{t+1})
\]

with

\[
y_t
\in
\{
\mathrm{running},
\mathrm{success},
\mathrm{failure},
\mathrm{exhausted}
\}
\]

for the generic population substrate.

Application trials may expose richer quality or closure metrics in addition to
the terminal outcome.

---

## 12. Trace and workflow

The full trace is

\[
T
=
(e_1,e_2,\ldots,e_K)
\]

where each trace event records at least:

- logical sequence/step;
- proposing operator;
- action/message kind;
- acceptance or rejection;
- rejection reason when applicable;
- causal references or produced artifact identity where required;
- accounting contribution.

Define the accepted workflow projection

\[
W(T)
=
(m_{j_1},m_{j_2},\ldots,m_{j_L})
\]

as the ordered subsequence of accepted semantic actions.

Two runs have different **semantic trajectories** when

\[
W(T_a)\neq W(T_b)
\]

or when an experiment-specific semantic digest over accepted/rejected actions
differs.

Trace-byte identity is a stronger condition than semantic trajectory identity.

---

## 13. Cost

The common core cost vector is

\[
J_{\mathrm{core}}
=
(
C_{\mathrm{comm}},
C_{\mathrm{comp}},
C_{\mathrm{viol}}
).
\]

Experiments extend this vector with observables such as:

\[
J =
(
C_{\mathrm{comm}},
C_{\mathrm{comp}},
C_{\mathrm{infer}},
C_{\mathrm{dup}},
C_{\mathrm{tool}},
T,
V,
\ldots
).
\]

Starlings does not currently freeze one universal scalar objective.

A scalarization may be introduced for a specific experiment:

\[
J_w
=
w^\top J
\]

but the weights must be declared before interpreting an optimization result.

For paired quality-threshold experiments, coordination savings may be defined as

\[
\mathrm{Savings}(Q^\star)
=
1
-
\frac{
C_{\mathrm{emergent}}(Q\ge Q^\star)
}{
C_{\mathrm{fixed}}(Q\ge Q^\star)
}.
\]

This is a measurement definition, not yet a universal Starlings law.

---

## 14. Terminal predicates

A terminal state is determined by \(\Phi\) together with runtime constraints.

Define

\[
\mathrm{Terminal}(\Sigma)
\in
\{0,1\}.
\]

A STOP proposal is admissible only when its domain-specific terminal predicate
is satisfied.

### Reconstruction example

A critic may STOP only after an evaluation artifact exists and satisfies the
runtime quality floor or the explicitly bounded work envelope.

### GEOINT epistemic closure

Let \(\mathcal{F}\) be the required evidence-field set and
\(\mathrm{status}(f)\) the epistemic state of field \(f\).

Define closure:

\[
\mathrm{Closed}(\Sigma)
\iff
\forall f\in\mathcal{F},
\quad
\mathrm{status}(f)\neq\mathrm{unknown}.
\]

The GEOINT runtime requires:

\[
\mathrm{STOP}
\implies
\mathrm{Closed}(\Sigma).
\]

This permits uncertainty while forbidding silent omission.

---

## 15. Formal definition of decentralization

A Starlings execution is **policy-local** if:

\[
\forall i,
\quad
p_{i,t}
=
\pi_i(\Omega_i(\Sigma_t))
\]

and \(\Omega_i\) excludes the domain's prohibited global state.

It is **role-restricted** if:

\[
\forall i,
\quad
\mathrm{range}(\pi_i)\subseteq M_i
\]

and \(M_i\) is enforced by \(C\).

It is **centrally unscheduled** if all of the following hold:

1. no global workflow sequence \(W^\star\) is supplied as an execution input;
2. no runtime variable stores a global "next semantic action" program;
3. \(\alpha\) does not branch on a hand-authored semantic phase schedule;
4. each proposal originates from a local policy;
5. runtime admissibility constrains actions but does not prescribe a complete
   successful action sequence.

This definition does **not** claim that local policies contain no structure.
Local policies encode capabilities, dependencies, heuristics, and stopping
conditions. The claim is narrower: the global action sequence is not explicitly
enumerated by a central controller.

---

## 16. Operational definition of workflow emergence

Starlings uses an operational, falsifiable definition rather than a
philosophical one.

A workflow is **emergent with respect to architecture**
\(\mathfrak{A}\) when:

### E1 — locality

The execution is policy-local.

### E2 — specialization

At least two roles have distinct permitted action sets:

\[
\exists i\neq j:
M_i\neq M_j.
\]

For a multi-role workflow, no single role is permitted to emit every accepted
semantic action.

### E3 — no central schedule

The execution is centrally unscheduled.

### E4 — global validity

The accepted trace satisfies all runtime invariants and reaches a valid terminal
state.

### E5 — trajectory sensitivity

Under the same architecture and policy family, there exist two admissible
contexts or seeds such that:

\[
W(T_a)\neq W(T_b)
\]

while both runs remain valid.

E1-E5 define **workflow emergence** for current Starlings experiments.

They do not establish optimality, intelligence, novelty in an information-
theoretic sense, or safety under arbitrary adversaries.

---

## 17. Proven and conditional properties

The statements below are deliberately classified by proof status.

### Theorem 1 — admissibility safety

**Status:** proved by runtime construction.

For every accepted proposal \(p_t\),

\[
\mathrm{accepted}(p_t)
\implies
C(\Sigma_t,p_t)=1.
\]

**Proof sketch.** The runtime computes \(C\) before invoking the semantic
transition. The accepted branch is reachable only when the rejection reason is
empty. Therefore no semantic transition generated by the runtime can originate
from an inadmissible proposal.

This theorem is only as strong as the completeness of \(C\). A missing safety
rule is not repaired by the theorem.

---

### Theorem 2 — provenance parent closure

**Status:** proved by Merkle-DAG insertion construction.

For every stored provenance event \(e\),

\[
e\in D_t
\implies
P(e)\subseteq D_t.
\]

**Proof sketch.** Insertion rejects an event if any declared parent identity is
unknown. Therefore a newly stored node has all declared parents already present.
Induction over insertion order gives closure for the entire DAG.

---

### Theorem 3 — deterministic replay

**Status:** conditional theorem; validated repeatedly in the canonical
experiments.

Assume:

1. fixed initial state \(\Sigma_0\);
2. fixed immutable context \(E\);
3. fixed seed / entropy stream \(\xi\);
4. deterministic \(\Omega,\Pi,\alpha,C,F,\Phi\);
5. canonical external artifacts are byte-identical;
6. no unmodeled nondeterministic side effects affect transitions.

Then the execution trace is unique:

\[
T(\Sigma_0,E,\xi)
=
T'(\Sigma_0,E,\xi).
\]

**Proof.** By induction on logical step \(t\).

At \(t=0\), states are equal by assumption.

If \(\Sigma_t=\Sigma'_t\), deterministic observation gives equal local
observations. Deterministic policies give equal candidate sets. Deterministic
arbitration selects equal proposals. Deterministic admissibility returns equal
decisions. Deterministic transition produces equal \(\Sigma_{t+1}\). Therefore
all future states and trace events are equal.

---

### Theorem 4 — finite monotone workflow termination

**Status:** conditional theorem.

Let \(\mathcal{R}\) be a finite set of required semantic obligations.

Assume:

1. every obligation is represented by a monotone predicate
   \(r_k(\Sigma)\in\{0,1\}\);
2. once \(r_k=1\), accepted transitions never reset it to zero;
3. whenever the system is nonterminal, at least one admissible proposal exists
   that permanently resolves at least one unresolved obligation;
4. arbitration is weakly fair over continuously enabled progress proposals;
5. every selected progress proposal terminates successfully;
6. terminal closure is reached when all obligations are resolved.

Then the execution terminates after finitely many accepted progress actions.

If each accepted progress action resolves at least one previously unresolved
obligation and introduces no new obligations, then:

\[
L
\le
|\mathcal{R}|.
\]

**Interpretation.** This theorem captures finite dependency-graph workloads such
as the bounded GEOINT closure trial. It does not yet cover open-ended search,
repeated refinement, adversarial reintroduction of obligations, or unfair
arbitration.

---

### Theorem 5 — monotone fact convergence under eventual delivery

**Status:** conditional theorem; supported by the fact-diffusion substrate.

Suppose local knowledge states form a finite join-semilattice
\((X,\sqcup)\).

Assume every knowledge transition is inflationary:

\[
x_i(t+1)
=
x_i(t)\sqcup e
\]

for received evidence \(e\), communication is eventually delivered across a
temporally connected graph, and no operator deletes knowledge.

Then every active operator eventually reaches the join of all information that
is causally reachable from the active population:

\[
x_i(\infty)
=
\bigsqcup_{j\in A_{\mathrm{reachable}}} x_j(0).
\]

**Interpretation.** The bitset-union fact experiments are a concrete finite
instance. Fault worlds that permanently destroy all routes for some information
violate the eventual-delivery/reachability assumptions and therefore need not
converge.

---

## 18. Deadlock and progress

Define the admissibly enabled proposal set:

\[
\mathrm{Enabled}(\Sigma_t)
=
\{
p_{i,t}
\mid
p_{i,t}\neq\bot
\land
C(\Sigma_t,p_{i,t})=1
\}.
\]

A state is a **deadlock** when

\[
\neg\mathrm{Terminal}(\Sigma_t)
\land
\mathrm{Enabled}(\Sigma_t)=\varnothing.
\]

A state is **resource exhausted** when work may remain but a frozen resource
bound prevents additional admissible progress.

These are distinct outcomes.

The D3 and GEOINT validators explicitly check:

\[
\mathrm{terminated}=\mathrm{yes},
\qquad
\mathrm{deadlocked}=\mathrm{no}.
\]

A future theorem should characterize graph/policy conditions under which
deadlock freedom holds without enumerating the application workflow.

---

## 19. Fault model

Let

\[
Z_t
\]

represent the fault state.

The validated deterministic fault family includes:

- message loss;
- duplication;
- latency and jitter;
- forced reordering;
- partition / reconnect;
- crash / restart with persistent state;
- crash / restart with reset state;
- stale policy-visible state;
- bounded queues;
- compound contested worlds.

A missing item in the F1a substrate must end in exactly one terminal causal
class:

\[
\{
\mathrm{pending\_at\_censor},
\mathrm{crashed\_before\_merge},
\mathrm{delivery\_faulted},
\mathrm{never\_transmitted}
\}.
\]

Unattributed loss is a validation failure.

The current model does not yet include a canonical Byzantine operator that
emits deliberately deceptive but syntactically valid information. That is the
next adversarial extension after this formalization.

---

## 20. Existing empirical laws

The following are **empirical results**, not axioms of the protocol.

### 20.1 Sparse-load coordinate

Within the tested sparse deterministic regimes, Stage 5C identified the
horizon-normalized information load

\[
\lambda
=
\frac{F}{BH}
\]

where:

- \(F\) is information volume;
- \(B\) is local bandwidth;
- \(H\) is the round horizon.

For fixed \(N=128,R=2\), topology/policy families show distinct transition
ranges in \(\lambda\).

Therefore the current evidence supports \(\lambda\) as a useful regime
coordinate, not a universal threshold constant.

---

### 20.2 Missing-information hazard

For one-round reachability under independent source-route failure, let:

\[
K_0
=
\text{facts initially present at the collector},
\]

\[
M
=
F-K_0,
\]

\[
p
=
\text{source-route fault probability},
\]

\[
R
=
\text{copies per missing fact}.
\]

The exact independence-null hazard is

\[
h
=
-M\log(1-p^R).
\]

The corresponding parameter-free probability approximation is

\[
P(\mathrm{reachable})
\approx
e^{-h}.
\]

At low hazard:

\[
h
\approx
Mp^R.
\]

The coordinate strongly organizes held-out population, density, redundancy,
severity, and compound extrapolation, but the \(e^{-h}\) approximation
underpredicts rare successes in the high-hazard tail.

Therefore the validated claim is primarily about the **hazard coordinate**,
not an exact universal probability law.

---

### 20.3 State-aware inference control

F3 shows that blind probabilistic inference gating did not yield a selected
zero-failure improvement, while deterministic state-aware cache invalidation
did.

The validated controller refreshes local inference when:

\[
\mathrm{no\ cache}
\lor
\mathrm{knowledge\ changed}
\lor
\mathrm{cached\ action\ invalid}
\lor
\mathrm{cached\ action\ semantically\ stale}.
\]

Otherwise the cached local decision is reused.

The exact accounting identity is:

\[
C_{\mathrm{policy}}
=
C_{\mathrm{inference}}
+
C_{\mathrm{cache\ reuse}}.
\]

This is an empirical control mechanism, not a theorem about all local inference
systems.

---

## 21. Empirical emergence evidence

### 21.1 D3 reconstruction workflow

The same six-role architecture generated two valid trajectories:

| Seed | Views | Tool calls | Mock wall time | Final quality |
| --- | ---: | ---: | ---: | ---: |
| 0 | 4 | 16 | 282 ms | 834 |
| 1 | 1 | 7 | 119 ms | 940 |

Both runs:

- used all six roles;
- terminated;
- did not deadlock;
- satisfied runtime invariants;
- replayed deterministically for a fixed seed;
- produced distinct semantic traces across seeds.

Under Definition E1-E5, this is a validated instance of workflow emergence with
respect to the D3 architecture.

It is not evidence that the emergent policy is globally optimal.

---

### 21.2 GEOINT state-dependent operator activation

The same 12-role architecture was tested under two matched contexts.

Base context:

\[
\mathrm{datetime}=\varnothing,
\qquad
\mathrm{shadow\ geometry}=\varnothing.
\]

Result:

\[
\mathrm{ShadowFinderCalls}=0,
\qquad
\mathrm{candidateRegion}=\mathrm{blocked}.
\]

Shadow-ready context:

\[
\mathrm{datetime}\neq\varnothing,
\qquad
\mathrm{shadow\ geometry}\neq\varnothing.
\]

Result:

\[
\mathrm{ShadowFinderCalls}=1,
\qquad
\mathrm{candidateRegion}=\mathrm{derived}.
\]

Both executions reached epistemic closure and STOP.

Thus a local evidence change altered the global operator trajectory without
changing the protocol, role set, or central scheduler.

---

## 22. Candidate Starlings conjectures

The following are the main mathematical targets for the next research phase.

They are **not yet proved**.

### Conjecture A — local dependency emergence

For a finite role-restricted population with monotone causal prerequisites,
semantic-blind arbitration, and locally complete capability coverage, valid
global workflows can be generated without any operator receiving the complete
workflow specification.

A useful target statement is:

\[
\Pr(
\mathrm{Terminal}
\mid
\mathrm{dependency\ satisfiable},
\mathfrak{A}
)
\ge
1-\epsilon.
\]

The immediate task is to identify the structural variables controlling
\(\epsilon\).

---

### Conjecture B — evidence sensitivity

Let \(E\) and \(E'\) differ only in one locally observable evidence variable.

If that evidence changes the enabled set of at least one specialist, then under
a nondegenerate arbitration rule there exists a class of contexts for which:

\[
W(T_E)
\neq
W(T_{E'}).
\]

The GEOINT trial is one concrete witness.

The research question is when this sensitivity is guaranteed, likely, or
suppressed by arbitration and competing proposals.

---

### Conjecture C — bounded graceful degradation

For fault level \(f\) below a topology/redundancy-dependent threshold, expected
quality loss and missing-information mass remain bounded:

\[
\mathbb{E}[\Delta Q]
\le
g(f,G,R,\Pi)
\]

and

\[
\mathbb{E}[M_{\mathrm{missing}}]
\le
h(f,G,R,\Pi).
\]

The existing F1/F2/Stage-6 data constrain candidate forms of \(g\) and \(h\),
but Byzantine behavior is not yet included.

---

### Conjecture D — coordination efficiency from state novelty

For tasks where local action value changes only when locally visible semantic
state changes, state-aware policy refresh should dominate blind periodic
refresh over a nontrivial operating region:

\[
C_{\mathrm{inference}}^{\mathrm{state}}
<
C_{\mathrm{inference}}^{\mathrm{blind}}
\]

subject to matched terminal quality and failure rate.

F3b is supporting evidence for one fact-diffusion family.

---

### Conjecture E — protocol-class transfer

There exists a set of protocol-level coordinates

\[
\chi
=
(
\mathrm{dependency\ depth},
\mathrm{branching},
\mathrm{local\ observability},
\mathrm{redundancy},
\mathrm{bandwidth},
\mathrm{fault\ hazard},
\mathrm{role\ overlap},
\ldots
)
\]

such that coordination behavior across application domains is better predicted
by \(\chi\) than by application identity.

D3 reconstruction and GEOINT are the first cross-domain evidence that motivates
this conjecture.

---

## 23. Byzantine extension

The next adversarial experiment should not merely inject more transport loss.

Introduce a Byzantine subset

\[
B\subseteq A
\]

whose operators remain protocol-valid but may strategically choose:

- false evidence;
- selective withholding;
- equivocation;
- redundant proposal spam;
- locally admissible but globally harmful actions;
- inconsistent confidence;
- adversarial STOP pressure.

The formal target is to characterize a tolerance region such as:

\[
|B|
\le
b^\star(G,R,\Pi,C)
\]

under which selected invariants still hold.

Candidate properties to test separately:

1. **safety containment** — invalid/forbidden actions remain rejected;
2. **provenance containment** — causal origin remains attributable;
3. **epistemic contamination** — false but admissible evidence does not exceed a
   bounded influence region;
4. **progress** — honest operators can still reach terminal closure;
5. **cost amplification** — Byzantine operators cannot cause unbounded
   communication/computation below a declared budget.

This experiment should be designed to falsify a formal robustness claim, not
added as an unstructured new fault matrix.

---

## 24. Mapping from mathematics to code

| Formal object | Current implementation |
| --- | --- |
| \(A\) | \`src/core/formal_population.zig::Population\` |
| \(G\) | \`Topology\` and experiment transport/topology substrates |
| \(X\) | domain \`State\`, operator state, evidence/artifact state |
| \(M\) | \`src/core/message.zig::Kind\` + domain action enums |
| \(\Omega\) | domain \`observe\`, D3/GEOINT role-local observation projections |
| \(\Pi\) | \`Policy\`, D3/GEOINT specialist policies, Stage-7 parameterized policy |
| \(\alpha\) | synchronous batch, queue order, D3/GEOINT BLAKE3 arbitration |
| \(C\) | \`Spec.apply\` validation, D0/D3/GEOINT runtime gates |
| \(F\) | \`Spec.apply\`, operator transitions, tool-backed state transitions |
| \(H\) | BLAKE3 content identity + Merkle-DAG provenance |
| \(\Phi\) | \`Spec.evaluate\`, critic/evaluator, epistemic closure |
| \(J\) | communication/computation/violations + experiment-specific accounting |
| \(T\) | canonical runtime traces, semantic trace digests |

---

## 25. Proof obligations for v0.2

The next formal revision should attempt to close these proof obligations.

### O1 — generalized deterministic replay

Prove replay across synchronous, queue-driven, and semantic-blind event-driven
execution using one common transition-system statement.

### O2 — finite dependency-workflow progress

Formalize dependency obligations as a finite partial order and prove termination
under the actual D3/GEOINT arbitration assumptions rather than abstract weak
fairness.

### O3 — minimal conditions for workflow emergence

Characterize which of E1-E5 are logically necessary versus merely convenient
experimental gates.

### O4 — asynchronous convergence

Lift the semilattice convergence theorem to delayed, reordered, duplicated
delivery with explicit right-censoring and bounded queues.

### O5 — robustness envelope

Connect the missing-information hazard \(h\), topology reachability, and
population dynamics into a theorem with explicit assumptions.

### O6 — Byzantine falsification theorem

State a concrete robustness proposition before running the Byzantine trial.

---

## 26. Claim discipline

Starlings uses the following claim hierarchy.

### Proven

A statement derived from explicit runtime semantics with a proof under declared
assumptions.

### Validated invariant

A structural property checked across the frozen canonical experiment matrix but
not yet proved for the full abstract model.

### Empirical law

A compact relationship supported on declared training/holdout regimes.

### Conjecture

A mathematical statement motivated by evidence but not yet proved or fully
validated.

### Demonstration

A concrete application-level witness of a formal phenomenon.

Every future Starlings result should identify which level it occupies.

---

## 27. Immediate next step

With Formal Model v0.1 frozen, the next work should be:

1. extract the D3 and GEOINT dependency structures into one generic finite
   obligation graph;
2. prove or falsify the finite-progress result under the actual BLAKE3 arbiter;
3. express the F1/F2/F3/F4 datasets in the same symbols;
4. choose one robustness theorem to attack with Byzantine operators.

The intended research loop is now:

\`\`\`text
formal statement
      ↓
derive prediction / invariant
      ↓
run existing or targeted experiment
      ↓
falsify or retain
      ↓
tighten assumptions
      ↓
repeat
\`\`\`

The purpose of the next experiments is therefore no longer to accumulate demos.
It is to attack the mathematics.
