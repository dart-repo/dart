"""
    Oracle selection that matches the gold SQL to DART's options by structure.

    AutoSelector searches each option's text in the gold SQL with regexes. That misses
    options whose names differ from the SQL text (e.g. output option "max_accelerate" for
    MAX(accelerate)), confuses aliases, keeps every join candidate when the gold has a
    JOIN, and drops the whole output selection when one option over- or under-matches.

    FixedAutoSelector parses each gold SQL (sqlglot), resolves aliases and unqualified
    columns against the record's schema, and then, for each detected ambiguity, keeps the
    options the gold query actually uses:
      column / join  "t.c" options whose column t.c the gold references
      table          tables in the gold FROM/JOIN clauses
      aggregate      "FUNC(t.c)" if the gold applies FUNC to t.c; a pre-computed "t.c" if
                     the gold references it
      output         gold-driven: each gold SELECT item is mapped to one option
                     (t.c / c / t_c, FUNC(c) -> max_c, avg_c, ... ); all items must map, or none
                     is selected
    Only DART's own options are ever selected. Same interface as AutoSelector: the result is
    written to each item's `selection` field in the detection file.
"""
import json
import os
import sys

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify
from tqdm import tqdm

# names DART gives to aggregated output columns, by SQL function
AGG_PREFIXES = {
    "max": ["max", "maximum"],
    "min": ["min", "minimum"],
    "avg": ["avg", "average"],
    "sum": ["sum", "total"],
    "count": ["count", "num", "number_of", "total_number_of"],
}
COUNT_STAR = "<count(*)>"


def parse_schema(schema):
    """'t1 : c1 , c2 | t2 : c3' -> {"t1": {"c1": "TEXT", ...}, ...} (lowercased)."""
    tables = {}
    for part in schema.split("|"):
        if ":" not in part:
            continue
        table, cols = part.split(":", 1)
        tables[table.strip().lower()] = {c.strip().lower(): "TEXT" for c in cols.split(",") if c.strip()}
    return tables


def col_name(column, alias_to_table):
    """exp.Column -> 'table.col' (table resolved from its alias) or 'col' if unresolved."""
    col = column.name.lower()
    table = column.table.lower()
    table = alias_to_table.get(table, table)
    return f"{table}.{col}" if table else col


class GoldQuery:
    """What one gold SQL uses: tables, columns, aggregates and SELECT items."""

    def __init__(self, sql, schema):
        tree = sqlglot.parse_one(sql, read="sqlite")
        try:
            tree = qualify(tree, schema=schema, dialect="sqlite", validate_qualify_columns=False,
                           identify=False, quote_identifiers=False)
        except Exception:
            pass  # keep the unqualified tree; aliases are still resolved below

        self.alias_to_table = {}
        for t in tree.find_all(exp.Table):
            name = t.name.lower()
            self.alias_to_table[(t.alias or t.name).lower()] = name
        self.tables = set(self.alias_to_table.values())
        only_table = next(iter(self.tables)) if len(self.tables) == 1 else None

        def name_of(column):
            n = col_name(column, self.alias_to_table)
            return f"{only_table}.{n}" if "." not in n and only_table else n

        self.name_of = name_of
        self.columns = {name_of(c) for c in tree.find_all(exp.Column)}
        self.aggregates = set()  # (func, 'table.col' or '*')
        for agg in tree.find_all(exp.AggFunc):
            arg = agg.this.this if isinstance(agg.this, exp.Distinct) else agg.this
            if isinstance(agg.this, exp.Distinct) and agg.this.expressions:
                arg = agg.this.expressions[0]
            target = name_of(arg) if isinstance(arg, exp.Column) else "*"
            self.aggregates.add((agg.key.lower(), target))

        node = tree
        while isinstance(node, exp.SetOperation):  # UNION etc.: the left-most SELECT defines the output
            node = node.left
        self.select_items = node.expressions if isinstance(node, exp.Select) else []

    def uses_column(self, option):
        option = option.lower()
        if "." in option:
            return option in self.columns
        return any(c.split(".")[-1] == option for c in self.columns)

    def output_keys(self, item):
        """Option names (most to least preferred) that denote one SELECT item, or None."""
        alias = item.alias.lower() if isinstance(item, exp.Alias) else None
        inner = item.unalias()
        keys = []
        if isinstance(inner, exp.Column):
            full = self.name_of(inner)
            keys = [full, full.split(".")[-1]]
            if "." in full:  # DART also names output columns "<table>_<col>" (stadium.name -> stadium_name)
                keys.append(full.replace(".", "_", 1))
        elif isinstance(inner, exp.AggFunc):
            func = inner.key.lower()
            arg = inner.this.this if isinstance(inner.this, exp.Distinct) else inner.this
            if isinstance(inner.this, exp.Distinct) and inner.this.expressions:
                arg = inner.this.expressions[0]
            if isinstance(arg, exp.Column):
                col = arg.name.lower()
                keys = [f"{p}_{col}" for p in AGG_PREFIXES.get(func, [func])] + [col]
            elif func == "count":
                keys = [COUNT_STAR]
        if alias:
            keys.insert(0, alias)
        return keys or None


