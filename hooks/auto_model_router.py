#!/usr/bin/env python3
"""
Auto model router for Claude Code.

Backend selection (priority: env var > ~/.claude/router.conf > default):
  CLAUDE_ROUTER_BACKEND = ollama | deepseek | groq | heuristic

Quick switch:
  echo "CLAUDE_ROUTER_BACKEND=deepseek" > ~/.claude/router.conf
  echo "CLAUDE_ROUTER_BACKEND=ollama"   > ~/.claude/router.conf
  echo "CLAUDE_ROUTER_BACKEND=groq"     > ~/.claude/router.conf

Ollama model override:
  CLAUDE_ROUTER_OLLAMA_MODEL=qwen2.5:0.5b  (default)
  CLAUDE_ROUTER_OLLAMA_MODEL=llama3.2:1b

Timeout (seconds, default 5):
  CLAUDE_ROUTER_TIMEOUT=5
"""
import json, sys, os, re, urllib.request

# ── Load config file (won't override env vars already set) ───────────────────
_conf = os.path.expanduser('~/.claude/router.conf')
if os.path.exists(_conf):
    with open(_conf) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith('#') and '=' in _line:
                _k, _v = _line.split('=', 1)
                os.environ.setdefault(_k.strip(), _v.strip())

# ── Config ───────────────────────────────────────────────────────────────────
BACKEND      = os.environ.get('CLAUDE_ROUTER_BACKEND', 'heuristic').lower()
OLLAMA_MODEL = os.environ.get('CLAUDE_ROUTER_OLLAMA_MODEL', 'qwen2.5:0.5b')
OLLAMA_URL   = os.environ.get('CLAUDE_ROUTER_OLLAMA_URL', 'http://localhost:11434')
TIMEOUT      = int(os.environ.get('CLAUDE_ROUTER_TIMEOUT', '5'))
MODELS       = ['haiku', 'sonnet', 'opus']

SYSTEM_PROMPT = """\
You are a task complexity classifier for an AI coding assistant.
Given the user prompt, reply with EXACTLY ONE word — nothing else:

haiku  → trivial: short factual lookups, single-line edits, yes/no questions
sonnet → moderate: explanations, multi-step tasks, moderate code changes
opus   → complex: architecture design, deep analysis, large codebase work,
          comprehensive implementations, debugging intricate systems

One word only: haiku, sonnet, or opus"""


# ── Backends ─────────────────────────────────────────────────────────────────

def _parse(text: str) -> str:
    t = text.strip().lower().split()[0] if text.strip() else ''
    for m in MODELS:
        if m in t:
            return m
    return 'sonnet'


def classify_ollama(prompt: str) -> str:
    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "prompt": f"{SYSTEM_PROMPT}\n\nUser prompt:\n{prompt[:800]}\n\nClassification:",
        "stream": False,
        "options": {"temperature": 0, "num_predict": 8},
    }).encode()
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return _parse(json.load(r).get('response', ''))


