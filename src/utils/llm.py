import re
import json
import logging

from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

MODEL_PRICING = {
    "gpt-4o": {"input": 2.50, "cached-input": 1.25, "output": 10.00},
}

usage_totals = {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
    "cost_usd": 0.0,
    "calls": 0,
}

logger = logging.getLogger(__name__)

def setup_logger(log_path):
    handler = logging.FileHandler(log_path)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)

def calc_cost(model, prompt_tokens, completion_tokens):
    pricing = MODEL_PRICING.get(model)
    if not pricing:
        return 0.0
    return (prompt_tokens * pricing["input"] + completion_tokens * pricing["output"]) / 1_000_000

def log_usage_summary():
    t = usage_totals
    logger.info(
        f"[SUMMARY] calls={t['calls']} | "
        f"in={t['prompt_tokens']} out={t['completion_tokens']} total={t['total_tokens']} | "
        f"total_cost=${t['cost_usd']:.6f}"
    )
    
def ask_llm(model, prompt, temperature=0):
    client = OpenAI()
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
    )
    
    usage = response.usage
    cost = calc_cost(model, usage.prompt_tokens, usage.completion_tokens)

    usage_totals["prompt_tokens"]     += usage.prompt_tokens
    usage_totals["completion_tokens"] += usage.completion_tokens
    usage_totals["total_tokens"]      += usage.total_tokens
    usage_totals["cost_usd"]          += cost
    usage_totals["calls"]             += 1
    
    logger.info(
        f"[LLM] model={model} | "
        f"in={usage.prompt_tokens} out={usage.completion_tokens} total={usage.total_tokens} | "
        f"cost=${cost:.6f}"
    )
    logger.info(
        f"[TOT] calls={usage_totals['calls']} | "
        f"in={usage_totals['prompt_tokens']} out={usage_totals['completion_tokens']} total={usage_totals['total_tokens']} | "
        f"running_cost=${usage_totals['cost_usd']:.6f}"
    )
    
    return response.choices[0].message.content

def parse_json_response(response):
    response = response.strip()
    match = re.findall(r"```json(.*?)```", response, re.DOTALL)
    if match:
        response = match[0].strip()
    try:
        return json.loads(response)
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse JSON response: {e}\nResponse: {response}")
    
def parse_sql_response(res):
    if isinstance(res, list):
        return res

    res = res.replace("\n", " ")
    sql = ""

    if "```sql" in res:
        try:
            sql = re.findall("```sql(.*?)```", res)[0]
            sql = " ".join(sql.split()).strip()
        except:
            print(res)
    elif res.strip().lower().startswith("select"):
        sql = res.strip()

    return sql
