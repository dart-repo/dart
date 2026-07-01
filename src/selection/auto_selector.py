"""
    Automatic selection of detected ambiguities against the gold SQLs.

    Given a detection file, this walks through every item and, for each detected ambiguity, keeps the candidate(s) consistent with the gold query, and the he kept candidates are written back to each item's `selection` field.
"""
import json
import os
import re
import sys

from tqdm import tqdm

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.selection_utils import (
    extract_columns, map_alias_table, resolve_alias_sql, resolve_alias_all,
    is_table_col_referenced, match_aggregate, select_clause_unwrapped,
)


class AutoSelector:
    def __init__(self, args):
        self.detection_path = args.detection_path

    def select(self):
        with open(self.detection_path, 'r') as f:
            detection = json.load(f)

        for data in tqdm(detection):
            gold_sqls = data.get("ambiguous_queries") or [data["query1"], data["query2"]]
            
            column_map = data["detection"].get("column", {})
            table_map = data["detection"].get("table", {})
            join_map = data["detection"].get("join", {})
            aggregate_map = data["detection"].get("aggregate", {})

            per_query = []
            for gold_sql in gold_sqls:
                gold_cols_raw, select_item_count = extract_columns(gold_sql)
                gold_cols = [col.lower() for col in gold_cols_raw]

                # map alias to table
                alias_tbls = map_alias_table(gold_sql)
                alias_refs = resolve_alias_all(alias_tbls, gold_sql)

                # column
                selected_columns = {}
                for token, candidates in column_map.items():
                    matched = []
                    for c in candidates:
                        if c is None:
                            continue
                        col = c.split(".")[-1] if "." in c else c
                        
                        # 1. check if column is in gold SQL
                        if not re.search(rf'\b{re.escape(col)}\b', gold_sql, re.IGNORECASE):
                            continue
                        
                        # 2. check if table is in gold SQL
                        if "." in c:
                            table = c.split(".")[0].lower()
                            if not re.search(rf'\b{re.escape(table)}\b', gold_sql, re.IGNORECASE):
                                continue
                            
                        # 3. check if table.col is referenced in gold SQL
                        if not is_table_col_referenced(c, alias_refs):
                            continue
                        
                        matched.append(c)
                    
                    if matched:
                        selected_columns[token] = matched

                # table
                selected_tables = {}
                for token, candidates in table_map.items():
                    matched = []
                    for t in candidates:
                        if t is None:
                            continue
                        
                        # check if table is in gold SQL
                        if re.search(rf'\b{re.escape(t)}\b', gold_sql, re.IGNORECASE):
                            matched.append(t)
                    
                    if matched:
                        selected_tables[token] = matched
                
                # join
                selected_joins = {}
                if join_map:
                    has_join = re.search(r'\bjoin\b', gold_sql, re.IGNORECASE)
                    for token, candidates in join_map.items():
                        cands = [c for c in candidates if c]
                        if has_join:
                            # gold has a JOIN: all candidate tables must be present (joined),
                            # and at least one candidate table.col must be referenced
                            tables = {c.split(".")[0].lower() for c in cands if "." in c}
                            all_tables = bool(tables) and all(
                                re.search(rf'\b{re.escape(t)}\b', gold_sql, re.IGNORECASE) for t in tables)
                            any_col = any(
                                re.search(rf'\b{re.escape(c.split(".")[-1])}\b', gold_sql, re.IGNORECASE)
                                and is_table_col_referenced(c, alias_refs)
                                for c in cands)
                            if all_tables and any_col:
                                selected_joins[token] = cands
                        else:
                            # no JOIN: table.col existence check
                            matched = []
                            for c in cands:
                                col = c.split(".")[-1].lower() if "." in c else c.lower()
                                if col not in gold_cols:
                                    continue
                                if "." in c:
                                    table = c.split(".")[0].lower()
                                    if not re.search(rf'\b{re.escape(table)}\b', gold_sql, re.IGNORECASE):
                                        continue
                                if not is_table_col_referenced(c, alias_refs):
                                    continue
                                matched.append(c)
                            if matched:
                                selected_joins[token] = matched[-1:] if len(matched) > 1 else matched

                # resolve aliases on the gold SQL
                resolved_gold_sql = resolve_alias_sql(alias_tbls, gold_sql)

                # aggregate
                selected_aggregates = {}
                for token, candidates in aggregate_map.items():
                    matched = []
                    for c in candidates:
                        if c is not None and match_aggregate(c, resolved_gold_sql):
                            matched.append(c)

                    if matched:
                        selected_aggregates[token] = matched

                # output
                output_schema = []
                select_unwrapped = select_clause_unwrapped(resolved_gold_sql)
                for c in data["detection"]["output"]:
                    if c is None or c in output_schema:
                        continue
                    
                    if "(" in c:
                        matched = match_aggregate(c, resolved_gold_sql)
                    
                    elif "." in c:
                        table, col = c.split(".", 1)
                        matched = (
                            re.search(rf'\b{re.escape(col)}\b', select_unwrapped, re.IGNORECASE)
                            and re.search(rf'\b{re.escape(table)}\b', gold_sql, re.IGNORECASE)
                            and is_table_col_referenced(c, alias_refs)
                        )
                    
                    else:
                        matched = re.search(rf'\b{re.escape(c)}\b', select_unwrapped, re.IGNORECASE) is not None
                    
                    if matched:
                        output_schema.append(c)
                
                # every gold SELECT item must be covered
                if len(output_schema) != select_item_count:
                    output_schema = []

                per_query.append({
                    "column": selected_columns,
                    "table": selected_tables,
                    "join": selected_joins,
                    "aggregate": selected_aggregates,
                    "output": output_schema,
                })

            data["selection"] = per_query

        with open(self.detection_path, 'w', encoding="utf-8") as f:
            json.dump(detection, f, indent=4)
