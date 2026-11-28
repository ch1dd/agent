"""Local Qwen yes/no relevance scoring. Only search uses this model."""
import os
from threading import Lock


class Reranker:
    def __init__(self, name):
        self.name = name
        self.model = None
        self.lock = Lock()

    def score(self, query, passages):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        with self.lock, torch.inference_mode():
            if self.model is None:
                device = os.getenv("RAG_RERANK_DEVICE") or ("cuda" if torch.cuda.is_available() else "cpu")
                self.tokenizer = AutoTokenizer.from_pretrained(self.name, padding_side="left")
                self.model = AutoModelForCausalLM.from_pretrained(
                    self.name, dtype=torch.float16 if device == "cuda" else torch.float32
                ).to(device).eval()
            tok = self.tokenizer
            suffix = tok.encode("<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n", add_special_tokens=False)
            labels = tok.convert_tokens_to_ids(["no", "yes"])
            scores = []
            for start in range(0, len(passages), 2):
                inputs = []
                for passage in passages[start:start + 2]:
                    text = ("<|im_start|>system\nJudge whether the document meets the query requirements. "
                            "Answer only yes or no.<|im_end|>\n<|im_start|>user\n"
                            "<Instruct>: Retrieve corporate rules that answer the question under its stated conditions.\n"
                            f"<Query>: {query}\n<Document>: {passage}")
                    ids = tok.encode(text, add_special_tokens=False, truncation=True, max_length=4096-len(suffix))
                    inputs.append({"input_ids": ids + suffix})
                batch = tok.pad(inputs, padding=True, return_attention_mask=True, return_tensors="pt").to(self.model.device)
                logits = self.model(**batch, logits_to_keep=1, use_cache=False).logits[:, -1, labels]
                scores.extend(logits.float().softmax(dim=-1)[:, 1].tolist())
            return scores
