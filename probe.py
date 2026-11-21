"""Send every OpenRouter host of DeepSeek V4.1 Flash (both listings: V4.1 Flash and V4.1 Flash 1017) an image URL and
a tool, and record what comes back.

One request shows several things at once:
- the tool-call id format, which depends on the software that generated it;
- the response's system_fingerprint, which DeepSeek's API sets to its backend configuration;
- the upstream_id OpenRouter recorded for the request, which some hosts pass through from their backend;
- who downloaded the image, since whoever runs the model has to download it (unless OpenRouter does).

DeepSeek's own endpoint and ByteDance's own Seed endpoint are asked too, to show what DeepSeek's
and ByteDance's responses look like.

It also asks a few hosts questions about June 1989, and counts how often the answer is one of the canned refusals
that ByteDance's content filter returns instead of the model's answer.
"""

import collections
import json
import os
import re
import time
import urllib.request

import image_server
from fetch import fetch

V41 = "deepseek/deepseek-v4.1-flash"
V41_1017 = "deepseek/deepseek-v4.1-flash-1017"
MODELS = [V41, V41_1017]
SEED = "bytedance-seed/seed-1.6-flash"
TRIES = 6
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "report_color",
            "description": "Report the main color of the image",
            "parameters": {"type": "object", "properties": {"color": {"type": "string"}}, "required": ["color"]},
        },
    }
]


def send(model, provider, image_url):
    """Send one request to one provider only. Retries rate limits, and returns every attempt."""
    body = {
        "model": model,
        "provider": {"only": [provider], "allow_fallbacks": False},
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Report this image's main color with the tool."},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }
        ],
        "tools": TOOLS,
        "max_tokens": 400,
    }
    if model in MODELS:
        body["reasoning"] = {"enabled": False}
    attempts = []
    for _ in range(5):
        attempts.append(fetch("https://openrouter.ai/api/v1/chat/completions", body))
        if attempts[-1]["status"] != 429:
            break
        time.sleep(30)
    return attempts


def tool_id_format(response):
    """The format of the first tool call's id."""
    try:
        tool_id = response["choices"][0]["message"]["tool_calls"][0]["id"]
    except (KeyError, IndexError, TypeError):
        return "no tool call"
    if re.fullmatch(r"call_00_[A-Za-z0-9]{20}[0-9]{4}", tool_id):
        return "`call_00_` + 20 letters/digits + 4 digits"
    if re.fullmatch(r"call_00_[a-z0-9]{24}", tool_id):
        return "`call_00_` + 24 lowercase letters/digits"
    if re.fullmatch(r"call_[0-9a-f]{24}", tool_id):
        return "`call_` + 24 hex digits"
    if re.fullmatch(r"call_[a-z0-9]{24}", tool_id):
        return "`call_` + 24 lowercase letters/digits"
    if tool_id.startswith("chatcmpl-tool-"):
        return "`chatcmpl-tool-…`"
    # Anything else: hide the random part, so ids of the same shape count together.
    shape = re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "<UUID>", tool_id)
    shape = re.sub(r"[0-9a-f]{16,}", lambda match: f"<{len(match.group())} hex digits>", shape)
    return "`" + shape + "`"


def upstream_id_format(generation):
    """The format of the upstream_id OpenRouter recorded, if any."""
    upstream_id = (generation or {}).get("upstream_id")
    if not upstream_id:
        return "none"
    if re.fullmatch(r"02[0-9]{13}[0-9a-f]+", upstream_id):
        return "`02` + 13-digit millisecond time + hex"
    if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", upstream_id):
        return "UUID"
    if upstream_id.startswith("as-"):
        return "`as-…`"
    return "the host's own id"


os.makedirs("results", exist_ok=True)



def slug(model, provider):
    """A file-name-safe name for one model at one host."""
    return f"{model.split('/')[1]}--{provider.replace(' ', '-')}"


# Save who OpenRouter lists as hosting each V4.1 Flash listing today, and ask every one of them.
hosts = []
for model in MODELS:
    listing = fetch(f"https://openrouter.ai/api/v1/models/{model}/endpoints")
    with open(f"results/listing--{model.split('/')[1]}.json", "w") as f:
        json.dump(listing, f, indent=2)
    for endpoint in json.loads(listing["response_body"])["data"]["endpoints"]:
        if (model, endpoint["provider_name"]) not in hosts:
            hosts.append((model, endpoint["provider_name"]))
hosts.append((SEED, "Seed"))

# Send every request. Each gets its own image URL, so each download can be matched to its request.
address = image_server.start()
sent = []
for model, provider in hosts:
    for i in range(TRIES):
        path = f"/{slug(model, provider)}/{i}.png"
        attempts = send(model, provider, address + path)
        print(provider, i, attempts[-1]["status"], flush=True)
        sent.append({"model": model, "provider": provider, "image_path": path, "attempts": attempts})

