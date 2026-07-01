#!/bin/bash

python3 -u ./src/preprocess.py \
        --dataset_name ambrosia \
        --input_path ./dataset/ambrosia/data/ambrosia.csv \
        --output_path ./data/ambrosia/ambrosia.json \
        --db_root_directory ./dataset/ambrosia

python3 -u ./src/preprocess.py \
        --dataset_name ambiqt \
        --input_path ./dataset/ambiqt/benchmark \
        --output_path ./data/ambiqt/ambiqt.json \
        --db_root_directory ./dataset/ambiqt/db-content/database

python3 -u ./src/preprocess.py \
        --dataset_name clambsql \
        --input_path ./dataset/clambsql/clambsql.json \
        --output_path ./data/clambsql/clambsql.json \
        --db_root_directory ./dataset/clambsql/database

python3 -u ./src/preprocess.py \
        --dataset_name arcwise_plat_sql \
        --input_path ./dataset/arcwise_plat_sql/arcwise_plat_sql_only_with_diff.json \
        --output_path ./data/arcwise_plat_sql/arcwise_plat_sql.json \
        --db_root_directory ./dataset/arcwise_plat_sql/dev_databases
