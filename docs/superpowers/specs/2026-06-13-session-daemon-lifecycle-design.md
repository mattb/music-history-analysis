# Session Daemon Lifecycle Design

## Goal

Keep named Last.fm analysis sessions cheap when idle and dependable when reused. A daemon exits after 30 minutes without work. A later analysis command transparently reconstructs it from persisted session metadata and retries the request once.

## Behavioral Contract

- A daemon is idle only when no request is executing and 30 minutes have elapsed since startup or the most recently completed request.
- A request that runs longer than 30 minutes is never interrupted by idle shutdown.
- Any analysis command using `--session` may wake an expired daemon.
- `session-list` and `session-status` are observational. They report `running: false` for expired sessions and never start a process.
- `session-stop` and `session-cleanup` retain their explicit management semantics and never trigger automatic restart.
- A remote analysis error is returned unchanged and does not trigger restart.
- Automatic recovery is attempted once. A second transport or startup failure is returned to the caller.

## Persisted State

The existing per-session directory remains the persistence boundary. `metadata.json` retains the absolute source CSV path and loaded dataset metadata after daemon exit. The Unix socket and PID file describe only the live process and are removed when that process exits normally.

A session can be restarted only when its metadata is readable and its recorded CSV path exists. Missing, corrupt, or incomplete metadata is a hard error; the client must not guess a data source.

## Daemon Lifecycle

`UnixAgentServer` owns an idle tracker using monotonic time, an active-request count, and a lock. Request handling increments the active count before dispatch. Completion decrements it and records the new last-activity time in a `finally` block, including failed requests.

A lightweight watchdog checks the tracker periodically. When the active count is zero and the idle interval reaches 1,800 seconds, it requests orderly server shutdown. Normal shutdown closes and removes the socket and removes the PID file only if that file still names the current process. Metadata remains in place.

The idle duration is a production constant. The server object accepts an injected duration and clock for deterministic tests; no public CLI option is added.

## Client Recovery

All recovery lives behind `dispatch_to_session`, so every analysis command receives identical behavior.

The client first attempts the existing socket request. Missing sockets, refused connections, resets, and truncated responses are transport failures eligible for recovery. On such a failure it:

1. Acquires a per-session filesystem restart lock.
2. Rechecks socket connectivity because another client may already have restarted the daemon.
3. Reads and validates the persisted absolute CSV path.
4. Starts a replacement daemon while consuming startup events privately, so the analysis command still emits exactly one JSON response.
5. Waits for the daemon's `ready` event.
6. Releases the lock and retries the original request once.

If the socket becomes usable while waiting for the lock, the client skips spawning and proceeds directly to the retry. The restart lock uses an advisory file lock scoped to cooperating Last.fm clients; the lock file may persist, but the operating system releases the lock when the client process exits.

`RemoteAgentError` is not a transport failure and bypasses recovery. Metadata validation failures and daemon startup failures are reported with stable, specific messages.

## Status Reporting

`session-list` and `session-status` derive a `running` boolean from the connectable socket or a verified daemon process. Persisted metadata remains visible when `running` is false, allowing users to see sessions that can be woken later. A stale recorded PID is never treated as live unless its process command matches the expected session daemon and session ID.

## Testing

Tests proceed from behavior to implementation:

- idle expiration occurs after the configured interval;
- active requests suppress expiration, including requests longer than the interval;
- request completion resets the idle clock even on command failure;
- orderly exit removes socket and owned PID while preserving metadata;
- a missing socket with valid metadata causes one silent restart and one retry;
- a stale or refused socket follows the same recovery path;
- concurrent clients serialize restart and spawn only one daemon;
- missing, corrupt, incomplete, or nonexistent CSV metadata fails without spawning;
- remote command errors are not retried;
- a failed retry is returned rather than entering a loop;
- status and list report `running: false` without spawning;
- existing one-shot and live-session command behavior remains unchanged.

Focused tests run first, followed by the full test suite and Ruff lint and format checks for touched Python files.

## Documentation

The repository guide and local Last.fm analysis instructions will distinguish persisted sessions from live daemons: named session metadata persists, daemons sleep after 30 idle minutes, and analysis commands wake them transparently. Management commands remain side-effect-free unless their name explicitly requests a start, stop, or cleanup.
