import json
import argparse
import os
import re
from tqdm import tqdm

from utils.evaluate_utils import (
    normalize_sql,
    compare_sql,
    get_column_reverse_map,
    has_any_alt_column,
    has_alt_table,
    rewrite_columns_to_original,
    rewrite_tables_to_original,
    rewrite_join_query,
    rewrite_aggregate_query,
    summarize_matches,
    
    parse_gold_ambiguity_column_alts,
    parse_gold_ambiguity_table_alts,
    sql_has_table_column,
    sql_has_table,
)

def run_eval_clambsql(input_path):
    data = json.load(open(input_path))

    for item in tqdm(data):
        index = item["index"]
        ambig_type = item["ambig_type"]
        db_file = item["db_file"]
        sqls = item.get("prediction") or item.get("predict_sql")
        if sqls is None:
            print(f"No prediction found for index {item['index']}")
            continue
        if isinstance(sqls, str):
            sqls = [sqls]
        gold_sqls = item.get("ambiguous_queries") or item.get("gold_queries")

        matches = {}
        gold_empty = False
        
        if ambig_type == "column":
            alt_pairs = parse_gold_ambiguity_column_alts(item)
            if alt_pairs is None or len(gold_sqls or []) < 2: # wrong gold ambiguity
                print(f"{index}\t{ambig_type}\t-1\t-1\t-1")
                continue

            for i, sql in enumerate(sqls):
                has_q1_col = sql_has_table_column(sql, alt_pairs[0][0], alt_pairs[0][1])
                has_q2_col = sql_has_table_column(sql, alt_pairs[1][0], alt_pairs[1][1])

                if has_q1_col: # this includes where both columns are present
                    _, gold_res, result = compare_sql(sql, gold_sqls[0], db_file)
                    if result is None and isinstance(gold_res, list) and len(gold_res) == 0:
                        gold_empty = True
                    matches[i, 0] = (result == 1)
                    matches[i, 1] = False
                elif has_q2_col:
                    _, gold_res, result = compare_sql(sql, gold_sqls[1], db_file)
                    if result is None and isinstance(gold_res, list) and len(gold_res) == 0:
                        gold_empty = True
                    matches[i, 0] = False
                    matches[i, 1] = (result == 1)
                else: # if neither column is present, compare with first gold SQL by default
                    _, gold_res, result = compare_sql(sql, gold_sqls[0], db_file)
                    if result is None and isinstance(gold_res, list) and len(gold_res) == 0:
                        gold_empty = True
                    matches[i, 0] = (result == 1)
                    matches[i, 1] = False

            if gold_empty:
                print(f"{index}\t{ambig_type}\t-1\t-1\t-1")
                continue

            (
                item_gold_matched,
                item_pred_matched,
                item_all_found,
                item_recall,
                item_precision,
            ) = summarize_matches(matches, 2, len(sqls))
            print(f"{index}\t{ambig_type}\t{item_recall:.4f}\t{item_precision:.4f}\t{int(item_all_found)}")
            continue
        
        elif ambig_type == "table":
            alt_tables = parse_gold_ambiguity_table_alts(item)
            if alt_tables is None or len(gold_sqls or []) < 2:
                print(f"{index}\t{ambig_type}\t-1\t-1\t-1")
                continue

            for i, sql in enumerate(sqls):
                has_q1_table = all(sql_has_table(sql, table_name) for table_name in alt_tables[0])
                has_q2_table = all(sql_has_table(sql, table_name) for table_name in alt_tables[1])

                if has_q1_table: # this includes where both tables are present
                    _, gold_res, result = compare_sql(sql, gold_sqls[0], db_file)
                    if result is None and isinstance(gold_res, list) and len(gold_res) == 0:
                        gold_empty = True
                    matches[i, 0] = (result == 1)
                    matches[i, 1] = False
                elif has_q2_table:
                    _, gold_res, result = compare_sql(sql, gold_sqls[1], db_file)
                    if result is None and isinstance(gold_res, list) and len(gold_res) == 0:
                        gold_empty = True
                    matches[i, 0] = False
                    matches[i, 1] = (result == 1)
                else: # if neither table is present, compare with first gold SQL by default
                    _, gold_res, result = compare_sql(sql, gold_sqls[0], db_file)
                    if result is None and isinstance(gold_res, list) and len(gold_res) == 0:
                        gold_empty = True
                    matches[i, 0] = (result == 1)
                    matches[i, 1] = False

            if gold_empty:
                print(f"{index}\t{ambig_type}\t-1\t-1\t-1")
                continue

            (
                item_gold_matched,
                item_pred_matched,
                item_all_found,
                item_recall,
                item_precision,
            ) = summarize_matches(matches, 2, len(sqls))
            print(f"{index}\t{ambig_type}\t{item_recall:.4f}\t{item_precision:.4f}\t{int(item_all_found)}")
            continue
        
        elif ambig_type == "join":
            for i, sql in enumerate(sqls):
                if re.search(r"\bjoin\b", sql, re.IGNORECASE): # if sql has join
                    _, gold_res, result = compare_sql(sql, gold_sqls[1], db_file)
                    if result is None and isinstance(gold_res, list) and len(gold_res) == 0:
                        gold_empty = True
                    matches[i, 0] = False
                    matches[i, 1] = (result == 1)
                else:
                    _, gold_res, result = compare_sql(sql, gold_sqls[0], db_file)
                    if result is None and isinstance(gold_res, list) and len(gold_res) == 0:
                        gold_empty = True
                    matches[i, 0] = (result == 1)
                    matches[i, 1] = False

            if gold_empty:
                print(f"{index}\t{ambig_type}\t-1\t-1\t-1")
                continue

            (
                item_gold_matched,
                item_pred_matched,
                item_all_found,
                item_recall,
                item_precision,
            ) = summarize_matches(matches, 2, len(sqls))
            print(f"{index}\t{ambig_type}\t{item_recall:.4f}\t{item_precision:.4f}\t{int(item_all_found)}")
            continue
        
        elif ambig_type == "aggregate":
            for i, sql in enumerate(sqls):
                if re.search(r"\b(min|max|avg|sum)\s*\(", sql, re.IGNORECASE):
                    _, gold_res, result = compare_sql(sql, gold_sqls[0], db_file)
                    if result is None and isinstance(gold_res, list) and len(gold_res) == 0:
                        gold_empty = True
                    matches[i, 0] = (result == 1)
                    matches[i, 1] = False
                else:
                    _, gold_res, result = compare_sql(sql, gold_sqls[1], db_file)
                    if result is None and isinstance(gold_res, list) and len(gold_res) == 0:
                        gold_empty = True
                    matches[i, 0] = False
                    matches[i, 1] = (result == 1)

            if gold_empty:
                print(f"{index}\t{ambig_type}\t-1\t-1\t-1")
                continue

            (
                item_gold_matched,
                item_pred_matched,
                item_all_found,
                item_recall,
                item_precision,
            ) = summarize_matches(matches, 2, len(sqls))
            print(f"{index}\t{ambig_type}\t{item_recall:.4f}\t{item_precision:.4f}\t{int(item_all_found)}")
            continue

        # vague, attachment, scope
        for i, sql in enumerate(sqls):
            for j, gold_sql in enumerate(gold_sqls):
                _, gold_res, result = compare_sql(sql, gold_sql, db_file)
                if result is None and isinstance(gold_res, list) and len(gold_res) == 0:
                    gold_empty = True
                matches[i, j] = result == 1

        if gold_empty:
            print(f"{index}\t{ambig_type}\t-1\t-1\t-1")
            continue

        item_gold_matched = sum(
            1 for j in range(len(gold_sqls))
            if any(matches[i, j] for i in range(len(sqls)))
        )
        item_pred_matched = sum(
            1 for i in range(len(sqls))
            if any(matches[i, j] for j in range(len(gold_sqls)))
        )
        item_all_found = all(
            any(matches[i, j] for i in range(len(sqls)))
            for j in range(len(gold_sqls))
        )

        item_recall = item_gold_matched / len(gold_sqls) if gold_sqls else 0.0
        item_precision = item_pred_matched / len(sqls) if sqls else 0.0
        print(f"{index}\t{ambig_type}\t{item_recall:.4f}\t{item_precision:.4f}\t{int(item_all_found)}")
        
