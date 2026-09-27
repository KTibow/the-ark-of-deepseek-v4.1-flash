# The Ark of DeepSeek V4.1 Flash

Some OpenRouter hosts of DeepSeek V4.1 Flash don't run it themselves. This repo shows which ones pass requests to DeepSeek's own API, and which pass them to Ark, ByteDance's model API (Volcano Engine Ark in China, BytePlus ModelArk elsewhere).

Each request carries an image URL and a tool, so one response shows four things:

- **The tool-call id.** The software that generates the tool call picks its format. DeepSeek's API writes `call_00_` + 20 letters/digits + 4 digits. On V4.1 Flash, Ark writes `call_00_` + 24 lowercase letters/digits.
- **`system_fingerprint`.** DeepSeek's API sets it to "the backend configuration that the model runs with". Hosts running the model themselves usually leave it empty.
- **The `upstream_id` OpenRouter recorded** ([`/api/v1/generation`](https://openrouter.ai/docs/api-reference/get-a-generation)). Most hosts replace it with their own id, but some pass their backend's through. ByteDance's are `02` + the time in milliseconds + hex.
- **Who downloaded the image.** To answer, whoever runs the model has to download the image, unless OpenRouter downloads it first and sends the bytes. The registries (through [RDAP](https://about.rdap.org)) say who owns each downloading IP address.

DeepSeek's own endpoint and ByteDance's own Seed endpoint get the same request, to show what DeepSeek's and ByteDance's responses look like. Seed is a ByteDance model, not V4.1 Flash, so its tool-call ids don't have V4.1's `00_`; its `upstream_id`s and the networks it downloads from are ByteDance's.

So a host's requests go to DeepSeek when they come back with DeepSeek's tool-call id format and fingerprint, and to Ark when they come back with Ark's tool-call id format and either a ByteDance `upstream_id` or a ByteDance network downloading the image. Hosts that download images in their own gateway and replace `upstream_id` only show the tool-call id.

## What runs

[`probe.py`](probe.py) runs in GitHub Actions ([workflow](.github/workflows/probe.yml)). It:

1. Saves OpenRouter's list of V4.1 Flash hosts.
2. Starts [`image_server.py`](image_server.py), which serves a red square at a public `https://<random words>.trycloudflare.com` address. Cloudflare tells the server the IP address of each download.
3. Sends every V4.1 Flash host, and Seed, this request 6 times, pinned to that host with `"provider": {"only": ["<host>"], "allow_fallbacks": false}`. Each request's image URL is unique, so each download can be matched to its request.

   ```json
   {
     "messages": [{"role": "user", "content": [
       {"type": "text", "text": "Report this image's main color with the tool."},
       {"type": "image_url", "image_url": {"url": "<address>/<host>/<n>.png"}}
     ]}],
     "tools": [{"type": "function", "function": {"name": "report_color", "description": "Report the main color of the image", "parameters": {"type": "object", "properties": {"color": {"type": "string"}}, "required": ["color"]}}}],
     "reasoning": {"enabled": false},
     "max_tokens": 400
   }
   ```

   (Seed's request leaves out `reasoning`.) It retries a rate-limited (429) request up to 4 times, 30 seconds apart, and keeps every attempt.
4. Fetches OpenRouter's generation record for every request that worked, and looks up the owner of every IP address that downloaded an image.
5. Writes `results/<host>.json` for each host (every attempt's request body, timestamps, HTTP status, response headers and raw response body, plus the generation record; the API key is not included), `results/downloads.json` (every download with its headers, and the registry lookups), and `results/summary.md`. The summary counts, for each host, how many requests gave each combination of tool-call id format, `system_fingerprint`, `upstream_id` format and downloader.

### Attestation

The workflow then signs a [build provenance attestation](https://docs.github.com/en/actions/concepts/security/artifact-attestations) over every file in `results/`. The attestation ties each file's SHA-256 to this repository, the commit and the workflow run. It is also recorded in Sigstore's public [Rekor](https://search.sigstore.dev) transparency log, which fixes the time it was signed. The files and the attestation (`attestation.jsonl`) are published as a release, because release files don't expire like workflow logs do.

## Verify

Download a release's files, then run:

```sh
gh attestation verify summary.md --repo KTibow/the-ark-of-deepseek-v4.1-flash --bundle attestation.jsonl
```

The same command works for every other file in the release. Leave out `--bundle` to fetch the attestation from GitHub instead. The attestation names the commit it ran from, so you can read the exact code that produced the files.
