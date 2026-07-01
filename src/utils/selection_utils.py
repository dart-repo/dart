import re


def extract_columns(sql):
    match = re.search(r'SELECT\s+(.*?)\s+FROM', sql, re.IGNORECASE | re.DOTALL)
    if not match:
        return [], 0

    select_clause = match.group(1).strip() # string between SELECT and FROM

    # drop DISTINCT
    select_clause = re.sub(r'^DISTINCT\s+', '', select_clause, flags=re.IGNORECASE)
    
    columns = []
    depth = 0
    current = []

    # extract list of raw select columns
    for char in select_clause:
        if char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
        elif char == ',' and depth == 0:
            columns.append(''.join(current).strip())
            current = []
            continue
        current.append(char)

    if current:
        columns.append(''.join(current).strip())

    select_item_count = len(columns)

    result = []
    for col in columns:
        # strip alias
        col = re.split(r'\s+AS\s+', col, flags=re.IGNORECASE)[0]

        col = col.strip().strip('`').strip('"').strip("'").strip()

        # strip table/alias prefix (before unwrapping parens, matching dev-backup)
        if '.' in col:
            col = col.split('.')[-1]

        # extract the column inside the innermost parentheses
        if '(' in col:
            inner = re.search(r'\(([^()]+)\)', col)
            if inner:
                inner_col = inner.group(1).strip()
                inner_col = re.sub(r'^DISTINCT\s+', '', inner_col, flags=re.IGNORECASE).strip()
                if inner_col != '*' and ' ' not in inner_col:
                    col = inner_col.strip('`').strip('"').strip("'")
                else:
                    continue
            else:
                continue

        if col and col != '*':
            result.append(col)

    return result, select_item_count

def map_alias_table(sql):
    # map alias to actual table (e.g., {"t1": "singer", "t2": "album"})
    alias_map = {}
    pattern = re.finditer(
        r'\b(?:FROM|JOIN)\s+(\w+)\s+(?:AS\s+)?(\w+)\b',
        sql,
        re.IGNORECASE
    )
    for m in pattern:
        table_name = m.group(1).lower()
        alias = m.group(2).lower()
        keywords = {'on', 'where', 'order', 'group', 'having', 'limit', 'set', 'inner', 'outer', 'left', 'right'}
        if alias not in keywords:
            alias_map[alias] = table_name
    return alias_map

def resolve_alias_sql(alias_tbls, sql):
    # replace alias.col with table.col in the SQL
    if not alias_tbls:
        return sql

    for alias, table in alias_tbls.items():
        sql = re.sub(rf'\b{re.escape(alias)}\.', f'{table}.', sql, flags=re.IGNORECASE)
    
    return sql

def resolve_alias_all(alias_tbls, sql):
    # replace alias.col with table.col for those used in the SQL
    if not alias_tbls:
        return None

    refs = set()
    for m in re.finditer(r'\b(\w+)\.(\w+)\b', sql):
        prefix = m.group(1).lower()
        col = m.group(2).lower()
        if prefix in alias_tbls:
            refs.add(f"{alias_tbls[prefix]}.{col}")

    return refs if refs else None

def is_table_col_referenced(candidate, alias_refs):
    if "." not in candidate:
        return True
    
    table = candidate.split(".")[0].lower()
    col = candidate.split(".")[-1].lower()
    table_col = f"{table}.{col}"

    if alias_refs is not None:
        same_col_refs = {ref for ref in alias_refs if ref.split(".")[-1] == col}
        if same_col_refs:
            return table_col in same_col_refs
    
    return True

def match_aggregate(candidate, gold_sql):
    gold = gold_sql.lower().replace(" ", "")
    c = candidate.lower().replace(" ", "")

    if "(" in c:
        # aggregate-function candidate: must appear as an aggregate in the gold
        if c in gold:
            return True
        # strip table prefix inside parens; "AVG(singer.age)" -> "AVG(age)"
        c_stripped = re.sub(r'\b\w+\.(\w+)', r'\1', c)
        if c_stripped in gold: # check table
            tables_in_candidate = re.findall(r'\b(\w+)\.\w+', candidate.lower())
            for table in tables_in_candidate:
                if not re.search(rf'\b{re.escape(table)}\b', gold_sql, re.IGNORECASE):
                    return False
            return True
        return False

    # raw-column candidate: the column must appear without any aggregate
    # and (if table-qualified) the table must be present.
    stripped_sql = re.sub(r'\([^()]*\)', '', gold_sql)  # aggregate contents removed, spaces kept
    col_only = candidate.split('.')[-1]
    if not re.search(rf'\b{re.escape(col_only)}\b', stripped_sql, re.IGNORECASE):
        return False
    if '.' in candidate:
        table = candidate.split('.')[0]
        if not re.search(rf'\b{re.escape(table)}\b', gold_sql, re.IGNORECASE):
            return False
    return True

def select_clause_unwrapped(sql):
    # SELECT columns without any parenthesized contents
    # so columns that only appear inside aggregate calls drop out
    m = re.search(r'SELECT\s+(.*?)\s+FROM', sql, re.IGNORECASE | re.DOTALL)
    if not m:
        return ""
    s = m.group(1)
    
    while True:
        new = re.sub(r'\([^()]*\)', '', s)
        if new == s:
            return s
        s = new