def run_eval_ambrosia(input_path):
    data = json.load(open(input_path))

    for item in tqdm(data):
        index = item["index"]
        ambig_type = item["ambig_type"]
        db_file = item.get("db_file")
        if not db_file or not os.path.exists(db_file):
            # CLEAR results store a db_file relative to another root; rebuild it from db_id
            # (e.g. "Hospitality/attachment_1tab_val_event_space"), as DART/D-then-P paths look.
            db_id = item["db_id"]
            db_name = db_id.split("/")[-1]
            db_file = f"./dataset/ambrosia/data/{ambig_type}/{db_id}/{db_name}.sqlite"
        sqls = item.get("prediction") or item.get("predict_sql")

        if sqls is None:
            print(f"No prediction found for index {item['index']}")
            continue
        
        if isinstance(sqls, str):
            sqls = [sqls]
        
        gold_sqls = item.get("ambiguous_queries") or item.get("gold_queries")
        
        matches = {}
        gold_empty = False
                
        for i, sql in enumerate(sqls):
            for j, gold_sql in enumerate(gold_sqls):
                _, gold_res, result = compare_sql(sql, gold_sql, db_file)
                if result is None and isinstance(gold_res, list) and len(gold_res) == 0:
                    gold_empty = True
                matches[i, j] = result == 1

        if gold_empty: # skip if any gold SQL returns empty result
            print(f"{index}\t{ambig_type}\t-1\t-1\t-1")
            continue

        item_gold_matched = sum(
            1 for j in range(len(gold_sqls))
            if any(matches[i, j] for i in range(len(sqls)))
        )
        item_pred_matched = sum(
            1 for i in range(len(sqls))
            if any(matches[i, j] for j in range(len(gold_sqls)))
        )
        item_all_found = all(
            any(matches[i, j] for i in range(len(sqls)))
            for j in range(len(gold_sqls))
        )

        item_recall = item_gold_matched / len(gold_sqls) if gold_sqls else 0.0
        item_precision = item_pred_matched / len(sqls) if sqls else 0.0

        print(f"{index}\t{ambig_type}\t{item_recall:.4f}\t{item_precision:.4f}\t{int(item_all_found)}")

