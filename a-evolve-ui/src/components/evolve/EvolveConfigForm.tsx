import { useMemo, useState, type FormEvent } from "react";
import {
  EVOLVE_MODEL_OPTIONS,
  type EvolveConfig,
  type EvolveEnvironment
} from "@/types/evolve-config";

const DEFAULT_CONFIG: EvolveConfig = {
  environment: "appworld",
  main_model: "claude-haiku-4-5-20251001",
  proposer_model: "claude-sonnet-4-5-20250929",
  num_tasks: 10,
  batch_size: 5,
  num_test_tasks: 10,
  on_policy: true,
  eval_vanilla: true,
  eval_evolved: true,
  api_key: ""
};

function apiKeyLabel(model: string): string {
  const m = model.toLowerCase();
  if (m.startsWith("gpt") || m.startsWith("o")) return "OpenAI API Key";
  if (m.startsWith("gemini")) return "Google API Key";
  return "Anthropic API Key";
}

function NumberInput({
  label,
  value,
  onChange,
  min = 1,
  max = 500
}: {
  label: string;
  value: number;
  onChange: (next: number) => void;
  min?: number;
  max?: number;
}) {
  return (
    <label className="flex flex-col gap-1 text-xs text-slate-300">
      {label}
      <input
        type="number"
        min={min}
        max={max}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
        className="rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-slate-100"
      />
    </label>
  );
}

export function EvolveConfigForm({
  onSubmit,
  loading,
  error
}: {
  onSubmit: (config: EvolveConfig) => Promise<void> | void;
  loading?: boolean;
  error?: string | null;
}) {
  const [config, setConfig] = useState<EvolveConfig>(DEFAULT_CONFIG);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const modelOptions = useMemo(() => EVOLVE_MODEL_OPTIONS, []);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    await onSubmit(config);
  };

  return (
    <form onSubmit={submit} className="flex flex-wrap items-end gap-2 text-xs">
      <label className="flex flex-col gap-1 text-slate-300">
        Environment
        <select
          value={config.environment}
          onChange={(event) =>
            setConfig((prev) => ({
              ...prev,
              environment: event.target.value as EvolveEnvironment
            }))
          }
          className="rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-slate-100"
        >
          <option value="appworld">AppWorld</option>
        </select>
      </label>

      <label className="flex flex-col gap-1 text-slate-300">
        Main Model
        <select
          value={config.main_model}
          onChange={(event) =>
            setConfig((prev) => ({ ...prev, main_model: event.target.value }))
          }
          className="rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-slate-100"
        >
          {modelOptions.map((model) => (
            <option key={model} value={model}>
              {model}
            </option>
          ))}
        </select>
      </label>

      <label className="flex flex-col gap-1 text-slate-300">
        Proposer Model
        <select
          value={config.proposer_model}
          onChange={(event) =>
            setConfig((prev) => ({ ...prev, proposer_model: event.target.value }))
          }
          className="rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-slate-100"
        >
          {modelOptions.map((model) => (
            <option key={model} value={model}>
              {model}
            </option>
          ))}
        </select>
      </label>

      <NumberInput
        label="Num Tasks"
        value={config.num_tasks}
        min={1}
        max={500}
        onChange={(next) => setConfig((prev) => ({ ...prev, num_tasks: next }))}
      />
      <NumberInput
        label="Batch Size"
        value={config.batch_size}
        min={1}
        max={100}
        onChange={(next) => setConfig((prev) => ({ ...prev, batch_size: next }))}
      />
      <NumberInput
        label="Num Test Tasks"
        value={config.num_test_tasks}
        min={1}
        max={500}
        onChange={(next) => setConfig((prev) => ({ ...prev, num_test_tasks: next }))}
      />

      <label className="flex flex-col gap-1 text-xs text-slate-300">
        {apiKeyLabel(config.main_model)}
        <input
          type="password"
          placeholder="sk-... (optional if set in env)"
          value={config.api_key ?? ""}
          onChange={(event) =>
            setConfig((prev) => ({ ...prev, api_key: event.target.value }))
          }
          autoComplete="off"
          className="w-48 rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-slate-100 placeholder:text-slate-500"
        />
      </label>

      <button
        type="button"
        className="rounded-md border border-slate-700 bg-slate-800 px-2 py-1 text-xs text-slate-200"
        onClick={() => setShowAdvanced((prev) => !prev)}
      >
        {showAdvanced ? "Hide Advanced" : "Show Advanced"}
      </button>

      <button
        type="submit"
        disabled={loading}
        className="rounded-md border border-indigo-500 bg-indigo-500/20 px-3 py-1.5 text-sm font-semibold text-indigo-200 disabled:cursor-not-allowed disabled:opacity-60"
      >
        {loading ? "Starting..." : "Start Evolve"}
      </button>

      {error ? (
        <div className="w-full rounded-md border border-rose-600/50 bg-rose-900/20 px-3 py-2 text-xs text-rose-300">
          {error}
        </div>
      ) : null}

      {showAdvanced ? (
        <div className="mt-1 flex w-full flex-wrap gap-3 rounded-md border border-slate-700 bg-slate-900/70 p-2 text-xs">
          <label className="inline-flex items-center gap-1 text-slate-300">
            <input
              type="checkbox"
              checked={config.on_policy}
              onChange={(event) =>
                setConfig((prev) => ({ ...prev, on_policy: event.target.checked }))
              }
            />
            on-policy
          </label>
          <label className="inline-flex items-center gap-1 text-slate-300">
            <input
              type="checkbox"
              checked={config.eval_vanilla}
              onChange={(event) =>
                setConfig((prev) => ({ ...prev, eval_vanilla: event.target.checked }))
              }
            />
            eval-vanilla
          </label>
          <label className="inline-flex items-center gap-1 text-slate-300">
            <input
              type="checkbox"
              checked={config.eval_evolved}
              onChange={(event) =>
                setConfig((prev) => ({ ...prev, eval_evolved: event.target.checked }))
              }
            />
            eval-evolved
          </label>
        </div>
      ) : null}
    </form>
  );
}
