MODEL_REGISTRY = {
    "gpt2": {
        "architecture": "gpt2",
        "num_hidden_layers": 12,
        "num_attention_heads": 12,
        "hidden_size": 768,
        "intermediate_size": 3072,
        "vocab_size": 50257,
    },
    "tinyllama": {
        "architecture": "llama",
        "num_hidden_layers": 22,
        "num_attention_heads": 32,
        "hidden_size": 2048,
        "intermediate_size": 5632,
        "vocab_size": 32000,
    },
    "llama-3.2-3b": {
        "architecture": "llama",
        "num_hidden_layers": 28,
        "num_attention_heads": 24,
        "hidden_size": 3072,
        "intermediate_size": 8192,
        "vocab_size": 128256,
    },
}
