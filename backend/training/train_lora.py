"""LoRA SFT of a small open model on the NLU dataset, plus held-out evaluation.

Runs on a free Colab T4 (Gemma-2B: ~25 min for 3 epochs) or on a Mac (slow). Needs the
backend package installed (`pip install -e backend`) plus `torch transformers peft accelerate`.

  python training/train_lora.py train --base google/gemma-2b-it --out training/adapter
  python training/train_lora.py eval  --base google/gemma-2b-it --adapter training/adapter --out training/reports/after-gemma2b-local.json
  python training/train_lora.py eval  --base google/gemma-2b-it                            # base model, no adapter (zero-shot)
  python training/train_lora.py push  --adapter training/adapter --repo <hf-user>/vca-nlu-gemma-2b-lora

The adapter targets the attention projections only and uses rank 8, which is what
Cloudflare Workers AI accepts for BYO LoRA (`adapter_config.json` + `adapter_model.safetensors`,
< 300 MB, non-quantised base). Loss is computed on the completion only (prompt tokens masked).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]  # backend/
sys.path.insert(0, str(ROOT))

from app.domain.models import DialogPhase  # noqa: E402
from app.domain.turn_context import constrain, context_for  # noqa: E402
from app.domain.understanding import validate_llm_output  # noqa: E402
from app.evaluation.nlu_bench import compact, markdown, score, summarise  # noqa: E402
from app.training.format import GEMMA_END, parse_model_json, prompt_for, target_for  # noqa: E402

DATA = ROOT / "training" / "data"
TARGET_MODULES = ["q_proj", "k_proj", "v_proj", "o_proj"]


def load_jsonl(p: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def pick_dtype(name: str) -> Any:
    """auto: bf16 on Ampere+, else fp16 on CUDA, else fp32. Gemma in fp16 can overflow (nan loss)
    on a T4; if that happens pass --dtype fp32 with a smaller batch."""
    import torch

    if name == "fp32":
        return torch.float32
    if name == "bf16":
        return torch.bfloat16
    if name == "fp16":
        return torch.float16
    if torch.cuda.is_available():
        return torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    return torch.float32


# ----------------------------------------------------------------------------- train
def cmd_train(a: argparse.Namespace) -> int:
    import torch
    from peft import LoraConfig, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer, Trainer, TrainingArguments

    tok = AutoTokenizer.from_pretrained(a.base)
    tok.padding_side = "right"
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    dtype = pick_dtype(a.dtype)
    model = AutoModelForCausalLM.from_pretrained(
        a.base, dtype=dtype, device_map="auto" if torch.cuda.is_available() else None
    )
    model.config.use_cache = False
    if hasattr(model, "enable_input_require_grads"):
        model.enable_input_require_grads()
    lcfg = LoraConfig(
        r=a.r, lora_alpha=a.alpha, lora_dropout=0.05, target_modules=TARGET_MODULES, task_type="CAUSAL_LM"
    )
    model = get_peft_model(model, lcfg)
    model.print_trainable_parameters()

    rows = load_jsonl(DATA / "train.jsonl")

    def encode(rec: dict[str, Any]) -> dict[str, Any]:
        p_ids = tok(prompt_for(rec), add_special_tokens=True)["input_ids"]
        t_ids = tok(target_for(rec), add_special_tokens=False)["input_ids"]
        ids = (p_ids + t_ids)[: a.max_len]
        labels = ([-100] * len(p_ids) + t_ids)[: a.max_len]
        return {"input_ids": ids, "labels": labels, "attention_mask": [1] * len(ids)}

    class DS(torch.utils.data.Dataset):  # type: ignore[type-arg]
        def __init__(self, items: list[dict[str, Any]]) -> None:
            self.items = [encode(r) for r in items]

        def __len__(self) -> int:
            return len(self.items)

        def __getitem__(self, i: int) -> dict[str, Any]:
            return self.items[i]

    def collate(batch: list[dict[str, Any]]) -> dict[str, Any]:
        n = max(len(b["input_ids"]) for b in batch)
        pad = tok.pad_token_id
        out = {"input_ids": [], "labels": [], "attention_mask": []}
        for b in batch:
            k = n - len(b["input_ids"])
            out["input_ids"].append(b["input_ids"] + [pad] * k)
            out["labels"].append(b["labels"] + [-100] * k)
            out["attention_mask"].append(b["attention_mask"] + [0] * k)
        return {key: torch.tensor(v) for key, v in out.items()}

    args = TrainingArguments(
        output_dir=str(Path(a.out) / "checkpoints"),
        num_train_epochs=a.epochs,
        per_device_train_batch_size=a.batch,
        gradient_accumulation_steps=a.grad_accum,
        learning_rate=a.lr,
        lr_scheduler_type="cosine",
        warmup_steps=8,
        logging_steps=10,
        save_strategy="no",
        report_to=[],
        bf16=dtype == torch.bfloat16,
        fp16=dtype == torch.float16,
        gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        max_steps=a.max_steps if a.max_steps else -1,
        seed=a.seed,
    )
    trainer = Trainer(model=model, args=args, train_dataset=DS(rows), data_collator=collate)
    t0 = time.time()
    trainer.train()
    model.save_pretrained(a.out, safe_serialization=True)
    meta = {
        "base": a.base,
        "r": a.r,
        "alpha": a.alpha,
        "epochs": a.epochs,
        "lr": a.lr,
        "max_len": a.max_len,
        "n_train": len(rows),
        "target_modules": TARGET_MODULES,
        "train_seconds": round(time.time() - t0),
        "seed": a.seed,
    }
    (Path(a.out) / "training_meta.json").write_text(json.dumps(meta, indent=2))
    print(json.dumps(meta, indent=2))
    return 0


# ----------------------------------------------------------------------------- eval
def cmd_eval(a: argparse.Namespace) -> int:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(a.base)
    tok.padding_side = "left"
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    dtype = pick_dtype(a.dtype)
    model = AutoModelForCausalLM.from_pretrained(
        a.base, dtype=dtype, device_map="auto" if torch.cuda.is_available() else None
    )
    if a.adapter:
        from peft import PeftModel

        model = PeftModel.from_pretrained(model, a.adapter)
        model = model.merge_and_unload()
    model.eval()
    rows = load_jsonl(Path(a.data))
    if a.limit:
        rows = rows[: a.limit]
    end_id = tok.convert_tokens_to_ids(GEMMA_END)
    results: list[dict[str, Any]] = []
    for i in range(0, len(rows), a.batch):
        chunk = rows[i : i + a.batch]
        prompts = [prompt_for(r) for r in chunk]
        enc = tok(prompts, return_tensors="pt", padding=True).to(model.device)
        t0 = time.monotonic()
        with torch.no_grad():
            out = model.generate(
                **enc,
                max_new_tokens=a.max_new,
                do_sample=False,
                eos_token_id=[end_id, tok.eos_token_id],
                pad_token_id=tok.pad_token_id,
            )
        per_item_ms = (time.monotonic() - t0) * 1000 / len(chunk)
        for rec, seq in zip(chunk, out, strict=True):
            text = tok.decode(seq[enc["input_ids"].shape[1] :], skip_special_tokens=False)
            try:
                interp = constrain(validate_llm_output(parse_model_json(text)), context_for(DialogPhase(rec["phase"])))
                pred: list[dict[str, Any]] | None = compact(interp.actions)
                err = None
            except Exception as e:
                pred, err = None, f"{type(e).__name__}: {str(e)[:120]}"
            results.append(
                {
                    "id": rec["id"],
                    "category": rec["category"],
                    "language": rec["language"],
                    "phase": rec["phase"],
                    "utterance": rec["utterance"],
                    "gold": rec["gold"]["actions"],
                    "pred": pred,
                    "raw": text[:300],
                    "error": err,
                    "latency_ms": round(per_item_ms, 1),
                    **score(rec["gold"]["actions"], pred),
                }
            )
        print(f"{min(i + a.batch, len(rows))}/{len(rows)}", file=sys.stderr, flush=True)
    summary = summarise(results)
    label = f"{a.base}" + (f" + {a.adapter}" if a.adapter else " (zero-shot)") + " [local raw]"
    report = {
        "label": label,
        "base": a.base,
        "adapter": a.adapter,
        "path": "raw",
        "data": str(a.data),
        "device": str(model.device),
        "summary": summary,
        "results": results,
    }
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md = markdown(summary, label)
    if a.md:
        with Path(a.md).open("a", encoding="utf-8") as f:
            f.write(md + "\n")
    print(md)
    lat = [r["latency_ms"] for r in results]
    print(
        f"local generation latency (batched, informational only): median {statistics.median(lat):.0f} ms/item",
        file=sys.stderr,
    )
    return 0 if summary["gate_rights_recall_ok"] else 2


# ----------------------------------------------------------------------------- push
def cmd_push(a: argparse.Namespace) -> int:
    from huggingface_hub import HfApi

    api = HfApi()
    api.create_repo(a.repo, exist_ok=True, private=a.private)
    api.upload_folder(
        folder_path=a.adapter,
        repo_id=a.repo,
        allow_patterns=["adapter_config.json", "adapter_model.safetensors", "training_meta.json", "README.md"],
    )
    print(f"pushed {a.adapter} -> https://huggingface.co/{a.repo}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("train")
    t.add_argument("--base", default="google/gemma-2b-it")
    t.add_argument("--out", default=str(ROOT / "training" / "adapter"))
    t.add_argument("--epochs", type=float, default=3)
    t.add_argument("--r", type=int, default=8)
    t.add_argument("--alpha", type=int, default=16)
    t.add_argument("--lr", type=float, default=2e-4)
    t.add_argument("--batch", type=int, default=4)
    t.add_argument("--grad-accum", type=int, default=4)
    t.add_argument("--max-len", type=int, default=640)
    t.add_argument("--seed", type=int, default=20260928)
    t.add_argument("--dtype", default="auto", choices=["auto", "fp32", "fp16", "bf16"])
    t.add_argument("--max-steps", type=int, default=0, help="smoke test: stop after N optimizer steps")
    e = sub.add_parser("eval")
    e.add_argument("--base", default="google/gemma-2b-it")
    e.add_argument("--adapter", default=None)
    e.add_argument("--data", default=str(DATA / "heldout.jsonl"))
    e.add_argument("--limit", type=int, default=0)
    e.add_argument("--batch", type=int, default=8)
    e.add_argument("--max-new", type=int, default=120)
    e.add_argument("--out", default=None)
    e.add_argument("--md", default=None)
    e.add_argument("--dtype", default="auto", choices=["auto", "fp32", "fp16", "bf16"])
    p = sub.add_parser("push")
    p.add_argument("--adapter", default=str(ROOT / "training" / "adapter"))
    p.add_argument("--repo", required=True)
    p.add_argument("--private", action="store_true")
    a = ap.parse_args()
    return {"train": cmd_train, "eval": cmd_eval, "push": cmd_push}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
