#!/bin/bash

source .env

python3 -u ./src/parser.py \
        --input_path ./data/ambrosia/rewriting.json \
        --output_path ./data/ambrosia/prediction.json \
        --log_path ./data/ambrosia/prediction.log \
        --llm_model gpt-4o

python3 -u ./src/parser.py \
        --input_path ./data/ambiqt/rewriting.json \
        --output_path ./data/ambiqt/prediction.json \
        --log_path ./data/ambiqt/prediction.log \
        --llm_model gpt-4o

python3 -u ./src/parser.py \
        --input_path ./data/clambsql/rewriting.json \
        --output_path ./data/clambsql/prediction.json \
        --log_path ./data/clambsql/prediction.log \
        --llm_model gpt-4o

python3 -u ./src/parser.py \
        --input_path ./data/arcwise_plat_sql/rewriting.json \
        --output_path ./data/arcwise_plat_sql/prediction.json \
        --log_path ./data/arcwise_plat_sql/prediction.log \
        --llm_model gpt-4o
