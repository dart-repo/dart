import os
import argparse
import json
import csv
import sqlite3

from tqdm import tqdm

def get_schema(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [t[0] for t in cursor.fetchall()]

        schema_parts = []
        schema_parts_with_content = []
        for table in tables:
            if table.lower() in ["sqlite_sequence", "sqlite_master"]:
                continue
            cursor.execute(f"PRAGMA table_info(\"{table}\")")
            columns = [col[1] for col in cursor.fetchall()]
            schema_parts.append(f"{table} : {', '.join(columns)}")
            
            col_parts = []
            for col in columns:
                cursor.execute(
                    f"SELECT \"{col}\" FROM \"{table}\" "
                    f"WHERE \"{col}\" IS NOT NULL ORDER BY RANDOM() LIMIT 3"
                )
                examples = ", ".join(repr(row[0]).replace("|", "") for row in cursor.fetchall())
                col_parts.append(f"{col} ({examples})")
            schema_parts_with_content.append(f"{table} : {', '.join(col_parts)}")

        schema = " | ".join(schema_parts)
        schema_with_content = " | ".join(schema_parts_with_content)
        
        return schema, schema_with_content

    except sqlite3.Error as e:
        print(e)
    finally:
        cursor.close()
        conn.close()

def preprocess_arcwise_plat_sql(input_path, output_path, db_root_directory):
    print("Preprocessing Arcwise-Plat-SQL...")
    
    with open(input_path, 'r') as f:
        data = json.load(f)

    processed = []
    for item in tqdm(data):
        db_file = item["db_id"]
        db_path = os.path.join(db_root_directory, db_file, f"{db_file}.sqlite")
        schema, schema_with_content = get_schema(db_path)
        processed.append({
            "index": int(item["question_id"]),
            "db_file": db_path,
            "question": item["question"],
            "evidence": item["evidence"],
            "schema": schema,
            "schema_with_content": schema_with_content,
            "ambiguous_queries": [item["SQL"]]
        })

    if not os.path.exists(os.path.dirname(output_path)):
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
    with open(output_path, 'w') as f:
        json.dump(processed, f, indent=4)

    print(f"Arcwise-Plat-SQL preprocessing complete: {len(processed)} examples saved to {output_path}")
    
def preprocess_clambsql(input_path, output_path, db_root_directory):
    print("Preprocessing CLAMBSQL...")
    
    with open(input_path, 'r') as f:
        data = json.load(f)

    processed = []
    for item in tqdm(data):
        processed.append({
            "index": item["index"],
            "ambig_type": item["ambig_type"],
            "db_file": f"{db_root_directory}/{item['db_file']}",
            "question": item["question"],
            "schema": item["schema_without_content"],
            "schema_with_content": item["schema_with_content"],
            "ambiguous_queries": item["ambiguous_queries"]
        })

    if not os.path.exists(os.path.dirname(output_path)):
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
    with open(output_path, 'w') as f:
        json.dump(processed, f, indent=4)

    print(f"CLAMBSQL preprocessing complete: {len(processed)} examples saved to {output_path}")

def preprocess_ambrosia(input_path, output_path, db_root_directory):
    print("Preprocessing AMBROSIA...")

    with open(input_path, encoding='utf-8') as f:
        csv_dataset = list(csv.reader(f))[1:]

    processed = []
    for i, data in enumerate(tqdm(csv_dataset)):
        db_file = data[11]
        db_path = os.path.join(db_root_directory, db_file)
        schema, schema_with_content = get_schema(db_path)
        sqls = [" ".join(sql.replace("\n", " ").split()) for sql in data[2].split("\n\n")]
        processed.append({
            "index": i,
            "is_ambig": data[12],
            "ambig_type": data[4],
            "db_file": db_path,
            "question": data[1],
            "schema": schema,
            "schema_with_content": schema_with_content,
            "ambiguous_queries": sqls
        })

    if not os.path.exists(os.path.dirname(output_path)):
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
    with open(output_path, 'w') as f:
        json.dump(processed, f, indent=4)

    print(f"AMBROSIA preprocessing complete: {len(processed)} examples saved to {output_path}")

def preprocess_ambiqt(input_path, output_path):
    print("Preprocessing AmbiQT...")

    subsets = {
        "col-synonyms": "column",
        "tbl-agg":      "aggregate",
        "tbl-split":    "join",
        "tbl-synonyms": "table",
    }
    
    ambig_type_keys = {
        "column":    ["extra_map"],
        "aggregate": ["all_raw_cols", "all_cols", "new_table_name", "tables_with_pkeys"],
        "join":      ["primary_key", "split_map"],
        "table":     ["extra_table_map"],
    }

    processed = []
    i = 0
    for folder, ambig_type in subsets.items():
        file_path = os.path.join(input_path, folder, "validation.json")
        with open(file_path, 'r') as f:
            data = json.load(f)

        for item in tqdm(data, desc=folder):
            extra_keys = {key: item[key] for key in ambig_type_keys[ambig_type] if key in item}
            
            processed.append({
                "index": i,
                "ambig_type": ambig_type,
                "db_id": item["db_id"],
                "question": item["question"],
                **extra_keys,
                "orig_query": item["orig_query"],
                "schema": item["schema_without_content"],
                "schema_with_content": item["schema_with_content"],
                "query1": item["query1"],
                "query2": item["query2"]
            })
            i += 1

    if not os.path.exists(os.path.dirname(output_path)):
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    with open(output_path, 'w') as f:
        json.dump(processed, f, indent=4)

    print(f"AmbiQT preprocessing complete: {len(processed)} examples saved to {output_path}")
    

if __name__ == '__main__':
    args_parser = argparse.ArgumentParser()
    args_parser.add_argument('--dataset_name', type=str, required=True)
    args_parser.add_argument('--input_path', type=str, required=True)
    args_parser.add_argument('--output_path', type=str, required=True)
    args_parser.add_argument('--db_root_directory', type=str, default="")

    args = args_parser.parse_args()

    if args.dataset_name == 'arcwise_plat_sql':
        preprocess_arcwise_plat_sql(args.input_path, args.output_path, args.db_root_directory)
    elif args.dataset_name == 'clambsql':
        preprocess_clambsql(args.input_path, args.output_path, args.db_root_directory)
    elif args.dataset_name == 'ambrosia':
        preprocess_ambrosia(args.input_path, args.output_path, args.db_root_directory)
    elif args.dataset_name == 'ambiqt':
        preprocess_ambiqt(args.input_path, args.output_path)
    else:
        raise ValueError(f"Unknown dataset: {args.dataset_name}")
    