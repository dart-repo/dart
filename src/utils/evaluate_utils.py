import re
import json
import sqlite3
import itertools

from sql_metadata import Parser

def normalize_sql(sql):
    sql = sql.strip().rstrip(';')
    return " ".join(sql.lower().split())

def normalize_row(row, cols):
    return tuple(v for _, v in sorted(zip(cols, row), key=lambda x: str(x[0])))

def _row_keys(rows, order_matters):
    # stringify each value
    # keep row order for list-based comparison; sort rows for set-based comparison.
    keys = [tuple(map(str, r)) for r in rows]
    return keys if order_matters else sorted(keys)

def results_equivalent(predicted_res, gold_res, order_matters):
    if len(predicted_res) != len(gold_res):
        return False

    if not gold_res:
        return True

    ncol = len(gold_res[0])
    if any(len(r) != ncol for r in predicted_res) or any(len(r) != ncol for r in gold_res):
        return False

    gold_key = _row_keys(gold_res, order_matters)
    if _row_keys(predicted_res, order_matters) == gold_key:          # identity (fast path)
        return True

    for perm in itertools.permutations(range(ncol)):
        permuted = [tuple(r[i] for i in perm) for r in predicted_res]
        if _row_keys(permuted, order_matters) == gold_key:
            return True

    return False

def compare_sql(pred_sql, gold_sql, db_file):
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()

    try:
        cursor.execute(pred_sql)
        predicted_res = cursor.fetchall()

        cursor.execute(gold_sql)
        gold_res = cursor.fetchall()
    except Exception as e:
        conn.close()
        return None, None, f"{db_file}\tERROR: {e}"

    conn.close()

    if not gold_res:
        return predicted_res, gold_res, None

    order_matters = "order by" in gold_sql.lower()
    result = 1 if results_equivalent(predicted_res, gold_res, order_matters) else 0

    return predicted_res, gold_res, result

def get_column_reverse_map(extra_map, alt_num=0):
    # return shape: {table_name: {alt_col: orig_col}}
    reverse_map = {}
    for table, col_map in extra_map.items():
        table = table.lower()
        reverse_map[table] = {}
        for orig_col, alts in col_map.items():
            if isinstance(alts, list) and len(alts) > alt_num:
                reverse_map[table][alts[alt_num].lower()] = orig_col.lower()
    return reverse_map

def has_any_alt_column(sql, reverse_map):
    sql = normalize_sql(sql)
    for _, col_map in reverse_map.items():
        for alt_col in col_map.keys():
            if re.search(rf"\b(?:\w+\.)?{re.escape(alt_col)}\b", sql):
                return True
    return False

def has_alt_table(sql, table_map, alt_num):
    sql = normalize_sql(sql)
    for _, alt_tables in table_map.items():
        alt_table = alt_tables[alt_num].lower()
        if re.search(rf"\b{re.escape(alt_table)}\b", sql):
            return True
    return False

def rewrite_columns_to_original(sql, reverse_map):
    sql = normalize_sql(sql)
    
    # reverse_map shape: {table_name: {alt_col: orig_col}}
    table, col_map = next(iter(reverse_map.items()))

    # find aliases bound to this table: from table t / from table as t / join table t
    aliases = set(
        re.findall(
            rf"\b(?:from|join)\s+{re.escape(table)}\s+(?:as\s+)?(\w+)\b",
            sql,
        )
    )

    for column, orig_column in col_map.items():
        # table.column -> table.orig_column
        sql = re.sub(
            rf"\b{re.escape(table)}\.{re.escape(column)}\b",
            f"{table}.{orig_column}",
            sql,
        )

        # alias.column -> alias.orig_column (only aliases of mapped table)
        for alias in aliases:
            sql = re.sub(
                rf"\b{re.escape(alias)}\.{re.escape(column)}\b",
                f"{alias}.{orig_column}",
                sql,
            )

        if sql == "select student_id from highschooler except select student_id from friend": # exception
            # only replace the first found column
            sql = re.sub(rf"(?<!\.)\b{re.escape(column)}\b", orig_column, sql, count=1)
        else:
            # bare column replacement only if mapped table appears and query has no joins
            sql = re.sub(rf"(?<!\.)\b{re.escape(column)}\b", orig_column, sql)

    return sql.replace(") '", ")'")

def rewrite_tables_to_original(sql, table_map):
    sql = normalize_sql(sql)
    
    for orig_table, alt_tables in table_map.items():
        for alt_table in alt_tables:
            sql = re.sub(
                rf"\b{re.escape(alt_table)}\b", orig_table, sql, flags=re.IGNORECASE
            )
            
    return sql

