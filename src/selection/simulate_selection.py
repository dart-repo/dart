import argparse
import copy
import json
import logging
import os
import random
import re
import sys

from tqdm import tqdm

from annotate_common import (
    DEFAULT_MODEL, LLM_CONFIG, ask_llm, log_usage_summary, save_records, setup_logger, usage_totals,
)

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "dart", "src"))
from rewriting.rewriting_rules import (  # noqa: E402  (DART's own rewriting rules)
    get_hints, rewrite_attach_ambiguity, rewrite_scope_ambiguity,
)

DART_ROOT = "../dart"
SYSTEM_PATHS = {
    "dart": f"{DART_ROOT}/revision/results/clambsql/prediction.json",
    "clear": f"{DART_ROOT}/dev-backup/data_dart_paper/clambsql/clear_merged.json",
    "dtp": f"{DART_ROOT}/dev-backup/data_dart_paper/disambiguate_then_parse/clambsql/prediction.json",
}
MAX_RETRIES = 3
log = logging.getLogger("simulate_selection")

# ----------------------------------------------------------------------------- prompt

SIMULATOR_PROMPT = """You are using a system that answers questions over a database.

Question: {question}
Additional information about the question:
{intent}
{schema_block}
The question is ambiguous to the system, so it asks:
{clarification}
{options}

{instruction}
Judge only by meaning: ignore the wording, length, and position of the options.
Answer with JSON only: {{"choice": [<option numbers>]}}"""

MULTI_INSTRUCTION = ("Using the question and its additional information, choose every option "
                     "that matches what is being asked. If none of them does, choose only {none}.")
SINGLE_INSTRUCTION = ("Using the question and its additional information, choose the one option "
                      "that matches what is being asked. If none of them does, choose {none}.")
NONE_TEXT = "None of these."
# --strict_output: stricter instruction for DART's output-columns question only. The default
# MULTI_INSTRUCTION let the user pick the nearest columns when a needed one was missing,
# which adds a contradicting "List ..." hint (see analysis of oracle vs simulated selection).
STRICT_OUTPUT_INSTRUCTION = (
    "Using the question and its additional information, choose every column you want to see "
    "in the result. If any column you want to see is not among the options, choose only {none}.")
PROMPT_OPTIONS = {"strict_output": False}
EXCLUDED_TYPES = {"scope", "attachment"}


# ----------------------------------------------------------------------------- intent

def build_intent(record, i):
    """Additional information for gold interpretation i: user_intent + output_intent."""
    lines = []
    ui = record.get("user_intent")
    if ui and i < len(ui) and ui[i]:
        lines += list(ui[i].values())
    oi = record.get("output_intent")
    if oi and i < len(oi) and oi[i]:
        lines.append(f"In the result, {oi[i]}")
    return "\n".join(lines)


# ----------------------------------------------------------------------------- systems
# Each system turns a result record into a list of clarification "turns":
#   {"qid", "clarification", "options": [str], "multi": bool, ...system-specific}
# and turns the user's answers back into a resulting question (and SQL when available).

DART_TYPES = ["column", "table", "join", "aggregate"]  # scope/attach are never asked
DART_TEMPLATES = {  # verbatim from dart/src/selection/user_selector.py
    "column": 'Column Ambiguity: What does "{t}" refer to?',
    "table": 'Table Ambiguity: Which table does "{t}" refer to?',
    "join": 'Join Ambiguity: How should "{t}" be accessed?',
    "aggregate": 'Aggregate Ambiguity: What does "{t}" refer to?',
}
DART_OUTPUT_TEMPLATE = "Output Schema: Which columns should be returned?"


def normalize_question(q):
    """Same normalization DART's QuestionRewriter applies."""
    q = " ".join(q.strip().split())
    return q if q.endswith((".", "?")) else q + "."


def dart_turns(sys_rec):
    det = sys_rec.get("detection") or {}
    turns = []
    for t in DART_TYPES:
        for token, cands in (det.get(t) or {}).items():
            if cands:
                turns.append({"qid": f"{t}:{token}", "type": t, "token": token,
                              "clarification": DART_TEMPLATES[t].format(t=token),
                              "options": list(cands), "multi": True})
    if det.get("output"):
        turns.append({"qid": "output", "type": "output", "token": None,
                      "clarification": DART_OUTPUT_TEMPLATE,
                      "options": list(det["output"]), "multi": True})
    return turns