def run_eval_ambiqt(args):
    data = json.load(open(args.input_path))

    gold_matched = pred_matched = all_found = 0
    num_alts = 2 # query 1 and query 2 for each question
    
    results = []
    for item in tqdm(data):
        index = item["index"]
        ambig_type = item.get("ambig_type") or item.get("ambiqt_type")
        db_id = item["db_id"]
        db_file = os.path.join(args.db_dir, db_id, f"{db_id}.sqlite")
        
        orig_query = normalize_sql(item["orig_query"])
        sqls = item.get("prediction") or item.get("predict_sqls")
        if sqls is None:
            continue
        if isinstance(sqls, str):
            sqls = [sqls]
        sqls = [normalize_sql(s) for s in sqls] # normalize predicted SQLs
        
        if ambig_type == "column":
            reverse_map_q1 = get_column_reverse_map(item["extra_map"], alt_num=0)
            reverse_map_q2 = get_column_reverse_map(item["extra_map"], alt_num=1)
            
            # sanity checks for alternatives
            rewritten_q1 = rewrite_columns_to_original(item["query1"], reverse_map_q1)
            _, _, result_q1 = compare_sql(rewritten_q1, orig_query, db_file)
            if result_q1 is None:
                results.append((index, f"{index}\t{ambig_type}\t-1\t-1\t-1"))
                continue
            if result_q1 != 1:
                print(f"{index} ERROR: query1 does not match orig_query\nquery1: {rewritten_q1}\norig_query: {orig_query}")
                continue

            rewritten_q2 = rewrite_columns_to_original(item["query2"], reverse_map_q2)
            _, _, result_q2 = compare_sql(rewritten_q2, orig_query, db_file)
            if result_q2 is None:
                results.append((index, f"{index}\t{ambig_type}\t-1\t-1\t-1"))
                continue
            if result_q2 != 1:
                print(f"{index} ERROR: query2 does not match orig_query\nquery2: {rewritten_q2}\norig_query: {orig_query}")
                continue
            
            # check prediction
            matches = {}
            gold_empty = False
            for i, pred in enumerate(sqls):
                has_q1_col = has_any_alt_column(pred, reverse_map_q1)
                has_q2_col = has_any_alt_column(pred, reverse_map_q2)

                if has_q1_col:
                    rewritten = rewrite_columns_to_original(pred, reverse_map_q1)
                    _, _, result = compare_sql(rewritten, orig_query, db_file)
                    if result is None:
                        gold_empty = True
                    matches[i, 0] = (result == 1)
                else:
                    matches[i, 0] = False

                if has_q2_col:
                    rewritten = rewrite_columns_to_original(pred, reverse_map_q2)
                    _, _, result = compare_sql(rewritten, orig_query, db_file)
                    if result is None:
                        gold_empty = True
                    matches[i, 1] = (result == 1)
                else:
                    matches[i, 1] = False

            if gold_empty:
                results.append((index, f"{index}\t{ambig_type}\t-1\t-1\t-1"))
                continue

            (
                item_gold_matched,
                item_pred_matched,
                item_all_found,
                item_recall,
                item_precision,
            ) = summarize_matches(matches, num_alts, len(sqls))
            gold_matched += item_gold_matched
            pred_matched += item_pred_matched
            all_found += 1 if item_all_found else 0
            results.append((index, f"{index}\t{ambig_type}\t{item_recall:.4f}\t{item_precision:.4f}\t{int(item_all_found)}"))
        
        elif ambig_type == "table":
            extra_table_map = item["extra_table_map"]
            orig_table = list(extra_table_map.keys())[0].lower()

            # sanity checks for alternatives
            rewritten_q1 = rewrite_tables_to_original(item["query1"], extra_table_map)
            _, _, result_q1 = compare_sql(rewritten_q1, orig_query, db_file)
            if result_q1 is None:
                results.append((index, f"{index}\t{ambig_type}\t-1\t-1\t-1"))
                continue
            if result_q1 != 1:
                print(f"{index} ERROR: query1 does not match orig_query\nquery1: {rewritten_q1}\norig_query: {orig_query}")
                continue
            
            rewritten_q2 = rewrite_tables_to_original(item["query2"], extra_table_map)
            _, _, result_q2 = compare_sql(rewritten_q2, orig_query, db_file)
            if result_q2 is None:
                results.append((index, f"{index}\t{ambig_type}\t-1\t-1\t-1"))
                continue
            if result_q2 != 1:
                print(f"{index} ERROR: query2 does not match orig_query\nquery2: {rewritten_q2}\norig_query: {orig_query}")
                continue
            
            # check prediction
            matches = {}
            gold_empty = False
            for i, pred in enumerate(sqls):
                has_q1_table = has_alt_table(pred, extra_table_map, alt_num=0)
                has_q2_table = has_alt_table(pred, extra_table_map, alt_num=1)
                
                if has_q1_table:
                    rewritten = rewrite_tables_to_original(pred, extra_table_map)
                    _, _, result = compare_sql(rewritten, orig_query, db_file)
                    if result is None:
                        gold_empty = True
                    matches[i, 0] = (result == 1)
                else:
                    matches[i, 0] = False
                    
                if has_q2_table:
                    rewritten = rewrite_tables_to_original(pred, extra_table_map)
                    _, _, result = compare_sql(rewritten, orig_query, db_file)
                    if result is None:
                        gold_empty = True
                    matches[i, 1] = (result == 1)
                else:
                    matches[i, 1] = False
            
            if gold_empty:
                results.append((index, f"{index}\t{ambig_type}\t-1\t-1\t-1"))
                continue

            (
                item_gold_matched,
                item_pred_matched,
                item_all_found,
                item_recall,
                item_precision,
            ) = summarize_matches(matches, num_alts, len(sqls))
            gold_matched += item_gold_matched
            pred_matched += item_pred_matched
            all_found += 1 if item_all_found else 0
            results.append((index, f"{index}\t{ambig_type}\t{item_recall:.4f}\t{item_precision:.4f}\t{int(item_all_found)}"))
            
        elif ambig_type == "join":
            split_map = item["split_map"]
            primary_key = item["primary_key"]
            present_tbl = list(split_map.keys())[0].lower()
            split_col = split_map[present_tbl].lower()
            split_tbl = f"{present_tbl}_{split_col}"

            # sanity checks for alternatives
            query1 = normalize_sql(item["query1"])
            _, _, result = compare_sql(query1, orig_query, db_file)
            if result is None:
                results.append((index, f"{index}\t{ambig_type}\t-1\t-1\t-1"))
                continue
            if result != 1:
                print(f"{index} ERROR: query1 does not match orig_query\nquery1: {query1}\norig_query: {orig_query}")
                continue

            query2 = rewrite_join_query(normalize_sql(item["query2"]), split_map, primary_key)
            _, _, result = compare_sql(query2, orig_query, db_file)
            if result != 1:
                print(f"{index} ERROR: rewritten query2 does not match orig_query\nrewritten_query2: {query2}\norig_query: {orig_query}")
                continue

            # check prediction
            matches = {}
            gold_empty = False
            for i, pred in enumerate(sqls):
                pred_sql = normalize_sql(pred)
                if f" {split_tbl} " in pred_sql:
                    # compare against query2 path: rewrite then compare with orig_query
                    rewritten = rewrite_join_query(pred_sql, split_map, primary_key)
                    _, _, result = compare_sql(rewritten, orig_query, db_file)
                    if result is None:
                        gold_empty = True
                    matches[i, 0] = False
                    matches[i, 1] = result == 1
                else:
                    # compare against query1 path: direct execution compare
                    _, _, result = compare_sql(pred_sql, orig_query, db_file)
                    if result is None:
                        gold_empty = True
                    matches[i, 0] = result == 1
                    matches[i, 1] = False

            if gold_empty:
                results.append((index, f"{index}\t{ambig_type}\t-1\t-1\t-1"))
                continue

            (
                item_gold_matched,
                item_pred_matched,
                item_all_found,
                item_recall,
                item_precision,
            ) = summarize_matches(matches, num_alts, len(sqls))
            gold_matched += item_gold_matched
            pred_matched += item_pred_matched
            all_found += 1 if item_all_found else 0
            results.append((index, f"{index}\t{ambig_type}\t{item_recall:.4f}\t{item_precision:.4f}\t{int(item_all_found)}"))
            
        elif ambig_type == "aggregate":
            all_raw_cols = item["all_raw_cols"]
            all_cols = item["all_cols"]
            new_table_name = item["new_table_name"].lower()

            # sanity checks for alternatives
            query1 = normalize_sql(item["query1"])
            query2 = normalize_sql(item["query2"])

            if query1 != orig_query:
                print(
                    f"{index} ERROR: query1 does not match orig_query\n"
                    f"query1: {query1}\norig_query: {orig_query}"
                )
                continue

            rewritten_q2 = rewrite_aggregate_query(query2, orig_query, all_raw_cols, all_cols)
            _, _, result_q2 = compare_sql(rewritten_q2, orig_query, db_file)
            if result_q2 is None:
                results.append((index, f"{index}\t{ambig_type}\t-1\t-1\t-1"))
                continue
            if result_q2 != 1:
                print(
                    f"{index} ERROR: rewritten query2 does not match orig_query\n"
                    f"query2: {query2}\nrewritten_q2: {rewritten_q2}\norig_query: {orig_query}"
                )
                continue
            
            # check prediction
            matches = {}
            gold_empty = False
            for i, pred in enumerate(sqls):
                pred_sql = normalize_sql(pred)
                if re.search(rf"\b{re.escape(new_table_name)}\b", pred_sql):
                    rewritten = rewrite_aggregate_query(pred_sql, orig_query, all_raw_cols, all_cols)
                    _, _, result = compare_sql(rewritten, orig_query, db_file)
                    if result is None:
                        gold_empty = True
                    matches[i, 0] = False
                    matches[i, 1] = (result == 1)
                else:
                    _, _, result = compare_sql(pred_sql, orig_query, db_file)
                    if result is None:
                        gold_empty = True
                    matches[i, 0] = (result == 1)
                    matches[i, 1] = False

            if gold_empty:
                results.append((index, f"{index}\t{ambig_type}\t-1\t-1\t-1"))
                continue

            (
                item_gold_matched,
                item_pred_matched,
                item_all_found,
                item_recall,
                item_precision,
            ) = summarize_matches(matches, num_alts, len(sqls))
            gold_matched += item_gold_matched
            pred_matched += item_pred_matched
            all_found += 1 if item_all_found else 0
            results.append((index, f"{index}\t{ambig_type}\t{item_recall:.4f}\t{item_precision:.4f}\t{int(item_all_found)}"))
                    
    # for _, line in sorted(results, key=lambda x: x[0]):
    for _, line in results:
        print(line)