def rewrite_join_query(query, split_map, primary_key):
    query = normalize_sql(query)
    present_tbl = list(split_map.keys())[0].lower()
    split_col = split_map[present_tbl].lower()
    split_tbl = f"{present_tbl}_{split_col}"
    pkey = primary_key[list(primary_key.keys())[0]].lower()

    sql = re.sub(rf"\b{re.escape(split_tbl)}\.", f"{present_tbl}.", query)

    pattern = (
        rf"{re.escape(present_tbl)}(?:\s+(?:as\s+)?(\w+))?\s+join\s+{re.escape(split_tbl)}(?:\s+(?:as\s+)?(\w+))?"
        rf"\s+on\s+(?:\w+)\.{re.escape(pkey)}\s*=\s*(?:\w+)\.{re.escape(pkey)}"
        rf"|"
        rf"{re.escape(split_tbl)}(?:\s+(?:as\s+)?(\w+))?\s+join\s+{re.escape(present_tbl)}(?:\s+(?:as\s+)?(\w+))?"
        rf"\s+on\s+(?:\w+)\.{re.escape(pkey)}\s*=\s*(?:\w+)\.{re.escape(pkey)}"
    )

    match = re.search(pattern, sql)
    if not match:
        if re.search(rf"\b{re.escape(split_tbl)}\b", sql):
            sql = re.sub(rf"\b{re.escape(split_tbl)}\b", present_tbl, sql)
            sql = re.sub(rf"\b{re.escape(present_tbl)}\.", "", sql)
            return normalize_sql(sql)
        raise ValueError(f"Failed to rewrite query; join pattern not found: {query}")

    groups = match.groups()
    left_alias, right_alias = (groups[0], groups[1]) if (groups[0] is not None or groups[1] is not None) else (groups[3], groups[2])

    sql = re.sub(pattern, present_tbl, sql)
    sql = re.sub(rf"\b{re.escape(present_tbl)}\.", "", sql)
    sql = re.sub(rf"\b{re.escape(split_tbl)}\.", "", sql)
    for alias in [left_alias, right_alias]:
        if alias:
            sql = re.sub(rf"\b{re.escape(alias)}\.", "", sql)

    sql = re.sub(
        rf"\b{re.escape(present_tbl)}\s+(?!on\b|join\b|where\b|group\b|order\b|limit\b|having\b)(\w+)\b",
        present_tbl,
        sql,
    )
    return " ".join(sql.split())

def split_select_items(sql):
    match = re.search(r"\bselect\s+(.*?)\s+from\b", sql, re.IGNORECASE | re.DOTALL)
    if not match:
        return []

    select_clause = match.group(1).strip()
    select_clause = re.sub(r"^distinct\s+", "", select_clause, flags=re.IGNORECASE)

    items = []
    depth = 0
    current = []

    for char in select_clause:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            item = "".join(current).strip()
            if item:
                items.append(item)
            current = []
            continue
        current.append(char)

    if current:
        item = "".join(current).strip()
        if item:
            items.append(item)

    return items

def build_raw_col_reference_map(orig_query):
    col_map = {}
    for item in split_select_items(orig_query):
        raw_item = re.split(r"\s+as\s+", item, flags=re.IGNORECASE)[0].strip()
        agg_match = re.match(r"\b(sum|avg|min|max|count)\s*\(([^()]+)\)", raw_item, re.IGNORECASE)
        if agg_match:
            inner = agg_match.group(2).strip()
            if agg_match.group(1).lower() == "count" and inner == "*":
                col_map.setdefault("number", raw_item)
                continue
            base_col = inner.split(".")[-1].strip().strip("`").strip('"').strip("'")
            col_map[base_col.lower()] = inner
            continue

        if "." in raw_item:
            base_col = raw_item.split(".")[-1]
        else:
            base_col = raw_item
        base_col = base_col.strip().strip("`").strip('"').strip("'")
        if base_col:
            col_map[base_col.lower()] = raw_item

    return col_map

