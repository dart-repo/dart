import argparse
import json
import os
import time

from openai import OpenAI
from dotenv import load_dotenv

from tqdm import tqdm
from utils.llm import setup_logger, ask_llm, parse_sql_response, calc_cost

load_dotenv()

GENERATION_PROMPT = '''## Text-to-SQL task
### The task is to write SQL queries based on the provided questions in English. 
### Questions can take the form of an instruction or command. 
### Do not include any explanations, and do not select extra columns beyond those requested in the question.

Given the following SQLite database schema:

[SCHEMA]

Answer the following:
[QUESTION]

SQL:'''

GENERATION_PROMPT_W_EVIDENCE = '''## Text-to-SQL task
### The task is to write SQL queries based on the provided questions in English. 
### Questions can take the form of an instruction or command. 
### Do not include any explanations, and do not select extra columns beyond those requested in the question.

Given the following SQLite database schema:

[SCHEMA]

Answer the following:
[QUESTION]

Hint: [HINT]

SQL:'''

POLL_INTERVAL = 30
BATCH_JSONL_SUFFIX = "_batch.jsonl"

def generate_sql(llm_model, schema, question, hint=None):
    if hint:
        prompt = (GENERATION_PROMPT_W_EVIDENCE
                    .replace('[SCHEMA]', schema)
                    .replace('[QUESTION]', question)
                    .replace('[HINT]', hint))
    else:
        prompt = (GENERATION_PROMPT
                    .replace('[SCHEMA]', schema)
                    .replace('[QUESTION]', question))
    
    res = ask_llm(llm_model, prompt)
    return parse_sql_response(res)

def build_request(custom_id: str, model: str, schema: str, question: str, hint: str = None) -> dict:
    if hint:
        prompt = (GENERATION_PROMPT_W_EVIDENCE
                  .replace('[SCHEMA]', schema)
                  .replace('[QUESTION]', question)
                  .replace('[HINT]', hint))
    else:
        prompt = (GENERATION_PROMPT
                  .replace('[SCHEMA]', schema)
                  .replace('[QUESTION]', question))
    
    return {
        "custom_id": custom_id,
        "method": "POST",
        "url": "/v1/chat/completions",
        "body": {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        },
    }

def submit_batch(mode: str, client: OpenAI, dataset: list, model: str, jsonl_path: str) -> str:
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for data in dataset:
            data_idx = data["index"]
            schema = data["schema_with_content"]
            hint = data.get("evidence")
            
            if mode == "dart":
                for q_idx, question in enumerate(data["rewriting"]):
                    if len(data["rewriting"]) == 4:
                        if data["detection"]["scope"]:
                            if q_idx not in [0, 2]:
                                continue
                        elif data["detection"]["attach"]:
                            if q_idx not in [1, 3]:
                                continue
                    elif len(data["rewriting"]) == 8:
                        if q_idx not in [0, 4]:
                            continue
                    custom_id = f"{data_idx}-{q_idx}"
                    req = build_request(custom_id, model, schema, question, hint)
                    f.write(json.dumps(req) + "\n")
            elif mode == "clear":
                if data.get("dq"):
                    questions = [data["dq"]]
                else:
                    seen = set()
                    questions = []
                    for question in data["DQs"]:
                        if question in seen:
                            continue
                        seen.add(question)
                        questions.append(question)

                for q_idx, question in enumerate(questions):
                    custom_id = f"{data_idx}-{q_idx}"
                    req = build_request(custom_id, model, schema, question, hint)
                    f.write(json.dumps(req) + "\n")
            elif mode == "nodisambig":
                custom_id = str(data_idx)
                question = data["question"]
                req = build_request(custom_id, model, schema, question, hint)
                f.write(json.dumps(req) + "\n")
                    
    input("Press Enter to continue after checking the JSONL file...")

    with open(jsonl_path, "rb") as f:
        upload = client.files.create(file=f, purpose="batch")

    batch = client.batches.create(
        input_file_id=upload.id,
        endpoint="/v1/chat/completions",
        completion_window="24h",
    )

    print(f"Batch submitted. batch_id={batch.id}")
    print("Save this ID in case you need to resume with --batch_id")

    return batch.id

def poll_batch(client: OpenAI, batch_id: str) -> object:
    terminal = {"completed", "failed", "expired", "cancelled"}
   
    while True:
        batch = client.batches.retrieve(batch_id)
        counts = batch.request_counts
        status_msg = (
            f"[{batch.status}] "
            f"total={counts.total} "
            f"completed={counts.completed} "
            f"failed={counts.failed}"
        )
        print(status_msg)

        if batch.status in terminal:
            return batch

        time.sleep(POLL_INTERVAL)

