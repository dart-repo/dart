import json
import os

from tqdm import tqdm
from rewriting.rewriting_rules import *

class QuestionRewriter:
    def __init__(self, args):
        self.dataset = args.dataset
        self.detection_path = args.detection_path
        self.rewriting_path = args.rewriting_path

    def rewrite(self):
        with open(self.detection_path, 'r') as f:
            detection_data = json.load(f)

        # load existing rewriting results
        if os.path.exists(self.rewriting_path):
            with open(self.rewriting_path, 'r') as f:
                results = json.load(f)
            processed = {item["index"] for item in results}
        else:
            results = []
            processed = set()

        for item in tqdm(detection_data):
            if item["index"] in processed:
                continue

            question = item["question"].strip()
            question = " ".join(question.split())
            if not question.endswith(".") and not question.endswith("?"):
                question += "."

            ambiguity = item["detection"]
            per_query_selection = item["selection"]  # one per gold query

            # rewriting for structural ambiguity
            base_variants = rewrite_scope_ambiguity(question, ambiguity["scope"])
            base_variants += rewrite_attach_ambiguity(question, ambiguity["attach"])
            base_variants = base_variants or [question]

            # rewriting for mapping ambiguity
            rewritten = []
            for query_sel in per_query_selection:
                for base in base_variants:
                    hints = get_hints(query_sel)
                    rewritten.append(base + hints if hints else base)

            results.append({
                **item,
                "rewriting": list(dict.fromkeys(rewritten if rewritten else base_variants))
            })

            with open(self.rewriting_path, 'w', encoding="utf-8") as f:
                json.dump(results, f, indent=4)
