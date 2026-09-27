"""Send every OpenRouter host of DeepSeek V4.1 Flash an image URL and a tool, and record what comes back.

One request shows several things at once:
- the tool-call id format, which depends on the software that generated it;
- the response's system_fingerprint, which DeepSeek's API sets to its backend configuration;
- the upstream_id OpenRouter recorded for the request, which some hosts pass through from their backend;
- who downloaded the image, since whoever runs the model has to download it (unless OpenRouter does).

DeepSeek's own endpoint and ByteDance's own Seed endpoint are asked too, to show what DeepSeek's
and ByteDance's responses look like.
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
    if model == V41:
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

# Save who OpenRouter lists as hosting V4.1 Flash today, and ask every one of them.
listing = fetch(f"https://openrouter.ai/api/v1/models/{V41}/endpoints")
with open("results/listing.json", "w") as f:
    json.dump(listing, f, indent=2)
hosts = []
for endpoint in json.loads(listing["response_body"])["data"]["endpoints"]:
    if (V41, endpoint["provider_name"]) not in hosts:
        hosts.append((V41, endpoint["provider_name"]))
hosts.append((SEED, "Seed"))

# Send every request. Each gets its own image URL, so each download can be matched to its request.
address = image_server.start()
sent = []
for model, provider in hosts:
    for i in range(TRIES):
        path = f"/{provider.replace(' ', '-')}/{i}.png"
        attempts = send(model, provider, address + path)
        print(provider, i, attempts[-1]["status"], flush=True)
        sent.append({"model": model, "provider": provider, "image_path": path, "attempts": attempts})

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
    with open(f"results/{provider.replace(' ', '-')}.json", "w") as f:
        json.dump([request for request in sent if request["provider"] == provider], f, indent=2)
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
            if request["provider"] != provider:
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