def run_eval_arcwise_plat_sql(input_path):
    data = json.load(open(input_path))

    results = []
    for item in tqdm(data):
        index = item["index"]
        db_file = item.get("db_file")
        if db_file is None:
            # CLEAR results store only db_id; build the same path DART/D-then-P records use.
            db_id = item["db_id"]
            db_file = f"./dataset/arcwise_plat_sql/dev_databases/{db_id}/{db_id}.sqlite"
        sqls = item["prediction"]
        gold_sqls = item.get("ambiguous_queries") or [item.get("query")]

        matches = {}
        gold_empty = False
        for i, sql in enumerate(sqls):
            for j, gold_sql in enumerate(gold_sqls):
                _, gold_res, result = compare_sql(sql, gold_sql, db_file)
                if result is None and isinstance(gold_res, list) and len(gold_res) == 0:
                    gold_empty = True
                matches[i, j] = result == 1

        if gold_empty:
            results.append((index, f"{index}\t-1"))
            continue

        # exec_acc = 1 if matches.get((0, 0), False) else 0
        exec_acc = 1 if any(
            matches.get((i, j), False)
            for i in range(len(sqls))
            for j in range(len(gold_sqls))
        ) else 0
        precision = sum(matches.get((i, j), False) for i in range(len(sqls)) for j in range(len(gold_sqls))) / len(sqls) if sqls else 0.0
        results.append((index, f"{index}\t{exec_acc}\t{precision:.4f}"))

    for _, line in sorted(results, key=lambda x: int(x[0])):
        print(line)
 

if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument("--dataset", type=str, required=True)
    parser.add_argument("--input_path", type=str, required=True)
    parser.add_argument("--db_dir", type=str, default="./dataset/ambiqt/db-content/database", help="root directory for AmbiQT databases (<db_dir>/<db_id>/<db_id>.sqlite)")
    
    args = parser.parse_args()
    
    if args.dataset == "clambsql":
        run_eval_clambsql(args.input_path)
    elif args.dataset == "ambrosia":
        run_eval_ambrosia(args.input_path)
    elif args.dataset == "ambiqt":
        run_eval_ambiqt(args)
    elif args.dataset == "arcwise_plat_sql":
        run_eval_arcwise_plat_sql(args.input_path)
    else:
        print(f"Not supported: {args.dataset}")
