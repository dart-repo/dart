import json
import time

from openai import OpenAI
from tqdm import tqdm

from utils.prompts import *
from utils.llm import ask_llm, parse_json_response, calc_cost

AMBIG_TYPES = ["scope", "attach", "column", "table", "join", "aggregate", "output"]
POLL_INTERVAL = 10
BATCH_IDS_SUFFIX = "_batch_ids.json"

def detect_ambiguity(ambig_type, llm_model, question, hint="", schema=""):
    prompt = get_prompt(ambig_type, question=question, hint=hint, schema=schema)
    res = ask_llm(llm_model, prompt)
    return parse_json_response(res)

def build_request(custom_id: str, model: str, ambig_type: str,
                  question: str, hint: str, schema: str) -> dict:
    prompt = get_prompt(ambig_type, question=question, hint=hint, schema=schema)
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

def submit_batches(client: OpenAI, dataset: list, model: str,
                   detection_path: str) -> dict[str, str]:
    batch_ids = {}
    base = detection_path.removesuffix(".json")

    for ambig_type in AMBIG_TYPES:
        jsonl_path = f"{base}_{ambig_type}_batch.jsonl"

        with open(jsonl_path, "w", encoding="utf-8") as f:
            for idx, item in enumerate(dataset):
                if item.get("is_ambig") == "False":
                    continue
                
                req = build_request(
                    custom_id=str(idx),
                    model=model,
                    ambig_type=ambig_type,
                    question=item["question"],
                    hint=item.get("evidence", ""),
                    schema=item["schema"],
                )
                f.write(json.dumps(req) + "\n")
                
        with open(jsonl_path, "rb") as f:
            upload = client.files.create(file=f, purpose="batch")

        batch = client.batches.create(
            input_file_id=upload.id,
            endpoint="/v1/chat/completions",
            completion_window="24h",
        )
        batch_ids[ambig_type] = batch.id
        
        print(f"[{ambig_type}] batch submitted: {batch.id}")

    ids_path = base + BATCH_IDS_SUFFIX
    with open(ids_path, "w", encoding="utf-8") as f:
        json.dump(batch_ids, f, indent=4)
    print(f"Batch IDs saved to {ids_path}")

    return batch_ids

def poll_batches(client: OpenAI, batch_ids: dict[str, str]) -> dict[str, object]:
    terminal = {"completed", "failed", "expired", "cancelled"}
    remaining = dict(batch_ids)
    done: dict[str, object] = {}

    while remaining:
        for ambig_type, batch_id in list(remaining.items()):
            batch = client.batches.retrieve(batch_id)
            counts = batch.request_counts
            status_msg = (
                f"[{ambig_type}] status={batch.status} "
                f"total={counts.total} "
                f"completed={counts.completed} "
                f"failed={counts.failed}"
            )
            print(status_msg)
            
            if batch.status in terminal:
                done[ambig_type] = batch
                del remaining[ambig_type]

        if remaining:
            time.sleep(POLL_INTERVAL)

    return done

def collect_results(client: OpenAI, batches: dict[str, object],
                    dataset: list, detection_path: str, model: str) -> None:
    prompt_tokens_total = 0
    completion_tokens_total = 0
    type_results: dict[str, dict[str, object]] = {}

    for ambig_type, batch in batches.items():
        if batch.status != "completed":
            print(f"Warning: [{ambig_type}] ended with status '{batch.status}' — skipping.")
            type_results[ambig_type] = {}
            continue

        content = client.files.content(batch.output_file_id).text
        results_map: dict[str, object] = {}

        for line in content.strip().splitlines():
            item = json.loads(line)
            custom_id = item["custom_id"]
            if item.get("error"):
                results_map[custom_id] = None
                continue

            response_body = item["response"]["body"]
            raw_text = response_body["choices"][0]["message"]["content"]
            results_map[custom_id] = parse_json_response(raw_text)

            usage = response_body.get("usage", {})
            prompt_tokens_total += usage.get("prompt_tokens", 0)
            completion_tokens_total += usage.get("completion_tokens", 0)

        type_results[ambig_type] = results_map

    cost = calc_cost(model, prompt_tokens_total, completion_tokens_total) * 0.5
    print(f"Total Cost: ${cost:.6f}")

    output = []
    for idx, item in enumerate(dataset):
        if item.get("is_ambig") == "False":
            output.append(item)
            continue

        detection = {
            ambig_type: type_results.get(ambig_type, {}).get(str(idx))
            for ambig_type in AMBIG_TYPES
        }
        output.append({**item, "detection": detection})

    with open(detection_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=4)

    print(f"Done. Results written to {detection_path}")
    
class AmbiguityDetector:
    def __init__(self, args):
        self.input_path = args.input_path
        self.detection_path = args.detection_path
        self.llm_model = args.llm_model
        self.resume = args.resume

    def detect(self):
        with open(self.input_path, 'r') as f:
            data = json.load(f)

        # load existing detection results
        if os.path.exists(self.detection_path):
            with open(self.detection_path, 'r') as f:
                results = json.load(f)
            processed = {item["index"] for item in results}
        else:
            results = []
            processed = set()
            
        for item in tqdm(data):
            if item["index"] in processed:
                continue
            
            if item.get("is_ambig") == "False": # ambrosia
                continue
            
            question = item["question"]
            hint = item.get("evidence", "")
            schema = item["schema"]

            results.append({
                **item,
                "detection": {
                    ambig_type: detect_ambiguity(
                        ambig_type, self.llm_model, question, hint, schema
                    ) for ambig_type in [
                        "scope", "attach", 
                        "column", "table", "join",
                        "aggregate", "output"
                    ]
                }
            })

            with open(self.detection_path, 'w', encoding="utf-8") as f:
                json.dump(results, f, indent=4)
                
    def detect_batch(self):
        with open(self.input_path, "r") as f:
            dataset = json.load(f)

        client = OpenAI()
        batch_ids_path = self.detection_path.removesuffix(".json") + BATCH_IDS_SUFFIX

        if os.path.exists(batch_ids_path) and self.resume:
            with open(batch_ids_path, "r") as f:
                batch_ids = json.load(f)
            print(f"Resuming with batch IDs from {batch_ids_path}")
        else:
            batch_ids = submit_batches(client, dataset, self.llm_model, self.detection_path)

        batches = poll_batches(client, batch_ids)
        collect_results(client, batches, dataset, self.detection_path, self.llm_model)
