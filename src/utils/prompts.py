import os

PROMPTS_ROOT_PATH = "prompts"

PROMPT_TEMPLATES = {
    "scope":     ("scope_ambiguity",     ["question"]),
    "attach":    ("attach_ambiguity",    ["question"]),
    "column":    ("column_ambiguity",    ["question", "hint", "schema"]),
    "table":     ("table_ambiguity",     ["question", "hint", "schema"]),
    "join":      ("join_ambiguity",      ["question", "hint", "schema"]),
    "aggregate": ("aggregate_ambiguity", ["question", "hint", "schema"]),
    "output":    ("output_ambiguity",    ["question", "hint", "schema"]),
}

def load_prompt(prompt_name):
    prompt_path = os.path.join(PROMPTS_ROOT_PATH, f"{prompt_name}.txt")
    with open(prompt_path, 'r', encoding='utf-8') as f:
        return f.read()

def get_prompt(ambig_type, **kwargs):
    prompt_name, expected_keys = PROMPT_TEMPLATES[ambig_type]
    prompt = load_prompt(prompt_name)
    for key in expected_keys:
        prompt = prompt.replace(f"[{key.upper()}]", kwargs[key])
    return prompt
