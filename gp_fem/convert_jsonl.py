import json, re, sys
from pathlib import Path

ROOT = Path("/workspaces/whatsapp-autoresponder")
DATA = ROOT / "data"
FILES = [
    DATA/"dialogs_style_converted.norm.jsonl",
    DATA/"dialogs_style_with_personality_converted.norm.jsonl",
]

def norm_role(v):
    if not isinstance(v, str): return None
    r = v.strip().lower()
    if r in ("user","human","cliente"): return "user"
    if r in ("assistant","bot","garota","gpt"): return "assistant"
    return None

def map_msg(m):
    if not isinstance(m, dict): return None
    role = m.get("role") or m.get("from") or m.get("frm") or m.get("rol") or m.get("role:")
    role = norm_role(role)
    content = m.get("content") or m.get("value") or m.get("msg") or m.get("text")
    if isinstance(content, str):
        content = re.sub(r"\s+\n", "\n", content).strip()
    return {"role": role, "content": content} if role in ("user","assistant") and isinstance(content, str) and content else None

def repair_dialog(messages):
    mapped = [map_msg(m) for m in messages if isinstance(m, dict)]
    mapped = [m for m in mapped if m]
    if not mapped: return None
    # garantir que começa com user
    while mapped and mapped[0]["role"] != "user":
        mapped.pop(0)
    if len(mapped) < 2: return None
    # forçar alternância
    cleaned = []
    for m in mapped:
        if not cleaned:
            cleaned.append(m); continue
        if m["role"] == cleaned[-1]["role"]:
            cleaned[-1] = m   # mantém só a última fala do mesmo papel
        else:
            cleaned.append(m)
    # precisa ter ao menos um assistant
    if len(cleaned) < 2 or not any(x["role"] == "assistant" for x in cleaned):
        return None
    return cleaned

def fix_file(path: Path):
    total = ok = 0
    out_lines = []
    with path.open("r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line: continue
            total += 1
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            msgs = None
            if isinstance(obj, dict):
                for key in ("messages","dialogue","conversation"):
                    if isinstance(obj.get(key), list):
                        msgs = obj[key]; break
            if not isinstance(msgs, list): continue
            repaired = repair_dialog(msgs)
            if not repaired: continue
            out = {"messages": repaired}  # removemos profile para evitar tipos mistos
            out_lines.append(json.dumps(out, ensure_ascii=False))
    tmp = path.with_suffix(path.suffix + ".fixed")
    with tmp.open("w", encoding="utf-8") as f:
        if out_lines:
            f.write("\n".join(out_lines) + "\n")
    tmp.replace(path)
    ok = len(out_lines)
    print(f"✅ {path.name}: válidas={ok} | descartadas={total-ok} | total_lidas={total}")

for p in FILES:
    if p.exists():
        fix_file(p)
    else:
        print(f"⚠️ Arquivo não encontrado: {p}", file=sys.stderr)
