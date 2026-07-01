"""
    Interactive user selection interface for detected ambiguities.

    Given a detection file, this walks through every item and, for each detected ambiguity, shows the numbered options. The user picks which option(s) are correct, and the choices are written back to each item's `selection` field.
"""
import json


DICT_TYPES = ["scope", "attach", "column", "table", "join", "aggregate"]

# user selection templates
TEMPLATES = {
    "scope":     'Scope Ambiguity: How should "{t}" be interpreted?',
    "attach":    'Attachment Ambiguity: What does "{t}" modify?',
    "column":    'Column Ambiguity: What does "{t}" refer to?',
    "table":     'Table Ambiguity: Which table does "{t}" refer to?',
    "join":      'Join Ambiguity: How should "{t}" be accessed?',
    "aggregate": 'Aggregate Ambiguity: What does "{t}" refer to?',
}
OUTPUT_TEMPLATE = "Output Schema: Which columns should be returned?"

HINT = "(Enter = none, a = all, q = save & quit)"

def ask_keep(prompt_line, candidates):
    print(prompt_line)
    opts = "  ".join(f"[{i}] {c}" for i, c in enumerate(candidates, 1))
    print(f"{opts}  {HINT}")
    while True:
        ans = input("> ").strip().lower()
        if ans == "q":
            return "q"
        if ans == "":
            return []
        if ans == "a":
            return list(candidates)
        try:
            idx = [int(x) for x in ans.replace(" ", "").split(",") if x]
            if all(1 <= i <= len(candidates) for i in idx):
                return [candidates[i - 1] for i in idx]
        except ValueError:
            pass
        print("Invalid option(s). Please enter numbers, a, or q.")


def select_item(item, n, total):
    det = item.get("detection", {}) or {}
    selection = {t: {} for t in DICT_TYPES}
    selection["output"] = []

    print("=" * 90)
    print(f"Question {n}/{total}: {item.get('question')}")
    print("=" * 90)

    for t in DICT_TYPES:
        tmap = det.get(t, {})
        if not tmap:
            continue
        for token, candidates in tmap.items():
            print()
            kept = ask_keep(TEMPLATES[t].format(t=token), candidates)
            if kept == "q":
                return kept
            if kept:
                selection[t][token] = kept

    out = det.get("output", []) or []
    if out:
        print()
        kept = ask_keep(OUTPUT_TEMPLATE, out)
        if kept == "q":
            return kept
        selection["output"] = kept

    return selection


class UserSelector:
    def __init__(self, args):
        self.detection_path = args.detection_path

    def select(self):
        with open(self.detection_path, 'r') as f:
            detection = json.load(f)

        for n, item in enumerate(detection, 1):
            print()
            res = select_item(item, n, len(detection))
            if res == "q":
                break
            item["selection"] = res
            with open(self.detection_path, 'w', encoding="utf-8") as f:  # save every item
                json.dump(detection, f, indent=4, ensure_ascii=False)

        with open(self.detection_path, 'w', encoding="utf-8") as f:
            json.dump(detection, f, indent=4, ensure_ascii=False)
