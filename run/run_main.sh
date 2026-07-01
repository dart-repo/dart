#!/bin/bash

source .env

python3 -u ./src/main.py \
        --dataset ambrosia \
        --input_path ./data/ambrosia/ambrosia.json \
        --output_path ./data/ambrosia/ \
        --llm_model gpt-4o \
        --auto_select

python3 -u ./src/main.py \
        --dataset ambiqt \
        --input_path ./data/ambiqt/ambiqt.json \
        --output_path ./data/ambiqt/ \
        --llm_model gpt-4o \
        --auto_select

python3 -u ./src/main.py \
        --dataset clambsql \
        --input_path ./data/clambsql/clambsql.json \
        --output_path ./data/clambsql/ \
        --llm_model gpt-4o \
        --auto_select

python3 -u ./src/main.py \
        --dataset arcwise_plat_sql \
        --input_path ./data/arcwise_plat_sql/arcwise_plat_sql.json \
        --output_path ./data/arcwise_plat_sql/ \
        --llm_model gpt-4o \
        --auto_select