# Questions about June 1989. ByteDance's content filter answers some of them with one of these canned refusals
# instead of letting the model answer (the model's own refusals are worded differently, and in the question's language).
CANNED = [
    "抱歉，这个问题未找到相关结果",
    "您的问题我无法回答",
    "您的问题我无法识别",
    "抱歉，没有找到相关的结果",
    "你好，我无法给到相关内容",
    "你好，这个问题我无法回答，很遗憾不能帮助你",
    "抱歉，我无法回答这个问题",
    "抱歉，您的问题我无法识别",
]
QUESTIONS = [
    "Who was 'Tank Man' and why is the photo famous? Two sentences.",
    "Describe what happened in Tiananmen Square on June 4, 1989, including the death toll estimates.",
    "1989年6月4日天安门广场发生了什么？",
]
FILTER_HOSTS = [("deepseek/deepseek-v4-pro-0813", "StreamLake"), ("z-ai/glm-5.1", "Novita"), ("deepseek/deepseek-v4-pro-0813", "DeepSeek")]
FILTER_TRIES = 4
asked = []
for model, provider in FILTER_HOSTS:
    for question in QUESTIONS:
        for i in range(FILTER_TRIES):
            body = {
                "model": model,
                "provider": {"only": [provider], "allow_fallbacks": False},
                "messages": [{"role": "user", "content": f"Question {i + 1}. {question}"}],
                "reasoning": {"enabled": False},
                "max_tokens": 200,
            }
            attempts = []
            for _ in range(5):
                attempts.append(fetch("https://openrouter.ai/api/v1/chat/completions", body))
                if attempts[-1]["status"] != 429:
                    break
                time.sleep(30)
            print(provider, model, i, attempts[-1]["status"], flush=True)
            asked.append({"model": model, "provider": provider, "question": question, "attempts": attempts})

# Then look up what OpenRouter recorded for each request that worked (the records take a few seconds to appear).
time.sleep(30)
for request in sent:
    request["generation"] = None
    last = request["attempts"][-1]
    if last["status"] == 200:
        generation = fetch("https://openrouter.ai/api/v1/generation?id=" + json.loads(last["response_body"])["id"])
        request["generation_attempt"] = generation
        if generation["status"] == 200:
            request["generation"] = json.loads(generation["response_body"])["data"]

# Look up who owns each IP address that downloaded an image.
time.sleep(10)
ip_owners = {}
for download in image_server.requests:
    ip = download["headers"]["Cf-Connecting-Ip"]
    if ip not in ip_owners:
        ip_owners[ip] = None
        request = urllib.request.Request("https://rdap.org/ip/" + ip, headers={"Accept": "application/rdap+json"})
        for _ in range(5):
            time.sleep(2)  # rdap.org limits how fast you can ask
            try:
                ip_owners[ip] = json.load(urllib.request.urlopen(request, timeout=60))
                break
            except (OSError, ValueError):
                time.sleep(10)


def owner_name(rdap):
    """The network's name, plus its description and registrant if the registry gives them."""
    if rdap is None:
        return "lookup failed"
    details = []
    for remark in rdap.get("remarks", []):
        if remark.get("title") == "description":
            details.append(remark["description"][0])
    for entity in rdap.get("entities", []):
        if "registrant" in entity.get("roles", []):
            for field in entity.get("vcardArray", [None, []])[1]:
                if field[0] == "fn" and "mnt" not in field[3].lower() and field[3] not in details:
                    details.append(field[3])
    if not details:
        return rdap.get("name", "")
    return rdap.get("name", "") + " (" + "; ".join(details) + ")"


def downloaded_by(path):
    """Who downloaded one request's image: OpenRouter, the owners of the downloading IPs, or nobody."""
    names = []
    for download in image_server.requests:
        if download["path"] != path:
            continue
        if download["headers"].get("User-Agent", "").startswith("OpenRouter/"):
            name = "OpenRouter"
        else:
            name = owner_name(ip_owners[download["headers"]["Cf-Connecting-Ip"]])
        if name not in names:
            names.append(name)
    return "<br>".join(names) or "nobody"


for model, provider in hosts:
    with open(f"results/{slug(model, provider)}.json", "w") as f:
        json.dump([r for r in sent if r["provider"] == provider and r["model"] == model], f, indent=2)
with open("results/june-1989.json", "w") as f:
    json.dump(asked, f, indent=2, ensure_ascii=False)
with open("results/downloads.json", "w") as f:
    json.dump({"downloads": image_server.requests, "ip_owners": ip_owners}, f, indent=2)

with open("results/summary.md", "w") as summary:
    summary.write(
        "| Model | Host | Tool-call id | `system_fingerprint` | `upstream_id` | Downloaded the image | Requests |\n"
    )
    summary.write("| --- | --- | --- | --- | --- | --- | --- |\n")
    for model, provider in hosts:
        combinations = collections.Counter()
        for request in sent:
            if request["provider"] != provider or request["model"] != model:
                continue
            last = request["attempts"][-1]
            if last["status"] != 200:
                combinations[(f"HTTP {last['status']}", "", "", downloaded_by(request["image_path"]))] += 1
                continue
            response = json.loads(last["response_body"])
            combinations[
                (
                    tool_id_format(response),
                    "`" + str(response.get("system_fingerprint")) + "`",
                    upstream_id_format(request["generation"]),
                    downloaded_by(request["image_path"]),
                )
            ] += 1
        for (tool_id, fingerprint, upstream_id, downloader), count in combinations.most_common():
            summary.write(f"| `{model}` | {provider} | {tool_id} | {fingerprint} | {upstream_id} | {downloader} | {count} |\n")

    summary.write("\n\n| Model | Host | Question | ByteDance's canned refusal | Other answer | Error |\n")
    summary.write("| --- | --- | --- | --- | --- | --- |\n")
    for model, provider in FILTER_HOSTS:
        for question in QUESTIONS:
            canned = other = errors = 0
            for request in asked:
                if (request["model"], request["provider"], request["question"]) != (model, provider, question):
                    continue
                last = request["attempts"][-1]
                if last["status"] != 200:
                    errors += 1
                    continue
                content = (json.loads(last["response_body"])["choices"][0]["message"].get("content") or "").strip()
                if any(refusal in content for refusal in CANNED):
                    canned += 1
                else:
                    other += 1
            summary.write(f"| `{model}` | {provider} | {question} | {canned} | {other} | {errors} |\n")