def dart_resolve(sys_rec, turns, picks):
    """picks[k] = chosen option indices for turns[k] ([] = none).

    Mirrors DART's QuestionRewriter with the user's answers as the (single) selection and the selection is appended to each base as hint sentences.
    """
    question = normalize_question(sys_rec["question"])
    det = sys_rec.get("detection") or {}
    selection = {t: {} for t in DART_TYPES}
    selection["output"] = []
    for turn, chosen in zip(turns, picks):
        kept = [turn["options"][j] for j in chosen]
        if not kept:
            continue
        if turn["type"] == "output":
            selection["output"] = kept
        else:
            selection[turn["type"]][turn["token"]] = kept
    bases = rewrite_scope_ambiguity(question, det.get("scope") or {})
    bases += rewrite_attach_ambiguity(question, det.get("attach") or {})
    bases = bases or [question]
    hints = get_hints(selection)
    rewriting = list(dict.fromkeys(b + hints if hints else b for b in bases))
    return {"selection": selection, "rewriting": rewriting, "prediction": None}


def list_turns(options, clarification):
    """CLEAR / D-then-P: one single-select question over the whole option list.
    A single option means the system found no ambiguity, so nothing is asked."""
    if len(options) < 2:
        return []
    return [{"qid": "global", "clarification": clarification, "options": options,
             "multi": False}]


def clear_dq_tag(dq_tmp):
    """The JSON tag CLEAR appends to each DQ in DQs_tmp ({} for the plain question)."""
    m = re.search(r"\((\{.*\})\)\s*$", dq_tmp)
    return json.loads(m.group(1)) if m else {}


def clear_unique(sys_rec):
    """[(dq, k, is_structural)] per unique DQ; k indexes prediction (aligned to unique DQs).
    A DQ is structural when its tag maps a span to a reading string (scope/attach)."""
    out, seen = [], {}
    for dq, tmp in zip(sys_rec["DQs"], sys_rec["DQs_tmp"]):
        if dq in seen:
            continue
        seen[dq] = len(seen)
        structural = any(isinstance(v, str) for v in clear_dq_tag(tmp).values())
        out.append((dq, seen[dq], structural))
    return out


def clear_options(sys_rec):
    return [dq for dq, _, structural in clear_unique(sys_rec) if not structural]


def clear_option_pred(sys_rec, j):
    """SQL for clear_options(sys_rec)[j]."""
    k = [k for _, k, structural in clear_unique(sys_rec) if not structural][j]
    return sys_rec["prediction"][k]


def dtp_options(sys_rec):
    return list(sys_rec["rewriting"])  # prediction[k] aligns with rewriting[k]


SYSTEMS = {
    "dart": {"turns": dart_turns},
    "clear": {"options": clear_options,
              "clarification": "Which of these questions did you mean?"},
    "dtp": {"options": dtp_options,
            "clarification": "Which of these interpretations did you mean?"},
}


def get_turns(system, sys_rec):
    if system == "dart":
        return dart_turns(sys_rec)
    s = SYSTEMS[system]
    return list_turns(s["options"](sys_rec), s["clarification"])


def resolve(system, sys_rec, turns, picks):
    if system == "dart":
        return dart_resolve(sys_rec, turns, picks)
    options = SYSTEMS[system]["options"](sys_rec)
    pred = (lambda j: clear_option_pred(sys_rec, j)) if system == "clear" \
        else (lambda j: sys_rec["prediction"][j])
    if not turns:
        if len(options) == 1:  # the system's only (non-structural) reading
            return {"rewriting": options, "prediction": [pred(0)]}
        if system == "clear":  # only structural DQs, which are never chosen: keep them all
            unique = clear_unique(sys_rec)
            return {"rewriting": [dq for dq, _, _ in unique],
                    "prediction": [sys_rec["prediction"][k] for _, k, _ in unique]}
        return {"rewriting": options, "prediction": [pred(j) for j in range(len(options))]}
    chosen = picks[0]
    if not chosen:  # "none of these": fall back to the original question (needs parsing)
        return {"rewriting": [sys_rec["question"]], "prediction": None}
    return {"rewriting": [options[chosen[0]]], "prediction": [pred(chosen[0])]}