def collect_results(mode: str, client: OpenAI, batch: object, dataset: list,
                    output_path: str, model: str) -> None:
    if batch.status != "completed":
        raise RuntimeError(
            f"Batch ended with status '{batch.status}' — cannot collect results."
        )

    content = client.files.content(batch.output_file_id).text

    results_map: dict[str, str] = {}
    prompt_tokens_total = 0
    completion_tokens_total = 0

    for line in content.strip().splitlines():
        item = json.loads(line)
        custom_id = item["custom_id"]
        if item.get("error"):
            results_map[custom_id] = ""
            continue

        response_body = item["response"]["body"]
        raw_text = response_body["choices"][0]["message"]["content"]
        results_map[custom_id] = parse_sql_response(raw_text)

        usage = response_body.get("usage", {})
        prompt_tokens_total     += usage.get("prompt_tokens", 0)
        completion_tokens_total += usage.get("completion_tokens", 0)

    cost = calc_cost(model, prompt_tokens_total, completion_tokens_total) * 0.5
    print(f"Total Cost: ${cost:.6f}")

    output: list[dict] = []
    for data in dataset:
        data_idx = data["index"]
        predicted_sqls = []
        
        if mode == "dart":
            for q_idx in range(len(data["rewriting"])):
                custom_id = f"{data_idx}-{q_idx}"
                sql = results_map.get(custom_id, "")
                predicted_sqls.append(sql)
        elif mode == "clear":
            if data.get("dq"):
                questions = [data["dq"]]
            else:
                seen = set()
                questions = []
                for question in data["DQs"]:
                    if question in seen:
                        continue
                    seen.add(question)
                    questions.append(question)

            for q_idx in range(len(questions)):
                custom_id = f"{data_idx}-{q_idx}"
                sql = results_map.get(custom_id, "")
                predicted_sqls.append(sql)
        elif mode == "nodisambig":
            custom_id = str(data_idx)
            sql = results_map.get(custom_id, "")
            predicted_sqls.append(sql)
        
        data["prediction"] = predicted_sqls
        output.append(data)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=4)

    print(f"Done. Results written to {output_path}")
    
def parse(args):
    setup_logger(args.log_path)
    
    with open(args.input_path, 'r') as f:
        dataset = json.load(f)
        
    idx, results = 0, []
    if os.path.exists(args.output_path):
        with open(args.output_path, 'r') as f:
            results = json.load(f)
        idx = len(results)
    
    for i, data in enumerate(tqdm(dataset)):
        if i < idx:
            continue
        
        schema = data["schema_with_content"]
        hint = data.get("evidence")
        
        predicted_sqls = []
        
        if args.mode == "dart":
            for q_idx, question in enumerate(data["rewriting"]):
                if len(data["rewriting"]) == 4:
                    if data["detection"]["scope"]:
                        if q_idx not in [0, 2]:
                            continue
                    elif data["detection"]["attach"]:
                        if q_idx not in [1, 3]:
                            continue
                elif len(data["rewriting"]) == 8:
                    if q_idx not in [0, 4]:
                        continue
                sql = generate_sql(args.llm_model, schema, question, hint)
                predicted_sqls.append(sql)
        elif args.mode == "clear":
            if data.get("dq"):
                questions = [data["dq"]]
            else:
                seen = set()
                questions = []
                for question in data["DQs"]:
                    if question in seen:
                        continue
                    seen.add(question)
                    questions.append(question)

            for question in questions:
                sql = generate_sql(args.llm_model, schema, question, hint)
                predicted_sqls.append(sql)
        elif args.mode == "nodisambig":
            question = data["question"]
            sql = generate_sql(args.llm_model, schema, question, hint)
            predicted_sqls.append(sql)
        
        data["prediction"] = predicted_sqls
        results.append(data)
        
        with open(args.output_path, 'w', encoding="utf-8") as f:
            json.dump(results, f, indent=4)
            
def parse_batch(args):
    with open(args.input_path, "r") as f:
        dataset = json.load(f)

    client = OpenAI()
    jsonl_path = args.output_path.removesuffix(".json") + BATCH_JSONL_SUFFIX

    batch_id = args.batch_id
    if not batch_id:
        batch_id = submit_batch(args.mode, client, dataset, args.llm_model, jsonl_path)

    batch = poll_batch(client, batch_id)
    collect_results(args.mode, client, batch, dataset, args.output_path, args.llm_model)

def run(args):
    os.makedirs(os.path.dirname(args.output_path), exist_ok=True)
    
    if args.batch:
        parse_batch(args)
    else:
        parse(args)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", type=str, default="dart", choices=["dart", "clear", "nodisambig"])
    parser.add_argument("--input_path", type=str, required=True)
    parser.add_argument("--output_path", type=str, required=True)
    parser.add_argument("--log_path", type=str, required=True)
    parser.add_argument("--llm_model", type=str, default="gpt-4o")
    parser.add_argument('--batch', action='store_true')
    parser.add_argument("--batch_id", type=str, default=None)
    
    args = parser.parse_args()
    
    run(args)