def option_matches(option, key):
    option = option.lower()
    if key == COUNT_STAR:  # COUNT(*): "count", "num_x", "number_of_x", ...
        name = option.split(".")[-1]
        return name in ("count", "num", "number") or name.startswith(tuple(p + "_" for p in AGG_PREFIXES["count"]))
    return option == key or ("." in option and option.split(".")[-1] == key and "." not in key)


def select_output(gold, options):
    """Map every gold SELECT item to one option; all or nothing.

    An unused option is preferred; an option may be reused when two SELECT items share a
    name (e.g. Producer.name, Director.name with a single "name" option -> "name, name")."""
    chosen = []
    for item in gold.select_items:
        keys = gold.output_keys(item)
        if not keys:
            return []
        candidates = [o for k in keys for o in options if o is not None and option_matches(o, k)]
        match = next((o for o in candidates if o not in chosen), candidates[0] if candidates else None)
        if match is None:
            return []
        chosen.append(match)
    return chosen


def select_aggregate(gold, option):
    option = option.lower()
    if "(" in option:  # FUNC(t.c)
        func, arg = option.split("(", 1)
        arg = arg.rstrip(")").strip()
        func = func.strip()
        if "." in arg:
            return (func, arg) in gold.aggregates
        return any(f == func and t.split(".")[-1] == arg for f, t in gold.aggregates)
    return gold.uses_column(option)  # pre-computed column


def select_for_query(gold, detection):
    keep = lambda opts, test: [o for o in opts if o and test(o)]
    selection = {"column": {}, "table": {}, "join": {}, "aggregate": {}, "output": []}
    for kind in ["column", "join"]:
        for token, options in (detection.get(kind) or {}).items():
            matched = keep(options, gold.uses_column)
            if matched:
                selection[kind][token] = matched
    for token, options in (detection.get("table") or {}).items():
        matched = keep(options, lambda t: t.lower() in gold.tables)
        if matched:
            selection["table"][token] = matched
    for token, options in (detection.get("aggregate") or {}).items():
        matched = keep(options, lambda o: select_aggregate(gold, o))
        if matched:
            selection["aggregate"][token] = matched
    selection["output"] = select_output(gold, detection.get("output") or [])
    return selection


class FixedAutoSelector:
    def __init__(self, args):
        self.detection_path = args.detection_path

    def select(self):
        with open(self.detection_path, 'r') as f:
            detection = json.load(f)

        failed = 0
        for data in tqdm(detection):
            gold_sqls = data.get("ambiguous_queries") or [data["query1"], data["query2"]]
            schema = parse_schema(data.get("schema", ""))
            per_query = []
            for gold_sql in gold_sqls:
                try:
                    gold = GoldQuery(gold_sql, schema)
                except Exception:  # unparsable gold SQL: select nothing for this reading
                    failed += 1
                    per_query.append({"column": {}, "table": {}, "join": {}, "aggregate": {}, "output": []})
                    continue
                per_query.append(select_for_query(gold, data["detection"]))
            data["selection"] = per_query

        with open(self.detection_path, 'w', encoding="utf-8") as f:
            json.dump(detection, f, indent=4)
        print(f"FixedAutoSelector: {len(detection)} items, {failed} gold SQLs could not be parsed")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--detection_path", required=True, help="detection file; `selection` is written in place")
    FixedAutoSelector(parser.parse_args()).select()