# ----------------------------------------------------------------------------- simulator

def render_prompt(record, intent, turn, shown, with_schema):
    none_no = len(shown) + 1
    options = "\n".join(f"{n}. {opt}" for n, opt in enumerate(shown, 1))
    options += f"\n{none_no}. {NONE_TEXT}"
    if PROMPT_OPTIONS["strict_output"] and turn.get("type") == "output":
        template = STRICT_OUTPUT_INSTRUCTION
    else:
        template = MULTI_INSTRUCTION if turn["multi"] else SINGLE_INSTRUCTION
    instruction = template.format(none=none_no)
    schema_block = (f"\nDatabase schema:\n{record['schema_with_content']}\n"
                    if with_schema else "")
    return SIMULATOR_PROMPT.format(question=record["question"], intent=intent,
                                   schema_block=schema_block,
                                   clarification=turn["clarification"],
                                   options=options, instruction=instruction)


def parse_choice(resp, n_shown, multi):
    """-> list of 0-based positions in the shown order ([] = none), or None if unparsable."""
    m = re.search(r"\{.*\}", resp, re.S)
    try:
        nums = json.loads(m.group(0))["choice"] if m else None
    except (json.JSONDecodeError, KeyError, TypeError):
        nums = None
    if nums is None:
        return None
    if isinstance(nums, int):
        nums = [nums]
    nums = [n for n in nums if isinstance(n, int) and 1 <= n <= n_shown + 1]
    if not nums:
        return None
    picked = sorted({n - 1 for n in nums if n <= n_shown})  # drop "none" if mixed
    return picked if multi else picked[:1]


def simulate_turn(record, gold_i, intent, turn, args):
    rng = random.Random(f"{record['index']}-{gold_i}-{turn['qid']}")
    order = list(range(len(turn["options"])))
    rng.shuffle(order)
    shown = [turn["options"][j] for j in order]
    prompt = render_prompt(record, intent, turn, shown, args.with_schema)
    if args.dry_run:
        print("=" * 100 + f"\n[{record['index']} gold {gold_i} {turn['qid']}]\n{prompt}")
        return {"qid": turn["qid"], "shown_order": order, "chosen": [], "raw": None}
    for _ in range(MAX_RETRIES):
        resp = ask_llm(args.llm_model, prompt)
        picked = parse_choice(resp, len(shown), turn["multi"])
        if picked is not None:
            return {"qid": turn["qid"], "shown_order": order,
                    "chosen": [order[p] for p in picked],  # indices into turn["options"]
                    "raw": resp}
        log.warning("index %s gold %s %s: unparsable answer: %r",
                    record["index"], gold_i, turn["qid"], resp)
    raise ValueError(f"no parsable answer after {MAX_RETRIES} tries")


def simulate_record(record, sys_rec, system, args):
    turns = get_turns(system, sys_rec)
    sessions = []
    for gold_i in range(len(record["ambiguous_queries"])):
        intent = build_intent(record, gold_i)
        if not intent:
            sessions.append({"gold_i": gold_i, "skipped": "no intent annotation"})
            continue
        results = [simulate_turn(record, gold_i, intent, t, args) for t in turns]
        picks = [r["chosen"] for r in results]
        sessions.append({
            "gold_i": gold_i,
            "intent": intent,
            "turns": [{"clarification": t["clarification"], "options": t["options"],
                       "multi": t["multi"], **r} for t, r in zip(turns, results)],
            "num_questions": len(turns),
            "num_options_shown": sum(len(t["options"]) for t in turns),
            **resolve(system, sys_rec, turns, picks),
        })
    return {"index": record["index"], "ambig_type": record["ambig_type"],
            "prompt_variant": "strict_output" if PROMPT_OPTIONS["strict_output"] else "default",
            "system": system, "sessions": sessions}


# ----------------------------------------------------------------------------- outputs

