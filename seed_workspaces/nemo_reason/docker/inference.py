"""`inference` verb: load base + LoRA adapter, score prompts.

Data-parallel across all visible GPUs: each GPU runs an independent replica
(full model + LoRA) on its shard of the prompts, then shards are merged in
original order. This gives N× throughput on N GPUs, which is the best we can
do in Docker — vLLM's V1 engine segfaults on NemotronH regardless of TP size.

Env vars:
  AEVOLVE_WORKSPACE  (default /workspace)
  AEVOLVE_CKPT       (default /ckpt)
  AEVOLVE_OUT        (default /out)
  AEVOLVE_INFER_MODE (eval | data_gen)
  AEVOLVE_DP_WORKERS (override GPU count; default = torch.cuda.device_count())
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, "/opt/aevolve")
sys.path.insert(0, str(Path(os.environ.get("AEVOLVE_WORKSPACE", "/workspace")) / "docker"))

from common import env_path, extract_boxed, load_yaml, read_jsonl, resolve_uri, write_jsonl


def _build_chat_prompt(tokenizer, user_prompt: str, system_prompt: str | None) -> str:
    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})
    try:
        return tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    except Exception:
        return f"{system_prompt or ''}\n\nUser: {user_prompt}\nAssistant:"


def _run_shard_in_worker(
    gpu_id: int,
    prompts_shard: list[dict],
    base_model: str,
    ckpt_dir: str,
    system_prompt: str | None,
    max_tokens: int,
    batch_size: int,
    extractor: str,
    shard_output_path: str,
) -> None:
    """Runs inside a spawned worker subprocess. CUDA_VISIBLE_DEVICES already set."""
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tag = f"[worker gpu={gpu_id} pid={os.getpid()}]"
    print(f"{tag} starting, {len(prompts_shard)} prompts", flush=True)

    t0 = time.time()
    tokenizer = AutoTokenizer.from_pretrained(base_model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "left"

    model = AutoModelForCausalLM.from_pretrained(
        base_model, trust_remote_code=True, torch_dtype=torch.bfloat16,
        attn_implementation="eager",
    ).to("cuda:0")
    model = PeftModel.from_pretrained(model, ckpt_dir)
    model.eval()
    print(f"{tag} model ready in {time.time() - t0:.1f}s", flush=True)

    rendered = [_build_chat_prompt(tokenizer, p.get("prompt", ""), system_prompt)
                for p in prompts_shard]

    rows: list[dict[str, Any]] = []
    t_gen = time.time()
    for start in range(0, len(prompts_shard), batch_size):
        batch_prompts = prompts_shard[start:start + batch_size]
        batch_texts = rendered[start:start + batch_size]
        enc = tokenizer(
            batch_texts, return_tensors="pt", padding=True, truncation=True,
            max_length=8192 - max_tokens,
        ).to("cuda:0")
        with torch.no_grad():
            output_ids = model.generate(
                **enc,
                max_new_tokens=max_tokens,
                do_sample=False,
                temperature=None,
                top_p=None,
                pad_token_id=tokenizer.pad_token_id,
                eos_token_id=tokenizer.eos_token_id,
            )
        input_len = enc["input_ids"].shape[1]
        for j, prompt_row in enumerate(batch_prompts):
            new_ids = output_ids[j, input_len:]
            raw_response = tokenizer.decode(new_ids, skip_special_tokens=True)
            prediction = extract_boxed(raw_response) if extractor == "boxed" else raw_response
            rows.append({
                "_orig_idx": prompt_row["_orig_idx"],
                "task_id": prompt_row.get("task_id"),
                "prediction": f"\\boxed{{{prediction}}}" if extractor == "boxed" and prediction else prediction,
                "raw_response": raw_response,
            })
        elapsed = time.time() - t_gen
        print(f"{tag} batch {start // batch_size + 1}: {len(rows)}/{len(prompts_shard)} done "
              f"({elapsed:.0f}s elapsed)", flush=True)

    with open(shard_output_path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    print(f"{tag} wrote {len(rows)} rows → {shard_output_path}", flush=True)


def run_eval(cfg: dict, workspace: Path, ckpt_dir: Path, out_dir: Path) -> int:
    inf = cfg.get("inference", {})

    def _rebase(p: str) -> Path:
        path = Path(p)
        if str(path).startswith("/out"):
            return out_dir / path.relative_to("/out")
        return path

    prompts_path = _rebase(inf.get("prompts_path", "/out/prompts.jsonl"))
    output_path = _rebase(inf.get("output_path", "/out/scores.jsonl"))
    max_tokens = int(inf.get("max_tokens", 2048))
    batch_size = int(inf.get("batch_size", 4))
    extractor = inf.get("answer_extractor", "boxed")

    base_model = resolve_uri(load_yaml(workspace / "base_ckpt.yaml").get("uri", ""))
    if not base_model:
        print("[inference] base_ckpt.uri missing", file=sys.stderr)
        return 2

    prompts = read_jsonl(prompts_path)
    if not prompts:
        print(f"[inference] no prompts at {prompts_path} — writing empty scores.jsonl")
        write_jsonl(output_path, [])
        return 0

    data_cfg = load_yaml(workspace / "data_config.yaml")
    system_prompt = (data_cfg.get("format") or {}).get("system_prompt")

    # Figure out worker count without importing torch in the main process
    # (would initialize CUDA before CUDA_VISIBLE_DEVICES is set in children).
    override = os.environ.get("AEVOLVE_DP_WORKERS")
    if override:
        n_workers = int(override)
    else:
        # Use nvidia-smi to count GPUs without touching torch
        r = subprocess.run(["nvidia-smi", "--list-gpus"], capture_output=True, text=True)
        n_workers = len([l for l in r.stdout.splitlines() if l.strip()]) or 1
    n_workers = min(n_workers, len(prompts))
    print(f"[inference] DP fan-out across {n_workers} GPU(s), "
          f"{len(prompts)} prompts, max_tokens={max_tokens}, batch_size={batch_size}",
          flush=True)

    # Stamp each prompt with its original index so we can reassemble order.
    stamped = [{"_orig_idx": i, **p} for i, p in enumerate(prompts)]

    # Round-robin assign to workers (keeps shard sizes balanced).
    shards: list[list[dict]] = [[] for _ in range(n_workers)]
    for i, p in enumerate(stamped):
        shards[i % n_workers].append(p)

    shard_dir = out_dir / "_dp_shards"
    shard_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    procs: list[subprocess.Popen] = []
    log_paths: list[Path] = []
    shard_paths: list[Path] = []
    for gpu_id in range(n_workers):
        shard_in = shard_dir / f"prompts_{gpu_id}.jsonl"
        shard_out = shard_dir / f"scores_{gpu_id}.jsonl"
        log_path = shard_dir / f"worker_{gpu_id}.log"
        with open(shard_in, "w") as f:
            for p in shards[gpu_id]:
                f.write(json.dumps(p) + "\n")

        env = os.environ.copy()
        env["CUDA_VISIBLE_DEVICES"] = str(gpu_id)

        cmd = [
            sys.executable, __file__,
            "--worker-run",
            "--worker-gpu-id", str(gpu_id),
            "--worker-base-model", base_model,
            "--worker-ckpt-dir", str(ckpt_dir),
            "--worker-shard-in", str(shard_in),
            "--worker-shard-out", str(shard_out),
            "--worker-max-tokens", str(max_tokens),
            "--worker-batch-size", str(batch_size),
            "--worker-extractor", extractor,
        ]
        if system_prompt:
            cmd.extend(["--worker-system-prompt", system_prompt])

        log_f = open(log_path, "w")
        proc = subprocess.Popen(cmd, env=env, stdout=log_f, stderr=subprocess.STDOUT)
        procs.append(proc)
        log_paths.append(log_path)
        shard_paths.append(shard_out)

    # Wait for all workers
    failed = []
    for gpu_id, proc in enumerate(procs):
        rc = proc.wait()
        if rc != 0:
            failed.append((gpu_id, rc))

    if failed:
        for gpu_id, rc in failed:
            print(f"[inference] worker gpu={gpu_id} failed rc={rc} — "
                  f"log at {log_paths[gpu_id]}", file=sys.stderr)
            try:
                print(f"--- tail {log_paths[gpu_id]} ---", file=sys.stderr)
                with open(log_paths[gpu_id]) as f:
                    lines = f.readlines()[-40:]
                    for l in lines:
                        sys.stderr.write(l)
            except Exception:
                pass
        return 3

    # Merge shard outputs in original prompt order
    all_rows: list[dict] = []
    for sp in shard_paths:
        with open(sp) as f:
            for line in f:
                line = line.strip()
                if line:
                    all_rows.append(json.loads(line))
    all_rows.sort(key=lambda r: r["_orig_idx"])
    for r in all_rows:
        r.pop("_orig_idx", None)

    write_jsonl(output_path, all_rows)
    elapsed = time.time() - t0
    print(f"[inference] wrote {len(all_rows)} rows → {output_path} "
          f"(DP={n_workers}, total {elapsed:.0f}s)")
    return 0


def run_data_gen(cfg: dict, workspace: Path, ckpt_dir: Path, out_dir: Path) -> int:
    print("[inference] data_gen mode not implemented; emitting empty gen.jsonl")
    write_jsonl(out_dir / "gen.jsonl", [])
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config")
    # worker-mode args
    parser.add_argument("--worker-run", action="store_true")
    parser.add_argument("--worker-gpu-id", type=int)
    parser.add_argument("--worker-base-model")
    parser.add_argument("--worker-ckpt-dir")
    parser.add_argument("--worker-shard-in")
    parser.add_argument("--worker-shard-out")
    parser.add_argument("--worker-max-tokens", type=int)
    parser.add_argument("--worker-batch-size", type=int)
    parser.add_argument("--worker-extractor")
    parser.add_argument("--worker-system-prompt", default=None)
    args = parser.parse_args()

    if args.worker_run:
        shard_prompts = []
        with open(args.worker_shard_in) as f:
            for line in f:
                line = line.strip()
                if line:
                    shard_prompts.append(json.loads(line))
        _run_shard_in_worker(
            gpu_id=args.worker_gpu_id,
            prompts_shard=shard_prompts,
            base_model=args.worker_base_model,
            ckpt_dir=args.worker_ckpt_dir,
            system_prompt=args.worker_system_prompt,
            max_tokens=args.worker_max_tokens,
            batch_size=args.worker_batch_size,
            extractor=args.worker_extractor,
            shard_output_path=args.worker_shard_out,
        )
        return

    if not args.config:
        print("[inference] --config required", file=sys.stderr)
        sys.exit(2)

    workspace = env_path("AEVOLVE_WORKSPACE", "/workspace")
    ckpt_dir = env_path("AEVOLVE_CKPT", "/ckpt")
    out_dir = env_path("AEVOLVE_OUT", "/out")

    cfg_path = workspace / args.config
    if not cfg_path.exists():
        print(f"[inference] config not found: {cfg_path}", file=sys.stderr)
        sys.exit(2)
    cfg = load_yaml(cfg_path)

    mode = os.environ.get("AEVOLVE_INFER_MODE") or cfg.get("inference", {}).get("mode", "eval")
    if mode == "eval":
        sys.exit(run_eval(cfg, workspace, ckpt_dir, out_dir))
    if mode == "data_gen":
        sys.exit(run_data_gen(cfg, workspace, ckpt_dir, out_dir))
    print(f"[inference] unknown mode: {mode!r}", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
