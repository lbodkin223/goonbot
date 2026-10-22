import numpy as np

from .config import EMBED_MODEL, MAX_SEQ_LEN


def pick_device():
    import torch
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


class Embedder:
    """Thin wrapper over sentence-transformers. Queries get the model's retrieval prompt if it has one."""

    def __init__(self, model_name=EMBED_MODEL, device=None):
        import torch
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name
        self.device = device or pick_device()
        try:
            kwargs = {"model_kwargs": {"dtype": torch.float16}} if self.device != "cpu" else {}
            self.model = SentenceTransformer(model_name, device=self.device, **kwargs)
        except Exception:
            self.model = SentenceTransformer(model_name, device=self.device)
        self.model.max_seq_length = MAX_SEQ_LEN
        self.has_query_prompt = "query" in (getattr(self.model, "prompts", None) or {})

    def encode_docs(self, texts, batch_size=64, progress=False):
        return self.model.encode(
            list(texts), batch_size=batch_size, normalize_embeddings=True,
            convert_to_numpy=True, show_progress_bar=progress,
        ).astype(np.float32)

    def encode_query(self, text):
        kw = {"prompt_name": "query"} if self.has_query_prompt else {}
        return self.model.encode([text], normalize_embeddings=True, convert_to_numpy=True, **kw)[0].astype(np.float32)
