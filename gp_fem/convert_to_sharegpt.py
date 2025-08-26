# gp_fem/convert_both_sharegpt.py
from pathlib import Path
import json

ROOT = Path("/workspaces/whatsapp-autoresponder")
SOURCES = [
    (ROOT / "gp_fem" / "dialogs_style.jsonl",
     ROOT / "data"   / "dialogs_style.sharegpt.jsonl"),
    (ROOT / "gp_fem" / "dialogs_style_with_personality.jsonl",
     ROOT / "data"   / "dialogs_style_with_personality.sharegpt.jsonl"),
]

def iter_top_level_objects(text: str):
    """
    Itera objetos JSON de topo mesmo se estiverem 'colados' sem vírgulas
    (ex.: {...}{...}{...}). Respeita strings e colchetes/chaves aninhados.
    Retorna uma lista com os objetos decodificados; objetos inválidos são ignorados.
    """
    objs = []
    i, n = 0, len(text)
    in_str = False
    esc = False
    depth_curly = 0
    depth_brack = 0
    start = None

    while i < n:
        ch = text[i]

        if in_str:
            if esc:
                esc = False
            elif ch == '\\':
                esc = True
            elif ch == '"':
                in_str = False
            i += 1
            continue

        if ch == '"':
            in_str = True
            i += 1
            continue

        if ch == '{':
            if depth_curly == 0 and depth_brack == 0:
                start = i
            depth_curly += 1
        elif ch == '}':
            depth_curly -= 1
            if depth_curly == 0 and depth_brack == 0 and start is not None:
                snippet = text[start:i+1]
                try:
                    obj = json.loads(snippet)
                    objs.append(obj)
                except json.JSONDecodeError:
                    # ignora pedaços inválidos
                    pass
                start = None
        elif ch == '[':
            depth_brack += 1
        elif ch == ']':
            depth_brack -= 1

        i += 1
    return objs

def normalize_role(role):
    if not isinstance(role, str):
        return role
    r = role.strip().lower()
    if r == "cliente":
        return "user"
    if r == "garota":
        return "assistant"
    if r in ("user", "assistant"):
        return r
    return r  # mantém se vier algo fora do padrão

def to_sharegpt_record(obj: dict):
    """
    Aceita formatos:
      A) {"dialogue":[{"role":..., "content":...}, ...]}
      B) {"profile": {...}, "dialogue":[...]}
      C) {"messages":[...]}  (já no formato certo)
    Retorna dict com:
      {"messages":[...]} ou {"profile": {...}, "messages":[...]}
    """
    if not isinstance(obj, dict):
        return None

    # Já no formato ShareGPT?
    if isinstance(obj.get("messages"), list):
        msgs = []
        for m in obj["messages"]:
            if not isinstance(m, dict): 
                continue
            role = normalize_role(m.get("role"))
            content = m.get("content", "")
            if role in ("user", "assistant") and isinstance(content, str):
                msgs.append({"role": role, "content": content})
        if msgs:
            rec = {"messages": msgs}
            if isinstance(obj.get("profile"), dict):
                rec["profile"] = obj["profile"]
            return rec
        return None

    # Formatos A/B
    dlg = obj.get("dialogue")
    if not isinstance(dlg, list):
        return None

    msgs = []
    for m in dlg:
        if not isinstance(m, dict):
            continue
        role = normalize_role(m.get("role"))
        content = m.get("content", "")
        if role in ("user", "assistant") and isinstance(content, str):
            msgs.append({"role": role, "content": content})

    if not msgs:
        return None

    rec = {"messages": msgs}
    if isinstance(obj.get("profile"), dict):
        rec["profile"] = obj["profile"]
    return rec

def convert_file(src: Path, outp: Path):
    if not src.exists():
        print(f"❌ Arquivo não encontrado: {src}")
        return 0, 0
    raw = src.read_text(encoding="utf-8", errors="ignore")

    # 1) Tenta extrair vários objetos de topo
    objs = iter_top_level_objects(raw)

    # 2) Fallback: se vier um único objeto gigantesco com 'dialogue' sendo uma lista
    #    de mensagens, trata como UMA conversa.
    if not objs:
        try:
            obj = json.loads(raw)
            objs = [obj]
        except Exception:
            objs = []

    ok = 0
    outp.parent.mkdir(parents=True, exist_ok=True)
    with outp.open("w", encoding="utf-8") as f:
        for o in objs:
            rec = to_sharegpt_record(o)
            if rec:
                json.dump(rec, f, ensure_ascii=False)
                f.write("\n")
                ok += 1
    skipped = max(0, len(objs) - ok)
    print(f"✅ {src.name} → {outp.name} | conversas OK: {ok} | ignoradas: {skipped}")
    return ok, skipped

def main():
    total_ok = total_skipped = 0
    for src, outp in SOURCES:
        ok, skipped = convert_file(src, outp)
        total_ok += ok
        total_skipped += skipped
    print(f"🏁 CONCLUÍDO — total OK: {total_ok} | ignoradas: {total_skipped}")

if __name__ == "__main__":
    main()