def rewrite_aggregate_query(query2, orig_query, all_raw_cols, all_cols):
    query2 = normalize_sql(query2)
    orig_query = normalize_sql(orig_query)

    raw_cols = {c.lower() for c in all_raw_cols}
    all_cols_lower = {c.lower() for c in all_cols}
    col_ref_map = build_raw_col_reference_map(orig_query)

    rewritten_items = []
    for item in split_select_items(query2):
        raw_item = re.split(r"\s+as\s+", item, flags=re.IGNORECASE)[0].strip()
        base = raw_item.split(".")[-1].strip().strip("`").strip('"').strip("'")
        base_lower = base.lower()

        if base_lower in raw_cols:
            rewritten_items.append(col_ref_map.get(base_lower, base_lower))
            continue

        if base_lower.startswith("count_"): # handle exception
            raw_name = base_lower[len("count_"):]
            if raw_name in raw_cols:
                inner = col_ref_map.get(raw_name, raw_name)
                rewritten_items.append(f"count({inner})")
                continue

        if base_lower in all_cols_lower:
            for prefix in ["sum_", "avg_", "min_", "max_", "count_"]:
                if base_lower.startswith(prefix):
                    raw_name = base_lower[len(prefix):]
                    if raw_name in raw_cols:
                        inner = col_ref_map.get(raw_name, raw_name)
                        func = prefix[:-1]
                        rewritten_items.append(f"{func}({inner})")
                    else:
                        rewritten_items.append(base_lower)
                    break
            else:
                rewritten_items.append(base_lower)
            continue

        rewritten_items.append(base_lower)

    from_match = re.search(r"\bfrom\b\s+.+", orig_query, re.IGNORECASE | re.DOTALL)
    from_clause = from_match.group(0) if from_match else ""

    return f"select {', '.join(rewritten_items)} {from_clause}".strip()

def summarize_matches(matches, num_alts, num_preds):
    item_gold_matched = sum(
        1 for j in range(num_alts)
        if any(matches.get((i, j), False) for i in range(num_preds))
    )

    item_pred_matched = sum(
        1 for i in range(num_preds)
        if any(matches.get((i, j), False) for j in range(num_alts))
    )

    item_all_found = all(
        any(matches.get((i, j), False) for i in range(num_preds))
        for j in range(num_alts)
    )

    item_recall = item_gold_matched / num_alts if num_alts else 0.0
    item_precision = item_pred_matched / num_preds if num_preds else 0.0

    return item_gold_matched, item_pred_matched, item_all_found, item_recall, item_precision

def parse_gold_ambiguity_column_alts(item):
    gold_ambiguity = item.get("gold_ambiguity")
    if not gold_ambiguity:
        return None

    match_field = gold_ambiguity.get("match") if isinstance(gold_ambiguity, dict) else None
    if not match_field:
        return None

    try:
        match_obj = json.loads(match_field) if isinstance(match_field, str) else match_field
    except Exception:
        return None

    if not isinstance(match_obj, dict) or not match_obj:
        return None

    first_value = next(iter(match_obj.values()))
    if not isinstance(first_value, list) or len(first_value) < 2:
        return None

    alts = []
    for alt in first_value[:2]:
        if not isinstance(alt, dict) or not alt:
            return None
        table, columns = next(iter(alt.items()))
        if not isinstance(columns, list) or not columns:
            return None
        alts.append((str(table).lower(), str(columns[0]).lower()))

    return alts

def parse_gold_ambiguity_table_alts(item):
    gold_ambiguity = item.get("gold_ambiguity")
    if not isinstance(gold_ambiguity, dict):
        return None

    match_field = gold_ambiguity.get("match")
    if not match_field:
        return None

    try:
        match_obj = json.loads(match_field) if isinstance(match_field, str) else match_field
    except Exception:
        return None

    if not isinstance(match_obj, dict) or not match_obj:
        return None

    alt_tables = [set(), set()]
    for value in match_obj.values():
        if not isinstance(value, list) or len(value) < 2:
            return None

        for alt_idx in [0, 1]:
            alt = value[alt_idx]
            if not isinstance(alt, dict) or not alt:
                return None
            table = next(iter(alt.keys()))
            alt_tables[alt_idx].add(str(table).lower())

    return [sorted(alt_tables[0]), sorted(alt_tables[1])]

def sql_has_table_column(sql, table, column):
    table = table.lower()
    column = column.lower()

    sql_norm = normalize_sql(sql)
    parser = Parser(sql)

    parsed_tables = {t.lower() for t in parser.tables}
    if table not in parsed_tables:
        return False

    parsed_columns = {col.split(".")[-1].lower() for col in parser.columns}
    if column in parsed_columns:
        return True

    tokens = (
        sql_norm.replace(",", " , ")
        .replace("(", " ( ")
        .replace(")", " ) ")
        .split()
    )

    for idx, token in enumerate(tokens):
        if token != column:
            continue

        if idx > 0 and tokens[idx - 1] == "as":
            continue

        if idx >= 2 and tokens[idx - 2] in {"from", "join"}:
            continue

        if idx >= 3 and tokens[idx - 3] in {"from", "join"} and tokens[idx - 1] == "as":
            continue

        return True

    return False

def sql_has_table(sql, table):
    parser = Parser(sql)

    parsed_tables = {t.lower() for t in parser.tables}
    return table.lower() in parsed_tables
