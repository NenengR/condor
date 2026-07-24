import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  CheckCircle2,
  Clock,
  Minus,
  Radio,
  XCircle,
} from "lucide-react";

import { api } from "@/lib/api";
import type {
  AgentHealth,
  PipelineAlert,
  PipelineSignal,
} from "@/lib/api";

const STATUS_COLORS: Record<string, string> = {
  healthy: "text-emerald-400",
  degraded: "text-amber-400",
  stale: "text-orange-400",
  stopped: "text-red-400",
  unknown: "text-gray-400",
};

const STATUS_DOT: Record<string, string> = {
  healthy: "bg-emerald-400",
  degraded: "bg-amber-400",
  stale: "bg-orange-400",
  stopped: "bg-red-400",
  unknown: "bg-gray-400",
};

const DIRECTION_ICON: Record<string, typeof ArrowUp> = {
  long: ArrowUp,
  short: ArrowDown,
  neutral: Minus,
  reduce: ArrowDown,
};

const DIRECTION_COLOR: Record<string, string> = {
  long: "text-emerald-400",
  short: "text-red-400",
  neutral: "text-gray-400",
  reduce: "text-orange-400",
};

function formatAge(isoStr: string): string {
  const diff = Date.now() - new Date(isoStr).getTime();
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

function formatTime(isoStr: string): string {
  return new Date(isoStr).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatAgentName(id: string): string {
  return id.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function AlertsBanner({ alerts }: { alerts: PipelineAlert[] }) {
  if (!alerts.length) return null;

  const errors = alerts.filter((a) => a.level === "error");
  const warnings = alerts.filter((a) => a.level === "warning");

  return (
    <div className="space-y-2">
      {errors.map((a, i) => (
        <div
          key={`e-${i}`}
          className="flex items-center gap-2 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-2 text-sm text-red-300"
        >
          <XCircle className="h-4 w-4 shrink-0" />
          {a.message}
        </div>
      ))}
      {warnings.map((a, i) => (
        <div
          key={`w-${i}`}
          className="flex items-center gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-2 text-sm text-amber-300"
        >
          <AlertTriangle className="h-4 w-4 shrink-0" />
          {a.message}
        </div>
      ))}
    </div>
  );
}

function AgentHealthCard({ agent }: { agent: AgentHealth }) {
  return (
    <div className="flex items-start gap-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3">
      <span
        className={`mt-1 inline-block h-2.5 w-2.5 shrink-0 rounded-full ${STATUS_DOT[agent.status] ?? STATUS_DOT.unknown}`}
      />
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between">
          <span className="text-sm font-medium text-[var(--color-text)]">
            {formatAgentName(agent.agent_id)}
          </span>
          <span
            className={`text-xs font-medium ${STATUS_COLORS[agent.status] ?? STATUS_COLORS.unknown}`}
          >
            {agent.status}
          </span>
        </div>
        {agent.strategy && (
          <p className="text-xs text-[var(--color-text-muted)]">
            {agent.strategy}
          </p>
        )}
        <div className="mt-1 flex items-center gap-3 text-xs text-[var(--color-text-muted)]">
          {agent.last_tick && (
            <>
              <span>{agent.last_tick.tool_count} tools</span>
              <span>{agent.last_tick.response_chars} chars</span>
            </>
          )}
          {agent.last_tick_at && (
            <span className="flex items-center gap-1">
              <Clock className="h-3 w-3" />
              {formatAge(agent.last_tick_at)}
            </span>
          )}
          {agent.session_num != null && (
            <span>session #{agent.session_num}</span>
          )}
        </div>
      </div>
    </div>
  );
}

function SignalRow({ signal }: { signal: PipelineSignal }) {
  const DirIcon = DIRECTION_ICON[signal.direction] ?? Minus;
  const dirColor = DIRECTION_COLOR[signal.direction] ?? "text-gray-400";

  return (
    <div className="flex items-center gap-3 border-b border-[var(--color-border)] px-3 py-2 last:border-b-0">
      <span className="w-12 shrink-0 text-xs text-[var(--color-text-muted)]">
        {formatTime(signal.created_at)}
      </span>
      <span className="w-24 shrink-0 text-sm font-medium text-[var(--color-text)]">
        {signal.pair}
      </span>
      <span className={`flex items-center gap-1 text-sm ${dirColor}`}>
        <DirIcon className="h-3.5 w-3.5" />
        {signal.direction}
      </span>
      <span className="ml-auto text-sm tabular-nums text-[var(--color-text-muted)]">
        {(signal.confidence * 100).toFixed(0)}%
      </span>
      <span className="w-28 shrink-0 truncate text-xs text-[var(--color-text-muted)]">
        {signal.source}
      </span>
      <span className="w-16 shrink-0 text-xs text-[var(--color-text-muted)]">
        {signal.signal_type}
      </span>
    </div>
  );
}

function WatchlistCard({ signal }: { signal: PipelineSignal }) {
  const meta = signal.metadata || {};
  const tags: string[] = [];
  if (meta.volume_trend) tags.push(String(meta.volume_trend));
  if (meta.coiled_range) tags.push("coiled");
  if (meta.btc_trend) tags.push(`BTC ${meta.btc_trend}`);
  if (meta.liquidity) tags.push(String(meta.liquidity));

  return (
    <div className="flex items-center justify-between rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2">
      <div>
        <span className="text-sm font-medium text-[var(--color-text)]">
          {signal.pair}
        </span>
        <div className="mt-0.5 flex gap-1">
          {tags.slice(0, 3).map((t) => (
            <span
              key={t}
              className="rounded bg-[var(--color-primary)]/10 px-1.5 py-0.5 text-xs text-[var(--color-primary)]"
            >
              {t}
            </span>
          ))}
        </div>
      </div>
      <div className="text-right">
        <span className={`text-sm ${DIRECTION_COLOR[signal.direction] ?? "text-gray-400"}`}>
          {signal.direction}
        </span>
        <p className="text-xs text-[var(--color-text-muted)]">
          {(signal.confidence * 100).toFixed(0)}%
        </p>
      </div>
    </div>
  );
}

export function Pipeline() {
  const { data: signalsData, isLoading: signalsLoading } = useQuery({
    queryKey: ["pipeline-signals"],
    queryFn: () => api.getPipelineSignals(50),
    refetchInterval: 30_000,
  });

  const { data: healthData, isLoading: healthLoading } = useQuery({
    queryKey: ["pipeline-health"],
    queryFn: () => api.getPipelineHealth(),
    refetchInterval: 15_000,
  });

  const agents = healthData?.agents ?? [];
  const alerts = healthData?.alerts ?? [];
  const recentSignals = signalsData?.recent ?? [];
  const activeSignals = signalsData?.active ?? [];

  const watchlist = activeSignals.filter(
    (s) =>
      s.signal_type === "opportunity" ||
      (s.source === "market_screener" && s.pair === "WATCHLIST"),
  );

  const directionalSignals = recentSignals.filter(
    (s) => s.signal_type !== "opportunity" || s.pair !== "WATCHLIST",
  );

  const healthyCount = agents.filter((a) => a.status === "healthy").length;
  const allHealthy = alerts.length === 0 && healthyCount === agents.length;

  return (
    <div className="mx-auto max-w-7xl space-y-4 p-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-[var(--color-text)]">
          Pipeline Health
        </h1>
        <div className="flex items-center gap-2">
          {allHealthy ? (
            <span className="flex items-center gap-1.5 rounded-full bg-emerald-500/15 px-3 py-1 text-xs font-medium text-emerald-400">
              <CheckCircle2 className="h-3.5 w-3.5" />
              All OK
            </span>
          ) : (
            <span className="flex items-center gap-1.5 rounded-full bg-amber-500/15 px-3 py-1 text-xs font-medium text-amber-400">
              <AlertTriangle className="h-3.5 w-3.5" />
              {alerts.length} alert{alerts.length !== 1 ? "s" : ""}
            </span>
          )}
        </div>
      </div>

      {/* Alerts */}
      <AlertsBanner alerts={alerts} />

      {/* Main grid */}
      <div className="grid gap-4 lg:grid-cols-[340px_1fr]">
        {/* Left: Agent Health */}
        <div className="space-y-3">
          <h2 className="text-sm font-medium text-[var(--color-text-muted)]">
            Agent Health
          </h2>
          {healthLoading ? (
            <div className="py-8 text-center text-sm text-[var(--color-text-muted)]">
              Loading...
            </div>
          ) : agents.length === 0 ? (
            <div className="py-8 text-center text-sm text-[var(--color-text-muted)]">
              No agents found
            </div>
          ) : (
            <div className="space-y-2">
              {agents.map((a, i) => (
                <AgentHealthCard key={`${a.agent_id}-${a.strategy}-${i}`} agent={a} />
              ))}
            </div>
          )}
        </div>

        {/* Right: Signals + Watchlist */}
        <div className="space-y-4">
          {/* Recent Signals */}
          <div>
            <h2 className="mb-2 text-sm font-medium text-[var(--color-text-muted)]">
              Recent Signals
            </h2>
            <div className="overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]">
              {signalsLoading ? (
                <div className="py-8 text-center text-sm text-[var(--color-text-muted)]">
                  Loading...
                </div>
              ) : directionalSignals.length === 0 ? (
                <div className="py-8 text-center text-sm text-[var(--color-text-muted)]">
                  No recent signals
                </div>
              ) : (
                <div className="max-h-[400px] overflow-y-auto">
                  {directionalSignals.slice(0, 30).map((s) => (
                    <SignalRow key={s.signal_id} signal={s} />
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* Watchlist */}
          <div>
            <h2 className="mb-2 text-sm font-medium text-[var(--color-text-muted)]">
              Watchlist
            </h2>
            {watchlist.length === 0 ? (
              <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] py-6 text-center text-sm text-[var(--color-text-muted)]">
                No active watchlist signals
              </div>
            ) : (
              <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {watchlist.map((s) => (
                  <WatchlistCard key={s.signal_id} signal={s} />
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
