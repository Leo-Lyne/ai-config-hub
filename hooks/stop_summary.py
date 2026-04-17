#!/usr/bin/env python3
"""Stop hook: report which backend classified this turn and which model answered.

Reads state written by auto_model_router.py at UserPromptSubmit, and emits a
systemMessage so the user sees e.g. `[router] deepseek → haiku (think: ultrathink)`.
"""
import json, sys, os

def _state_path(session_id: str) -> str:
    sid = session_id or 'default'
    safe = ''.join(c for c in sid if c.isalnum() or c in '-_')[:64] or 'default'
    return f'/tmp/claude_router_{safe}.json'

def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    path = _state_path(data.get('session_id', ''))
    if not os.path.exists(path):
        sys.exit(0)

    try:
        with open(path) as f:
            st = json.load(f)
    except Exception:
        sys.exit(0)

    backend = st.get('backend', '?')
    model   = st.get('model', '?')
    think   = st.get('think')
    nav     = st.get('nav', False)

    parts = [f"{backend} → {model}"]
    if think:
        parts.append(f"think: {think}")
    if nav:
        parts.append("nav: injected")

    print(json.dumps({"systemMessage": f"[router] {' | '.join(parts)}"}))
    sys.exit(0)


if __name__ == '__main__':
    main()