SYSTEM_FIELDS_RENAMED = ("rewriting", "DQs", "dq", "prediction", "selection")
CLAMBSQL_DB_ROOT = f"{DART_ROOT}/dev-backup/dataset/clambsql/database"


def resolve_db_file(db_file):
    """Absolute path to the ClambSQL sqlite file. Systems store it differently
    (DART / D-then-P: ./dataset/clambsql/database/<type>/<db>/<db>.sqlite, CLEAR:
    <type>/<db>/<db>.sqlite). dart/src/evaluate.py opens db_file as given, and sqlite3
    silently creates an empty DB for a missing path, so resolve and check it here."""
    rel = db_file.split("clambsql/database/", 1)[-1].lstrip("./")
    path = os.path.join(CLAMBSQL_DB_ROOT, rel)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"ClambSQL database not found: {path} (from {db_file})")
    return path


def export_rewriting(results, sys_recs, system, path):
    """Write a parser-ready rewriting.json: one record per simulated ClambSQL record.

    Each record is a copy of the system's own record (inputs are never modified) with:
      rewriting           simulated rewrites of all golds, de-duplicated, in gold order
                          (the same flat layout DART's QuestionRewriter writes for the oracle)
      DQs                 same list, so dart/src/parser.py --mode clear parses every entry
                          (its --mode dart drops entries by position when there are 4 or 8)
      rewriting_per_gold  rewrites of each gold_i session (null if the session was skipped)
      selection           DART only: the simulated selection per gold, like the oracle's
      prediction_per_gold CLEAR / D-then-P only: the system's SQL for the chosen option(s)
    Fields of the system record that these replace are kept as <name>_system.
    """
    out = []
    for res in sorted(results, key=lambda r: r["index"]):
        rec = copy.deepcopy(sys_recs[res["index"]])
        rec["db_file"] = resolve_db_file(rec["db_file"])
        for k in SYSTEM_FIELDS_RENAMED:
            if k in rec:
                rec[f"{k}_system"] = rec.pop(k)
        per_gold = [sess.get("rewriting") for sess in res["sessions"]]
        flat = list(dict.fromkeys(q for g in per_gold if g for q in g))
        rec.update({"rewriting": flat, "DQs": flat, "rewriting_per_gold": per_gold})
        if system == "dart":
            rec["selection"] = [sess.get("selection") for sess in res["sessions"]]
        else:
            rec["prediction_per_gold"] = [sess.get("prediction") for sess in res["sessions"]]
        out.append(rec)
    save_records(out, path)


