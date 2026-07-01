#!/bin/bash

source .env

python3 -u ./src/evaluate.py \
            --dataset ambrosia \
            --input_path ./data/ambrosia/prediction.json > data/ambrosia/evaluate.out 2>&1

python3 -u ./src/evaluate.py \
            --dataset ambiqt \
            --input_path ./data/ambiqt/prediction.json \
            --db_dir ./dataset/ambiqt/db-content/database > data/ambiqt/evaluate_join.out 2>&1

python3 -u ./src/evaluate.py \
            --dataset clambsql \
            --input_path ./data/clambsql/prediction.json > data/clambsql/evaluate.out 2>&1

python3 -u ./src/evaluate.py \
            --dataset arcwise_plat_sql \
            --input_path ./data/arcwise_plat_sql/prediction.json > data/arcwise_plat_sql/evaluate.out 2>&1
