# FIXED (v1): do not evolve -- image dispatches off YAML.
# Bare-torch SFT loop. Single-GPU .to("cuda:0"), gradient_checkpointing(use_reentrant=False).

from __future__ import annotations

import csv
import json
import os
import sys
import time
from pathlib import Path

# When running inside Docker, common.py lives at /opt/aevolve/common.py.
# When running locally, it's at <workspace>/docker/common.py.
# Add both to sys.path so `import common` works either way.
sys.path.insert(0, "/opt/aevolve")
sys.path.insert(0, str(Path(os.environ.get("AEVOLVE_WORKSPACE", "/workspace")) / "docker"))

import torch
from common import env_path, load_yaml, resolve_uri


def main() -> None:
    workspace = env_path("AEVOLVE_WORKSPACE", "/workspace")
    out = env_path("AEVOLVE_OUT", "/out")
    out.mkdir(parents=True, exist_ok=True)

    training_cfg = load_yaml(workspace / "training_config.yaml")
    data_cfg = load_yaml(workspace / "data_config.yaml")
    base_cfg = load_yaml(workspace / "base_ckpt.yaml")

    from peft import LoraConfig, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer

    base_uri = resolve_uri(base_cfg["uri"])
    tokenizer_uri = resolve_uri(base_cfg.get("tokenizer", base_cfg["uri"]))

    tok = AutoTokenizer.from_pretrained(tokenizer_uri, trust_remote_code=True)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    # --- Data -----------------------------------------------------------------
    source = (data_cfg.get("sources") or [{}])[0]
    csv_path = Path(resolve_uri(source.get("uri", "")))
    if not csv_path.exists():
        data_root = env_path("AEVOLVE_DATA", "/data")
        rel = csv_path.relative_to("/data") if str(csv_path).startswith("/data") else csv_path
        csv_path = data_root / rel
    if not csv_path.exists():
        print(f"[train] CSV not found: {csv_path}", file=sys.stderr)
        sys.exit(2)

    fields = source.get("fields", {"prompt": "prompt", "answer": "answer"})
    system_prompt = (data_cfg.get("format") or {}).get(
        "system_prompt", r"Solve the puzzle. Put your final answer in \boxed{...}."
    )
    max_seq_len = int(training_cfg["model"].get("max_seq_len", 4096))

    print(f"[train] loading CSV {csv_path}")
    examples = []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": row[fields["prompt"]]},
            ]
            prefix = tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
            target = f"\\boxed{{{row[fields['answer']]}}}"
            full = prefix + target + tok.eos_token

            ids_p = tok.encode(prefix, add_special_tokens=False)
            ids_full = tok.encode(full, add_special_tokens=False)
            if len(ids_full) > max_seq_len:
                over = len(ids_full) - max_seq_len
                ids_full = ids_full[over:]
                ids_p = ids_p[max(0, len(ids_p) - (len(ids_full) - len(ids_p))):]
            labels = [-100] * min(len(ids_p), len(ids_full)) + ids_full[min(len(ids_p), len(ids_full)):]
            examples.append({"input_ids": ids_full, "labels": labels})
    print(f"[train] tokenized {len(examples)} examples")

    # --- Model ----------------------------------------------------------------
    print(f"[train] loading base model {base_uri}")
    t0 = time.time()
    model = AutoModelForCausalLM.from_pretrained(
        base_uri, trust_remote_code=True, torch_dtype=torch.bfloat16,
        attn_implementation="eager",
    ).to("cuda:0")
    model.config.use_cache = False
    print(f"[train] model loaded in {time.time() - t0:.1f}s")

    lora_cfg = training_cfg["model"]["lora"]
    targets = list(lora_cfg.get("target_modules", ["q_proj", "k_proj", "v_proj", "o_proj"]))
    model = get_peft_model(model, LoraConfig(
        r=int(lora_cfg["rank"]), lora_alpha=int(lora_cfg["alpha"]),
        lora_dropout=float(lora_cfg.get("dropout", 0.0)),
        bias="none", task_type="CAUSAL_LM", target_modules=targets,
    ))
    model.print_trainable_parameters()
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    model.train()

    # --- Training loop --------------------------------------------------------
    lr = float(training_cfg["optimizer"]["lr"])
    grad_accum = int(training_cfg["batch"].get("grad_accum", 8))
    max_steps = int(training_cfg["schedule"].get("total_steps", 200))
    warmup_steps = int(training_cfg["schedule"].get("warmup_steps", 5))

    optimizer = torch.optim.AdamW(
        [p for p in model.parameters() if p.requires_grad], lr=lr, fused=True,
    )

    print(f"[train] starting: max_steps={max_steps} lr={lr} grad_accum={grad_accum}")
    gstep, micro, accum_loss = 0, 0, 0.0
    optimizer.zero_grad()
    t_start = time.time()
    epoch = 0

    while gstep < max_steps:
        perm = torch.randperm(len(examples), generator=torch.Generator().manual_seed(42 + epoch))
        for idx in perm.tolist():
            if gstep >= max_steps:
                break
            ex = examples[idx]
            x = torch.tensor(ex["input_ids"], dtype=torch.long, device="cuda:0").unsqueeze(0)
            y = torch.tensor(ex["labels"], dtype=torch.long, device="cuda:0").unsqueeze(0)
            loss = model(x, labels=y).loss / grad_accum
            loss.backward()
            accum_loss += loss.detach().item() * grad_accum
            micro += 1
            if micro % grad_accum == 0:
                cur_lr = lr * min(1.0, (gstep + 1) / max(warmup_steps, 1))
                for g in optimizer.param_groups:
                    g["lr"] = cur_lr
                optimizer.step()
                optimizer.zero_grad()
                gstep += 1
                if gstep % 10 == 0 or gstep == 1:
                    print(f"  step {gstep}/{max_steps}  loss={accum_loss / grad_accum:.4f}  "
                          f"lr={cur_lr:.2e}  elapsed={(time.time() - t_start) / 60:.1f}min",
                          flush=True)
                accum_loss = 0.0
        epoch += 1

    elapsed = time.time() - t_start
    print(f"[train] done in {elapsed / 60:.1f} min ({gstep} steps)")

    adapter_dir = out / "adapter"
    model.save_pretrained(str(adapter_dir))
    tok.save_pretrained(str(adapter_dir))
    with open(out / "train_meta.json", "w") as f:
        json.dump({"cycle": os.environ.get("AEVOLVE_CYCLE", ""), "max_steps": max_steps,
                    "lr": lr, "lora_rank": int(lora_cfg["rank"]),
                    "wall_minutes": round(elapsed / 60, 2), "ok": True}, f)
    print(f"[train] adapter saved to {adapter_dir}")


if __name__ == "__main__":
    main()