def check_outputs_safe(args):
    """Inputs are only read. Refuse any output (selection.json, rewriting.json, their .tmp,
    the .log) that would overwrite an input file or land inside the DART repo."""
    inputs = {os.path.realpath(args.clambsql_path), os.path.realpath(args.system_path)}
    dart_root = os.path.realpath(DART_ROOT) + os.sep
    outs = [args.selection_path, args.rewriting_path, args.log_path]
    for out in outs + [o + ".tmp" for o in outs]:
        real = os.path.realpath(out)
        if real in inputs:
            sys.exit(f"Refusing to run: output {out} is an input file.")
        if real.startswith(dart_root):
            sys.exit(f"Refusing to run: output {out} is inside {DART_ROOT} (system results).")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--system", required=True, choices=sorted(SYSTEMS))
    parser.add_argument("--system_path", default=None, help="Defaults to SYSTEM_PATHS[system].")
    parser.add_argument("--clambsql_path", default=os.path.join(ROOT, "clambsql", "clambsql.json"))
    parser.add_argument("--output_dir", default=None,
                        help="Defaults to results/<system> (results/<system>_schema with --with_schema). "
                             "Holds selection.json, selection.log and rewriting.json.")
    parser.add_argument("--llm_model", default=DEFAULT_MODEL,
                        help=f"Same default as annotate_*.py ({DEFAULT_MODEL}).")
    parser.add_argument("--effort", default="medium",
                        choices=["low", "medium", "high", "xhigh", "max"])
    parser.add_argument("--strict_output", action="store_true",
                        help="Stricter instruction for DART's output-columns question: choose None "
                             "if any wanted column is missing. Results go to <system>_strict_output.")
    parser.add_argument("--with_schema", action="store_true",
                        help="Show schema_with_content to the simulated user (all systems).")
    parser.add_argument("--types", nargs="*", default=None, help="Only these ambig_types.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--indices_file", default=None,
                        help="JSON list of record indices to simulate (instead of --per_type).")
    parser.add_argument("--per_type", type=int, default=None,
                        help="Use only the first N records (by index) of each ambig_type. Counts "
                             "already-simulated records, so reruns resume instead of adding more.")
    parser.add_argument("--save_every", type=int, default=10)
    parser.add_argument("--dry_run", action="store_true",
                        help="Print the prompts for the selected records; no LLM calls, no output.")
    parser.add_argument("--export_only", action="store_true",
                        help="Rebuild rewriting.json from an existing selection.json; no LLM calls.")
    args = parser.parse_args()
    args.system_path = args.system_path or SYSTEM_PATHS[args.system]
    args.output_dir = args.output_dir or os.path.join(
        ROOT, "results", f"{args.system}{'_schema' if args.with_schema else ''}"
                         f"{'_strict_output' if args.strict_output else ''}")
    args.selection_path = os.path.join(args.output_dir, "selection.json")
    args.rewriting_path = os.path.join(args.output_dir, "rewriting.json")
    args.log_path = os.path.join(args.output_dir, "selection.log")
    LLM_CONFIG["effort"] = args.effort
    PROMPT_OPTIONS["strict_output"] = args.strict_output
    check_outputs_safe(args)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    with open(args.clambsql_path) as f:
        records = json.load(f)
    with open(args.system_path) as f:
        sys_recs = {r["index"]: r for r in json.load(f)}

    results = []
    if os.path.exists(args.selection_path) and not args.dry_run:
        with open(args.selection_path) as f:
            results = json.load(f)
    if args.export_only:
        export_rewriting(results, sys_recs, args.system, args.rewriting_path)
        print(f"Wrote {args.rewriting_path} ({len(results)} records).")
        return
    done = {r["index"] for r in results}

    todo = [r for r in records if r["index"] in sys_recs
            and r["ambig_type"] not in EXCLUDED_TYPES
            and (args.types is None or r["ambig_type"] in args.types)]
    if args.indices_file:
        with open(args.indices_file) as f:
            wanted = set(json.load(f))
        todo = [r for r in todo if r["index"] in wanted]
    if args.per_type is not None:
        seen = {}
        todo = [r for r in sorted(todo, key=lambda r: r["index"])
                if seen.setdefault(r["ambig_type"], []).append(r) or
                len(seen[r["ambig_type"]]) <= args.per_type]
    todo = [r for r in todo if r["index"] not in done]
    if args.limit is not None:
        todo = todo[:args.limit]
    if not args.dry_run:
        os.makedirs(args.output_dir, exist_ok=True)
        setup_logger(args.log_path)
        logging.getLogger("annotate.llm").info(  # same logger/file handler as the per-call cost lines
            f"[RUN] system={args.system} model={args.llm_model} effort={args.effort} "
            f"strict_output={args.strict_output} with_schema={args.with_schema}")

    failed = []
    for n, record in enumerate(tqdm(todo, desc=f"simulate:{args.system}", disable=args.dry_run), 1):
        try:
            out = simulate_record(record, sys_recs[record["index"]], args.system, args)
        except Exception as e:  # leave it out so a rerun retries it
            failed.append(record["index"])
            log.error("index %s: %s", record["index"], e)
            continue
        if args.dry_run:
            continue
        results.append(out)
        if n % args.save_every == 0:
            save_records(results, args.selection_path)
    if args.dry_run:
        return
    save_records(results, args.selection_path)
    export_rewriting(results, sys_recs, args.system, args.rewriting_path)
    log_usage_summary()
    print(f"Done. {len(results)} records -> {args.selection_path} and {args.rewriting_path}. "
          f"model={args.llm_model} LLM calls={usage_totals['calls']} cost=${usage_totals['cost_usd']:.4f}")
    if failed:
        print(f"{len(failed)} records failed (rerun to retry): {failed}")


if __name__ == "__main__":
    main()