def _openai_compat(url: str, api_key: str, model: str, prompt: str) -> str:
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": prompt[:1000]},
        ],
        "max_tokens": 8,
        "temperature": 0,
    }).encode()
    req = urllib.request.Request(
        url, data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return _parse(json.load(r)['choices'][0]['message']['content'])


def classify_deepseek(prompt: str) -> str:
    key = os.environ.get('DEEPSEEK_API_KEY', '')
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY not set")
    return _openai_compat(
        "https://api.deepseek.com/chat/completions",
        key, "deepseek-chat", prompt,
    )


def classify_groq(prompt: str) -> str:
    key = os.environ.get('GROQ_API_KEY', '')
    if not key:
        raise RuntimeError("GROQ_API_KEY not set")
    return _openai_compat(
        "https://api.groq.com/openai/v1/chat/completions",
        key, "llama-3.1-8b-instant", prompt,
    )


# ── Heuristic fallback ────────────────────────────────────────────────────────
_HEAVY = [
    '架构','设计','重构','分析','实现','优化','调试','解释','如何','为什么',
    '全面','完整','系统','整体','详细','全量','全部','追踪',
    'architecture','design','refactor','analyze','implement','optimize',
    'debug','explain','review','compare','trace','migrate','integrate',
]
_COMPLEX = [
    '从头','完整实现','整个系统','全套','详细设计','深入',
    'from scratch','full implementation','entire system','deep dive',
    'comprehensive','step by step','end to end',
]
_ULTRA = [
    '完整实现整个','全套系统','端到端','完全从头',
    'full system implementation','end to end implementation',
    'complete from scratch','entire codebase',
]

def classify_heuristic(text: str) -> str:
    n = len(text.split())
    has_code = text.count('```') >= 2
    code_lines = sum(
        1 for l in text.splitlines()
        if re.match(r'\s*(def |class |function |import |from |#include|public |private )', l)
    )
    low = text.lower()
    heavy    = sum(1 for kw in _HEAVY   if kw in low)
    complex_ = sum(1 for kw in _COMPLEX if kw in low)

    if complex_ >= 1 or n > 120 or (has_code and n > 60) or heavy >= 4 or code_lines >= 8:
        return 'opus'
    if n < 12 and not has_code and heavy == 0 and code_lines == 0:
        return 'haiku'
    return 'sonnet'


# ── Think level classification ────────────────────────────────────────────────
def classify_think(text: str, model: str) -> str | None:
    """Disabled: always return None to preserve Opus quota."""
    return None


# ── Explicit model override (highest priority) ────────────────────────────────
_EXPLICIT_PREFIX = re.compile(
    r'^\s*(opus|sonnet|haiku)\b', re.IGNORECASE
)

def _check_explicit_override(prompt: str) -> str | None:
    """If prompt starts with a model name, honor it directly."""
    m = _EXPLICIT_PREFIX.match(prompt)
    if m:
        return m.group(1).lower()
    return None


# ── Dispatch ──────────────────────────────────────────────────────────────────
def classify(prompt: str) -> tuple[str, str]:
    """Returns (model, backend_used)."""
    # Explicit prefix always wins — "opus think hard ..." → opus
    explicit = _check_explicit_override(prompt)
    if explicit:
        return explicit, 'explicit'

    dispatch = {
        'ollama':    classify_ollama,
        'deepseek':  classify_deepseek,
        'groq':      classify_groq,
        'heuristic': classify_heuristic,
    }
    fn = dispatch.get(BACKEND)
    if fn is None:
        return classify_heuristic(prompt), 'heuristic(unknown-backend)'

    if BACKEND == 'heuristic':
        return fn(prompt), 'heuristic'

    try:
        result = fn(prompt)
        return result, BACKEND
    except Exception as e:
        print(f"[auto-router] {BACKEND} failed ({e}), falling back to heuristic", file=sys.stderr)
        return classify_heuristic(prompt), 'heuristic(fallback)'


# ── Multi-domain context routing ──────────────────────────────────────────────
# Each route: name, keyword list, context file (relative to CWD).
# 'symbol_patterns' only on nav: also fires on CamelCase/snake_case/MACRO tokens.
CONTEXT_ROUTES = [
    {
        'name': 'nav',
        'keywords': [
            '在哪','定义','实现','声明','引用','调用','查找','搜索','哪个文件','哪里',
            '驱动','内核','makefile','android.bp','android.mk',
            'where is','defined in','implemented in','find symbol','look up',
            'which file','search for','grep for','global ','gtags',
            'driver','kernel','dts','dtsi',
        ],
        'file': '.claude/nav_ref.md',
        'symbol_patterns': True,  # also match CamelCase / snake_case / MACRO tokens
    },
    {
        'name': 'codeindex',
        'keywords': [
            '在哪里','在哪','定义','符号','引用','查找','搜索','文件','全文','grep',
            'global','gtags','rg','arg','fd','where','find','symbol','search',
            '代码索引','索引','编译','clangd','active files',
        ],
        'file': '.claude/contexts/codeindex.md',
    },
    {
        'name': 'xlang',
        'keywords': [
            'jni','aidl','hidl','binder','syscall','ioctl',
            '跨边界','跨层','native层','hal层','系统调用','内核调用',
        ],
        'file': '.claude/contexts/xlang.md',
    },
    {
        'name': 'domain',
        'keywords': [
            'selinux','avc','sysfs','initrc','init.rc','kconfig','compatible',
            '设备树','节点','属性','property','sepolicy','te文件',
            'device tree','kernel config',
        ],
        'file': '.claude/contexts/domain.md',
    },
    {
        'name': 'docs',
        'keywords': [
            'datasheet','寄存器','register','时序','协议','规范','spec',
            '文档','手册','manual','接口定义','interface definition',
            'mipi','i2c','spi','uart','usb','hdmi','pcie','dsi','csi',
        ],
        'file': '.claude/contexts/docsindex.md',
    },
]

# Pre-compile patterns for each route
for _r in CONTEXT_ROUTES:
    _r['_pattern'] = re.compile(
        '|'.join(re.escape(k) for k in _r['keywords']), re.IGNORECASE
    )


def _load_context_file(path: str) -> str | None:
    if os.path.exists(path):
        with open(path) as f:
            return f.read()
    return None


def _symbol_pattern_match(text: str) -> bool:
    """True if text contains a likely symbol name (CamelCase/snake_case/MACRO)."""
    for tok in text.split():
        tok = tok.strip('`\'".,;:()[]{}')
        if len(tok) >= 4 and (
            re.match(r'^[A-Z][a-z]+[A-Z]', tok)      # CamelCase
            or re.match(r'^[a-z]+_[a-z_]+$', tok)    # snake_case
            or re.match(r'^[A-Z][A-Z_]{4,}$', tok)   # MACRO (5+ chars, excludes MIPI/HDMI)
        ):
            return True
    return False


def match_context_routes(text: str) -> list[tuple[str, str]]:
    """Return list of (route_name, content) for every route that matches text."""
    results = []
    for route in CONTEXT_ROUTES:
        hit = bool(route['_pattern'].search(text))
        if not hit and route.get('symbol_patterns'):
            hit = _symbol_pattern_match(text)
        if hit:
            content = _load_context_file(route['file'])
            if content:
                results.append((route['name'], content))
    return results


# Kept for state-file backward compatibility
def needs_nav_context(text: str) -> bool:
    route = CONTEXT_ROUTES[0]
    if route['_pattern'].search(text):
        return True
    return _symbol_pattern_match(text)


# ── Settings helpers ──────────────────────────────────────────────────────────
_SETTINGS = os.path.expanduser('~/.claude/settings.json')

def read_settings() -> dict:
    try:
        with open(_SETTINGS) as f:
            return json.load(f)
    except Exception:
        return {}

def write_settings(s: dict):
    with open(_SETTINGS, 'w') as f:
        json.dump(s, f, indent=2)

def normalize(raw: str) -> str:
    raw = raw.lower()
    for m in MODELS:
        if m in raw:
            return m
    return 'sonnet'


# ── State file (shared with Stop hook) ────────────────────────────────────────
def _state_path(session_id: str) -> str:
    sid = session_id or 'default'
    safe = ''.join(c for c in sid if c.isalnum() or c in '-_')[:64] or 'default'
    return f'/tmp/claude_router_{safe}.json'


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    try:
        data   = json.load(sys.stdin)
        prompt = data.get('prompt', '') or data.get('user_prompt', '') or ''
    except Exception:
        sys.exit(0)

    if not prompt.strip():
        sys.exit(0)

    recommended, via = classify(prompt)
    think_level      = classify_think(prompt, recommended)
    settings         = read_settings()
    current          = normalize(settings.get('model', 'sonnet'))

    # Persist for Stop hook (best effort)
    try:
        with open(_state_path(data.get('session_id', '')), 'w') as _sf:
            json.dump({
                'backend': via,
                'model':   recommended,
                'think':   think_level,
                'nav':     needs_nav_context(prompt),
            }, _sf)
    except Exception:
        pass

    labels = {
        'haiku':  'Haiku  ⚡',
        'sonnet': 'Sonnet ⚖',
        'opus':   'Opus   🧠',
    }

    info_parts = []

    # ── Model switch ──────────────────────────────────────────────────────────
    if recommended != current:
        settings['model'] = recommended
        write_settings(settings)
        info_parts.append(f"model: {labels[current]} → {labels[recommended]}")

    # ── Think + multi-domain context injection ────────────────────────────────
    additional_parts: list[str] = []

    if think_level:
        info_parts.append(f"think: {think_level}")
        additional_parts.append(think_level)

    matched_routes = match_context_routes(prompt)
    if matched_routes:
        names = '+'.join(n for n, _ in matched_routes)
        info_parts.append(f"ctx: {names}")
        for _, content in matched_routes:
            additional_parts.append(content)

    # ── Output ────────────────────────────────────────────────────────────────
    if info_parts:
        print(f"[auto-router/{via}] {' | '.join(info_parts)}", file=sys.stderr)

    if additional_parts:
        hook_output = {
            'hookSpecificOutput': {
                'hookEventName': 'UserPromptSubmit',
                'additionalContext': '\n\n'.join(additional_parts),
            }
        }
        print(json.dumps(hook_output))

    sys.exit(0)


if __name__ == '__main__':
    main()
